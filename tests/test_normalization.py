import copy
import json

import pytest

from nk_manager.creation import baseline, key
from nk_manager.normalization import propose, eligible
from nk_manager.normalization_service import preview, apply
from nk_manager.nk import Engine
from nk_manager.service import Service, Conflict
from nk_manager.store import Store
from tests.test_presets import sample


def card(name='Штепсельный разъём PN-TAIL'):
    return {'id':'x','pn':'','tnved':'8536901000','name':name,'gtin':'04640000000011','category':'Соединители','card_type':'unit','attributes':{key('2504'):'BRAND'},'source_snapshot':{},'archived':0,'dirty':0,'owner':'','revision':1,'external_status':'Черновик'}


def test_both_name_orders_and_longest_exact_kind_recover_without_fuzzy_pn():
    for name,pn,kind in [('Штепсельный разъём PN-X','PN-X','Штепсельный разъём'),('PN-X Штепсельный разъём','PN-X','Штепсельный разъём'),('Разъём на кабель SEV100FDXL/24V','SEV100FDXL/24V','Разъём на кабель')]:
        original=card(name);p=propose(original,baseline());a=p['after']
        assert not p['issues']
        assert a['pn']==pn and a['name']==pn+' '+kind
        assert a['attributes'][key('13914')]==pn
        assert a['attributes'][key('2478')]==a['name']
        assert original['pn']==''
        assert a['category']==original['category']
        assert propose({**original,**a},baseline())['changes']==[]


def test_unknown_name_missing_brand_and_conflicting_values_are_not_guessed():
    c=card('Неизвестный товар XYZ');c['attributes']={key('13914'):'PN-A',key('13914')+' · 6':'PN-B',key('13933'):'8536699008'}
    p=propose(c,baseline())
    assert 'Не указан PN' in p['issues']
    assert 'Не указан товарный знак' in p['issues']
    assert any('Разные PN' in i for i in p['issues'])
    assert any('ТН ВЭД' in i for i in p['issues'])
    assert p['after']['pn']=='' and p['after']['name']==c['name']
    assert p['after']['attributes'][key('13914')]=='PN-A'


def test_format_aliases_filled_okpd_and_documents_preserved():
    c=card('Разъём на кабель PN-A');c['attributes'].update({'Модель / артикул':'PN-A','ОКПД2':'27.33.13.190','Декларация о соответствии [#23557]':'DECL-1','Минимальное значение [#24004]':'2'})
    p=propose(c,baseline());a=p['after']['attributes']
    assert 'Модель / артикул' not in a and a[key('13914')]=='PN-A'
    assert a[key('3961')]=='27.33.13.190'
    assert a['Декларация о соответствии [#23557]']=='DECL-1'
    assert a['Минимальное значение [#24004]']=='2'
    assert key('23300') not in a


def test_brand_in_repeated_model_row_is_not_used_as_pn():
    c=card();c['attributes'].update({key('13914'):'BRAND',key('13914')+' · 6':'PN-TAIL'})
    p=propose(c,baseline())
    assert p['after']['pn']=='PN-TAIL'
    assert p['after']['attributes'][key('13914')]=='BRAND'
    assert not p['issues']


@pytest.mark.parametrize('status',['moderation','notsigned','published','archive'])
def test_only_real_drafts_eligible(status):
    c=card();c['source_snapshot']={'nk':{'good_status':status,'good_detailed_status':[status]}}
    assert eligible(c)


def setup(tmp_path,n=2):
    store=Store(tmp_path);engine=Engine(store);svc=Service(store);ids=[]
    for i in range(n):
        raw=copy.deepcopy(sample());raw['good_id']=100+i;raw['good_name']=f'Разъём на кабель PN-{i}'
        raw['good_attrs']=[a for a in raw['good_attrs'] if a['attr_id']!=13914]
        raw['identified_by'][0]['value']=str(4640000000011+i)
        ids.append(engine.apply_remote(raw))
    return store,engine,svc,ids


def test_preview_then_partial_apply_revision_conflict_audit_and_idempotence(tmp_path):
    store,engine,svc,ids=setup(tmp_path)
    plan=preview(svc,'tester');assert len(plan['entries'])==2
    assert all(e['changes'] for e in plan['entries'])
    assert not any(c['dirty'] for c in svc.cards())
    svc.claim('tester',ids[1],svc.get(ids[1])['revision'])
    result=apply(svc,'tester',plan['id'],ids)
    assert {r['id']:r['status'] for r in result}=={ids[0]:'updated',ids[1]:'conflict'}
    saved=svc.get(ids[0]);assert saved['pn']=='PN-0'
    audit=json.loads(store.one("SELECT detail FROM audit WHERE action='draft_normalized_local'")['detail'])
    assert audit['before']['pn']=='' and audit['after']['pn']=='PN-0'
    again=preview(svc,'tester',[ids[0]])['entries'][0]
    assert not again['changes']
    with pytest.raises(Conflict):apply(svc,'tester',plan['id'],ids)


def test_moderation_transition_and_other_owner_excluded_before_apply(tmp_path):
    store,engine,svc,ids=setup(tmp_path)
    plan=preview(svc,'tester')
    with store.transaction() as db:
        db.execute('UPDATE cards SET owner=? WHERE id=?',('colleague',ids[0]))
    raw=json.loads(store.one('SELECT raw FROM nk_cards WHERE card_id=?',(ids[1],))['raw'])
    raw['good_detailed_status']=['moderation'];engine.apply_remote(raw)
    assert all(r['status']=='conflict' for r in apply(svc,'tester',plan['id'],ids))
    assert all(e['state']=='skipped' for e in preview(svc,'tester')['entries'])


def test_inferred_exact_duplicates_checked_together_before_any_write(tmp_path):
    store,engine,svc,ids=setup(tmp_path)
    with store.transaction() as db:db.execute('UPDATE cards SET name=?',('Разъём на кабель SAME',))
    plan=preview(svc,'tester')
    assert all(e['duplicates'] for e in plan['entries'])
    assert all(r['status']=='updated' for r in apply(svc,'tester',plan['id'],ids))
    assert all(c['pn']=='SAME' for c in svc.cards())
    assert all(not e['changes'] for e in preview(svc,'tester')['entries'])


def test_plan_ownership_and_unplanned_ids(tmp_path):
    store,engine,svc,ids=setup(tmp_path)
    plan=preview(svc,'tester',[ids[0]])
    with pytest.raises(Conflict):apply(svc,'other',plan['id'],[ids[0]])
    with pytest.raises(ValueError):apply(svc,'tester',plan['id'],[ids[1]])
    assert apply(svc,'tester',plan['id'],[ids[0]])[0]['status']=='updated'


def test_short_dictionary_kind_cannot_turn_unrecognized_description_into_pn():
    c=card('Штепсельный разъём несуществующее описание PN-123')
    p=propose(c,baseline())
    assert not p['after']['pn']
    assert 'Не указан PN' in p['issues']
    assert p['after']['name']==c['name']


def test_same_enum_meaning_is_canonicalized_without_changing_other_values():
    c=card();c['pn']='PN-TAIL';c['attributes'].update({key('12'):'Нет в справочнике',key('23828'):'Штепсельный разъём',key('24003'):'Количество'})
    p=propose(c,baseline())
    assert not p['issues']
    assert p['after']['name']=='PN-TAIL Штепсельный разъём'
    assert p['after']['attributes'][key('12')]=='НЕТ В СПРАВОЧНИКЕ'
    assert p['after']['attributes'][key('24003')]=='КОЛИЧЕСТВО'
