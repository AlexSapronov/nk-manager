"""Safe full rebuild from NK: stage remote data, then replace business tables atomically."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .store import audit, dumps, now, uid
from .service import Conflict
from .nk import digest, source_pn, STATUS

SCHEMA = """
CREATE TABLE IF NOT EXISTS nk_rebuild_stage(
 job_id TEXT NOT NULL, good_id INTEGER NOT NULL, raw TEXT NOT NULL,
 PRIMARY KEY(job_id, good_id)
);
CREATE TABLE IF NOT EXISTS nk_rebuild_history(
 job_id TEXT PRIMARY KEY, backup TEXT NOT NULL, previous_cards INTEGER NOT NULL,
 restored_cards INTEGER, state TEXT NOT NULL, created TEXT NOT NULL, completed TEXT
);
"""


def running(store):
    return store.one("SELECT id FROM nk_jobs WHERE kind='rebuild' AND state NOT IN ('confirmed','failed','cancelled')")


def backup_database(store, job_id):
    directory = store.directory / 'backups'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    filename = 'nk-before-rebuild-' + job_id + '.sqlite3'
    path = directory / filename
    if path.exists():
        raise Conflict('Резервная копия с этим идентификатором уже существует')
    try:
        with store.connect() as source:
            with sqlite3.connect(path) as target:
                source.backup(target)
                ok = target.execute('PRAGMA integrity_check').fetchone()[0]
                if ok != 'ok':
                    raise ValueError('Проверка резервной копии SQLite не пройдена')
        os.chmod(path, 0o600)
        return filename
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def begin(engine, actor):
    """Only an explicit administrator endpoint may call this. No delete here."""
    from datetime import datetime, timedelta, timezone
    with engine.lock:
        if not engine.config().get('api_key'):
            raise ValueError('Сначала подключите API НК')
        old = running(engine.store)
        if old:
            return old['id']
        if engine.store.one("SELECT id FROM nk_jobs WHERE state NOT IN ('confirmed','failed','cancelled')"):
            raise Conflict('Сначала завершите или сверьте все незавершённые операции НК')
        if engine.store.one("SELECT save_job FROM nk_draft_followups WHERE state='waiting'"):
            raise Conflict('Есть ожидающие отправки черновики; завершите их перед сбросом')
        if engine.store.one("SELECT id FROM nk_creation_batches WHERE state IN ('running','attention')"):
            raise Conflict('Остались незавершённые партии создания карточек')
        if engine.store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='nk_recovery_batches'"):
            if engine.store.one("SELECT id FROM nk_recovery_batches WHERE state IN ('running','scanning')"):
                raise Conflict('Остались незавершённые партии восстановления GTIN')
        job_id = uid()
        filename = backup_database(engine.store, job_id)
        payload = {
            'from': '2000-01-01 00:00:00',
            'to': (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'backup': filename
        }
        with engine.store.transaction() as db:
            previous = db.execute('SELECT COUNT(*) FROM cards').fetchone()[0]
            db.execute('INSERT INTO nk_jobs(id,kind,card_id,revision,actor,op_key,payload,sha256,state,created,updated) VALUES(?,\'rebuild\',NULL,NULL,?,?,?,?,\'prepared\',?,?)',
                       (job_id, actor, 'rebuild:' + job_id, dumps(payload), digest(payload), now(), now()))
            db.execute('INSERT INTO nk_rebuild_history VALUES(?,?,?,?,?,?,?)',
                       (job_id, filename, previous, None, 'scanning', now(), None))
            audit(db, actor, 'nk_rebuild_requested', job_id,
                  {'backup': filename, 'previous_cards': previous, 'staged_before_delete': True})
        return job_id


def stage(store, job_id, raws):
    """Idempotent when a pagination page is re-read after a crash."""
    with store.transaction() as db:
        db.executemany('INSERT OR REPLACE INTO nk_rebuild_stage VALUES(?,?,?)',
                       [(job_id, int(raw['good_id']), dumps(raw)) for raw in raws])


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def finish(engine, job):
    """All-or-nothing commit. A nonempty verified stage is required."""
    store = engine.store
    with store.transaction() as db:
        count = db.execute('SELECT COUNT(*) FROM nk_rebuild_stage WHERE job_id=?', (job['id'],)).fetchone()[0]
        if count < 1:
            raise Conflict('НК вернул пустой каталог. Локальные данные не удалены')
        hist = db.execute('SELECT * FROM nk_rebuild_history WHERE job_id=?', (job['id'],)).fetchone()
        if not hist:
            raise Conflict('Не найдены сведения о резервной копии. Данные сохранены')
        backup = store.directory / 'backups' / hist['backup']
        if not backup.is_file():
            raise Conflict('Резервная копия исчезла. Сброс запрещён')
        tables = _tables(db)
        # A secondary guard protects against concurrent client writes during staging.
        if db.execute("SELECT COUNT(*) FROM nk_jobs WHERE id!=? AND state NOT IN ('confirmed','failed','cancelled')",(job['id'],)).fetchone()[0]:
            raise Conflict('Во время загрузки появилась другая операция НК. Локальная база не изменена')
        # Immutable external receipts and operations remain in the audit archive.
        # Deleting them would violate explicit immutability triggers and lose evidence.
        for table in ('nk_draft_followups','nk_creation_rows','nk_recovery_rows'):
            if table in tables:
                db.execute('DELETE FROM ' + table)
        for table in ('nk_scopes','nk_snapshots','nk_cards','source_keys',
                      'nk_creation_batches','nk_recovery_batches',
                      'import_results','import_plans','imports','plans',
                      'preset_installations','templates','nk_schemas'):
            if table in tables:
                db.execute('DELETE FROM ' + table)
        db.execute('DELETE FROM cards')
        for saved in db.execute('SELECT raw FROM nk_rebuild_stage WHERE job_id=? ORDER BY good_id', (job['id'],)).fetchall():
            raw = json.loads(saved['raw'])
            good_id = int(raw['good_id'])
            attrs, mapping = {}, {}
            for index, attr in enumerate(raw.get('good_attrs', [])):
                key = f"{attr.get('attr_name','Атрибут')} [#{attr['attr_id']}]"
                if key in attrs:
                    key += ' · ' + str(index)
                attrs[key] = str(attr.get('attr_value') or '')
                if attr.get('attr_value_type'):
                    attrs[key + '::unit'] = str(attr['attr_value_type'])
                mapping[key] = attr
            gtin = next((str(i['value']) for i in raw.get('identified_by',[])
                         if i.get('type')=='gtin' and i.get('level')=='trade-unit'), '')
            tnved = next((str(a.get('attr_value') or '') for a in raw.get('good_attrs',[])
                          if str(a.get('attr_id'))=='13933'), str(raw.get('tnved') or ''))
            cats = raw.get('categories') or []
            statuses = raw.get('good_detailed_status') or [raw.get('good_status','')]
            label = ' / '.join(STATUS.get(s,s) for s in statuses)
            card_id = uid()
            snapshot = {'nk':raw,'field_map':mapping,'confirmed_at':now()}
            db.execute("INSERT INTO cards VALUES(?,?,'nk_api',?,?,?,?,?,?,?,?,?,1,'',0,0,?,?)",
                       (card_id,str(good_id),label,source_pn(raw),gtin,
                        tnved if len(tnved)==10 else '',cats[0].get('cat_name','') if cats else '',
                        'set' if raw.get('is_set') else 'kit' if raw.get('is_kit') else 'unit',
                        raw.get('good_name',''),dumps(attrs),dumps(snapshot),now(),now()))
            db.execute('INSERT INTO nk_cards VALUES(?,?,?,?,?)',(card_id,good_id,dumps(raw),dumps(mapping),now()))
            db.execute('INSERT INTO nk_snapshots VALUES(?,?,?,?)',(card_id,digest(raw),dumps(raw),now()))
        # Leave a confirmed sync checkpoint for normal safe external operations.
        # Historical NK jobs and receipts remain immutable for reconciliation.
        checkpoint = uid()
        payload = {'full_rebuild':job['id'],'count':count}
        db.execute("INSERT INTO nk_jobs(id,kind,card_id,revision,actor,op_key,payload,sha256,state,result,created,updated) VALUES(?,'sync',NULL,NULL,?,?,?,?,?,?,?,?)",
                   (checkpoint,job['actor'],'sync:rebuild:'+job['id'],dumps(payload),digest(payload),
                    'confirmed',dumps({'count':count,'rebuild':job['id']}),now(),now()))
        db.execute("UPDATE nk_rebuild_history SET restored_cards=?,state='completed',completed=? WHERE job_id=?",
                   (count,now(),job['id']))
        db.execute('DELETE FROM nk_rebuild_stage WHERE job_id=?',(job['id'],))
        audit(db,job['actor'],'nk_rebuild_committed',job['id'],
              {'before':hist['previous_cards'],'after':count,'backup':hist['backup'],
               'credentials_preserved':True,'accounts_preserved':True})
    # Install only signed, bundled templates. User-defined templates were removed.
    from .presets import install
    install(store)
    return {'count':count,'backup':hist['backup']}


def status(store):
    row = store.one("""SELECT h.job_id,h.backup,h.previous_cards,h.restored_cards,h.state,
                              j.state AS job_state,j.error,j.updated
                       FROM nk_rebuild_history h JOIN nk_jobs j ON j.id=h.job_id
                       ORDER BY h.created DESC LIMIT 1""")
    return row
