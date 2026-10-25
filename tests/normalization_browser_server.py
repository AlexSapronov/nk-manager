"""Mixed synthetic draft statuses; never contacts NK."""
import copy
import os
import uvicorn
from main import create_app
from tests.test_presets import sample
from tests.test_service import gtin

if __name__=='__main__':
    app=create_app(os.environ['NK_DATA']);app.state.store.setup('tester','isolated-test-admin-password')
    for i in range(10):
        raw=copy.deepcopy(sample());raw['good_id']=30000+i
        raw['good_name']=['Разъём на кабель PN-TAIL','PN-HEAD Разъём на кабель','Неизвестное описание','Разъём на кабель ON-MODERATION','Разъём на кабель PUBLISHED','Разъём на кабель OTHER-OWNER','Разъём на кабель ARCHIVED','Разъём на кабель BUSY','Разъём на кабель SAME','Разъём на кабель SAME'][i]
        raw['good_attrs']=[a for a in raw['good_attrs'] if a['attr_id']!=13914]
        raw['identified_by'][0]['value']=gtin(700+i)
        if i==3:raw['good_detailed_status']=['moderation']
        if i==4:raw.update(good_status='published',good_detailed_status=['published'],good_signed=True)
        id=app.state.nk.apply_remote(raw)
        with app.state.store.transaction() as db:
            if i==5:db.execute('UPDATE cards SET owner=? WHERE id=?',('colleague',id))
            if i==6:db.execute('UPDATE cards SET archived=1 WHERE id=?',(id,))
        if i==7:
            job=app.state.nk.enqueue('tester','save',{},app.state.service.get(id))
            app.state.nk.state(job,'unknown',{})
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ['NK_TEST_PORT']),access_log=False)
