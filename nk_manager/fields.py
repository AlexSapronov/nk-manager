"""Field visibility from published NK snapshots, never from local draft values."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def baseline():
    return json.loads((Path(__file__).resolve().parents[1] / 'presets/published-fields.json').read_text())['profiles']


def published_fields(store, tnved):
    reference = baseline().get(tnved)
    used = set(reference['attribute_ids']) if reference else set()
    # A newly published field becomes visible after the next catalog sync.
    found = False
    for row in store.all('SELECT n.raw FROM nk_cards n JOIN cards c ON c.id=n.card_id WHERE c.tnved=?', (tnved,)):
        raw = json.loads(row['raw'])
        code = next((str(a.get('attr_value') or '') for a in raw.get('good_attrs', []) if str(a.get('attr_id')) == '13933'), str(raw.get('tnved') or ''))
        if code != tnved or raw.get('good_status') != 'published' or raw.get('good_signed') is not True or raw.get('good_detailed_status') != ['published']:
            continue
        found = True
        used.update(str(a['attr_id']) for a in raw.get('good_attrs', []) if str(a.get('attr_value') or '').strip())
    return {'has_reference': reference is not None or found, 'attribute_ids': sorted(used, key=int)}
