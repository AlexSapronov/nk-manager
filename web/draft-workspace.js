/* Four clear stages. Local preparation is quiet; external actions have one confirmation. */
window.DraftUI = (() => {
 const stages=[['prepare','1. Заполнить'],['ready','2. Отправить'],['moderation','3. Модерация'],['sign','4. Подписать'],['processing','Выполняется'],['attention','Нужно действие']];
 let indexSource=null,index=new Map();
 const meta=id=>{
  if(indexSource!==state.draftWorkspace){
   indexSource=state.draftWorkspace;
   index=new Map((indexSource?.entries||[]).map(e=>[e.id,e]));
  }
  return index.get(id);
 };
 const hidden=c=>!!meta(c.id)?.published_ids.length;
 function scope(){const q=state.query.toLocaleLowerCase();return state.cards.filter(c=>meta(c.id)&&(!q||[c.pn,c.name,c.gtin].some(v=>v.toLocaleLowerCase().includes(q)))&&(!state.category||c.category===state.category)&&(!state.tnved||c.tnved===state.tnved));}
 function visible(){return scope().filter(c=>state.showPublishedDrafts||!hidden(c));}
 function filtered(){return visible().filter(c=>meta(c.id).stage===(state.draftStage||'prepare'));}
 function actionLabel(m){return {normalize:'Заполнить автоматически',edit:'Заполнить',gtin:'Получить GTIN',submit:'На модерацию',publish:'Подписать',job:'Посмотреть результат',owner:'Открыть',wait:'Открыть'}[m.action];}
 async function go(stage){
  if($('#dialog').open)$('#dialog').close();state.draftStage=stage;state.drawer=null;state.dirty=false;$('#drawer').hidden=true;
  if(state.page!=='drafts'){state.page='drafts';history.replaceState(null,'','#drafts');await window.render();}else render();
 }
 async function normalize(ids){
  const p=await api('/api/normalization/preview',json('POST',{ids}));const entries=p.entries.filter(e=>e.changes.length&&e.state!=='skipped');
  if(!entries.length){toast('Автоматических изменений нет. Заполните отмеченные поля.');return;}
  const r=await api('/api/normalization/'+p.id+'/apply',json('POST',{confirmed:true,ids:entries.map(e=>e.id)}));
  await reloadCards();state.selected.clear();registry();
  const saved=r.filter(e=>e.status==='updated'),failed=r.filter(e=>!['updated','skipped'].includes(e.status));
  if(failed.length){modal('Не все карточки заполнены',failed.map(e=>`<p>${esc(e.pn||e.name)}: ${esc(e.message)}</p>`).join(''));return;}
  const need=visible().filter(c=>meta(c.id).stage==='prepare'),ready=visible().filter(c=>meta(c.id).stage==='ready');
  modal('Заполнение завершено',`<p><b>Заполнено карточек: ${saved.length}.</b></p><p>Готовы к отправке: ${ready.length}. Нужно добавить данные вручную: ${need.length}.</p>`, '<button id="draft-ready">К готовым карточкам</button>'+(need.length?'<button id="draft-review" class="primary">Заполнить следующую</button>':''));
  bind('#draft-ready',()=>go('ready'));bind('#draft-review',()=>{$('#dialog').close();openCard(need[0].id);});
 }
 async function submit(ids){
  const duplicate=ids.some(id=>meta(id)?.external_duplicate),revisions=Object.fromEntries(ids.map(id=>[id,state.cards.find(c=>c.id===id).revision]));
  modal('Отправить на модерацию',`<p>Карточек: <b>${ids.length}</b>.</p><p>Программа сохранит изменения в НК, дождётся подтверждения и отправит карточки на модерацию.</p>${duplicate?'<label class="field"><span><input type="checkbox" id="draft-duplicate-confirm"> Это отдельные товары с совпадающим PN/GTIN — создать отдельные карточки</span></label>':''}`, '<button data-close>Отмена</button><button id="draft-confirm-submit" class="primary">Отправить на модерацию</button>');
  if(duplicate){$('#draft-confirm-submit').disabled=true;$('#draft-duplicate-confirm').onchange=e=>$('#draft-confirm-submit').disabled=!e.target.checked;}
  bind('#draft-confirm-submit',async()=>{
   const r=await api('/api/drafts/submit',json('POST',{ids,confirmed:true,revisions,allow_duplicates:$('#draft-duplicate-confirm')?.checked||false}));await reloadCards();state.selected.clear();
   const failed=r.filter(e=>e.status!=='queued');
   if(failed.length)modal('Не все карточки отправлены',r.map(e=>`<p>${esc(state.cards.find(c=>c.id===e.id)?.pn||'Карточка')}: ${esc(e.message||'Поставлена в очередь')}</p>`).join(''));
   else{toast('Карточки отправляются. После сохранения модерация начнётся автоматически.');await go('processing');return;}
   registry();
  });
 }
 async function act(id){const m=meta(id);if(m.action==='normalize')return normalize([id]);if(m.action==='submit')return submit([id]);if(m.action==='gtin')return NkUI.actions([id],'gtin');if(m.action==='publish')return NkUI.actions([id],'publish');if(m.action==='job')return NkUI.openJob(m.job_id);openCard(id);}
 function render(){
  const source=state.cards.filter(c=>meta(c.id)),all=scope(),data=visible();state.draftStage=state.draftStage||stages.find(([k])=>data.some(c=>meta(c.id).stage===k))?.[0]||'prepare';
  const hiddenCount=all.filter(hidden).length;
  const stage=state.draftStage,rows=data.filter(c=>meta(c.id).stage===stage),selected=rows.filter(c=>state.selected.has(c.id));
  const autos=rows.filter(c=>meta(c.id).automatic_changes>0&&meta(c.id).stage==='prepare'&&!hidden(c));
  const target=selected.length?selected:rows;
  const primary=stage==='prepare'?(autos.length?`Заполнить автоматически (${selected.length?selected.filter(c=>meta(c.id).automatic_changes).length:autos.length})`:'Заполнить следующую'):stage==='ready'?`Отправить на модерацию (${target.length})`:stage==='sign'?`Подписать и опубликовать (${target.length})`:'';
  $('#main').innerHTML=heading('Работа с черновиками','Заполните карточки, отправьте на модерацию, затем подпишите готовые.', '<button id="draft-refresh">Обновить</button><button id="create" class="primary">Создать карточку</button>')+`
   <div class="draft-stages">${stages.filter(([k])=>['prepare','ready','moderation','sign'].includes(k)||data.some(c=>meta(c.id).stage===k)).map(([k,l])=>`<button data-draft-stage="${k}" class="${stage===k?'active':''}"><span>${l}</span><b>${data.filter(c=>meta(c.id).stage===k).length}</b></button>`).join('')}</div>
   <section class="registry"><div class="filters"><input id="search" placeholder="PN, GTIN или наименование" aria-label="Поиск карточек" value="${esc(state.query)}"><div class="category-filter-host" id="category-filter-host"></div><select id="tnved-filter" aria-label="ТН ВЭД"><option value="">Все ТН ВЭД</option>${[...new Set(source.map(c=>c.tnved))].filter(Boolean).sort().map(t=>`<option value="${t}" ${state.tnved===t?'selected':''}>${t}</option>`).join('')}</select></div>
   ${hiddenCount?`<label class="draft-hidden-toggle"><input id="show-published-drafts" type="checkbox" ${state.showPublishedDrafts?'checked':''}> Показать черновики с уже опубликованным PN (${hiddenCount})</label>`:''}
   <div class="draft-toolbar"><p>${{prepare:'Здесь карточки, которым нужно заполнение. Программа дополнит общие поля; недостающие PN, бренд и вид товара укажите вручную.',ready:'Карточки заполнены. Одна команда сохранит их в НК и отправит на модерацию.',moderation:'Карточки проверяет НК. Когда модерация завершится, они появятся на шаге «Подписать».',sign:'Модерация пройдена. Подпишите карточки; после публикации они появятся в реестре «Карточки».',processing:'Программа выполняет отправку. Готовые карточки перейдут на следующий этап, опубликованные — в реестр «Карточки».',attention:'Только карточки, по которым требуется конкретное действие.'}[stage]}</p>${primary?`<button id="draft-primary" class="primary" ${target.length?'':'disabled'}>${primary}</button>`:''}</div>
   <div class="table-wrap" id="rows">${rows.length?`<table class="draft-table"><colgroup><col class="draft-check-col"><col class="draft-product-col"><col><col class="draft-action-col"></colgroup><thead><tr><th class="check-col"><input id="select-page" type="checkbox" aria-label="Выбрать карточки этапа" ${selected.length===rows.length?'checked':''}></th><th>Товар</th><th>Что дальше</th><th></th></tr></thead><tbody>${rows.map(c=>{const m=meta(c.id);return `<tr data-card="${c.id}"><td><input type="checkbox" data-draft-select="${c.id}" aria-label="Выбрать ${esc(c.pn||c.name)}" ${state.selected.has(c.id)?'checked':''}></td><td><button class="link-button" data-draft-open="${c.id}"><b>${esc(c.pn||c.name||c.gtin)}</b></button>${c.pn?`<small>${esc(c.name)}</small>`:''}<small>GTIN ${esc(c.gtin||'нужно получить')} · ТН ВЭД ${esc(c.tnved||'не указан')}</small></td><td>${hidden(c)?'Этот PN уже опубликован. Откройте опубликованную карточку.':esc(m.missing.length?m.missing.join('; '):m.label)}${m.error?`<p class="inline-error">${esc(m.error)}</p>`:''}</td><td>${hidden(c)?`<button data-published-open="${m.published_ids[0]}">Открыть опубликованную</button>`:`<button data-draft-action="${c.id}">${actionLabel(m)}</button>`}</td></tr>`;}).join('')}</tbody></table>`:'<div class="empty"><b>На этом этапе карточек нет</b>Выберите следующий шаг.</div>'}</div></section>`;
  CategoryFilter.mount($('#category-filter-host'),[...new Set(source.map(c=>c.category))].filter(Boolean).sort(),state.category,v=>{state.category=v;render();});
  $('#search').oninput=e=>{const cursor=e.target.selectionStart;state.query=e.target.value;render();$('#search').focus();$('#search').setSelectionRange(cursor,cursor);};$('#tnved-filter').onchange=e=>{state.tnved=e.target.value;render();};
  if($('#show-published-drafts'))$('#show-published-drafts').onchange=e=>{state.showPublishedDrafts=e.target.checked;state.selected.clear();render();};
  $$('[data-draft-stage]').forEach(b=>b.onclick=()=>{state.draftStage=b.dataset.draftStage;state.selected.clear();render();});$$('[data-draft-open]').forEach(b=>b.onclick=()=>openCard(b.dataset.draftOpen));$$('[data-draft-action]').forEach(b=>bind('[data-draft-action="'+b.dataset.draftAction+'"]',()=>act(b.dataset.draftAction)));$$('[data-published-open]').forEach(b=>b.onclick=()=>openCard(b.dataset.publishedOpen));
  $$('[data-draft-select]').forEach(b=>b.onchange=()=>{b.checked?state.selected.add(b.dataset.draftSelect):state.selected.delete(b.dataset.draftSelect);render();});if($('#select-page'))$('#select-page').onchange=e=>{rows.forEach(c=>e.target.checked?state.selected.add(c.id):state.selected.delete(c.id));render();};
  bind('#create',()=>CreationUI.open());bind('#draft-refresh',async()=>{await reloadCards();render();});
  bind('#draft-primary',()=>{if(stage==='prepare'){const eligible=selected.length?selected.filter(c=>meta(c.id).automatic_changes&&!hidden(c)):autos;return eligible.length?normalize(eligible.map(c=>c.id)):act(target[0].id);}if(stage==='ready')return submit(target.filter(c=>!hidden(c)).map(c=>c.id));if(stage==='sign')return NkUI.actions(target.map(c=>c.id),'publish');});
 }
 return {render,meta,hidden,filtered,normalize,submit,act};
})();
