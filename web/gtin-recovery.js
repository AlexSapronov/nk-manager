'use strict';
window.GtinRecovery = (() => {
 const base='/api/admin/recovery-batches';
 let timer;
 const states={scanning:'Проверяем GTIN в НК',preview:'Можно выбрать шаблоны',running:'Создаём черновики',completed:'Завершена'};
 async function mount(){
  const host=$('#gtin-recovery');
  if(!host||!state.user.admin)return;
  const batches=await api(base);
  host.innerHTML=`<h2>Восстановить карточки GS1 по GTIN</h2>
   <p>Загрузите XLSX с колонками <b>GTIN</b> и <b>Производитель</b>. PN и ТН ВЭД можно указать дополнительно, если они не доступны через API НК. До 1000 строк.</p>
   <p class="muted">Проверяем существующие карточки, используем шаблоны опубликованных видов товаров. Новый GTIN никогда не запрашивается, публикация и модерация не запускаются.</p>
   <div class="actions"><input id="recovery-file" type="file" accept=".xlsx" aria-label="Файл с GTIN и производителем"><button id="recovery-upload" class="primary">Проверить GTIN</button></div>
   <div class="content-scroll"><table><thead><tr><th>Файл</th><th>Состояние</th><th>Действие</th></tr></thead><tbody>
   ${batches.map(b=>`<tr><td>${esc(b.filename)}</td><td>${esc(states[b.state]||b.state)}</td><td><button data-recovery-open="${esc(b.id)}">Открыть</button></td></tr>`).join('')||'<tr><td colspan="3">Партий пока нет</td></tr>'}
   </tbody></table></div>`;
  bind('#recovery-upload',async()=>{
   const f=$('#recovery-file').files[0];
   if(!f)throw new Error('Выберите XLSX');
   const data=new FormData();data.append('file',f);
   await show(await api(base+'/preview',{method:'POST',body:data}));
  });
  $$('[data-recovery-open]',host).forEach(b=>b.onclick=()=>open(b.dataset.recoveryOpen).catch(showError));
 }
 async function open(id){await show(await api(base+'/'+encodeURIComponent(id)));}
 async function show(b){
  clearTimeout(timer);
  const counts={};for(const r of b.rows)counts[r.state]=(counts[r.state]||0)+1;
  const ready=b.state==='preview'&&b.ready>0;
  const pending=b.state==='scanning'||b.state==='running';
  const groups=Object.entries(b.groups||{});
  const choices=ready?`<p>Для каждого ТН ВЭД выберите проверенный вид товара. Он задаёт категорию, ОКПД2 и остальные атрибуты.</p>
   ${groups.map(([tnved,kinds])=>`<label class="field">ТН ВЭД ${esc(tnved)}<select data-recovery-kind="${esc(tnved)}">
    ${kinds.length===1?'':'<option value="">Выберите вид</option>'}
    ${kinds.map(k=>`<option value="${esc(k.id)}">${esc(k.name)} · ${esc(k.category)}</option>`).join('')}</select></label>`).join('')}
   <label class="field"><span><input type="checkbox" id="recovery-brand-confirm"> Если в НК не указан товарный знак, использовать в этом поле производителя из XLSX</span></label>`:'';
  modal('Восстановление GTIN из GS1',
   `<div data-recovery-id="${esc(b.id)}"><p><b>${esc(b.filename)}</b> · ${esc(states[b.state]||b.state)} · всего ${b.total} строк.</p>
   <p class="muted">${Object.entries(counts).map(([k,v])=>esc(({'ready':'Готовы','done':'Сохранены в НК','exists':'Уже опубликованы','conflict':'Требуют проверки','need_data':'Нет данных','error':'Ошибка','scan':'Проверяются','queued':'Отправляются','repeat':'Повторы'})[k]||k)+': '+v).join(' · ')}</p>
   <div class="notice info">НК не всегда возвращает по GTIN данные GS1. Если PN или ТН ВЭД не найден, добавьте эти колонки в файл. Опубликованные карточки и конфликты не изменяются.</div>
   ${choices}
   <div class="content-scroll"><table><thead><tr><th>Строка</th><th>GTIN</th><th>Производитель</th><th>PN</th><th>ТН ВЭД</th><th>Проверка</th></tr></thead><tbody>
   ${b.rows.slice(0,300).map(r=>`<tr><td>${r.row_no}</td><td class="mono">${esc(r.gtin)}</td><td>${esc(r.manufacturer)}</td><td>${esc(r.pn)}</td><td>${esc(r.tnved)}</td><td>${esc(r.label)} ${r.nk_found?'· НК':''}${r.error?' · '+esc(r.error):''}${r.save_job?` <button data-recovery-job="${esc(r.save_job)}">Операция</button>`:''}</td></tr>`).join('')}
   </tbody></table></div>${b.rows.length>300?'<p class="muted">Показаны первые 300 строк. Все строки доступны в Excel-отчёте.</p>':''}</div>`,
   `<button data-close>Закрыть</button><button id="recovery-refresh">Обновить</button>
    <a class="button" href="${base}/${encodeURIComponent(b.id)}/export" download="nk-gtin-recovery.xlsx">Скачать результат</a>
    ${ready?'<button id="recovery-start" class="primary">Создать черновики в НК</button>':''}`);
  $('#dialog').classList.add('template-dialog');
  bind('#recovery-refresh',()=>open(b.id));
  $$('[data-recovery-job]').forEach(btn=>btn.onclick=()=>NkUI.openJob(btn.dataset.recoveryJob).catch(showError));
  if(ready){
   const valid=()=>{$('#recovery-start').disabled=$$('[data-recovery-kind]').some(s=>!s.value);};
   $$('[data-recovery-kind]').forEach(s=>s.onchange=valid);valid();
   bind('#recovery-start',async()=>{
    if(!window.confirm('Создать черновики в НК с существующими GTIN? Модерация и публикация не будут запущены.'))return;
    const kind_choices=Object.fromEntries($$('[data-recovery-kind]').map(s=>[s.dataset.recoveryKind,s.value]));
    await show(await api(base+'/'+encodeURIComponent(b.id)+'/start',json('POST',{confirmed:true,kind_choices,manufacturer_as_brand:$('#recovery-brand-confirm').checked})));
    await mount();
   });
  }
  if(pending)timer=setTimeout(async()=>{
   if($('#dialog').open&&$('#dialog [data-recovery-id]')?.dataset.recoveryId===b.id){
    try{await open(b.id);}catch(e){showError(e);}
   }
  },5000);
  else await mount();
 }
 return {mount,open};
})();
