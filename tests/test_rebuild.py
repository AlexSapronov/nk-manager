"""Full rebuild safety and performance-contract regression tests."""
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from nk_manager.rebuild import begin, status
from nk_manager.nk import Engine
from nk_manager.service import Conflict
from tests.test_nk import env, NK, tick
from tests.test_service import app, login, ORIGIN


def test_rebuild_stages_then_atomically_replaces_and_preserves_auth(env):
    store, service, engine, remote, *_ = env
    added=service.save('tester', {'pn':'NEVER-IN-NK','gtin':'','tnved':'','name':'','category':'','attributes':{}})
    assert len(service.cards())==2
    before=store.one('SELECT COUNT(*) AS count FROM audit')['count']
    job=begin(engine,'tester')
    info=status(store)
    assert info['backup'].endswith('.sqlite3')
    backup=store.directory / 'backups' / info['backup']
    assert backup.is_file()
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0]==2
    assert len(service.cards())==2  # No deletion during remote download
    tick(engine)
    assert len(service.cards())==2
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(job,))['state']=='prepared'
    tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(job,))['state']=='confirmed'
    assert [c['pn'] for c in service.cards()]==['PN-ALPHA']
    assert store.one("SELECT COUNT(*) AS count FROM audit")['count']>before
    assert store.one("SELECT COUNT(*) AS count FROM templates")['count']>=11
    assert engine.status()['sync']['state']=='confirmed'
    assert status(store)['restored_cards']==1


def test_rebuild_leaves_all_local_data_on_empty_catalog(env):
    store, service, engine, remote, *_ = env
    original=len(service.cards())
    def empty(req):
        if req.url.path=='/v4/product-list':
            return httpx.Response(200,json={'result':{'total':0,'goods':[]}})
        return remote.handle(req)
    engine.client.transport=httpx.MockTransport(empty)
    job=begin(engine,'tester')
    tick(engine);tick(engine)
    assert store.one("SELECT state FROM nk_jobs WHERE id=?",(job,))['state']=='failed'
    assert len(service.cards())==original
    assert status(store)['backup']
    assert not store.one("SELECT action FROM audit WHERE action='nk_rebuild_committed'")


def test_rebuild_rejects_ongoing_operations(env):
    store, service, engine, remote, *_ = env
    card=service.cards()[0]
    engine.prepare('tester','moderate',card['id'],card['revision'])
    with pytest.raises(Conflict,match='незаверш'):
        begin(engine,'tester')


def test_quick_sync_avoids_immediate_repeat_and_full_mode_can_force(env):
    store, service, engine, remote, *_ = env
    first=store.one("SELECT id FROM nk_jobs WHERE kind='sync' ORDER BY created DESC LIMIT 1")['id']
    count=len(remote.requests)
    assert engine.sync('tester')==first
    assert len(remote.requests)==count
    forced=engine.sync('tester',full=True)
    assert forced!=first
    job=store.one('SELECT payload FROM nk_jobs WHERE id=?',(forced,))
    assert json.loads(job['payload'])['mode']=='full'


def test_admin_endpoint_requires_strict_phrase_and_admin(app):
    with TestClient(app,base_url=ORIGIN) as client:
        employee=login(client)
        assert client.get('/api/admin/nk/rebuild').status_code==403
        assert client.post('/api/admin/nk/rebuild',json={'confirmed':True,'discard_local_changes':True,'confirmation':'СБРОСИТЬ'},headers=employee).status_code==403
        admin=login(client,admin=True)
        assert client.post('/api/admin/nk/rebuild',json={'confirmed':True,'confirmation':'СБРОСИТЬ'},headers=admin).status_code==422
        assert client.post('/api/admin/nk/rebuild',json={'confirmed':True,'discard_local_changes':True,'confirmation':'СБРОСИТЬ'},headers=admin).status_code==422
        assert client.get('/api/workspace/version').status_code==200


def test_registry_summary_excludes_large_snapshot_but_full_card_is_available(app):
    with TestClient(app,base_url=ORIGIN) as client:
        headers=login(client,admin=True)
        saved=client.post('/api/cards',json={'pn':'EXACT-PN-A','attributes':{'Товарный знак [#2504]':'BRAND','Описание [#33]':'Large text'}},headers=headers).json()
        with app.state.store.transaction() as db:
            db.execute('UPDATE cards SET source_snapshot=? WHERE id=?',
                       (json.dumps({'nk':{'good_id':902,'notes':'x'*20000},'creation':{'cat_id':12}}),saved['id']))
        index=client.get('/api/cards/summary').json()
        assert len(index)==1
        assert index[0]['pn']=='EXACT-PN-A'
        assert index[0]['attributes']=={'Товарный знак [#2504]':'BRAND'}
        assert index[0]['source_snapshot']=={'creation':{'cat_id':12}}
        assert 'Large text' not in str(index)
        assert len(str(index))<2500
        detail=client.get('/api/cards/'+saved['id']).json()
        assert detail['attributes']['Описание [#33]']=='Large text'
        assert len(detail['source_snapshot']['nk']['notes'])==20000
