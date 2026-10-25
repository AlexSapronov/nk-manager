"""Synthetic external NK for the admin creation browser test."""
import os
import httpx
import uvicorn
from main import create_app
from tests.test_bulk_creation import BulkNK

if __name__=='__main__':
    app=create_app(os.environ['NK_DATA'])
    app.state.store.setup('tester','isolated-test-admin-password')
    engine=app.state.nk;remote=BulkNK()
    engine.client.transport=httpx.MockTransport(remote.handle)
    engine.save_config('tester',{'api_key':'synthetic-key','writes_enabled':True})
    engine.sync('tester');engine.tick()
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ['NK_TEST_PORT']),access_log=False)
