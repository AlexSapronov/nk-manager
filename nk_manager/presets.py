"""Versioned, bundled local defaults. Installing them never sends an NK request."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re

from .store import audit, dumps, now, uid

ROOT = Path(__file__).resolve().parents[1] / 'presets'
AUTO_IDS = {'3959', '13933', '3961'}


def attribute_id(key):
    match = re.search(r'\[#(\d+)\]', key)
    return match[1] if match and not key.endswith('::unit') else None


@lru_cache(maxsize=1)
def bundle():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    result = {}
    for profile in manifest['profiles']:
        body = json.loads((ROOT / profile['file']).read_text())
        if body.get('schema_version') != 1 or body['tnved'] != profile['key']:
            raise ValueError('Повреждена предустановка ' + profile['key'])
        automatic = {attribute_id(r['attribute']): r['value'] for r in body['rules'] if r['mode'] == 'fill_if_empty'}
        if automatic != {'3959': body['tnved'][:4], '13933': body['tnved'], '3961': profile['default_okpd2']}:
            raise ValueError('Некорректные автозначения предустановки')
        result[profile['key']] = {**profile, 'body': body}
    return result


def install(store):
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS preset_installations(preset_key TEXT PRIMARY KEY,template_id TEXT NOT NULL REFERENCES templates(id),sha256 TEXT NOT NULL)')
        for key, profile in bundle().items():
            body = profile['body']; encoded = dumps(body); sha = hashlib.sha256(encoded.encode()).hexdigest()
            previous = db.execute('SELECT p.*,t.status FROM preset_installations p JOIN templates t ON t.id=p.template_id WHERE preset_key=?', (key,)).fetchone()
            if previous and previous['sha256'] == sha:
                continue
            scope = (body['tnved'], body['category'], body['card_type'])
            active = db.execute("SELECT id FROM templates WHERE tnved=? AND category=? AND card_type=? AND status='active'", scope).fetchone()
            activate = (not previous or previous['status'] == 'active') and (not active or previous and active['id'] == previous['template_id'])
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM templates WHERE name=? AND tnved=? AND category=? AND card_type=?', (body['name'], *scope)).fetchone()[0]
            if activate and active:
                db.execute("UPDATE templates SET status='inactive' WHERE id=?", (active['id'],))
            id = uid()
            db.execute('INSERT INTO templates VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (id, body['name'], *scope, version, 'active' if activate else 'inactive', encoded, now(), 'builtin', 'builtin'))
            db.execute('INSERT OR REPLACE INTO preset_installations VALUES(?,?,?)', (key, id, sha))
            audit(db, 'system', 'preset_installed', id, {'key':key,'sha256':sha,'status':'active' if activate else 'inactive'})


def installed(store):
    # Trust the installation mapping, never a caller-supplied flag in a JSON body.
    return {row['template_id']: bundle()[row['preset_key']] for row in store.all('SELECT * FROM preset_installations') if row['preset_key'] in bundle()} if store.one("SELECT 1 FROM sqlite_master WHERE name='preset_installations'") else {}


def matches(profile, card):
    body = profile['body']
    if card['tnved'] != body['tnved'] or card['card_type'] not in ('unit', 'Единичный', 'Единичный товар'):
        return False
    raw = card.get('source_snapshot', {}).get('nk') or {}
    if raw.get('is_set') or raw.get('is_kit'):
        return False
    if card['category'] in profile['category_aliases']:
        return True
    ids = {str(c.get('cat_id')) for c in raw.get('categories', [])}
    return bool(ids & {str(i) for i in profile['category_ids']})


def changes(profile, card):
    changes = {}
    for rule in profile['body']['rules']:
        if rule['mode'] != 'fill_if_empty':
            continue
        id = attribute_id(rule['attribute'])
        if id not in AUTO_IDS:
            continue
        keys = [k for k in card['attributes'] if attribute_id(k) == id]
        # Never add a second version of an already-filled official attribute.
        if any(card['attributes'][k].strip() for k in keys):
            continue
        key = keys[0] if keys else rule['attribute']
        changes[key] = rule['value']
    return changes
