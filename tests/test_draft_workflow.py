import json
import re

import httpx
import pytest

from nk_manager.creation import draft
from nk_manager.draft_workflow import workspace, submit
from nk_manager.nk import Engine
from tests.test_nk import env, tick
from tests.test_creation import inputs


def ready(env,pn='DRAFT-READY'):
    store,svc,engine,remote,*_=env
    c=draft(store,{**inputs(),'tnved':'8536901000','kind_id':next(k['id'] for k in __import__('nk_manager.creation',fromlist=['baseline']).baseline() if k['tnved']=='8536901000' and k['name']=='Разъём на кабель'),'pn':pn,'brand':'BRAND'})
    raw=remote.raw;raw['good_name']=c['name'];raw['good_attrs']=[{'attr_id':int(re.search(r'\[#(\d+)\]',k)[1]),'attr_name':k.split(' [#')[0],'attr_value':v,**({'attr_value_type':c['attributes'][k+'::unit']} if k+'::unit' in c['attributes'] else {})} for k,v in c['attributes'].items() if not k.endswith('::unit')]
    raw['good_attrs'].append({'attr_id':1,'attr_name':'Страна','attr_value':'RU'})
    id=engine.apply_remote(raw)
    assert next(r for r in workspace(svc,'tester')['entries'] if r['id']==id)['stage']=='ready'
    return svc.get(id)


def test_hide_published_pn_exactly_and_skip_normalizing_hidden(env):
    store,svc,engine,remote,*_=env
    c=ready(env,'PN-X');raw=json.loads(json.dumps(remote.raw));raw.update(good_id=222,good_status='published',good_detailed_status=['published'],good_signed=True);raw['identified_by'][0]['value']='04640000000028';engine.apply_remote(raw)
    row=next(r for r in workspace(svc,'tester')['entries'] if r['id']==c['id'])
    assert row['published_ids']
    from nk_manager.normalization_service import preview
    assert preview(svc,'tester',[c['id']])['entries'][0]['message']=='PN уже опубликован'
    before=svc.get(c['id']);before['pn']='PN-Y';svc.save('tester',before,c['id'],admin=True)
    assert not next(r for r in workspace(svc,'tester')['entries'] if r['id']==c['id'])['published_ids']


def test_submission_saves_then_moderates_once_and_survives_restart(env):
    store,svc,engine,remote,*_=env
    c=ready(env);c['attributes']['Страна [#1]']='FR';c=svc.save('tester',c,c['id'],admin=True)
    result=submit(engine,'tester',[c['id']]);assert result[0]['status']=='queued'
    tick(engine)
    assert not any(r[1]=='/v3/feed-moderation' for r in remote.requests)
    restarted=Engine(store,httpx.MockTransport(remote.handle));tick(restarted);tick(restarted);tick(restarted)
    assert sum(r[1]=='/v3/feed' for r in remote.requests)==1
    assert sum(r[1]=='/v3/feed-moderation' for r in remote.requests)==1
    assert next(r for r in workspace(svc,'tester')['entries'] if r['id']==c['id'])['stage']=='moderation'
    assert store.one('SELECT state FROM nk_draft_followups')['state']=='queued'


def test_submission_unchanged_card_moderates_without_resaving(env):
    store,svc,engine,remote,*_=env;c=ready(env)
    assert submit(engine,'tester',[c['id']])[0]['status']=='queued'
    tick(engine)
    assert any(r[1]=='/v3/feed-moderation' for r in remote.requests)
    assert not any(r[1]=='/v3/feed' for r in remote.requests)


def test_unknown_save_never_triggers_moderation_or_resend(env):
    store,svc,engine,remote,*_=env;c=ready(env);c['attributes']['Страна [#1]']='FR';c=svc.save('tester',c,c['id'],admin=True)
    remote.timeout_path='/v3/feed';assert submit(engine,'tester',[c['id']])[0]['status']=='queued'
    for _ in range(3):tick(engine)
    assert sum(r[1]=='/v3/feed' for r in remote.requests)==1
    assert not any(r[1]=='/v3/feed-moderation' for r in remote.requests)
    assert next(r for r in workspace(svc,'tester')['entries'] if r['id']==c['id'])['stage']=='attention'


def test_rejected_save_stops_pipeline(env):
    store,svc,engine,remote,*_=env;c=ready(env);c['attributes']['Страна [#1]']='FR';c=svc.save('tester',c,c['id'],admin=True)
    submit(engine,'tester',[c['id']]);tick(engine)
    save=store.one("SELECT id FROM nk_jobs WHERE kind='save'");engine.state(save['id'],'failed',error='Rejected')
    tick(engine)
    assert store.one('SELECT state FROM nk_draft_followups')['state']=='stopped'
    assert not any(r[1]=='/v3/feed-moderation' for r in remote.requests)


def test_local_save_duplicate_does_not_block_moderation_of_existing_card(env):
    store,svc,engine,remote,*_=env;c=ready(env)
    other=svc.save('tester',{'pn':c['pn'],'name':'Другой локальный черновик'})
    assert other['id']!=c['id']
    assert engine.prepare('tester','moderate',c['id'],c['revision'])
    # Creating another real card is still explicitly guarded.
    with pytest.raises(ValueError,match='совпадения'):
        engine.prepare('tester','gtin',other['id'],other['revision'])


def test_submission_does_not_use_a_stale_confirmation(env):
    store,svc,engine,remote,*_=env;c=ready(env)
    result=submit(engine,'tester',[c['id']],revisions={c['id']:c['revision']-1})
    assert result[0]['status']=='error' and 'изменилась' in result[0]['message']
    assert not store.one("SELECT id FROM nk_jobs WHERE kind='moderate'")


def test_followup_stops_if_local_card_changes_after_save(env):
    store,svc,engine,remote,*_=env;c=ready(env)
    c['attributes']['Страна [#1]']='FR';c=svc.save('tester',c,c['id'],admin=True)
    job=engine.prepare('tester','save',c['id'],c['revision']);tick(engine);tick(engine)
    current=svc.get(c['id']);current['attributes']['Страна [#1]']='CN';svc.save('tester',current,current['id'],admin=True)
    with store.transaction() as db:db.execute("INSERT INTO nk_draft_followups(save_job,card_id,actor,state) VALUES(?,?,?,'waiting')",(job,c['id'],'tester'))
    tick(engine)
    assert store.one('SELECT state FROM nk_draft_followups')['state']=='stopped'
    assert not any(r[1]=='/v3/feed-moderation' for r in remote.requests)


def test_workspace_shows_actionable_nk_field_error(env):
    store,svc,engine,remote,*_=env;c=ready(env)
    job=engine.enqueue('tester','save',{},c)
    engine.state(job,'failed',{'error_details':{'items':[{'errors':[{'attr_id':'12','text':'Укажите вид товара'}]}]}},error='НК отклонил фид')
    row=next(r for r in workspace(svc,'tester')['entries'] if r['id']==c['id'])
    assert row['error']=='Укажите вид товара'


def test_published_pn_uses_remote_truth_even_if_local_published_copy_is_edited(env):
    store,svc,engine,remote,*_=env;c=ready(env,'REMOTE-PN')
    raw=json.loads(json.dumps(remote.raw));raw.update(good_id=222,good_status='published',good_detailed_status=['published'],good_signed=True);raw['identified_by'][0]['value']='04640000000028'
    pub_id=engine.apply_remote(raw);pub=svc.get(pub_id);pub['pn']='UNSENT-LOCAL-PN';svc.save('tester',pub,pub_id,admin=True)
    row=next(r for r in workspace(svc,'tester')['entries'] if r['id']==c['id'])
    assert row['published_ids']==[pub_id]
