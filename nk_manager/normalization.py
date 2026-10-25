"""Conservative, reviewable normalization of draft cards. No external calls."""
import copy
import re

from .creation import NAMES, BASIS, key, kind_name
from .presets import bundle


def label(text):
    return re.sub(r'[^\w]', '', str(text).casefold())


ALIASES={label(name):id for id,name in NAMES.items()}
ALIASES.update({label(name):'13914' for name in ('Модель','Модель / артикул','Артикул','PN','Артикул производителя')})
ALIASES.update({label('ТН ВЭД'):'13933',label('ОКПД2'):'3961',label('Полное наименование'):'2478',label('Бренд'):'2504'})


def attribute_id(name):
    match=re.search(r'\[#(\d+)\]',name)
    return match[1] if match else ALIASES.get(label(name.removesuffix('::unit')))


def eligible(card):
    raw=card.get('source_snapshot',{}).get('nk',{})
    statuses=set(raw.get('good_detailed_status') or ([raw['good_status']] if raw.get('good_status') else []))
    if card['archived']:
        return 'Остановлена локально'
    if raw.get('is_set') or raw.get('is_kit') or card['card_type'] not in ('','unit','Единичный','Единичный товар'):
        return 'Комплект или набор: отдельная обработка'
    if statuses and not statuses.issubset({'draft','errors'}):
        return 'Не черновик: '+str(card.get('external_status') or ', '.join(sorted(statuses)))
    status=str(card.get('external_status') or '').casefold()
    if not statuses and status and not any(s in status for s in ('чернов','ошиб','draft','error')):
        return 'Не черновик: '+str(card['external_status'])
    return ''


def propose(card,kinds):
    after={k:copy.deepcopy(card[k]) for k in ('pn','tnved','name','category','card_type','attributes')}
    attrs=after['attributes'];issues=[];notes=[]
    raw=card.get('source_snapshot',{}).get('nk',{})
    if not str(card.get('gtin') or '').strip():issues.append('Укажите или получите GTIN')
    if not after['category'].strip():issues.append('Не указана категория НК')
    def keys(id,unit=False):
        return [k for k in attrs if k.endswith('::unit')==unit and attribute_id(k)==id]
    def values(id):
        return list(dict.fromkeys(str(attrs[k]).strip() for k in keys(id) if str(attrs[k]).strip()))
    def put(id,value,unit=False,replace=False):
        if not str(value).strip():return
        found=keys(id,unit)
        if id=='13914' and not unit:
            found=[k for k in found if not brand or str(attrs[k]).strip().casefold()!=brand.casefold()]
        populated=[k for k in found if attrs[k].strip()]
        if populated and not replace:return
        target=(populated or found or [key(id)+('::unit' if unit else '')])[0]
        if id=='13914' and not unit and not found and target in attrs:target+=' · PN'
        if replace:
            for k in found:attrs[k]=value
        attrs[target]=value
    # Canonicalize recognizable Excel headers, retaining every nonempty value.
    for old in list(attrs):
        id=attribute_id(old)
        if id not in NAMES or '[#' in old:continue
        new=key(id)+('::unit' if old.endswith('::unit') else '')
        if new not in attrs or not attrs[new].strip():
            attrs[new]=attrs.pop(old)
        elif attrs[new]==attrs[old] or not attrs[old].strip():
            del attrs[old]
        else:
            issues.append('Разные значения поля «'+NAMES[id]+'»')
    for id in ('12','23828','13933','3961','24003','2504'):
        if len(values(id))>1:issues.append('Несколько значений: '+NAMES[id])
    brands=values('2504')
    brand=brands[0] if len(brands)==1 else ''
    if not brand:brand=str(raw.get('brand_name') or '').strip()
    if brand:put('2504',brand)
    else:issues.append('Не указан товарный знак')
    codes=[v for v in values('13933') if re.fullmatch(r'\d{10}',v)]
    code=str(after['tnved']).strip()
    if not code and len(codes)==1:code=codes[0];after['tnved']=code
    if not re.fullmatch(r'\d{10}',code):issues.append('Нужен полный ТН ВЭД из 10 цифр');code=''
    elif any(v!=code for v in codes):issues.append('ТН ВЭД в основных полях и характеристиках различается')
    options=[k for k in kinds if k['tnved']==code]
    pns=[v for v in values('13914') if not brand or v.casefold()!=brand.casefold()]
    pn=after['pn'].strip()
    if not pn and len(pns)==1:pn=pns[0];after['pn']=pn;notes.append('PN из модели/артикула')
    if len(pns)>1 or pn and any(v!=pn for v in pns):issues.append('Разные PN в основных полях и характеристиках')
    official=values('12');other=values('23828')
    if len(official)==1 and label(official[0])==label('НЕТ В СПРАВОЧНИКЕ'):
        put('12','НЕТ В СПРАВОЧНИКЕ',replace=True);official=['НЕТ В СПРАВОЧНИКЕ']
    for id,choices in [('24003',('КОЛИЧЕСТВО','ДЛИНА','ПЕРЕМЕННАЯ ХАРАКТЕРИСТИКА ОТСУТСТВУЕТ'))]:
        current=values(id)
        if len(current)==1:
            canonical=next((v for v in choices if label(v)==label(current[0])),None)
            if canonical:put(id,canonical,replace=True)
    kind=(other[0] if len(other)==1 else '') if official==['НЕТ В СПРАВОЧНИКЕ'] else official[0] if len(official)==1 else ''
    if not official and len(other)==1:kind=other[0];put('12','НЕТ В СПРАВОЧНИКЕ')
    # Recover only an exact dictionary phrase at the name boundary. PN is never fuzzy-matched.
    name=after['name'].strip();matches=[]
    if not kind or not pn:
        for item in options:
            text=item['name']
            for remaining in ([name[:-len(text)-1]] if name.endswith(' '+text) else [])+([name[len(text)+1:]] if name.startswith(text+' ') else []):
                # A leftover Russian description is not an article. Only an
                # explicitly known PN may contain such text without review.
                if not pn and re.search(r'\s',remaining.strip()) and re.search(r'[А-Яа-яЁё]{3,}',remaining):continue
                if remaining.strip() and (not pn or remaining.strip()==pn) and (not kind or text==kind):
                    matches.append((item,remaining.strip()))
        # Prefer the most specific full dictionary phrase; a short kind like
        # 'Разъём' must not make 'Разъём на кабель PN' ambiguous.
        longest=max((len(m[0]['name']) for m in matches),default=0)
        matches=[m for m in matches if len(m[0]['name'])==longest]
        identities={(m[0]['name'],m[1]) for m in matches}
        if len(identities)==1:
            item,recovered=matches[0]
            if not kind:kind=item['name'];put('12',item['nk_kind']);put('23828',item['other_kind'])
            if not pn:pn=recovered;after['pn']=pn;notes.append('PN из полного наименования по точному виду')
    if not pn:issues.append('Не указан PN')
    if not kind:issues.append('Выберите вид товара')
    matching=[k for k in options if k['name']==kind]
    # Ignore case/spacing variants only when they identify one dictionary kind.
    if kind and not matching:
        variants=[k for k in options if ' '.join(k['name'].casefold().split())==' '.join(kind.casefold().split())]
        if len(variants)==1:
            matching=variants
            notes.append('Вид сопоставлен по регистру/пробелам; написание в карточке сохранено')
    if code:
        put('13933',code);put('3959',code[:4])
        profile=bundle().get(code)
        if profile:put('3961',profile['default_okpd2'])
        else:issues.append('Нет эталонного шаблона для ТН ВЭД')
        put('2463','ОТСУТСТВУЕТ');put('23819','ОТСУТСТВУЕТ')
        # Existing conformity documents must not be supplemented with a contradictory exemption.
        documents=any(v.strip() and (attribute_id(k) in ('23557','13836','23558') or any(word in k.casefold() for word in ('деклараци','сертификат'))) for k,v in attrs.items())
        if not documents:
            put('23300',BASIS)
            if values('23300')==[BASIS]:put('23300','ДА',unit=True)
        if len(matching)==1:put('24003',matching[0]['variable_type'])
        elif not values('24003'):issues.append('Выберите тип переменной характеристики')
    if pn and not any('PN' in i for i in issues):
        put('13914',pn);put('13914','Артикул',unit=True)
    if pn and kind and not any('PN' in i or 'Вид товара' in i or 'Несколько значений: Вид' in i for i in issues):
        after['name']=pn+' '+kind
        put('2478',after['name'],replace=True)
    if not after['card_type']:after['card_type']='unit'
    changes=[]
    for f in ('pn','tnved','name','card_type'):
        if card[f]!=after[f]:changes.append({'field':f,'before':card[f],'after':after[f]})
    for k in dict.fromkeys([*card['attributes'],*attrs]):
        before=card['attributes'].get(k);new=attrs.get(k)
        if before!=new:changes.append({'field':k,'before':before,'after':new})
    # No category/GTIN inference or modifications of existing substantive values.
    return {'after':after,'changes':changes,'issues':list(dict.fromkeys(issues)),'notes':notes}
