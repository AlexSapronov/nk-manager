"""Synthetic full draft workflow: no production NK access."""
import copy
import json
import os

import httpx
import uvicorn
from fastapi.responses import JSONResponse

from main import create_app
from tests.nk_browser_server import BrowserNK
from tests.test_nk import signer
from tests.test_draft_workflow import ready

if __name__=='__main__':
    app=create_app(os.environ['NK_DATA']);app.state.store.setup('tester','isolated-test-admin-password')
    engine=app.state.nk;svc=app.state.service;store=app.state.store;remote=BrowserNK();engine.client.transport=httpx.MockTransport(remote.handle)
    key,cert,thumb=signer();engine.save_config('tester',{'api_key':'synthetic-key','writes_enabled':True,'thumbprint':thumb});engine.sync('tester');engine.tick()
    c=ready((store,svc,engine,remote,key,cert,thumb),'PIPELINE-READY');c['attributes']['Страна [#1]']='FR';svc.save('tester',c,c['id'])
    raw=copy.deepcopy(remote.raw);raw.update(good_id=333,good_status='published',good_detailed_status=['published'],good_signed=True,good_name='ALREADY-PUB Разъём на кабель');raw['identified_by'][0]['value']='04640000000028'
    for a in raw['good_attrs']:
        if a['attr_id']==13914:a['attr_value']='ALREADY-PUB'
        if a['attr_id']==2478:a['attr_value']=raw['good_name']
    engine.apply_remote(raw)
    for pn in ['ALREADY-PUB','already-pub','NEEDS-KIND']:
        svc.save('tester',{'pn':pn,'tnved':'8536901000','category':'Соединители','attributes':{'Товарный знак [#2504]':'BRAND'}})
    @app.get('/__test__/counts')
    def counts():
        return {'save':sum(r[1]=='/v3/feed' for r in remote.requests),'moderate':sum(r[1]=='/v3/feed-moderation' for r in remote.requests)}
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ['NK_TEST_PORT']),access_log=False)
