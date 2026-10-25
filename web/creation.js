/* PN + TN VED + explicit product kind. All preparation remains local. */
window.CreationUI = (() => {
 const attr=(c,id)=>Object.entries(c.attributes||{}).find(([k])=>k.includes('[#'+id+']')&&!k.endsWith('::unit'))?.[1]||'';
 function kindName(c){const kind=attr(c,'12')||c.attributes?.['Вид товара']||'';return kind==='НЕТ В СПРАВОЧНИКЕ'?(attr(c,'23828')||c.attributes?.['Вид товара (при отсутствии в справочнике)']||''):kind;}
 function syncName(c){
  const label=kindName(c);if(!label||!c.pn.trim())return;
  c.name=c.pn.trim()+' '+label;
  for(const [id,value,title] of [['2478',c.name,'Полное наименование товара'],['13914',c.pn.trim(),'Модель / артикул производителя']]){
   const brand=attr(c,'2504').trim().toLocaleLowerCase();
   let key=Object.keys(c.attributes).find(k=>k.includes('[#'+id+']')&&!k.endsWith('::unit')&&(id!=='13914'||!brand||String(c.attributes[k]).trim().toLocaleLowerCase()!==brand));
   if(!key&&Object.hasOwn(c.attributes,title))key=title;
   if(!key){key=`${title} [#${id}]`;if(id==='13914'&&Object.hasOwn(c.attributes,key))key+=' · PN';}
   c.attributes[key]=value;
  }
 }
 async function open(tnved='',target=null){
  if(!target&&!leave())return;
  if(target)capture();
  const {kinds}=await api('/api/creation/catalog');
  const currentKind=target?kindName(target):'';
  modal(target?'Выбрать вид и заполнить карточку':'Новая карточка по эталону',`
   <p>Укажите PN, ТН ВЭД и товарный знак, затем выберите вид товара из опубликованных карточек.</p>
   <div class="form-grid"><label class="field">PN / артикул<input id="creation-pn" maxlength="300" value="${esc(target?.pn||'')}" autocomplete="off"></label>
   <label class="field">ТН ВЭД · 10 цифр<input id="creation-tnved" list="creation-tnved-list" maxlength="10" value="${esc(tnved)}" ${target?.external_id?'readonly':''}><datalist id="creation-tnved-list">${[...new Set(kinds.map(k=>k.tnved))].map(t=>`<option value="${t}">`).join('')}</datalist></label>
   <label class="field full" id="creation-brand-field">Товарный знак<input id="creation-brand" list="creation-brand-list" maxlength="2000"><datalist id="creation-brand-list"></datalist><small id="creation-brand-note"></small></label>
   <label class="field full">Вид товара<select id="creation-kind"></select><small id="creation-kind-note"></small></label>
</div>
   <div class="section"><h3>Полное наименование</h3><p id="creation-name" class="mono"></p></div>
   <p class="muted">По эталону заполнятся категория, ОКПД2, модель/артикул, тип переменной характеристики, ФНН, номер реестра и основание отсутствия требований соответствия. GTIN не копируется; его можно получить отдельно на вкладке «НК». Значения можно проверить перед сохранением.</p>`,
   '<button data-close>Отмена</button><button id="creation-empty">Пустая карточка</button><button id="creation-prepare" class="primary">Заполнить и проверить</button>');
  const selected=()=>kinds.find(k=>k.id===$('#creation-kind').value);
  function name(){const k=selected(),pn=$('#creation-pn').value.trim();$('#creation-name').textContent=pn&&k?pn+' '+k.name:'PN + вид товара';$('#creation-prepare').disabled=!pn||!k||!$('#creation-brand').value.trim();}
  function brand(){const k=selected(),previous=$('#creation-brand').value|| (target?attr(target,'2504'):'');const brands=k?.brands||kinds.filter(k=>k.tnved===$('#creation-tnved').value.trim()).flatMap(k=>k.brands);$('#creation-brand-list').innerHTML=[...new Set(brands.map(b=>b.name))].map(b=>`<option value="${esc(b)}">`).join('');$('#creation-brand').value=previous;$('#creation-brand-note').textContent='Введите фактический товарный знак. Подсказки — из опубликованных карточек; можно указать новый бренд.';name();}
  function types(){const code=$('#creation-tnved').value.trim(),rows=kinds.filter(k=>k.tnved===code);$('#creation-kind').innerHTML='<option value="">Выберите вид товара</option>'+rows.map(k=>`<option value="${k.id}">${esc(k.name)} · ${k.published_count} опубликовано</option>`).join('');const found=rows.find(k=>k.name===currentKind);if(found)$('#creation-kind').value=found.id;else if(rows.length===1)$('#creation-kind').value=rows[0].id;$('#creation-kind-note').textContent=rows.length?`${rows.length} вариантов из опубликованных карточек. Написание сохранено из источника.`:'Нет опубликованных видов для этого ТН ВЭД. Можно открыть пустую карточку.';brand();}
  $('#creation-tnved').oninput=types;$('#creation-kind').onchange=brand;$('#creation-pn').oninput=name;$('#creation-brand').oninput=name;types();
  bind('#creation-empty',()=>{$('#dialog').close();if(!target)openCard(null);});
  bind('#creation-prepare',async()=>{
   const draft=await api('/api/creation/preview',json('POST',{pn:$('#creation-pn').value,tnved:$('#creation-tnved').value,kind_id:$('#creation-kind').value,brand:$('#creation-brand').value}));
   if(target){
    // Keep local values; only the explicitly selected identity/kind is replaced.
    for(const [key,value] of Object.entries(draft.attributes)){
     const id=key.match(/\[#(\d+)\]/)?.[1],unit=key.endsWith('::unit');
     const old=Object.keys(target.attributes).find(k=>k.includes('[#'+id+']')&&k.endsWith('::unit')===unit&&(id!=='13914'||unit||String(target.attributes[k]).trim().toLocaleLowerCase()!==attr(target,'2504').trim().toLocaleLowerCase()))||key;
     if(['12','23828','13914','2478','2504','3959','13933'].includes(id)||!target.attributes[old])target.attributes[old]=value;
    }
    target.pn=draft.pn;target.tnved=draft.tnved;if(!target.category)target.category=draft.category;if(!target.card_type)target.card_type='unit';
    if(!target.external_id){target.category=draft.category;target.creation_kind_id=draft.creation_kind_id;}
    syncName(target);
   }else{
    state.drawer={id:null,owner:state.user.username,revision:0,source:'local',external_status:null,...draft};state.allAttributeFields=false;
   }
   state.dirty=true;state.tab='main';$('#dialog').close();await drawCard();toast('Карточка заполнена по эталону. Проверьте значения и сохраните.');
  });
 }
 return {open,kindName,syncName};
})();
