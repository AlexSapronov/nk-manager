/* Review every draft, apply a revision-bound local plan, then send explicitly. */
window.NormalizationFlow = (() => {
 const names={pn:'PN',name:'Полное наименование',tnved:'ТН ВЭД',card_type:'Тип карточки'};
 const field=k=>names[k]||k.replace(/\s*\[#\d+\]/g,'').replace('::unit',' · тип/единица');
 const title=e=>e.pn||e.name||e.gtin||'Без наименования';
 const val=v=>v===null||v===undefined||v===''?'пусто':String(v);
 async function open(id){$('#dialog').close();await reloadCards();openCard(id);}
 async function preview(ids){
  const plan=await api('/api/normalization/preview',json('POST',ids?{ids}:{}));
  const drafts=plan.entries.filter(e=>e.state!=='skipped'),ready=drafts.filter(e=>e.changes.length),review=drafts.filter(e=>e.issues.length),skipped=plan.entries.filter(e=>e.state==='skipped');
  modal('Привести черновики к эталону',`
   <div class="flow-summary"><div><b>${drafts.length}</b><span>черновиков</span></div><div><b>${ready.length}</b><span>с изменениями</span></div><div><b>${review.length}</b><span>нужно дополнить</span></div><div><b>${skipped.length}</b><span>пропущено</span></div></div>
   <p>Проверьте значения «было → станет». Заполняются общие поля, согласуются PN, вид и полное наименование. GTIN, заполненные ОКПД2, бренд, диапазоны и документы сохраняются. Спорные значения остаются для ручной проверки.</p>
   <p class="muted">На этом шаге изменения сохраняются в менеджере. Отправка в НК — отдельное действие.</p>
   <label class="flow-select-all"><input id="normalize-all" type="checkbox" checked> Выбрать все черновики с изменениями</label>
   <div id="normalize-reason-area" hidden><label class="field">Причина продолжения при точных совпадениях PN/GTIN<textarea id="normalize-reason"></textarea></label></div>
   <div class="content-scroll"><table class="template-flow-table"><colgroup><col class="flow-check"><col class="flow-product"><col class="flow-fields"><col></colgroup><thead><tr><th></th><th>Карточка</th><th>Было → станет</th><th>Нужно проверить</th></tr></thead><tbody>${drafts.map(e=>`<tr><td>${e.changes.length?`<input data-normalize-select="${e.id}" type="checkbox" checked aria-label="Привести ${esc(title(e))}">`:''}</td><td class="flow-identity"><button class="link-button" data-normalize-open="${e.id}">${esc(title(e))}</button><small>GTIN ${esc(e.gtin||'не указан')}</small></td><td>${e.changes.length?`<details><summary>${e.changes.length} изменений</summary>${e.changes.map(c=>`<div class="flow-change"><span>${esc(field(c.field))}</span><small>${esc(val(c.before))} →</small><b>${esc(val(c.after))}</b></div>`).join('')}</details>`:'Соответствует эталону'}</td><td>${e.issues.map(i=>`<p>${esc(i)}</p>`).join('')||'<span class="flow-ok">Готово</span>'}${e.notes.length?`<p class="muted"><small>${esc(e.notes.join('; '))}</small></p>`:''}${e.duplicates.length?`<details><summary>Точные совпадения (${e.duplicates.length})</summary>${e.duplicates.map(d=>`<p>${esc(d.pn||d.name)} · ${esc(d.gtin)}<br>${esc(d.reason)}</p>`).join('')}</details>`:''}</td></tr>`).join('')||'<tr><td colspan="4">Доступных черновиков нет</td></tr>'}</tbody></table></div>
   ${skipped.length?`<details><summary>Пропущенные карточки (${skipped.length})</summary><p>Опубликованные, на модерации, ожидающие подписи, занятые и остановленные карточки исключены.</p>${skipped.slice(0,50).map(e=>`<p>${esc(title(e))}: ${esc(e.message)}</p>`).join('')}${skipped.length>50?'<p>Показаны первые 50.</p>':''}</details>`:''}`,
   '<button data-close>Отмена</button><button id="normalize-apply" class="primary">Применить выбранное</button>');
  $('#dialog').classList.add('template-dialog');
  const selection=()=>new Set($$('[data-normalize-select]:checked').map(b=>b.dataset.normalizeSelect));
  const update=()=>{const selected=selection(),duplicates=ready.some(e=>selected.has(e.id)&&e.duplicates.length);$('#normalize-reason-area').hidden=!duplicates;$('#normalize-apply').disabled=!selected.size||(duplicates&&!$('#normalize-reason').value.trim());$('#normalize-apply').textContent=`Применить локально (${selected.size})`;const all=$('#normalize-all');all.checked=selected.size===ready.length&&!!ready.length;all.indeterminate=!!selected.size&&selected.size!==ready.length;};
  $$('[data-normalize-select]').forEach(b=>b.onchange=update);$('#normalize-all').onchange=e=>{$$('[data-normalize-select]').forEach(b=>b.checked=e.target.checked);update();};$('#normalize-reason').oninput=update;
  $$('[data-normalize-open]').forEach(b=>b.onclick=()=>open(b.dataset.normalizeOpen));
  bind('#normalize-apply',async()=>{
   const result=await api('/api/normalization/'+plan.id+'/apply',json('POST',{confirmed:true,ids:[...selection()],duplicate_reason:$('#normalize-reason').value}));
   await reloadCards();const saved=result.filter(r=>r.status==='updated'),failed=result.filter(r=>!['updated','skipped'].includes(r.status));
   const prepared=saved.filter(r=>!r.issues.length);state.selected=new Set(prepared.map(r=>r.id));registry();
   const pending=drafts.filter(e=>e.issues.length),first=pending[0];
   modal('Результат приведения черновиков',`<div class="flow-result"><h3>Приведено: ${saved.length} из ${result.length}</h3><p>Изменения сохранены в менеджере. В НК они ещё не отправлены.</p></div>${pending.length?`<p>Нужно дополнить вручную: ${pending.length}. Проверьте PN, вид товара и другие отмеченные поля перед отправкой.</p>`:''}${failed.map(r=>`<p>${esc(title(r))}: ${esc(r.message)}</p>`).join('')}<p>Для отправки подготовленных карточек используйте «Отправить / опубликовать» в реестре. Карточки с нерешёнными вопросами сначала проверьте.</p>`, '<button data-close>К реестру</button><button id="normalize-again">Проверить ещё раз</button>'+(prepared.length?'<button id="normalize-send">Отправить готовые в НК</button>':'')+(first?'<button id="normalize-open-review" class="primary">Открыть неполную</button>':''));
   $('#dialog').classList.add('template-dialog');
   bind('#normalize-send',()=>NkUI.actions(prepared.map(r=>r.id),'save'));
   bind('#normalize-again',()=>preview(ids));bind('#normalize-open-review',()=>open(first.id));
  });update();
 }
 return {preview};
})();
