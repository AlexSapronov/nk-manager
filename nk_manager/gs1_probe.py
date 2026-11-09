"""Explicit, one-shot GS1 GTIN import probe via official NK feed.

The minimal feed is experimental: some NK installations reject missing
first-layer fields. A 4xx rejection must stay visible and NEVER be retried.
"""
from datetime import datetime, timezone
import json
import time

from .service import Conflict
from .store import audit, dumps, now, uid


def enqueue(engine, actor, batch_id, numbers):
    with engine.lock:
        batch=engine.store.one('SELECT * FROM nk_recovery_batches WHERE id=?',(batch_id,))
        if not batch:
            raise ValueError('Партия восстановления не найдена')
        if batch['state'] not in ('preview','probing'):
            raise Conflict('Дождитесь завершения чтения или сохранения партии')
        if not engine.config().get('writes_enabled') or not engine.config().get('api_key'):
            raise Conflict('Разрешите внешние действия НК и настройте API KEY')
        if not isinstance(numbers,list) or not 1<=len(numbers)<=10 or any(type(n) is not int for n in numbers) or len(set(numbers))!=len(numbers):
            raise ValueError('Выберите от 1 до 10 уникальных строк для проверки созданием')
        rows=engine.store.all('SELECT * FROM nk_recovery_rows WHERE batch_id=?',(batch_id,))
        selected={r['row_no']:r for r in rows if r['row_no'] in numbers}
        if len(selected)!=len(numbers) or any(r['state'] not in ('unregistered','need_data') or r['remote'] or r['probe_job'] for r in selected.values()):
            raise Conflict('Выбранные GTIN уже найдены, отправлены либо ранее проверялись. Повторная отправка запрещена')
        if engine.store.one("SELECT id FROM nk_jobs WHERE kind='rebuild' AND state NOT IN ('confirmed','failed','cancelled')"):
            raise Conflict('Сначала завершите перестройку каталога НК')
        previously_tried={
            str(json.loads(job['payload']).get('gtin') or '').zfill(14)
            for job in engine.store.all("SELECT payload FROM nk_jobs WHERE kind='gs1_probe'")
        }
        if any(selected[n]['gtin'].zfill(14) in previously_tried for n in numbers):
            raise Conflict('Этот GTIN уже отправлялся в попытке импорта, возможно в другой партии. Повторная отправка запрещена')
        result=[]
        # Atomic association and durable command. No calls to NK happen here.
        with engine.store.transaction() as db:
            for number in numbers:
                r=selected[number]
                payload={'gtin':r['gtin'],'manufacturer':r['manufacturer'],'batch_id':batch_id,'row_no':number}
                job=uid()
                db.execute("INSERT INTO nk_jobs(id,kind,card_id,revision,actor,op_key,payload,sha256,state,created,updated) VALUES(?,'gs1_probe',NULL,NULL,?,?,?,?, 'prepared',?,?)",
                           (job,actor,f'gs1-probe:{batch_id}:{number}',dumps(payload),engine_hash(payload),now(),now()))
                db.execute('UPDATE nk_recovery_rows SET state=?,probe_job=?,error=? WHERE batch_id=? AND row_no=?',
                           ('probing',job,'',batch_id,number))
                audit(db,actor,'gs1_probe_requested',job,{'gtin':r['gtin'],'batch':batch_id,'row':number})
                result.append(job)
            db.execute("UPDATE nk_recovery_batches SET state='probing' WHERE id=?",(batch_id,))
        return result


def engine_hash(value):
    from .nk import digest
    return digest(value)


def feed_body(gtin):
    """GTIN-only bootstrap: no made-up TN VED, PN, trademark, or category."""
    return {
        'gtin':gtin,
        'identified_by':[{'value':gtin,'type':'gtin','level':'trade-unit','multiplier':1,'unit':'шт'}],
        'is_set':False,'is_kit':False,'is_tech_gtin':False,'moderation':0,
        'good_attrs':[]
    }


def tick(engine,job):
    from .gtin_recovery import find_by_gtin
    payload=json.loads(job['payload'])
    gtin=payload['gtin']
    if job['state']=='prepared':
        if not engine.config().get('writes_enabled'):
            return
        # On each prepared command, preflight a genuine GTIN-based lookup.
        raw=find_by_gtin(engine,gtin)
        if raw:
            engine.state(job['id'],'confirmed',{'already_exists':True,'raw':raw})
            return
        # This is an irreversible request; save intent first. A crash/timeout
        # after dispatch must never cause automatic resending.
        with engine.store.transaction() as db:
            current=db.execute('SELECT state FROM nk_jobs WHERE id=?',(job['id'],)).fetchone()
            if not current or current['state']!='prepared':
                return
            db.execute("UPDATE nk_jobs SET state='dispatching',updated=? WHERE id=?",(now(),job['id']))
        response=engine.client.call('POST','/v3/feed',body=feed_body(gtin))
        if not isinstance(response,dict) or not response.get('feed_id'):
            engine.state(job['id'],'unknown',response,error='Ответ создания не содержит feed_id: проверьте НК вручную')
            return
        engine.state(job['id'],'submitted',response,external_id=str(response['feed_id']),next_at=time.time()+5)
        return
    if job['state'] not in ('submitted','checking'):
        return
    if not job['external_id']:
        engine.state(job['id'],'unknown',error='Нет ID отправленного фида. Требуется ручная сверка')
        return
    receipt = engine.store.one("SELECT created FROM nk_receipts WHERE job_id=? AND state='submitted' ORDER BY id LIMIT 1",
                               (job['id'],))
    sent_at = datetime.fromisoformat(receipt['created'] if receipt else job['created'])
    if (datetime.now(timezone.utc) - sent_at).total_seconds() >= 900:
        engine.state(job['id'], 'unknown', error='НК не подтвердил импорт GTIN за 15 минут. Сверьте карточку по GTIN')
        return
    response=engine.client.call('GET','/v3/feed-status',{'feed_id':job['external_id'],'verbose':'true'})
    if not isinstance(response,dict):
        raise ValueError('НК вернул недопустимый формат статуса фида')
    if response.get('status')=='Rejected' or response.get('error_details'):
        engine.state(job['id'],'failed',response,error='НК отклонил попытку импорта GTIN; посмотрите квитанцию фида')
        return
    if response.get('status')=='Processing':
        engine.state(job['id'],'submitted',response,next_at=time.time()+15)
        return
    raw=find_by_gtin(engine,gtin)
    if raw:
        engine.state(job['id'],'confirmed',{'feed':response,'raw':raw})
        return
    # Feed may be accepted before the generated draft becomes queryable.
    engine.state(job['id'],'submitted',response,error='Фид принят; ожидаем появление карточки с данными GS1',next_at=time.time()+20)


def reconcile(engine, job, payload):
    from .gtin_recovery import find_by_gtin

    raw = find_by_gtin(engine, payload['gtin'])
    if not raw:
        raise Conflict('Карточка с этим GTIN пока отсутствует в НК')
    # Reopen an attention row only while the party can still accept drafts.
    with engine.store.transaction() as db:
        batch_id = payload['batch_id']
        row = db.execute("""UPDATE nk_recovery_rows SET state='probing', error=''
            WHERE batch_id=? AND probe_job=? AND state='attention'
            AND EXISTS(SELECT 1 FROM nk_recovery_batches WHERE id=? AND state IN ('preview','probing','scanning'))""",
            (batch_id, job['id'], batch_id))
        if row.rowcount:
            db.execute("UPDATE nk_recovery_batches SET state='probing' WHERE id=?", (batch_id,))
    engine.state(job['id'], 'confirmed', {'reconciled': True, 'raw': raw})
