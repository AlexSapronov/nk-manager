"""Published product-kind dictionary and local creation defaults."""
import copy
import hashlib
from collections import Counter, defaultdict
import json
from functools import lru_cache

from .presets import ROOT, bundle

NAMES={'12':'Вид товара','23828':'Вид товара (при отсутствии в справочнике)','13914':'Модель / артикул производителя','2478':'Полное наименование товара','2504':'Товарный знак','3959':'Группа ТНВЭД','13933':'Код ТНВЭД','3961':'Код ОКПД2','24003':'Тип переменной характеристики','2463':'Номер в реестре российской промышленной продукции','23819':'Федеральный номенклатурный номер предмета снабжения (ФНН)','23300':'Требования подтверждения соответствия отсутствуют; основание'}
BASIS='ИМПОРТИРУЕМЫЕ КОМПЛЕКТУЮЩИЕ (ЗАПАСНЫЕ ЧАСТИ) ДЛЯ ПРОИЗВОДСТВА (СБОРКИ ПРОДУКЦИИ)'


def key(id):
    return f'{NAMES[id]} [#{id}]'


def value(attrs,id):
    return next((str(v).strip() for k,v in attrs.items() if f'[#{id}]' in k and not k.endswith('::unit')), str(attrs.get(NAMES.get(id,'')) or '').strip())


def kind_name(attrs):
    official=value(attrs,'12')
    return value(attrs,'23828') if official=='НЕТ В СПРАВОЧНИКЕ' else official


def synchronize_identity(card):
    """Derive both name representations from PN and the explicit kind, never reverse-parse PN."""
    attrs=card['attributes']
    label=kind_name(attrs)
    if not label or not card['pn']:
        return
    card['name']=card['pn'].strip()+' '+label
    for id,val in [('2478',card['name']),('13914',card['pn'].strip())]:
        keys=[k for k in attrs if f'[#{id}]' in k and not k.endswith('::unit')]
        if not keys and NAMES[id] in attrs:keys=[NAMES[id]]
        # Repeated PN rows may contain brand metadata; preserve those rows.
        if id=='13914':
            brand=value(attrs,'2504').casefold()
            keys=[k for k in keys if not brand or str(attrs[k]).strip().casefold()!=brand]
        target=keys[0] if keys else key(id)
        if id=='13914' and not keys and target in attrs:
            target += ' · PN'
        attrs[target]=val


@lru_cache(maxsize=1)
def baseline():
    return json.loads((ROOT/'product-kinds.json').read_text())['kinds']


def build_catalog(cards):
    from .nk import source_pn
    eligible=[]
    for card in cards:
        raw=card.get('source_snapshot',{}).get('nk',{})
        if not card.get('dirty') and raw.get('good_status')=='published' and raw.get('good_signed') is True and raw.get('good_detailed_status')==['published']:
            eligible.append(card)
    known_pns={source_pn(c.get('source_snapshot',{}).get('nk',{})) for c in cards}
    groups=defaultdict(list)
    for card in eligible:
        raw=card['source_snapshot']['nk']
        attrs={str(a['attr_id']):a for a in raw.get('good_attrs',[])}
        official=str(attrs.get('12',{}).get('attr_value') or '').strip()
        other=str(attrs.get('23828',{}).get('attr_value') or '').strip() if official=='НЕТ В СПРАВОЧНИКЕ' else ''
        label=other if official=='НЕТ В СПРАВОЧНИКЕ' else official
        tnved=str(attrs.get('13933',{}).get('attr_value') or card.get('tnved') or '').strip()
        if not label or label=='НЕТ В СПРАВОЧНИКЕ' or label in known_pns or len(tnved)!=10 or not tnved.isdigit() or not raw.get('categories'):
            continue
        groups[tnved,official,other].append((raw,attrs))
    kinds=[]
    for (tnved,official,other),rows in sorted(groups.items()):
        def mode(values):
            counts=Counter(v for v in values if v)
            return sorted(counts,key=lambda v:(-counts[v],str(v)))[0] if counts else ''
        cats=[(str(r['categories'][0]['cat_id']),r['categories'][0]['cat_name']) for r,_ in rows]
        cat_id,category=mode(cats)
        brands=Counter(str(r.get('brand_name') or '').strip() for r,_ in rows if str(r.get('brand_name') or '').strip())
        kinds.append({'id':hashlib.sha256((tnved+'\0'+official+'\0'+other).encode()).hexdigest()[:24], 'tnved':tnved,'name':other or official,'nk_kind':official,'other_kind':other,'published_count':len(rows),'category_id':cat_id,'category':category,'brands':[{'name':b,'count':brands[b]} for b in sorted(brands,key=lambda b:(-brands[b],b))], 'variable_type':mode(str(a.get('24003',{}).get('attr_value') or '') for _,a in rows)})
    return kinds


def catalog(store):
    # Merge new published kinds/brands after sync without persisting individual goods in presets.
    cards=[{'tnved':row['tnved'],'source_snapshot':{'nk':json.loads(row['raw'])}} for row in store.all('SELECT c.tnved,n.raw FROM nk_cards n JOIN cards c ON c.id=n.card_id')]
    result={k['id']:copy.deepcopy(k) for k in baseline()}
    for item in build_catalog(cards):
        if item['id'] not in result:
            result[item['id']]=item
        else:
            previous=result[item['id']]
            previous['published_count']=max(previous['published_count'],item['published_count'])
            brands={b['name']:b['count'] for b in previous['brands']}
            for b in item['brands']:brands[b['name']]=max(brands.get(b['name'],0),b['count'])
            previous['brands']=[{'name':b,'count':brands[b]} for b in sorted(brands,key=lambda b:(-brands[b],b))]
    return sorted(result.values(),key=lambda k:(k['tnved'],-k['published_count'],k['name']))


def selected(store,tnved,kind_id):
    found=next((k for k in catalog(store) if k['tnved']==tnved and k['id']==kind_id),None)
    if not found:
        raise ValueError('Выберите вид товара из опубликованных карточек этого ТН ВЭД')
    return found


def draft(store,data):
    tnved=str(data.get('tnved') or '').strip();pn=str(data.get('pn') or '').strip()
    if not pn or len(pn)>300:
        raise ValueError('Укажите PN, максимум 300 символов')
    item=selected(store,tnved,str(data.get('kind_id') or ''))
    brand=str(data.get('brand') or '').strip()
    if not brand:
        raise ValueError('Укажите фактический товарный знак')
    if len(brand)>2000:raise ValueError('Слишком длинный товарный знак')
    attrs={key('12'):item['nk_kind'],key('13914'):pn,key('13914')+'::unit':'Артикул',key('2504'):brand,key('3959'):tnved[:4],key('13933'):tnved,key('24003'):item['variable_type'],key('2463'):'ОТСУТСТВУЕТ',key('23819'):'ОТСУТСТВУЕТ',key('23300'):BASIS,key('23300')+'::unit':'ДА'}
    if item['other_kind']:attrs[key('23828')]=item['other_kind']
    if tnved in bundle():attrs[key('3961')]=bundle()[tnved]['default_okpd2']
    card={'pn':pn,'tnved':tnved,'gtin':'','name':'','category':item['category'],'card_type':'unit','attributes':attrs,'creation_kind_id':item['id']}
    synchronize_identity(card)
    return card
