"""Saved revision-bound plans for local draft normalization."""
import json

from .creation import catalog
from .normalization import eligible, propose
from .service import candidates, card_record, Conflict
from .store import audit, dumps, now, uid


def preview(service,actor,ids=None):
    cards=service.cards();kinds=catalog(service.store)
    selected=set(ids) if ids is not None else None
    busy={r['card_id'] for r in service.store.all("SELECT card_id FROM nk_jobs WHERE state NOT IN ('confirmed','failed','cancelled')")}
    entries=[]
    for card in cards:
        if selected is not None and card['id'] not in selected:continue
        blocked=eligible(card)
        if not blocked and card['owner'] not in ('',actor):blocked='Взята другим сотрудником'
        if not blocked and card['id'] in busy:blocked='Выполняется операция НК'
        base={k:card[k] for k in ('id','pn','name','gtin','revision')}
        if blocked:
            entries.append({**base,'state':'skipped','changes':[],'issues':[],'notes':[],'message':blocked,'duplicates':[]})
            continue
        result=propose(card,kinds)
        entries.append({**base,**result,'state':'review' if result['issues'] else 'ready' if result['changes'] else 'unchanged','message':'Нужна проверка отдельных полей' if result['issues'] else 'Готово к приведению' if result['changes'] else 'Соответствует эталону','duplicates':candidates(result['after']['pn'],card['gtin'],cards,card['id'])})
    projected={e['id']:e['after'] for e in entries if 'after' in e}
    comparison=[{**c,**{k:projected[c['id']][k] for k in ('pn','name')} } if c['id'] in projected else c for c in cards]
    for entry in entries:
        if 'after' in entry:entry['duplicates']=candidates(entry['after']['pn'],entry['gtin'],comparison,entry['id'])
    plan_id=uid()
    with service.store.transaction() as db:
        db.execute("INSERT INTO plans VALUES(?,'normalize',?,?,?,0)",(plan_id,actor,dumps(entries),now()))
    return {'id':plan_id,'entries':[{k:v for k,v in e.items() if k!='after'} for e in entries]}


def apply(service,actor,plan_id,ids,reason=''):
    if not isinstance(ids,list) or not ids or len(ids)>10000 or any(not isinstance(i,str) for i in ids):raise ValueError('Выберите черновики из предпросмотра')
    result=[]
    with service.store.transaction() as db:
        plan=db.execute("SELECT * FROM plans WHERE id=? AND actor=? AND kind='normalize'",(plan_id,actor)).fetchone()
        if not plan or plan['applied']:raise Conflict('Предпросмотр отсутствует или уже применён')
        entries=json.loads(plan['body']);selected=set(ids)
        if not selected.issubset({e['id'] for e in entries}):raise ValueError('Карточки отсутствуют в предпросмотре')
        for entry in entries:
            if entry['id'] not in selected:continue
            identity={k:entry[k] for k in ('id','pn','name','gtin')}
            if not entry['changes'] or entry['state']=='skipped':
                result.append({**identity,'status':'skipped','message':entry['message']});continue
            row=db.execute('SELECT * FROM cards WHERE id=?',(entry['id'],)).fetchone()
            card=card_record(row) if row else None
            busy=db.execute("SELECT id FROM nk_jobs WHERE card_id=? AND state NOT IN ('confirmed','failed','cancelled')",(entry['id'],)).fetchone()
            if not card or card['revision']!=entry['revision'] or eligible(card) or card['owner'] not in ('',actor) or busy:
                result.append({**identity,'status':'conflict','message':'Карточка изменилась, занята или уже не черновик. Пересоздайте предпросмотр'});continue
            after=entry['after']
            current=[dict(r) for r in db.execute('SELECT id,pn,gtin,name FROM cards')]
            intended={e['id']:e['after'] for e in entries if e['id'] in selected and 'after' in e}
            comparison=[{**c,**{k:intended[c['id']][k] for k in ('pn','name')}} if c['id'] in intended else c for c in current]
            matches=candidates(after['pn'],card['gtin'],current+comparison,card['id'])
            if matches and not str(reason).strip():
                result.append({**identity,'status':'warning','message':'Подтвердите точные совпадения PN/GTIN'});continue
            db.execute('UPDATE cards SET pn=?,tnved=?,name=?,card_type=?,attributes=?,owner=?,dirty=1,revision=revision+1,updated=? WHERE id=?',(after['pn'],after['tnved'],after['name'],after['card_type'],dumps(after['attributes']),actor,now(),card['id']))
            audit(db,actor,'draft_normalized_local',card['id'],{'before':{k:card[k] for k in ('pn','tnved','name','card_type','attributes','owner','dirty')},'after':after,'changes':entry['changes'],'issues':entry['issues'],'duplicate_reason':reason if matches else None,'plan':plan_id})
            result.append({**identity,'pn':after['pn'],'name':after['name'],'status':'updated','message':'Приведено локально','issues':entry['issues']})
        db.execute('UPDATE plans SET applied=1 WHERE id=?',(plan_id,))
    return result
