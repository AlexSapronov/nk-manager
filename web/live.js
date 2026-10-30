'use strict';
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={cards:'Карточки',drafts:'Черновики',import:'Импорт Excel',batches:'Партии',templates:'Шаблоны',admin:'Администрирование'};
const icons={cards:'▦',drafts:'▤',import:'↥',batches:'☷',templates:'◇',admin:'⚙'};
const state={user:null,cards:[],page:'cards',query:'',category:'',tnved:'',filter:'all',selected:new Set(),pageNo:1,drawer:null,tab:'main',dirty:false,expanded:false,file:null,importPlan:null,importStep:1,importPurpose:'catalog',header:1,sheet:'',mapping:null,importDefaults:{category:'',tnved:'',card_type:''}};
let toastTimer;
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,6500);}
async function api(path,options={}){
 const headers={...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(state.user?{'X-CSRF-Token':state.user.csrf}:{}),...options.headers};
 let response;
 try{response=await fetch(path,{cache:'no-store',credentials:'same-origin',...options,headers});}catch(error){throw new Error('Нет соединения с сервисом. Введённые данные остаются в открытой форме.');}
 if(response.status===403&&state.user&&path!=='/api/me'){
  let detail='';try{detail=(await response.clone().json()).detail;}catch(_){}
  if(detail==='Сессия изменилась; обновите страницу'){
   // Multiple tabs can share the new cookie but have different CSRF tokens.
   const fresh=await fetch('/api/me',{cache:'no-store',credentials:'same-origin'});
   if(fresh.ok){const refreshed=await fresh.json();if(refreshed.username!==state.user.username)throw new Error('В другой вкладке выполнен вход в другую учётную запись. Обновите страницу.');state.user=refreshed;headers['X-CSRF-Token']=state.user.csrf;response=await fetch(path,{cache:'no-store',credentials:'same-origin',...options,headers});}
  }
 }
 if(!response.ok){let body;try{body=await response.json();}catch(_){body={};}
  if(response.status===401&&state.user)throw new Error('Сессия завершилась. Войдите снова после сохранения несохранённых данных.');
  throw new Error(typeof body.detail==='string'?body.detail:`Ошибка ${response.status}. Проверьте журнал сервиса.`);
 }
 return response.json();
}
const json=(method,body)=>({method,body:JSON.stringify(body)});
function modal(title,body,footer='<button data-close>Закрыть</button>'){
 const d=$('#dialog');if(d.open)d.close();d.classList.remove('template-dialog');d.innerHTML=`<div class="dialog-head"><h2>${title}</h2><button data-close aria-label="Закрыть диалог">✕</button></div><div class="dialog-body">${body}<p class="inline-error" id="dialog-error" role="alert"></p></div><div class="dialog-footer">${footer}</div>`;d.showModal();$$('[data-close]',d).forEach(x=>x.onclick=()=>d.close());
}
function showError(error){if($('#dialog').open&&$('#dialog-error'))$('#dialog-error').textContent=error.message;else toast(error.message);}
function bind(id,fn){const el=$(id);if(el)el.onclick=async event=>{if(el.disabled||el.dataset.busy)return;el.dataset.busy='true';el.setAttribute('aria-busy','true');try{await fn(event);}catch(error){showError(error);}finally{delete el.dataset.busy;el.removeAttribute('aria-busy');}};}
function heading(title,description,actions=''){return `<div class="page-heading"><div><h1>${title}</h1><p>${description}</p></div><div class="actions">${actions}</div></div>`;}
function badge(c){return `<span class="badge ${c.dirty?'amber':''}">${esc(c.external_status||'Нет статуса НК')}</span>`;}
function leave(){return !state.dirty||window.confirm('Есть несохранённый ввод. Закрыть без сохранения?');}
async function reloadCards(force=false){
 const marker=await api('/api/workspace/version');
 if(!force&&state.workspaceVersion===marker.version)return false;
 const [cards,workspace]=await Promise.all([api('/api/cards/summary'),api('/api/drafts/workspace')]);
 state.cards=cards;state.draftWorkspace=workspace;state.workspaceVersion=marker.version;if(!state.showPublishedDrafts){const hidden=new Set(workspace.entries.filter(e=>e.published_ids.length).map(e=>e.id));state.selected=new Set([...state.selected].filter(id=>!hidden.has(id)));}return true;}
async function render(){
 $('#nav').innerHTML=Object.keys(labels).filter(p=>p!=='admin'||state.user.admin).map(p=>`<a href="#${p}" class="${p===state.page?'active':''}"><span>${icons[p]}</span><span class="nav-label">${labels[p]}</span></a>`).join('');$('#breadcrumb').textContent=labels[state.page];
 try{if(['cards','drafts'].includes(state.page)){await reloadCards();registry();}else if(state.page==='import')renderImport();else if(state.page==='templates')await templates();else if(state.page==='batches')await batches();else if(state.page==='admin')await admin();}catch(error){showError(error);}
}
function filtered(){if(state.page==='drafts')return DraftUI.filtered();const q=state.query.toLocaleLowerCase();return state.cards.filter(c=>(state.showPublishedDrafts||!DraftUI.hidden(c))&&(!q||[c.pn,c.name,c.gtin].some(v=>v.toLocaleLowerCase().includes(q)))&&(!state.category||c.category===state.category)&&(!state.tnved||c.tnved===state.tnved)&&(state.filter==='archive'?c.archived:(!c.archived||/Опубликована/i.test(c.external_status||'')))&&(state.page!=='drafts'||c.dirty||!c.external_status||/чернов|измен/i.test(c.external_status))&&(state.filter!=='dirty'||c.dirty));}
function registry(){
 if(state.page==='drafts'){DraftUI.render();return;}
 const cards=state.cards,dirty=cards.filter(c=>c.dirty).length;
 $('#main').innerHTML=heading(state.page==='drafts'?'Черновики и локальные правки':'Карточки товаров','Выберите черновики → заполните по шаблону → проверьте карточки → отправьте в НК.',`<button id="refresh">↻ Обновить список</button><button id="export-all">Экспорт для анализа</button><button id="normalize-drafts">Привести все черновики к эталону</button><button id="create" class="primary">+ Создать карточку</button>`)+`<div class="stats">${[['Всего в базе',cards.length],['Локальные правки',dirty],['Категории',new Set(cards.map(c=>c.category).filter(Boolean)).size],['Снимки из XLSX НК',cards.filter(c=>c.source==='nk_xlsx').length]].map(([l,n])=>`<div class="stat"><div><small>${l}</small><b>${n}</b></div></div>`).join('')}</div><section class="registry"><div class="tabs">${[['all','В работе'],['dirty','Локальные правки'],['archive','Локальный архив']].map(([key,l])=>`<button data-filter="${key}" class="${state.filter===key?'active':''}">${l}</button>`).join('')}</div><div class="filters"><input id="search" aria-label="Поиск карточек" placeholder="PN, GTIN или наименование" value="${esc(state.query)}"><div id="category-filter-host" class="category-filter-host"></div><select id="tnved-filter" aria-label="ТН ВЭД"><option value="">Все ТН ВЭД</option>${[...new Set(cards.map(c=>c.tnved))].filter(Boolean).sort().map(c=>`<option ${state.tnved===c?'selected':''}>${esc(c)}</option>`).join('')}</select></div>${state.draftWorkspace?.entries.some(e=>e.published_ids.length)?'<label class="draft-hidden-toggle"><input id="show-published-drafts" type="checkbox" '+(state.showPublishedDrafts?'checked':'')+'> Показать черновики с опубликованным PN</label>':''}<div class="table-wrap" id="rows"></div><div id="bulk"></div><div class="registry-footer" id="pagination"></div></section>`;
 bind('#refresh',async()=>{await reloadCards();registry();});bind('#export-all',()=>exportDialog());bind('#create',()=>CreationUI.open());bind('#normalize-drafts',()=>NormalizationFlow.preview());
 $('#search').oninput=e=>{state.query=e.target.value;state.pageNo=1;rows();};CategoryFilter.mount($('#category-filter-host'),[...new Set(cards.map(c=>c.category))].filter(Boolean).sort(),state.category,value=>{state.category=value;state.pageNo=1;rows();});$('#tnved-filter').onchange=e=>{state.tnved=e.target.value;state.pageNo=1;rows();};
 if($('#show-published-drafts'))$('#show-published-drafts').onchange=e=>{state.showPublishedDrafts=e.target.checked;state.selected.clear();rows();};
 $$('[data-filter]').forEach(b=>b.onclick=()=>{state.filter=b.dataset.filter;state.pageNo=1;registry();});rows();
}
function rows(){
 const data=filtered(),pages=Math.max(1,Math.ceil(data.length/15));state.pageNo=Math.min(state.pageNo,pages);const slice=data.slice((state.pageNo-1)*15,state.pageNo*15);
 $('#rows').innerHTML=data.length?`<table><thead><tr><th class="check-col"><input id="select-page" type="checkbox" aria-label="Выбрать видимую страницу" ${slice.every(c=>state.selected.has(c.id))?'checked':''}></th><th class="pn">PN / наименование</th><th>GTIN</th><th>ТН ВЭД</th><th>Снимок статуса НК</th><th>Локальная работа</th><th>Ответственный</th></tr></thead><tbody>${slice.map(c=>`<tr data-card="${c.id}"><td class="check-col"><input type="checkbox" data-select="${c.id}" aria-label="Выбрать ${esc(c.pn||c.name||c.gtin)}" ${state.selected.has(c.id)?'checked':''}></td><td class="pn"><button class="link-button" data-open="${c.id}"><b>${esc(c.pn||c.name||c.gtin||'Без наименования')}</b></button><small title="${esc(c.name)}">${esc(c.pn?(c.name||'Без наименования'):'PN не указан')}</small></td><td class="mono">${esc(c.gtin||'Не указан')}</td><td class="mono">${esc(c.tnved||'Не указан')}</td><td>${badge(c)}</td><td>${c.archived?'Остановлена':c.dirty?'Есть локальные правки':'Без локальных правок'}</td><td>${esc(c.owner||'Свободна')}</td></tr>`).join('')}</tbody></table>`:`<div class="empty"><b>${state.cards.length?'Нет результатов':'Каталог пуст'}</b>${state.cards.length?'Измените поиск или фильтр.':'Получите каталог через API в администрировании, импортируйте XLSX или создайте карточку. Здесь нет демонстрационных товаров.'}</div>`;
 $$('[data-card]').forEach(tr=>tr.onclick=e=>{if(!e.target.closest('input'))openCard(tr.dataset.card);});
 $$('[data-select]').forEach(box=>box.onchange=()=>{box.checked?state.selected.add(box.dataset.select):state.selected.delete(box.dataset.select);rows();});
 if($('#select-page'))$('#select-page').onchange=e=>{slice.forEach(c=>e.target.checked?state.selected.add(c.id):state.selected.delete(c.id));rows();};
 $('#pagination').innerHTML=`<span>${data.length} карточек · страница ${state.pageNo}/${pages}</span><div class="actions"><button id="prev" ${state.pageNo===1?'disabled':''}>←</button><button id="next" ${state.pageNo===pages?'disabled':''}>→</button></div>`;
 bind('#prev',()=>{state.pageNo--;rows();});bind('#next',()=>{state.pageNo++;rows();});
 $('#bulk').innerHTML=state.selected.size?`<div class="bulk"><strong>Выбрано: ${state.selected.size}</strong>${data.some(c=>!state.selected.has(c.id))&&data.length>15?`<button id="select-filtered">Выбрать все найденные (${data.length})</button>`:''}<button id="apply-templates">Заполнить по шаблону</button><button id="export-selected">Экспорт выбранных</button><button id="external-action">Отправка в НК</button><button id="clear-selection">Снять выделение</button></div>`:'';
 bind('#select-filtered',()=>{data.forEach(c=>state.selected.add(c.id));rows();});bind('#clear-selection',()=>{state.selected.clear();rows();});bind('#export-selected',()=>exportDialog([...state.selected]));bind('#external-action',unavailable);bind('#apply-templates',templatePlan);
}
function exportDialog(ids){
 const selected=ids||filtered().map(c=>c.id);if(!selected.length){toast('Нет карточек для выгрузки');return;}
 const params=new URLSearchParams();params.set('ids',selected.join(','));
 if(params.toString().length>7000){toast('Для большого каталога используйте выгрузку всех записей в разделе «Шаблоны».');return;}
 modal('Выгрузить для создания шаблонов',`<p>Будет выгружено: <b>${selected.length}</b>. ZIP содержит полный JSON, таблицу XLSX и манифест. Токены и пароли не включаются.</p><div class="notice">Проверьте коммерчески чувствительные сведения перед передачей. Локальные правки отмечены отдельно; выгрузка не является свежим ответом API НК.</div>`,'<button data-close>Отмена</button><button class="primary" id="download-zip">Скачать ZIP</button><button id="download-xlsx">Скачать XLSX</button>');
 bind('#download-zip',()=>download('/api/export?format=zip&'+params,'nk-catalog-analysis.zip'));bind('#download-xlsx',()=>download('/api/export?format=xlsx&'+params,'nk-catalog-analysis.xlsx'));
}
async function download(url,name){const response=await fetch(url);if(!response.ok)throw new Error('Не удалось выгрузить файл. Проверьте сессию.');const blob=await response.blob();const anchor=document.createElement('a');anchor.href=URL.createObjectURL(blob);anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(anchor.href),30000);toast('Файл сформирован.');}
async function openCard(id){
 if(!leave())return;
 try{
  // Full attributes and official NK snapshots are loaded only when needed.
  const card=id?await api('/api/cards/'+encodeURIComponent(id)):
   {id:null,pn:'',gtin:'',name:'',tnved:'',category:'',card_type:'',attributes:{},owner:state.user.username,revision:0,source:'local',external_status:null};
  if(state.dirty&&!leave())return;
  state.allAttributeFields=false;state.drawer=card;state.dirty=false;state.tab='main';await drawCard();
 }catch(error){showError(error);}
}
function capture(){if(!state.drawer)return;$$('[data-field]',$('#drawer')).forEach(i=>state.drawer[i.dataset.field]=i.value);$$('[data-attribute]',$('#drawer')).forEach(i=>state.drawer.attributes[i.dataset.attribute]=i.value);CreationUI.syncName(state.drawer);}
function field(key,label,value,readonly=false){return `<label class="field">${label}<input data-field="${key}" id="field-${key}" value="${esc(value)}" ${readonly?'readonly':''}></label>`;}
async function drawCard(){
 const d=$('#drawer'),c=state.drawer;d.hidden=!c;if(!c)return;d.classList.toggle('expanded',state.expanded);const workflow=DraftUI.meta(c.id),canEdit=!c.archived&&!workflow?.job_id&&!['processing','moderation','sign'].includes(workflow?.stage)&&(c.owner===''||c.owner===state.user.username||state.user.admin);
 d.innerHTML=`<div class="drawer-head"><div class="drawer-controls"><button id="close-drawer">← К реестру</button><button id="expand-drawer">${state.expanded?'↙ Свернуть':'↗ На весь экран'}</button></div><h2>${esc(c.pn||c.name||(c.id?'PN не указан':'Новая карточка'))}</h2><div class="actions">${badge(c)}<span class="muted">Ревизия ${c.revision}</span></div><p class="muted">${esc(c.owner||'Свободна')} · ${esc(c.category||'Категория не задана')}</p></div><div class="drawer-body"><div class="tabs">${[['main','Основное'],['attributes','Характеристики'],['nk','НК'],['history','История']].map(([key,l])=>`<button data-tab="${key}" class="${state.tab===key?'active':''}">${l}</button>`).join('')}</div><div id="card-body"></div><p class="inline-error" id="card-error" role="alert"></p></div><div class="drawer-footer"><button id="save-card" class="primary" ${canEdit?'':'disabled'}>Сохранить</button>${c.id&&state.page!=='drafts'?`<button id="claim-card">${c.owner===state.user.username?'Освободить':'Взять в работу'}</button>`:''}${DraftUI.meta(c.id)?'<button id="draft-next-action">'+esc(({normalize:'Заполнить по эталону',edit:'Заполнить недостающее',gtin:'Получить GTIN',submit:'Отправить на модерацию',publish:'Подписать и опубликовать',wait:'Посмотреть этап',job:'Посмотреть результат',owner:'Посмотреть этап'})[DraftUI.meta(c.id).action])+'</button>':''}<button id="next-card">Следующая →</button></div>`;
 bind('#close-drawer',()=>{if(leave()){state.drawer=null;state.dirty=false;d.hidden=true;}});bind('#expand-drawer',()=>{capture();state.expanded=!state.expanded;drawCard();});
 $$('[data-tab]').forEach(b=>b.onclick=()=>{capture();state.tab=b.dataset.tab;drawCard();});
 bind('#save-card',async()=>{capture();const url='/api/cards'+(c.id?'/'+c.id:'');try{state.drawer=await api(url,json(c.id?'PUT':'POST',c));state.dirty=false;await reloadCards();if(['cards','drafts'].includes(state.page))registry();await drawCard();toast('Сохранено в базе. В НК ничего не отправлено.');}catch(error){$('#card-error').textContent=error.message;}});
 bind('#claim-card',async()=>{if(!leave())return;state.drawer=await api('/api/cards/'+c.id+'/claim',json('POST',{revision:c.revision,release:c.owner===state.user.username}));state.dirty=false;await reloadCards();drawCard();});
 bind('#draft-next-action',()=>{if(state.dirty){toast('Сохраните введённые данные.');return;}const m=DraftUI.meta(c.id);return m.action==='edit'?CreationUI.open(c.tnved,c):DraftUI.act(c.id);});
 bind('#check-card',()=>{capture();state.tab='checks';drawCard();});bind('#next-card',()=>{const list=filtered(),i=list.findIndex(x=>x.id===c.id);if(i>=0&&i+1<list.length)openCard(list[i+1].id);else toast('Последняя карточка текущего фильтра.');});
 const content=$('#card-body');
 if(state.tab==='main')content.innerHTML=`${!canEdit?'<div class="notice">Для редактирования возьмите свободную карточку в работу. Остановленную сначала восстановите.</div>':''}<div class="form-grid">${field('pn','PN / модель',c.pn,!canEdit)}${field('gtin','Существующий GTIN',c.gtin,!canEdit)}${field('name','Полное наименование · PN + вид товара',c.name,!canEdit||!!CreationUI.kindName(c))}${field('tnved','ТН ВЭД · 10 цифр',c.tnved,!canEdit)}${field('category','Категория источника',c.category,!canEdit)}${field('card_type','Тип карточки источника',c.card_type,!canEdit)}</div><p><b>Вид товара:</b> ${esc(CreationUI.kindName(c)||'Не выбран')}</p><button id="choose-product-kind" ${canEdit?'':'disabled'}>Выбрать вид и заполнить по эталону</button><div class="section"><h3>Источник данных</h3><p class="muted">${esc(c.source)} · внешний ID: ${esc(c.external_id||'В файле не указан')}<br>Последнее локальное обновление: ${esc(c.updated||'не сохранено')}</p>${c.id?`<button id="archive-card" class="danger">${c.archived?'Вернуть в локальную работу':'Остановить локальную работу'}</button>`:''}</div>`;
 if(state.tab==='main'&&state.page==='drafts'){
   const brandKey=Object.keys(c.attributes).find(k=>k.includes('[#2504]'))||'Товарный знак [#2504]';
  content.innerHTML=`<div class="form-grid">${field('pn','PN / артикул',c.pn,!canEdit)}${field('tnved','ТН ВЭД',c.tnved,!canEdit)}<label class="field">Товарный знак<input data-attribute="${esc(brandKey)}" value="${esc(c.attributes[brandKey]||c.source_snapshot?.nk?.brand_name||'')}" ${canEdit?'':'readonly'}></label>${field('name','Полное наименование',c.name,true)}</div><p><b>Вид товара:</b> ${esc(CreationUI.kindName(c)||'Нужно выбрать')}</p><button id="choose-product-kind" ${canEdit?'':'disabled'}>Выбрать вид товара</button><p class="muted">GTIN: ${esc(c.gtin||'нужно получить на вкладке НК')}</p><details><summary>Дополнительные данные</summary><p>${esc(c.category)} · ${esc(c.card_type)}</p><p>Источник: ${esc(c.source)} · ID НК: ${esc(c.external_id||'не создана')}</p></details>`;
 }
 if(state.tab==='attributes'){content.innerHTML=await NkUI.attributesHtml(c,canEdit);if(state.drawer!==c||state.tab!=='attributes')return;}
 if(state.tab==='nk'){content.innerHTML=await NkUI.cardHtml(c);if(state.drawer!==c||state.tab!=='nk')return;NkUI.bindCard(c);}
 if(state.tab==='history'){
 const history=c.id?await api('/api/cards/'+c.id+'/history'):[];if(state.drawer!==c||state.tab!=='history')return;
 content.innerHTML=history.map(e=>`<div class="history-event"><small>${esc(e.created)} · ${esc(e.actor)}</small><b>${esc(eventName(e.action))}</b><details><summary>Подробности</summary><pre class="source-details">${esc(JSON.stringify(JSON.parse(e.detail),null,2))}</pre></details></div>`).join('')||'<p class="muted">Действий пока нет.</p>';content.innerHTML+=`<details><summary>Исходный снимок XLSX</summary><pre class="source-details">${esc(JSON.stringify(c.source_snapshot||{},null,2))}</pre></details>`;
 }
 $$('input,textarea,select',content).forEach(i=>i.oninput=()=>state.dirty=true);
 bind('#choose-product-kind',()=>CreationUI.open(c.tnved,c));
 if($('#field-pn'))$('#field-pn').oninput=()=>{c.pn=$('#field-pn').value;CreationUI.syncName(c);if(CreationUI.kindName(c))$('#field-name').value=c.name;state.dirty=true;};
 bind('#toggle-attribute-fields',()=>{capture();state.allAttributeFields=!state.allAttributeFields;drawCard();});
 bind('#nk-action',()=>NkUI.actions([c.id]));bind('#add-attribute',()=>{capture();modal('Новое локальное поле','<label class="field">Название<input id="attribute-name" maxlength="200"></label>','<button data-close>Отмена</button><button id="confirm-attribute" class="primary">Добавить</button>');bind('#confirm-attribute',()=>{const key=$('#attribute-name').value.trim();if(!key||Object.hasOwn(c.attributes,key))throw new Error('Укажите новое непустое имя поля');c.attributes[key]='';state.allAttributeFields=true;state.dirty=true;$('#dialog').close();drawCard();});});
 bind('#archive-card',()=>{modal(c.archived?'Вернуть в работу':'Остановить работу','<p>Изменяется только локальная отметка. Внешний статус НК остаётся прежним.</p><label class="field">Причина<textarea id="archive-reason"></textarea></label>','<button data-close>Отмена</button><button id="archive-confirm" class="primary">Подтвердить</button>');bind('#archive-confirm',async()=>{state.drawer=await api('/api/cards/'+c.id+'/archive',json('POST',{revision:c.revision,reason:$('#archive-reason').value,restore:!!c.archived}));state.dirty=false;await reloadCards();$('#dialog').close();registry();drawCard();});});
}
function eventName(name){return ({card_saved:'Сохранены локальные правки',import_create:'Карточка импортирована',import_update:'Обновлён снимок XLSX',claim:'Взята в работу',release:'Освобождена',stopped_local:'Локальная работа остановлена',restored_local:'Возвращена в локальную работу',template_applied_local:'Применён локальный шаблон',draft_normalized_local:'Черновик приведён к эталону'})[name]||name;}
function renderImport(){
 const step=state.importStep,p=state.importPlan;
 $('#main').innerHTML=heading('Импорт настоящего XLSX','Файл сохраняется на сервере; импорт не создаёт товары и GTIN в НК')+`<div class="wizard"><div class="stepper">${['Файл','Сопоставление','Проверка','Подтверждение'].map((l,i)=>`<span class="step ${step===i+1?'active':''}">${i+1}. ${l}</span>`).join('')}</div><div id="import-body"></div><p id="import-error" class="inline-error" role="alert"></p><div class="wizard-footer"><button id="import-back" ${step===1?'disabled':''}>← Назад</button><button id="import-next" class="primary">${step===4?'Импортировать выбранные строки':'Далее →'}</button></div></div>`;
 const body=$('#import-body');
 if(step===1){body.innerHTML=`<h2>Выберите файл</h2><label class="field">Назначение<select id="import-purpose"><option value="catalog">Официальная выгрузка существующего каталога НК</option><option value="new">Новые товары из рабочего Excel</option></select></label><label class="field">XLSX до 20 МБ<input id="import-file" type="file" accept=".xlsx" class="file-input"></label><p class="muted" id="selected-file">${esc(state.file?.name||'Файл не выбран')}</p><label class="field">Номер строки заголовков<input id="header-row" type="number" min="1" max="100" value="${state.header}"></label><div class="notice info">Для шаблонов категорий нужен полный отчёт НК по каждой категории. Краткая выгрузка содержит меньше характеристик.</div>`;$('#import-purpose').value=state.importPurpose;$('#import-purpose').onchange=e=>state.importPurpose=e.target.value;$('#import-file').onchange=e=>{state.file=e.target.files[0];state.sheet='';state.mapping=null;$('#selected-file').textContent=state.file?.name||'';};$('#header-row').onchange=e=>state.header=Number(e.target.value);}
 if(step===2&&p){body.innerHTML=`<h2>Сопоставьте поля</h2><label class="field">Лист<select id="sheet-select">${p.sheets.map(s=>`<option ${p.sheet===s?'selected':''}>${esc(s)}</option>`).join('')}</select></label><div class="content-scroll import-preview"><table><tr><th>Поле</th><th>Колонка источника</th></tr>${[['pn','PN / модель'],['gtin','GTIN'],['tnved','ТН ВЭД'],['name','Наименование'],['category','Категория'],['card_type','Тип карточки'],['external_id','Внешний ID НК'],['external_status','Статус НК']].map(([key,l])=>`<tr><td>${l}</td><td><select data-map="${key}" aria-label="Колонка ${l}"><option value="">Не сопоставлено</option>${p.headers.map((h,i)=>`<option value="${i}" ${p.mapping[key]===i?'selected':''}>${esc(h)}</option>`).join('')}</select></td></tr>`).join('')}</table></div><p class="muted">Остальные колонки сохранятся как характеристики. Сопоставляйте только настоящие внешние ID; не используйте номер строки вместо ID НК.</p><details><summary>Общие значения, если их нет в файле</summary><p class="muted">Введите только известные значения из выбранной категории НК. Непустые колонки не перезаписываются.</p>${[['category','Категория'],['tnved','ТН ВЭД · 10 цифр'],['card_type','Тип карточки']].map(([key,label])=>`<label class="field">${label}<input data-default="${key}" value="${esc(state.importDefaults[key])}"></label>`).join('')}</details><h3>Исходные строки</h3><div class="content-scroll import-preview"><pre class="source-details">${esc(JSON.stringify(p.rows.slice(0,3).map(r=>r.raw),null,2))}</pre></div>`;$('#sheet-select').onchange=async e=>{try{state.sheet=e.target.value;state.mapping=null;await previewUpload();renderImport();}catch(err){$('#import-error').textContent=err.message;}};}
 if(step===3&&p){body.innerHTML=`<h2>Результаты проверки</h2><p>${p.rows.length} строк · ${p.rows.filter(r=>r.errors.length).length} с ошибками · ${p.rows.filter(r=>r.duplicates.length).length} с совпадениями.</p><div class="content-scroll import-preview"><table><tr><th>Строка</th><th>PN</th><th>Действие</th><th>Проверки</th></tr>${p.rows.slice(0,500).map(r=>`<tr><td>${r.row}</td><td>${esc(r.values.pn)}</td><td>${r.target?'Обновить снимок':'Создать локальную запись'}</td><td>${esc(r.errors.join('; ')||r.duplicates.map(c=>c.pn+': '+c.reason).join('; ')||'Локальная проверка пройдена')}</td></tr>`).join('')}</table></div><p class="muted">Показаны первые 500 результатов; следующий шаг содержит весь набор выбора. Проверяются только полные совпадения PN или GTIN. Регистр, внутренние пробелы и знаки в PN учитываются.</p>`;}
 if(step===4&&p){body.innerHTML=`<h2>Подтвердите состав</h2><p>Будут обработаны только явно выбранные строки без блокирующих ошибок. Повторное нажатие не повторяет успешный импорт.</p><div class="check-list">${p.rows.map(r=>`<label><input type="checkbox" data-import-row="${r.row}" ${r.errors.length?'disabled':'checked'}><span>Строка ${r.row}: <b>${esc(r.values.pn||'Без PN')}</b> ${esc(r.errors.join('; ')||r.duplicates.map(c=>c.pn+': '+c.reason).join('; ')||'')}</span></label>`).join('')}</div><label class="field"><span><input id="confirm-import" type="checkbox"> Состав просмотрен; сохранить выбранные строки локально</span></label>`;}
 bind('#import-back',()=>{state.importStep--;renderImport();});bind('#import-next',async()=>{try{if(step===1){if(!state.file)throw new Error('Выберите XLSX');state.header=Number($('#header-row').value);await previewUpload();state.importStep=2;renderImport();}else if(step===2){$$('[data-default]').forEach(i=>state.importDefaults[i.dataset.default]=i.value.trim());state.mapping={};$$('[data-map]').forEach(s=>{if(s.value!=='')state.mapping[s.dataset.map]=Number(s.value);});await previewUpload();state.importStep=3;renderImport();}else if(step===3){state.importStep=4;renderImport();}else{if(!$('#confirm-import').checked)throw new Error('Подтвердите состав');const rows=$$('[data-import-row]:checked').map(b=>Number(b.dataset.importRow));const result=await api('/api/import/'+p.id+'/commit',json('POST',{confirmed:true,rows,duplicate_reason:''}));await reloadCards();showResults('Результат импорта',result);if(result.some(r=>['created','updated'].includes(r.status))){state.importStep=1;state.importPlan=null;state.file=null;state.mapping=null;state.sheet='';}}}catch(error){$('#import-error').textContent=error.message;}});
}
async function previewUpload(){const data=new FormData();data.append('file',state.file);data.append('header',state.header);data.append('purpose',state.importPurpose);data.append('sheet',state.sheet);data.append('defaults',JSON.stringify(state.importDefaults));if(state.mapping!==null)data.append('mapping',JSON.stringify(state.mapping));state.importPlan=await api('/api/import/preview',{method:'POST',body:data});}
function showResults(title,result){modal(title,`<p>${result.filter(r=>['created','updated'].includes(r.status)).length} записей сохранено. Остальные строки не изменены.</p><div class="content-scroll"><table><tr><th>Строка / PN</th><th>Результат</th><th>Причина</th></tr>${result.map(r=>`<tr><td>${esc(r.row??r.pn)}</td><td>${esc(({created:'Создано локально',updated:'Обновлено локально',skipped:'Пропущено',conflict:'Конфликт',warning:'Нужно подтверждение',error:'Ошибка'})[r.status]||r.status)}</td><td>${esc(r.message||'')}</td></tr>`).join('')}</table></div>`);}
async function templates(){
 const data=await api('/api/templates');
 $('#main').innerHTML=heading('Шаблоны категорий','11 предустановок по опубликованному каталогу и ваши собственные версии',`<button id="export-catalog">Выгрузить весь каталог для анализа</button><button id="import-template" class="primary">Импорт JSON</button>`)+`<div class="notice info">Предустановки уже активны. ТН ВЭД и самый частый ОКПД2 заполняются в пустых полях; заполненные значения сохраняются. Выберите карточки в реестре → «Применить шаблоны» и подтвердите план. Собственные JSON требуют утверждения администратора.</div><div class="stack">${data.map(t=>`<section class="template-card"><span class="badge ${t.status==='active'?'green':'amber'}">${({active:'Активен локально',proposed:'Предложен',inactive:'Неактивен'})[t.status]} · v${t.version}</span><h2>${esc(t.name)}</h2>${t.builtin?'<p class="muted">Предустановлен · ОКПД2 можно изменить вручную</p><button data-preset-create="'+t.id+'">Создать карточку по шаблону</button>':''}<p>${esc(t.tnved)} · ${esc(t.category||'Без категории')} · ${esc(t.card_type||'Без типа')}</p><details><summary>Правила и происхождение</summary><pre class="source-details">${esc(JSON.stringify(JSON.parse(t.body),null,2))}</pre></details>${state.user.admin&&!(t.builtin&&t.status==='active')?`<div class="template-actions"><button data-approve="${t.id}">${t.status==='inactive'?'Откатить к этой версии':'Утвердить локальную версию'}</button></div>`:''}</section>`).join('')||'<div class="section empty"><b>Шаблонов пока нет</b>Сначала выгрузите каталог для анализа, затем импортируйте предложения JSON.</div>'}</div>`;
 bind('#export-catalog',()=>download('/api/export?format=zip','nk-catalog-analysis.zip'));
 $$('[data-preset-create]').forEach(b=>bind('[data-preset-create="'+b.dataset.presetCreate+'"]',()=>CreationUI.open(data.find(t=>t.id===b.dataset.presetCreate).tnved)));
 bind('#import-template',()=>{modal('Импорт предложения шаблона','<p>11 предустановок уже установлены. Для своего шаблона выберите один файл из папки templates, а не ZIP или reference-model.json. Названия attribute должны совпадать с характеристиками.</p><input id="template-file" type="file" accept=".json"><label class="field">JSON предложения<textarea id="template-json" rows="12"></textarea></label>','<button data-close>Отмена</button><button id="save-template" class="primary">Сохранить предложение</button>');$('#template-file').onchange=async e=>{const f=e.target.files[0];if(f&&f.size<=1024*1024)$('#template-json').value=await f.text();else showError(new Error('JSON до 1 МБ'));};bind('#save-template',async()=>{let body;try{body=JSON.parse($('#template-json').value);}catch(_){throw new Error('Некорректный JSON');}await api('/api/templates',json('POST',body));$('#dialog').close();await templates();toast('Предложение сохранено. Оно ещё не активно.');});});
 $$('[data-approve]').forEach(b=>b.onclick=async()=>{if(!window.confirm('Утвердить только для локальной подготовки? Схема НК не подтверждена.'))return;try{await api('/api/templates/'+b.dataset.approve+'/approve',json('POST',{}));await templates();}catch(e){showError(e);}});
}
async function templatePlan(){await TemplateFlow.preview([...state.selected]);}

async function batches(){await NkUI.refreshStatus();const data=await api('/api/imports');$('#main').innerHTML=heading('Партии и операции НК','Фактические результаты и подтверждения внешних операций')+NkUI.jobsHtml(state.nkStatus)+`<div class="stack">${data.map(p=>`<section class="section"><h2>${esc(p.filename)}</h2><p class="muted">${esc(p.created)} · ${esc(p.actor)}</p><p>${JSON.parse(p.results).filter(r=>['created','updated'].includes(r.status)).length} строк сохранено</p><button data-result="${p.id}">Показать результаты</button></section>`).join('')||'<div class="section empty"><b>Импортов пока нет</b>Здесь появятся партии после сохранения настоящих файлов.</div>'}</div>`;NkUI.bindJobs();$$('[data-result]').forEach(b=>b.onclick=()=>showResults('Результаты партии',JSON.parse(data.find(p=>p.id===b.dataset.result).results)));}
async function admin(){await NkUI.refreshStatus();if(!state.user.admin){state.page='cards';await render();return;}const [health,users,events]=await Promise.all([api('/health'),api('/api/admin/users'),api('/api/admin/audit')]);$('#main').innerHTML=heading('Администрирование','Настоящая серверная сессия администратора')+`<section id="bulk-creation" class="section"></section>`+NkUI.adminHtml(state.nkStatus)+`<section class="section"><h2>НК и версия</h2><div class="notice">Проверьте ключ, выполните синхронизацию и подключите Windows-подписант. Внешние действия требуют отдельного подтверждения.</div><p>Версия ${esc(health.version)} · схема БД ${health.schema}<br>Commit <span class="mono">${esc(health.commit)}</span><br>Assets ${esc(health.assets)} · запуск ${esc(health.started)}</p></section><section class="section"><h2>Сотрудники</h2><div class="user-list">${users.map(u=>`<div class="row user-row"><b>${esc(u.username)}</b> <span class="muted">${u.is_admin?'Администратор':'Сотрудник'} · ${u.password_set?'Пароль установлен':'Ожидает первого входа'}</span> <button data-reset-password="${esc(u.username)}">Сбросить пароль</button></div>`).join('')}</div><div class="actions"><input id="new-user" placeholder="Новый username" maxlength="64"><button id="add-user">Создать сотрудника</button></div></section><section class="section"><h2>Журнал последних действий</h2><div class="content-scroll"><table><tr><th>Время</th><th>Кто</th><th>Действие</th></tr>${events.map(e=>`<tr><td>${esc(e.created)}</td><td>${esc(e.actor)}</td><td>${esc(eventName(e.action))}</td></tr>`).join('')}</table></div></section>`;NkUI.bindAdmin();await BulkCreation.mount();setupAdminSections();bind('#add-user',async()=>{const created=await api('/api/admin/users',json('POST',{username:$('#new-user').value}));await admin();showTemporaryCode(created);});
 $$('[data-reset-password]').forEach(button=>button.onclick=async()=>{
  const username=button.dataset.resetPassword;
  if(!confirm('Сбросить пароль пользователя '+username+'? Все его сессии завершатся.'))return;
  try{
   const result=await api('/api/admin/users/'+encodeURIComponent(username)+'/reset-password',json('POST',{}));
   if(username!==state.user.username)await admin();
   showTemporaryCode(result);
  }catch(error){showError(error);}
 });
}
function setupAdminSections(){
 const root=$('#main');if(!root)return;
 const sections=$$(':scope > section.section',root);
 const tabs=[['connection','Подключение'],['bulk','Массовые операции'],['people','Сотрудники'],['audit','Журнал']];
 const nav=document.createElement('nav');nav.className='admin-tabs';nav.setAttribute('aria-label','Разделы администрирования');
 nav.innerHTML=tabs.map(([id,label])=>`<button type="button" data-admin-tab="${id}">${label}</button>`).join('');
 root.querySelector('.page-heading')?.insertAdjacentElement('afterend',nav);
 for(const section of sections){
   const title=section.querySelector('h2')?.textContent||'';
   section.dataset.adminGroup=section.id==='bulk-creation'||section.id==='gtin-recovery'?'bulk':
     /Сотрудники/.test(title)?'people':/Журнал/.test(title)?'audit':'connection';
 }
 const switchTo=id=>{
   state.adminTab=id;
   for(const section of sections)section.hidden=section.dataset.adminGroup!==id;
   $$('[data-admin-tab]',nav).forEach(button=>{
     const active=button.dataset.adminTab===id;
     button.classList.toggle('active',active);
     button.setAttribute('aria-pressed',String(active));
   });
 };
 $$('[data-admin-tab]',nav).forEach(button=>button.onclick=()=>switchTo(button.dataset.adminTab));
 switchTo(state.adminTab||'connection');
}
function showTemporaryCode(result){
 modal('Одноразовый код: '+esc(result.username),`<p>Передайте этот код лично пользователю. При первом входе он укажет username и этот код, затем дважды введёт собственный пароль.</p><label class="field">Код (отображается только сейчас)<input id="temporary-code" readonly value="${esc(result.temporary_code)}"></label><p class="muted">Код хранится на сервере только в виде хеша. После установки пароля повторное использование невозможно.</p>`,'<button id="copy-temp-code" class="primary">Копировать код</button><button data-close>Закрыть</button>');
 $('#temporary-code').onclick=()=>$('#temporary-code').select();
 bind('#copy-temp-code',async()=>{await navigator.clipboard.writeText(result.temporary_code);toast('Код скопирован');});
}
function unavailable(){return NkUI.actions([...state.selected]);}
function help(){modal('Как работать с черновиками','<ol class="workflow-help"><li><b>Заполнить.</b> Откройте «Черновики» и нажмите «Заполнить автоматически». В оставшихся карточках укажите PN, ТН ВЭД, товарный знак и выберите вид товара. Сохраните.</li><li><b>Отправить.</b> Выберите готовые карточки и нажмите «Отправить на модерацию». Программа сохранит данные в НК и после подтверждения сама запустит модерацию.</li><li><b>Модерация.</b> Дождитесь результата НК. Ошибка, если она есть, появится у карточки.</li><li><b>Подписать.</b> Запустите Windows-подписант и нажмите «Подписать и опубликовать». Опубликованные товары появятся в разделе «Карточки».</li></ol><p>Черновики с уже опубликованным PN скрыты по умолчанию; их можно показать переключателем. Статусы обновляются автоматически. Подробности операций доступны в «Партиях».</p>');}
$('#help-button').onclick=help;
let activationCode='';
function cancelActivation(){
 activationCode='';
 $('#password-setup').hidden=true;
 $('#login-password').parentElement.hidden=false;
 $('#login-password').value='';
 $('#login-password').readOnly=false;
 $('#new-password').value='';
 $('#repeat-password').value='';
 $('#new-password').required=false;
 $('#repeat-password').required=false;
 $('#login-submit').textContent='Войти';
 $('#login-error').textContent='';
}
$('#cancel-password-setup').onclick=cancelActivation;
$('#login-form').onsubmit=async event=>{
 event.preventDefault();$('#login-error').textContent='';
 try{
  if(activationCode){
   const response=await api('/api/password/activate',json('POST',{
    username:$('#login-username').value.trim(),code:activationCode,
    new_password:$('#new-password').value,repeat_password:$('#repeat-password').value
   }));
   state.user=response;cancelActivation();await start();
  }else{
   const result=await api('/api/login',json('POST',{username:$('#login-username').value.trim(),password:$('#login-password').value}));
   if(result.password_setup_required){
    activationCode=$('#login-password').value;
    $('#login-password').parentElement.hidden=true;
    $('#password-setup').hidden=false;
    $('#new-password').required=true;$('#repeat-password').required=true;
    $('#login-submit').textContent='Установить пароль и войти';
    $('#new-password').focus();
    return;
   }
   state.user=result;$('#login-password').value='';await start();
  }
 }catch(error){$('#login-error').textContent=error.message;}
};
$('#change-password').onclick=()=>{
 modal('Сменить пароль',`<label class="field">Текущий пароль<input id="old-password" type="password" autocomplete="current-password"></label><label class="field">Новый пароль (минимум 8 символов)<input id="own-new-password" type="password" autocomplete="new-password" minlength="8"></label><label class="field">Повторите новый пароль<input id="own-repeat-password" type="password" autocomplete="new-password" minlength="8"></label>`,'<button id="save-password" class="primary">Сохранить</button><button data-close>Отмена</button>');
 bind('#save-password',async()=>{
  await api('/api/password/change',json('POST',{current_password:$('#old-password').value,new_password:$('#own-new-password').value,repeat_password:$('#own-repeat-password').value}));
  $('#dialog').close();toast('Пароль изменён. Другие сеансы пользователя завершены.');
 });
};
$('#logout').onclick=async()=>{if(!leave())return;try{await api('/api/logout',json('POST',{}));location.reload();}catch(e){toast(e.message);}};
async function start(){await reloadCards();await NkUI.refreshStatus();$('#login-screen').hidden=true;$('#shell').hidden=false;$('#user-label').textContent=state.user.username+(state.user.admin?' · администратор':' · сотрудник');const page=location.hash.slice(1);state.page=labels[page]&&(page!=='admin'||state.user.admin)?page:'cards';await render();}
window.addEventListener('hashchange',async()=>{if(!state.user)return;if(!leave()){history.replaceState(null,'','#'+state.page);return;}state.dirty=false;state.drawer=null;$('#drawer').hidden=true;state.page=labels[location.hash.slice(1)]?location.hash.slice(1):'cards';state.pageNo=1;await render();});
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&state.drawer&&!$('#dialog').open&&leave()){state.drawer=null;state.dirty=false;$('#drawer').hidden=true;}});
api('/api/me').then(user=>{state.user=user;return start();}).catch(()=>{});

window.addEventListener('unhandledrejection',event=>{event.preventDefault();showError(event.reason instanceof Error?event.reason:new Error('Операция не завершена. Обновите сессию.'));});

setInterval(()=>{if(state.user&&document.visibilityState==='visible')NkUI.refreshStatus();},30000);

setInterval(async()=>{if(state.user&&state.page==='drafts'&&!state.dirty&&!state.drawer&&!$('#dialog').open&&document.visibilityState==='visible'&&!['INPUT','SELECT'].includes(document.activeElement?.tagName)){try{if(await reloadCards())DraftUI.render();}catch(_){/* Explicit refresh remains available. */}}},30000);
