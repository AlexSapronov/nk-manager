import copy

import pytest

from nk_manager.nk import Engine
from nk_manager.presets import install
from nk_manager.service import Service, candidates
from nk_manager.store import Store
from tests.test_presets import sample


def test_blank_or_separator_only_pn_are_not_duplicates():
    rows=[{'id':'a','pn':'','gtin':'111','name':'Первый'}, {'id':'b','pn':'   ','gtin':'222','name':'Второй'}, {'id':'c','pn':'---','gtin':'333','name':'Третий'}]
    assert candidates('','444',rows)==[]
    assert candidates('   ','',rows)==[]
    assert candidates('___','',rows)==[]
    assert candidates('','222',rows)[0]['reason'].startswith('Совпадает GTIN')
    assert candidates('ABC-123','',[{'id':'x','pn':'abc123','gtin':'','name':'Товар'}])==[]


def drafts(tmp_path,n=3):
    store=Store(tmp_path);engine=Engine(store);install(store);service=Service(store)
    ids=[]
    for i in range(n):
        raw=copy.deepcopy(sample());raw['good_id']=111+i;raw['good_name']='Разъём TEST-'+str(i)
        raw['good_attrs']=[a for a in raw['good_attrs'] if a['attr_id']!=13914]
        raw['identified_by'][0]['value']=str(4640638343000+i)
        ids.append(engine.apply_remote(raw))
    return store,service,ids


def test_ninety_sparse_drafts_preview_and_apply_without_false_confirmation(tmp_path):
    store,svc,ids=drafts(tmp_path,90)
    plan=svc.template_plan('tester',ids)
    assert len(plan['entries'])==90
    assert all(e['name'] and e['gtin'] and e['needs_input']==['PN'] for e in plan['entries'])
    assert all(not e['duplicates'] for e in plan['entries'])
    result=svc.apply_template_plan('tester',plan['id'],'',ids)
    assert len(result)==90 and all(r['status']=='updated' for r in result)
    assert all(r['name'] and r['id'] for r in result)
    assert all(svc.get(id)['attributes']['Код ОКПД2 [#3961]']=='27.33.13.110' for id in ids)


def test_only_checked_cards_are_applied_and_unplanned_ids_are_rejected(tmp_path):
    store,svc,ids=drafts(tmp_path)
    plan=svc.template_plan('tester',ids[:2])
    with pytest.raises(ValueError):svc.apply_template_plan('tester',plan['id'],'',[ids[2]])
    assert not store.one('SELECT applied FROM plans WHERE id=?',(plan['id'],))['applied']
    result=svc.apply_template_plan('tester',plan['id'],'',[ids[1]])
    assert len(result)==1 and result[0]['id']==ids[1]
    assert 'Код ОКПД2 [#3961]' not in svc.get(ids[0])['attributes']
    assert svc.get(ids[1])['dirty']==1


def test_real_gtin_duplicate_still_requires_confirmation_and_can_retry(tmp_path):
    store,svc,ids=drafts(tmp_path,2)
    with store.transaction() as db:db.execute('UPDATE cards SET gtin=(SELECT gtin FROM cards WHERE id=?) WHERE id=?',(ids[0],ids[1]))
    plan=svc.template_plan('tester',ids[:1])
    assert len(plan['entries'][0]['duplicates'])==1
    result=svc.apply_template_plan('tester',plan['id'],'',ids[:1])
    assert result[0]['status']=='warning' and result[0]['id']==ids[0]
    retry=svc.template_plan('tester',ids[:1])
    assert svc.apply_template_plan('tester',retry['id'],'Проверены исполнения',ids[:1])[0]['status']=='updated'


def test_unfinished_operation_is_explained_before_confirmation(tmp_path):
    store,svc,ids=drafts(tmp_path,1)
    Engine(store).enqueue('tester','save',{'entry':{}},svc.get(ids[0]))
    entry=svc.template_plan('tester',ids)['entries'][0]
    assert entry['changes']=={} and 'НК' in entry['message']


@pytest.mark.parametrize('other', ['SEV100FDXL/12V', 'SEV100FDXM/24V', 'sev100fdxl/24v', 'SEV100FDXL24V', 'SEV100FDXL /24V', 'SEV100FDXL-24V'])
def test_similar_component_pn_does_not_warn(other):
    assert candidates('SEV100FDXL/24V', '', [{'id':'x','pn':other,'gtin':''}]) == []


def test_exact_pn_or_gtin_warns_and_excludes_self():
    rows=[{'id':'x','pn':' SEV100FDXL/24V ','gtin':'04640000000011'}]
    assert candidates('SEV100FDXL/24V', '', rows)[0]['reason'] == 'Точный PN'
    assert candidates('OTHER', '4640000000011', rows)[0]['reason'].startswith('Совпадает GTIN')
    assert candidates('SEV100FDXL/24V', '', rows, exclude='x') == []
