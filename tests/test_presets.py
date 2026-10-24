import copy
import json

from main import create_app
from nk_manager.nk import Engine, recover_missing_pn, source_pn
from nk_manager.presets import bundle, install
from nk_manager.service import Service
from nk_manager.store import Store
from tests.test_nk import NK


def sample():
    raw=NK().raw
    raw['categories']=[{'cat_id':551143,'cat_name':'8536901000 Соединители и контактные элементы для проводов и кабелей'}]
    raw['good_attrs'][1].update(attr_id=13914,attr_name='Модель / артикул производителя',attr_value='PN-123')
    return raw


def test_factory_installs_active_presets_once_and_no_cards(tmp_path):
    app=create_app(tmp_path)
    store=app.state.store
    assert len(store.all("SELECT * FROM templates WHERE status='active'"))==11
    assert len(store.all('SELECT * FROM cards'))==0
    before=store.all('SELECT * FROM templates ORDER BY id')
    create_app(tmp_path)
    assert store.all('SELECT * FROM templates ORDER BY id')==before
    for p in bundle().values():
        automatic=[r for r in p['body']['rules'] if r['mode']=='fill_if_empty']
        assert len(automatic)==3
        assert next(r['value'] for r in automatic if '[#3961]' in r['attribute'])==p['default_okpd2']


def test_pn_uses_official_id_not_brand_or_first_repeated_value():
    raw=sample()
    raw['good_attrs'][1]['attr_name']='Изменённое имя атрибута'
    raw['good_attrs'].insert(0,{'attr_id':13914,'attr_name':'Модель / артикул производителя','attr_value':raw['brand_name']})
    assert source_pn(raw)=='PN-123'
    raw['good_attrs'].append({'attr_id':13914,'attr_name':'Модель','attr_value':'OTHER'})
    assert source_pn(raw)==''  # No guessing between distinct model values.


def test_startup_repairs_only_empty_clean_unblocked_pn(tmp_path):
    store=Store(tmp_path);engine=Engine(store);raw=sample()
    ids=[]
    for i in range(4):
        raw['good_id']=100+i
        raw['identified_by'][0]['value']='464063834300'+str(i)
        ids.append(engine.apply_remote(copy.deepcopy(raw)))
    with store.transaction() as db:
        db.execute("UPDATE cards SET pn='' WHERE id IN (?,?,?)",ids[:3])
        db.execute('UPDATE cards SET dirty=1 WHERE id=?',(ids[1],))
        db.execute("UPDATE cards SET pn='LOCAL' WHERE id=?",(ids[3],))
    card=Service(store).get(ids[2]);engine.enqueue('a','save',{'entry':{}},card)
    recover_missing_pn(store)
    assert Service(store).get(ids[0])['pn']=='PN-123'
    assert Service(store).get(ids[1])['pn']==''
    assert Service(store).get(ids[2])['pn']==''
    assert Service(store).get(ids[3])['pn']=='LOCAL'
    count=len(store.all("SELECT * FROM audit WHERE action='nk_pn_recovered'"))
    recover_missing_pn(store)
    assert len(store.all("SELECT * FROM audit WHERE action='nk_pn_recovered'"))==count==1


def test_builtin_matches_nk_category_id_adds_missing_okpd_locally(tmp_path):
    store=Store(tmp_path);engine=Engine(store);install(store);svc=Service(store)
    id=engine.apply_remote(sample());card=svc.get(id)
    assert card['pn']=='PN-123'
    plan=svc.template_plan('a',[id]);changes=plan['entries'][0]['changes']
    assert changes=={'Код ОКПД2 [#3961]':'27.33.13.110','Группа ТНВЭД [#3959]':'8536'}
    result=svc.apply_template_plan('a',plan['id'],'')
    assert result[0]['status']=='updated'
    current=svc.get(id)
    assert current['external_id']=='111' and current['dirty']==1
    assert current['category']==card['category']  # Scope match does not rewrite NK identity.
    assert json.loads(store.one('SELECT raw FROM nk_cards WHERE card_id=?',(id,))['raw'])==sample()
    current['attributes']['Код ОКПД2 [#3961]']='27.33.13.120'
    current=svc.save('a',current,id)
    assert svc.template_plan('a',[id])['entries'][0]['changes']=={}


def test_builtin_resolves_attribute_id_and_never_duplicates_filled_okpd(tmp_path):
    store=Store(tmp_path);install(store);svc=Service(store);p=bundle()['8536901000']['body']
    card=svc.save('a',{'pn':'CASE','tnved':p['tnved'],'category':p['category'],'card_type':'unit','attributes':{'Другое название [#3961]':'MANUAL'}})
    changes=svc.template_plan('a',[card['id']])['entries'][0]['changes']
    assert all('[#3961]' not in k for k in changes)
    assert len(changes)==2


def test_wrong_category_and_set_do_not_receive_builtin_defaults(tmp_path):
    store=Store(tmp_path);install(store);svc=Service(store)
    for kind,category in [('unit','Неподтверждённая категория'),('set',bundle()['8536901000']['body']['category'])]:
        card=svc.save('a',{'pn':kind,'tnved':'8536901000','category':category,'card_type':kind,'attributes':{}})
        assert svc.template_plan('a',[card['id']])['entries'][0]['changes']=={}


def test_custom_template_overrides_builtin_and_survives_restart(tmp_path):
    store=Store(tmp_path);install(store);svc=Service(store)
    p=copy.deepcopy(bundle()['8536901000']['body'])
    card=svc.save('a',{'pn':'CUSTOM','tnved':p['tnved'],'category':p['category'],'card_type':'unit','attributes':{'Код ОКПД2 [#3961]':''}})
    p['name']='Мой ОКПД2';p['rules']=[{'attribute':'Код ОКПД2 [#3961]','mode':'fill_if_empty','value':'27.33.13.120'}]
    id=svc.import_template('a',p);svc.approve_template('admin',id)
    install(store)
    assert store.one('SELECT status FROM templates WHERE id=?',(id,))['status']=='active'
    assert svc.template_plan('a',[card['id']])['entries'][0]['changes']=={'Код ОКПД2 [#3961]':'27.33.13.120'}


def test_edit_pn_updates_model_not_repeated_brand_record(tmp_path):
    import httpx
    remote=NK();remote.raw=sample()
    remote.raw['good_attrs'].insert(0,{'attr_id':13914,'attr_name':'Модель / артикул производителя','attr_value':remote.raw['brand_name']})
    store=Store(tmp_path);engine=Engine(store,httpx.MockTransport(remote.handle));svc=Service(store)
    engine.save_config('a',{'api_key':'isolated-test-key','writes_enabled':True})
    engine.sync('a');engine.tick()
    card=svc.cards()[0];card['pn']='PN-UPDATED'
    card=svc.save('a',card,card['id'],admin=True)
    job=engine.prepare('a','save',card['id'],card['revision'],admin=True)
    entry=json.loads(store.one('SELECT payload FROM nk_jobs WHERE id=?',(job,))['payload'])['entry']
    model_edits=[a for a in entry['good_attrs'] if a['attr_id']==13914]
    assert len(model_edits)==1 and model_edits[0]['attr_value']=='PN-UPDATED'
    assert remote.raw['good_attrs'][0]['attr_value']==remote.raw['brand_name']
