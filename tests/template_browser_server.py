"""Sparse NK drafts for UI regression. All records are synthetic, no NK calls."""
import copy
import os

import uvicorn
from main import create_app
from tests.test_presets import sample
from tests.test_service import gtin

if __name__=='__main__':
    app=create_app(os.environ['NK_DATA'])
    app.state.store.setup('tester','isolated-test-admin-password')
    for i in range(92):
        raw=copy.deepcopy(sample())
        raw['good_id']=20000+i
        raw['good_name']=f'Штепсельный разъём TEST-{i:03}' if i<90 else f'Реальное совпадение {i}'
        raw['good_attrs']=[a for a in raw['good_attrs'] if a['attr_id']!=13914]
        raw['identified_by'][0]['value']=gtin(i+50 if i<90 else 500)
        app.state.nk.apply_remote(raw)
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ['NK_TEST_PORT']),access_log=False)
