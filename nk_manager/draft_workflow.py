"""Action-oriented draft workspace and durable save -> moderation handoff."""
import json

from .creation import catalog
from .normalization import eligible, propose
from .store import audit


def published(card):
    raw=card.get('source_snapshot',{}).get('nk',{})
    return raw.get('good_status')=='published' or 'published' in raw.get('good_detailed_status',[]) or 'опубликована' in str(card.get('external_status') or '').casefold()


def published_index(cards,store=None):
    from .nk import source_pn
    # Unsaved-to-NK edits of a published local copy are not publication truth.
    latest={r['card_id']:json.loads(r['raw']) for r in store.all('SELECT n.card_id,n.raw FROM nk_cards n JOIN cards c ON c.id=n.card_id WHERE c.dirty=1')} if store else {}
    result={}
    for c in cards:
        raw=latest.get(c['id'],c.get('source_snapshot',{}).get('nk',{}))
        if raw:
            if raw.get('good_status')!='published' and 'published' not in raw.get('good_detailed_status',[]):continue
            pn=source_pn(raw)
        elif published(c):pn=c['pn'].strip()
        else:continue
        if pn:result.setdefault(pn,[]).append(c['id'])
    return result


def workspace(service,actor):
    cards=service.cards();kinds=catalog(service.store);index=published_index(cards,service.store)
    known_ids={r['card_id'] for r in service.store.all('SELECT card_id FROM nk_cards')}
    from .service import candidates
    active={}
    for j in service.store.all("SELECT id,card_id,kind,state,error FROM nk_jobs WHERE card_id IS NOT NULL AND state NOT IN ('confirmed','failed','cancelled') ORDER BY created"):
        active[j['card_id']]=j
    stopped={r['card_id']:r for r in service.store.all("SELECT card_id,error,save_job FROM nk_draft_followups WHERE state='stopped'")}
    latest={}
    for j in service.store.all("SELECT id,card_id,kind,state,error,result,revision FROM nk_jobs WHERE card_id IS NOT NULL ORDER BY created"):
        latest[j['card_id']]=j
    entries=[]
    for c in cards:
        if c['archived'] or published(c):continue
        raw=c.get('source_snapshot',{}).get('nk',{});statuses=raw.get('good_detailed_status') or []
        if c['external_status'] and not statuses and not any(t in c['external_status'].casefold() for t in ('чернов','ошиб','модерац','подпис','draft','error')):continue
        draft=not eligible(c)
        if not draft and not any(s in statuses for s in ('moderation','notsigned')) and not any(t in str(c['external_status'] or '').casefold() for t in ('модерац','подпис')):continue
        proposal=propose(c,kinds) if draft else None
        pn=c['pn'].strip() or (proposal['after']['pn'] if proposal else '')
        hidden=index.get(pn,[]) if draft and pn else []
        row={'id':c['id'],'stage':'prepare','action':'edit','missing':[],'automatic_changes':0,'published_ids':hidden,'job_id':None,'error':'','label':'','external_duplicate':c['id'] not in known_ids and bool(candidates(c['pn'],c['gtin'],cards,c['id']))}
        if draft:
            row['missing']=[i for i in proposal['issues'] if not (i=='Выберите тип переменной характеристики' and 'Выберите вид товара' in proposal['issues'])];row['automatic_changes']=len(proposal['changes'])
            if not row['missing'] and not row['automatic_changes']:row.update(stage='ready',action='submit',label='Готова к отправке')
            elif row['automatic_changes']:row.update(action='normalize',label='Можно заполнить автоматически')
            else:row['label']='Нужно заполнить: '+', '.join(row['missing'])
            if row['missing']==['Укажите или получите GTIN'] and not row['automatic_changes']:row.update(action='gtin',label='Нужно получить GTIN')
        if 'moderation' in statuses or 'модерац' in str(c['external_status'] or '').casefold():row.update(stage='moderation',action='wait',label='На модерации')
        if 'notsigned' in statuses or 'подпис' in str(c['external_status'] or '').casefold():row.update(stage='sign',action='publish',label='Готова к подписанию')
        job=active.get(c['id'])
        if job:
            row.update(stage='processing',action='wait',job_id=job['id'],label={'save':'Сохраняем в НК','moderate':'Отправляем на модерацию','publish':'Подписываем и публикуем','gtin':'Получаем GTIN'}.get(job['kind'],'Выполняется операция'))
            if job['state']=='unknown':row.update(stage='attention',action='job',label='Нужно сверить результат',error=job['error'] or 'НК не подтвердил результат отправки')
        previous=latest.get(c['id'])
        if not job and previous and stopped.get(c['id'],{}).get('save_job')==previous['id']:row['error']=stopped[c['id']]['error'] or ''
        if not job and previous and previous['state']=='failed' and previous['revision']==c['revision']:
            row['error']=previous['error'] or 'НК отклонил операцию'
            try:
                receipt=json.loads(previous['result'] or '{}')
                errors=[e.get('text','') for item in (receipt.get('error_details') or {}).get('items',[]) for e in item.get('errors',[]) if e.get('text')]
                if errors:row['error']='; '.join(errors[:3])
            except (ValueError,TypeError,AttributeError):pass
        if c['owner'] not in ('',actor):row.update(stage='attention',action='owner',label='В работе у '+c['owner'])
        entries.append(row)
    return {'entries':entries}


def submit(engine,actor,ids,admin=False,allow_duplicates=False,revisions=None):
    from .service import Service
    results=[]
    with engine.lock:
        svc=Service(engine.store);rows={r['id']:r for r in workspace(svc,actor)['entries']}
        for id in dict.fromkeys(ids):
            row=rows.get(id)
            if not row:results.append({'id':id,'status':'skipped','message':'Карточка уже не черновик'});continue
            if row['published_ids']:results.append({'id':id,'status':'skipped','message':'Этот PN уже опубликован'});continue
            if row['stage']=='processing':results.append({'id':id,'status':'queued','job_id':row['job_id']});continue
            if row['stage']!='ready':results.append({'id':id,'status':'error','message':row['label'] or 'Сначала заполните карточку','fields':row['missing']});continue
            c=svc.get(id)
            if revisions is not None and revisions.get(id)!=c['revision']:
                results.append({'id':id,'status':'error','message':'Карточка изменилась. Обновите список перед отправкой'});continue
            try:
                known=engine.store.one('SELECT card_id FROM nk_cards WHERE card_id=?',(id,))
                if known and not c['dirty']:
                    job=engine.prepare(actor,'moderate',id,c['revision'],admin=admin)
                else:
                    job=engine.prepare(actor,'save',id,c['revision'],{'duplicate_reason':'Создание отдельной карточки с точным совпадением подтверждено сотрудником' if allow_duplicates else ''},admin=admin)
                    with engine.store.transaction() as db:
                        db.execute("INSERT OR IGNORE INTO nk_draft_followups(save_job,card_id,actor,state) VALUES(?,?,?,'waiting')",(job,id,actor))
                        audit(db,actor,'draft_submit_requested',id,{'save_job':job,'after_save':'moderate'})
                results.append({'id':id,'status':'queued','job_id':job})
            except ValueError as e:results.append({'id':id,'status':'error','message':str(e)})
    return results


def advance(engine):
    """Resume after restarts; no resend on rejection or unknown outcome."""
    from .service import Service
    for row in engine.store.all("SELECT f.*,j.state AS save_state,j.error AS save_error FROM nk_draft_followups f JOIN nk_jobs j ON j.id=f.save_job WHERE f.state='waiting'"):
        if row['save_state'] in ('failed','cancelled'):
            with engine.store.transaction() as db:db.execute("UPDATE nk_draft_followups SET state='stopped',error=? WHERE save_job=?",(row['save_error'],row['save_job']))
            continue
        if row['save_state']!='confirmed':continue
        try:
            c=Service(engine.store).get(row['card_id'])
            job=engine.prepare(row['actor'],'moderate',c['id'],c['revision'])
            with engine.store.transaction() as db:
                db.execute("UPDATE nk_draft_followups SET moderate_job=?,state='queued' WHERE save_job=?",(job,row['save_job']))
                audit(db,row['actor'],'draft_moderation_after_save',c['id'],{'save_job':row['save_job'],'moderate_job':job})
        except ValueError as e:
            with engine.store.transaction() as db:db.execute("UPDATE nk_draft_followups SET state='stopped',error=? WHERE save_job=?",(str(e),row['save_job']))
