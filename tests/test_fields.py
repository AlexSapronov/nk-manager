import copy

from nk_manager.fields import baseline, published_fields
from nk_manager.nk import Engine
from nk_manager.store import Store
from tests.test_presets import sample


def test_bundled_published_profiles_are_aggregate_only():
    data=baseline()
    assert len(data)==11
    assert sum(p['published_count'] for p in data.values())==1685
    assert '13914' in data['8536699008']['attribute_ids']
    assert '24005' not in data['8539519009']['attribute_ids']
    assert '24005' in data['8504408300']['attribute_ids']


def test_drafts_cannot_expand_reference_but_synced_published_cards_can(tmp_path):
    store=Store(tmp_path);engine=Engine(store)
    raw=copy.deepcopy(sample())
    raw['good_attrs'].append({'attr_id':999999,'attr_name':'Новая характеристика','attr_value':'0'})
    engine.apply_remote(raw)
    assert '999999' not in published_fields(store,'8536901000')['attribute_ids']
    raw.update(good_status='published',good_detailed_status=['published'],good_signed=True)
    engine.apply_remote(raw)
    assert '999999' in published_fields(store,'8536901000')['attribute_ids']
    assert '999999' not in published_fields(store,'8504408300')['attribute_ids']
    assert published_fields(store,'0000000000') == {'has_reference':False,'attribute_ids':[]}
