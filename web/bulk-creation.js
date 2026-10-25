'use strict';
window.BulkCreation = (() => {
 let refreshTimer;
 const base='/api/admin/creation-batches';
 const batchState={preview:'Ожидает запуска',running:'Создаём карточки',attention:'Нужна сверка',completed:'Обработка завершена'};
 async function mount(){
  const host=$('#bulk-creation');if(!host||!state.user.admin)return;
  const batches=await api(base);
  host.innerHTML=`<h2>Создать карточки из Excel и получить GTIN</h2><p>Три колонки: <b>PN</b>, <b>ТН ВЭД</b> (10 цифр), <b>Товарный знак</b>. XLSX, до 1000 строк. Заголовки в первой строке первого листа.</p><div class="actions"><input id="bulk-file" aria-label="Excel для создания карточек" type="file" accept=".xlsx"><button id="bulk-upload" class="primary">Загрузить Excel</button></div><p class="muted">Карточки создаются в НК как черновики. Результат можно скачать здесь и после перезапуска сервиса.</p><div class="content-scroll"><table><thead><tr><th>Файл</th><th>Состояние</th><th>Действие</th></tr></thead><tbody>${batches.map(b=>`<tr><td>${esc(b.filename)}<br><small>${esc(new Date(b.created).toLocaleString('ru-RU'))}</small></td><td>${esc(batchState[b.state])}</td><td><button data-bulk-open="${b.id}">Открыть</button></td></tr>`).join('')||'<tr><td colspan="3">Пока нет загруженных партий</td></tr>'}</tbody></table></div>`;
  bind('#bulk-upload',async()=>{const f=$('#bulk-file').files[0];if(!f)throw new Error('Выберите файл XLSX');const data=new FormData();data.append('file',f);await show(await api(base+'/preview',{method:'POST',body:data}));});
  $$('[data-bulk-open]',host).forEach(b=>b.onclick=()=>open(b.dataset.bulkOpen).catch(showError));
 }
 async function open(id){await show(await api(base+'/'+id));}
 async function show(b){
  clearTimeout(refreshTimer);
  if(b.state==='preview'){
   const valid=b.rows.filter(r=>!r.errors.length).length;
   modal('Создать карточки из Excel',`<div data-bulk-id="${b.id}"><p><b>${esc(b.filename)}</b> · ${b.rows.length} строк. Готовы к подготовке: ${valid}. Ошибок: ${b.rows.length-valid}.</p><p>Выберите вид товара для каждого ТН ВЭД. Полное наименование будет в формате <b>PN + вид товара</b>, остальные поля заполнятся по шаблону.</p><div class="bulk-kind-choices">${Object.entries(b.groups).map(([tnved,kinds])=>`<label class="field">ТН ВЭД ${esc(tnved)}<select data-bulk-kind="${esc(tnved)}">${kinds.length>1?'<option value="">Выберите вид товара</option>':''}${kinds.map(k=>`<option value="${esc(k.id)}">${esc(k.name)} · опубликовано ${k.published_count}</option>`).join('')}</select></label>`).join('')}</div><p class="muted">Точное совпадение PN с тем же ТН ВЭД и брендом использует существующий GTIN. Повторные строки получают один GTIN. Нажатие кнопки запускает выделение GTIN и создание черновиков в НК.</p><div class="content-scroll"><table><thead><tr><th>Строка</th><th>PN</th><th>ТН ВЭД</th><th>Товарный знак</th><th>Результат проверки</th></tr></thead><tbody>${b.rows.map(r=>`<tr><td>${r.row}</td><td>${esc(r.values.pn)}</td><td>${esc(r.values.tnved)}</td><td>${esc(r.values.brand)}</td><td>${esc(r.errors.join('; ')||(r.existing_gtin?'Есть в НК · '+r.existing_gtin:'Готова'))}</td></tr>`).join('')}</tbody></table></div></div>`,'<button data-close>Отмена</button><button id="bulk-start" class="primary">Создать карточки и получить GTIN</button>');
   $('#dialog').classList.add('template-dialog');
   const update=()=>{$('#bulk-start').disabled=!valid||$$('[data-bulk-kind]').some(s=>!s.value);};
   $$('[data-bulk-kind]').forEach(s=>s.onchange=update);update();
   bind('#bulk-start',async()=>{const choices=Object.fromEntries($$('[data-bulk-kind]').map(s=>[s.dataset.bulkKind,s.value]));await show(await api(base+'/'+b.id+'/start',json('POST',{confirmed:true,kind_choices:choices})));await mount();});return;
  }
  const ok=b.rows.filter(r=>['done','existing'].includes(r.state)).length,errors=b.rows.filter(r=>['error','attention'].includes(r.state)).length;
  const active=b.rows.some(r=>['pending','allocate','save','attention'].includes(r.state));
  modal('Результат создания карточек',`<div data-bulk-id="${b.id}"><p><b>${esc(b.filename)}</b> · ${esc(batchState[b.state])}</p><p>Готово: <b>${ok} из ${b.total}</b>${errors?' · требуют внимания: '+errors:''}.</p><p class="muted">GTIN остаётся в результате, даже если НК отклонил заполнение. Исправить такую карточку можно в «Черновиках».</p><div class="content-scroll"><table><thead><tr><th>Строка</th><th>PN</th><th>GTIN</th><th>Состояние</th><th>Подробности</th></tr></thead><tbody>${b.rows.map(r=>`<tr><td>${r.row_no}</td><td>${esc(r.pn)}</td><td class="mono">${esc(r.gtin||'—')}</td><td>${esc(r.label)}${r.repeat_of?' · повтор строки '+r.repeat_of:''}</td><td>${esc(r.error)} ${r.state==='attention'&&(r.save_job||r.gtin_job)?`<button data-bulk-job="${r.save_job||r.gtin_job}">Открыть операцию</button>`:''}</td></tr>`).join('')}</tbody></table></div></div>`,`<button data-close>Закрыть</button><button id="bulk-refresh">Обновить</button><a class="button primary" id="bulk-export" href="${base}/${b.id}/export" download="nk-cards-gtin.xlsx">${active?'Скачать текущий результат':'Скачать Excel с GTIN'}</a>`);
  $('#dialog').classList.add('template-dialog');bind('#bulk-refresh',()=>open(b.id));
  $$('[data-bulk-job]').forEach(button=>button.onclick=()=>NkUI.openJob(button.dataset.bulkJob).catch(showError));
  if(!active)await mount();
  if(active)refreshTimer=setTimeout(async()=>{if($('#dialog').open&&$('#dialog [data-bulk-id]')?.dataset.bulkId===b.id){try{await open(b.id);}catch(e){showError(e);}}},5000);
 }
 return {mount,open};
})();
