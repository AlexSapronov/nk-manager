import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from nk_manager.creation import baseline, catalog, draft, key
from tests.test_nk import env
from tests.test_presets import sample
from tests.test_service import app, login, ORIGIN


def acdc():
    return next(k for k in baseline() if k['tnved']=='8504409100' and k['name']=='AC/DC преобразователь')


def inputs():
    return {'pn':'LM50-23B24R2','tnved':'8504409100','kind_id':acdc()['id'],'brand':'MORNSUN'}


def test_dictionary_keeps_distinct_kinds_and_omits_pn_as_kind():
    assert len(baseline())==155
    assert len({k['tnved'] for k in baseline()})==11
    assert sum(k['published_count'] for k in baseline())==1683
    assert not any(k['name']=='URB2424LD-60WR3' for k in baseline())
    assert len({k['id'] for k in baseline()})==len(baseline())


def test_screenshot_identity_and_standard_defaults_without_copying_gtin(env):
    store,service,engine,*_=env
    c=draft(store,inputs())
    assert c['name']=='LM50-23B24R2 AC/DC преобразователь'
    a=c['attributes']
    assert a[key('2478')]==c['name']
    assert a[key('13914')]=='LM50-23B24R2' and a[key('13914')+'::unit']=='Артикул'
    assert a[key('12')]=='НЕТ В СПРАВОЧНИКЕ' and a[key('23828')]=='AC/DC преобразователь'
    assert a[key('24003')]=='КОЛИЧЕСТВО'
    assert a[key('2463')]==a[key('23819')]=='ОТСУТСТВУЕТ'
    assert a[key('23300')+'::unit']=='ДА'
    assert a[key('3961')]=='27.11.50.120'
    assert not c['gtin'] and not any('[#24004]' in k or '[#24005]' in k for k in a)
    c['name']='Неверное ручное имя';c['pn']='LM50-23B24R3'
    saved=service.save('tester',c)
    assert saved['name']=='LM50-23B24R3 AC/DC преобразователь'
    assert saved['attributes'][key('2478')]==saved['name']
    assert store.one('SELECT cat_id FROM nk_scopes WHERE card_id=?',(saved['id'],))['cat_id']==acdc()['category_id']
    assert saved['source_snapshot']['creation']['kind_id']==acdc()['id']
    saved['pn']='LM50-23B24R4'
    updated=service.save('tester',saved,saved['id'])
    assert updated['name']=='LM50-23B24R4 AC/DC преобразователь'
    assert updated['attributes'][key('13914')]=='LM50-23B24R4'


def test_wrong_scope_and_ambiguous_brand_are_not_guessed(env):
    store,*_=env
    with pytest.raises(ValueError,match='Выберите вид'):
        draft(store,{**inputs(),'tnved':'8536699008'})
    with pytest.raises(ValueError,match='товарный знак'):
        draft(store,{**inputs(),'brand':''})
    one=next(k for k in baseline() if len(k['brands'])==1)
    c=draft(store,{'pn':'UNIQUE-PN','tnved':one['tnved'],'kind_id':one['id'],'brand':one['brands'][0]['name']})
    assert c['attributes'][key('2504')]==one['brands'][0]['name']


def test_new_published_kind_added_after_sync_drafts_excluded(env):
    store,service,engine,*_=env
    raw=copy.deepcopy(sample())
    raw['good_attrs'] += [{'attr_id':12,'attr_name':'Вид товара','attr_value':'НЕТ В СПРАВОЧНИКЕ'}, {'attr_id':23828,'attr_name':'Вид товара (при отсутствии в справочнике)','attr_value':'Новый уникальный вид'}]
    engine.apply_remote(raw)
    assert not any(k['name']=='Новый уникальный вид' for k in catalog(store))
    raw.update(good_status='published',good_detailed_status=['published'],good_signed=True)
    engine.apply_remote(raw)
    assert sum(k['name']=='Новый уникальный вид' for k in catalog(store))==1


def test_new_card_prepares_with_stored_category_and_brand_without_retyping(env):
    store,service,engine,remote,*_=env
    c=draft(store,inputs());c['gtin']='04640000000028'
    c=service.save('tester',c)
    def transport(request):
        if request.url.path=='/v3/attributes':
            from nk_manager.creation import NAMES
            return httpx.Response(200,json={'result':[{'attr_id':int(id),'attr_name':name,'attr_type':'m' if id in ('3959','13933') else 'o','attr_field_type':'text',**({'attr_preset_only':True,'attr_preset':['dictionary input differs from read-back code']} if id in ('3959','13933') else {})} for id,name in NAMES.items()]})
        return remote.handle(request)
    engine.client.transport=httpx.MockTransport(transport)
    job=engine.prepare('tester','save',c['id'],c['revision'])
    payload=json.loads(store.one('SELECT payload FROM nk_jobs WHERE id=?',(job,))['payload'])['entry']
    assert payload['brand']=='MORNSUN'
    assert payload['categories']==[int(acdc()['category_id'])]
    assert payload['good_name']=='LM50-23B24R2 AC/DC преобразователь'
    assert payload['gtin']=='04640000000028'
    assert payload['tnved']=='8504409100'
    assert not any(a['attr_id'] in (3959,13933) for a in payload['good_attrs'])
    assert service.get(c['id'])['attributes'][key('3959')]=='8504'
    assert service.get(c['id'])['attributes'][key('13933')]=='8504409100'
    assert any(a['attr_id']==2478 and a['attr_value']==payload['good_name'] for a in payload['good_attrs'])
    assert not any(r[1]=='/v3/feed' for r in remote.requests)


def test_creation_endpoints_are_local_and_authenticated(app):
    with TestClient(app,base_url=ORIGIN) as client:
        assert client.get('/api/creation/catalog').status_code==401
        headers=login(client)
        assert len(client.get('/api/creation/catalog').json()['kinds'])==155
        response=client.post('/api/creation/preview',json=inputs(),headers=headers)
        assert response.status_code==200,response.text
        assert response.json()['name']=='LM50-23B24R2 AC/DC преобразователь'
        assert client.get('/api/cards').json()==[]


def test_model_brand_metadata_row_is_preserved_when_pn_changes():
    from nk_manager.service import validate_card
    attrs={key('12'):'НЕТ В СПРАВОЧНИКЕ',key('23828'):'Разъём',key('2504'):'BRAND',key('13914'):'BRAND',key('13914')+' · 6':'OLD-PN'}
    c=validate_card({'pn':'NEW-PN','attributes':attrs})
    assert c['name']=='NEW-PN Разъём'
    assert c['attributes'][key('13914')]=='BRAND'
    assert c['attributes'][key('13914')+' · 6']=='NEW-PN'
    assert attrs[key('13914')+' · 6']=='OLD-PN'
