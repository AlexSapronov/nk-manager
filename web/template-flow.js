/* Guided local template preparation. External NK actions stay separate. */
window.TemplateFlow = (() => {
 const title = c => c.pn || c.name || c.gtin || 'Карточка без названия';
 const field = key => key.replace(/\s*\[#\d+\]/g, '').replace(/\s*·\s*\d+$/, '');
 function identity(c){return `<strong>${esc(title(c))}</strong>${c.pn&&c.name?`<span>${esc(c.name)}</span>`:''}<small>GTIN: ${esc(c.gtin||'не указан')}${!c.pn?' · PN не указан':''}</small>`;}
 function duplicates(entry){return entry.duplicates.length?`<details class="flow-duplicates"><summary>Проверить совпадения (${entry.duplicates.length})</summary>${entry.duplicates.map(d=>`<div>${identity(d)}<small>${esc(d.reason)}</small></div>`).join('')}</details>`:'<span class="flow-ok">Совпадений нет</span>';}
 async function open(id){$('#dialog').close();await reloadCards();openCard(id);if(!state.drawer.pn)$('#field-pn')?.focus();}
 async function preview(ids){
  if(!ids.length)throw new Error('Выберите карточки в реестре');
  const plan=await api('/api/template-plan',json('POST',{ids}));
  const ready=plan.entries.filter(e=>Object.keys(e.changes).length),unchanged=plan.entries.filter(e=>!Object.keys(e.changes).length);
  const warnings=ready.filter(e=>e.duplicates.length),missing=plan.entries.filter(e=>e.needs_input?.length);
  const missingText=[['PN',missing.filter(e=>e.needs_input.includes('PN')).length],['наименование',missing.filter(e=>e.needs_input.includes('Наименование')).length]].filter(([,n])=>n).map(([label,n])=>label+' ('+n+')').join(', ');
  const row=e=>`<tr data-plan-row="${e.id}"><td><input type="checkbox" data-plan-select="${e.id}" checked aria-label="Заполнить ${esc(title(e))}"></td><td class="flow-identity">${identity(e)}</td><td>${Object.entries(e.changes).map(([k,v])=>`<div class="flow-change"><span>${esc(field(k))}</span><b>${esc(v)}</b></div>`).join('')}</td><td>${duplicates(e)}</td></tr>`;
  modal('Заполнить карточки по шаблону',`
   <div class="flow-summary" role="status"><div><b>${plan.entries.length}</b><span>выбрано</span></div><div><b>${ready.length}</b><span>можно заполнить</span></div><div><b>${unchanged.length}</b><span>без изменений / недоступно</span></div><div><b>${warnings.length}</b><span>проверить совпадения</span></div></div>
   <p>Шаблон заполнит только пустые поля. ОКПД2 можно изменить в карточке. Сейчас данные сохраняются в менеджере; отправка в НК — отдельный шаг.</p>
   ${missing.length?`<p class="flow-next-note">Ещё нужно указать: ${esc(missingText)}. Это не мешает заполнить общие поля; индивидуальные данные добавите следующим шагом.</p>`:''}
   <div id="plan-reason-area" ${warnings.length?'':'hidden'}><label class="field">Почему продолжаем с совпадающими товарами?<textarea id="plan-duplicate-reason" placeholder="Например: проверены, это разные исполнения"></textarea></label><p id="plan-reason-hint" class="inline-error">Проверьте совпадения и укажите причину, либо снимите выбор с этих карточек.</p></div>
   ${ready.length?`<label class="flow-select-all"><input id="plan-select-all" type="checkbox" checked> Выбрать все карточки с изменениями</label><div class="content-scroll"><table class="template-flow-table"><colgroup><col class="flow-check"><col class="flow-product"><col class="flow-fields"><col></colgroup><thead><tr><th></th><th>Товар</th><th>Что заполнится</th><th>Совпадения</th></tr></thead><tbody>${ready.map(row).join('')}</tbody></table></div>`:'<div class="notice info">Шаблону нечего добавить. Общие поля уже заполнены либо для этих карточек нет подходящего шаблона. Откройте карточку, чтобы проверить индивидуальные данные.</div>'}
   ${unchanged.length?`<details class="flow-unchanged"><summary>Без изменений или недоступно (${unchanged.length})</summary><ul>${unchanged.map(e=>`<li><b>${esc(title(e))}</b> — ${esc(e.message)}</li>`).join('')}</ul></details>`:''}`,
   `<button data-close>Отмена</button>${plan.entries.length&&(missing.length||!ready.length)?'<button id="plan-open-first">Открыть карточку</button>':''}<button id="apply-plan" class="primary" ${ready.length?'':'disabled'}>Заполнить выбранные (${ready.length})</button>`);
  $('#dialog').classList.add('template-dialog');
  const selection=()=>new Set($$('[data-plan-select]:checked').map(c=>c.dataset.planSelect));
  function update(){const selected=selection(),review=ready.some(e=>selected.has(e.id)&&e.duplicates.length);$('#plan-reason-area').hidden=true;$('#apply-plan').disabled=!selected.size;$('#apply-plan').textContent=`Заполнить выбранные (${selected.size})`;const all=$('#plan-select-all');if(all){all.checked=selected.size===ready.length;all.indeterminate=selected.size>0&&selected.size<ready.length;}}
  $$('[data-plan-select]').forEach(c=>c.onchange=update);
  if($('#plan-select-all'))$('#plan-select-all').onchange=e=>{$$('[data-plan-select]').forEach(c=>c.checked=e.target.checked);update();};
  $('#plan-duplicate-reason').oninput=update;
  bind('#plan-open-first',()=>open((missing[0]||plan.entries[0]).id));
  bind('#apply-plan',async()=>{
   const selected=selection();if(!selected.size)throw new Error('Выберите карточки для заполнения');
   const reason=$('#plan-duplicate-reason').value.trim();
   const result=await api('/api/template-plan/'+plan.id+'/apply',json('POST',{confirmed:true,ids:[...selected],duplicate_reason:reason}));
   await reloadCards();const saved=result.filter(r=>r.status==='updated');if(saved.length)state.selected=new Set(saved.map(r=>r.id));
   if(['cards','drafts'].includes(state.page))registry();
   showResult(result);
  });
  update();
 }
 function showResult(result){
  const saved=result.filter(r=>r.status==='updated'),failed=result.filter(r=>!['updated','skipped'].includes(r.status));
  const incomplete=saved.map(r=>state.cards.find(c=>c.id===r.id)).filter(c=>c&&(!c.pn||!c.name));
  const next=incomplete[0]||saved[0];
  const missingText=[['PN',incomplete.filter(c=>!c.pn).length],['наименование',incomplete.filter(c=>!c.name).length]].filter(([,n])=>n).map(([label,n])=>label+' ('+n+')').join(', ');
  modal('Результат заполнения',`<div class="flow-result" role="status"><h3>Заполнено: ${saved.length} из ${result.length}</h3><p>${saved.length?'Общие поля сохранены в менеджере. В НК изменения ещё не отправлены.':'Изменения не сохранены. Причины указаны ниже.'}</p></div>
   ${incomplete.length?`<div class="notice info">Следующий шаг — заполнить: ${esc(missingText)}. Нажмите «Заполнить недостающее», проверьте карточку и сохраните её.</div>`:saved.length?'<p>Следующий шаг: откройте карточку, проверьте характеристики, затем на вкладке «НК» нажмите «Создать / сохранить в НК».</p>':''}
   ${failed.length?`<div class="error-box"><b>Не сохранено: ${failed.length}</b>${failed.map(r=>`<p>${esc(title(r))}: ${esc(r.message)}</p>`).join('')}<p>Можно вернуться к проверке этих карточек.</p></div>`:''}
   ${saved.length?`<details class="flow-saved"><summary>Заполненные карточки (${saved.length})</summary><ul>${saved.map(r=>`<li>${esc(title(r))} · GTIN ${esc(r.gtin||'не указан')}</li>`).join('')}</ul></details>`:''}`,
   `<button data-close>К реестру</button>${failed.length?'<button id="template-retry">Вернуться к проверке</button>':''}${next?`<button id="template-open-next" class="primary">${incomplete.length?'Заполнить недостающее':'Открыть карточку'}</button>`:''}`);
  $('#dialog').classList.add('template-dialog');
  bind('#template-retry',()=>preview(failed.map(r=>r.id)));
  bind('#template-open-next',()=>open(next.id));
 }
 return {preview};
})();
