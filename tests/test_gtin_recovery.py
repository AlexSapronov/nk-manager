"""Regression tests: previously registered GTINs are never allocated twice."""
import copy
import json
from io import BytesIO

import httpx
from openpyxl import Workbook, load_workbook
from fastapi.testclient import TestClient

from nk_manager.gtin_recovery import preview, start, snapshot, export
from nk_manager.nk import Engine
from nk_manager.creation import baseline, NAMES
from tests.test_nk import NK, env, tick
from tests.test_service import app, login, ORIGIN, gtin


def xlsx(rows, extra=False):
    book=Workbook();ws=book.active
    ws.append(['GTIN','Производитель']+(['PN','ТН ВЭД'] if extra else []))
    for r in rows:ws.append(r)
    output=BytesIO();book.save(output);book.close();return output.getvalue()


def kind():
    return next(k for k in baseline() if k['tnved']=='8504409100' and k['name']=='AC/DC преобразователь')


class RecoveryNK(NK):
    def __init__(self, present=True, category=False):
        super().__init__()
        self.good = {
            'good_id': 801, 'good_name': 'AC/DC преобразователь RECOVER-1',
            'good_status':'draft','good_detailed_status':['draft'],
            'brand_name':'MORNSUN', 'tnved':'8504409100',
            'categories':[{'cat_id':int(kind()['category_id']),'cat_name':kind()['category']}] if category else [],
            'identified_by':[{'type':'gtin','level':'trade-unit','value':gtin(42)}],
            'good_attrs':[{'attr_id':13933,'attr_name':'Код ТН ВЭД','attr_value':'8504409100'}]
        } if present else None
        self.feeds=0;self.allocations=0
    def handle(self, request):
        path=request.url.path
        if path=='/v3/feed-product' and 'gtin' in request.url.params:
            self.requests.append((request.method,path,dict(request.url.params),request.content))
            item=self.good if self.good and self.good['identified_by'][0]['value']==request.url.params['gtin'] else None
            return httpx.Response(200,json={'result':[copy.deepcopy(item)] if item else []})
        if path=='/v3/attributes':
            self.requests.append((request.method,path,dict(request.url.params),request.content))
            return httpx.Response(200,json={'result':[{'attr_id':int(k),'attr_name':name,'attr_type':'o'} for k,name in NAMES.items()]})
        if path=='/v3/feed':
            self.requests.append((request.method,path,dict(request.url.params),request.content))
            body=json.loads(request.content);self.feeds+=1
            if self.good is None:
                self.good={'good_id':902,'good_name':body['good_name'],'good_status':'draft','good_detailed_status':['draft'],
                           'brand_name':body['brand'],'identified_by':body['identified_by'],'good_attrs':[],'categories':[]}
            self.good['good_name']=body.get('good_name',self.good['good_name'])
            if 'categories' in body:
                self.good['categories']=[{'cat_id':int(c),'cat_name':kind()['category']} for c in body['categories']]
            for a in body['good_attrs']:
                self.good['good_attrs']=[x for x in self.good['good_attrs'] if str(x['attr_id'])!=str(a['attr_id'])]+[dict(a,attr_name=NAMES.get(str(a['attr_id']),'Поле'))]
            return httpx.Response(200,json={'result':{'feed_id':self.feeds}})
        if path=='/v3/feed-status':
            self.requests.append((request.method,path,dict(request.url.params),request.content))
            return httpx.Response(200,json={'result':{'feed_id':self.feeds,'status':'Received'}})
        if path=='/v3/generate-gtins':
            self.allocations+=1
            raise AssertionError('Recovery MUST NOT allocate a GTIN')
        return super().handle(request)


def test_two_column_gs1_recovers_missing_nk_category(env):
    store, svc, engine, *_ = env
    remote=RecoveryNK()
    engine.client.transport=httpx.MockTransport(remote.handle)
    data=xlsx([[gtin(42),'MORNSUN']])
    batch=preview(engine,'tester',data,'gs1.xlsx')
    assert batch['state']=='scanning'
    tick(engine)
    scanned=snapshot(store,batch['id'])
    assert scanned['rows'][0]['state']=='ready'
    assert scanned['rows'][0]['pn']=='RECOVER-1'
    assert scanned['rows'][0]['tnved']=='8504409100'
    start(engine,'tester',batch['id'],{'8504409100':kind()['id']})
    for _ in range(8):tick(engine)
    result=snapshot(store,batch['id'])
    assert result['rows'][0]['state']=='done',result
    assert result['rows'][0]['card_id']
    saved=svc.get(result['rows'][0]['card_id'])
    assert saved['gtin']==gtin(42) and saved['external_id']=='801'
    assert saved['category']==kind()['category']
    assert remote.feeds==1 and remote.allocations==0
    assert not any(path=='/v3/feed-moderation' for _,path,_,_ in remote.requests)
    book=load_workbook(BytesIO(export(store,batch['id'])))
    assert book.active['A2'].data_type=='s' and book.active['A2'].value==gtin(42)
    assert preview(engine,'tester',data,'rename.xlsx')['id']==batch['id']


def test_gtin_not_found_requires_pn_and_tnved(env):
    store, _, engine, *_=env
    remote=RecoveryNK(present=False)
    engine.client.transport=httpx.MockTransport(remote.handle)
    batch=preview(engine,'tester',xlsx([[gtin(45),'MORNSUN']]),'missing.xlsx')
    tick(engine)
    row=snapshot(store,batch['id'])['rows'][0]
    assert row['state']=='need_data'
    assert remote.feeds==remote.allocations==0


def test_existing_gtin_can_create_new_nk_draft_without_allocation(env):
    store, svc, engine, *_=env
    remote=RecoveryNK(present=False)
    engine.client.transport=httpx.MockTransport(remote.handle)
    batch=preview(engine,'tester',xlsx([[gtin(45),'MORNSUN','RECOVER-2','8504409100']],extra=True),'new.xlsx')
    tick(engine)
    assert snapshot(store,batch['id'])['rows'][0]['state']=='ready'
    start(engine,'tester',batch['id'],{'8504409100':kind()['id']},confirm_brand=True)
    for _ in range(8):tick(engine)
    result=snapshot(store,batch['id'])
    assert result['rows'][0]['state']=='done',result
    assert remote.feeds==1 and remote.allocations==0
    assert svc.get(result['rows'][0]['card_id'])['gtin']==gtin(45)


def test_duplicate_input_only_creates_one_draft(env):
    store, _, engine, *_=env
    remote=RecoveryNK()
    engine.client.transport=httpx.MockTransport(remote.handle)
    batch=preview(engine,'tester',xlsx([[gtin(42),'MORNSUN'],[gtin(42),'MORNSUN']]),'duplicates.xlsx')
    tick(engine)
    start(engine,'tester',batch['id'],{'8504409100':kind()['id']})
    for _ in range(8):tick(engine)
    rows=snapshot(store,batch['id'])['rows']
    assert [r['state'] for r in rows]==['done','done']
    assert rows[0]['card_id']==rows[1]['card_id']
    assert remote.feeds==1 and remote.allocations==0


def test_recovery_routes_admin_only(app):
    data=xlsx([[gtin(42),'MORNSUN']])
    with TestClient(app,base_url=ORIGIN) as client:
        headers=login(client)
        assert client.get('/api/admin/recovery-batches').status_code==403
        assert client.post('/api/admin/recovery-batches/preview',files={'file':('x.xlsx',data)},headers=headers).status_code==403
        assert client.get('/api/admin/recovery-batches/abc').status_code==403
        assert client.get('/api/admin/recovery-batches/abc/export').status_code==403
        assert client.post('/api/admin/recovery-batches/abc/start',json={'confirmed':True},headers=headers).status_code==403
        headers=login(client,admin=True)
        response=client.post('/api/admin/recovery-batches/preview',files={'file':('x.xlsx',data)},headers=headers)
        assert response.status_code==200,response.text
        id=response.json()['id']
        assert client.post('/api/admin/recovery-batches/'+id+'/start',json={'confirmed':False},headers=headers).status_code==422
