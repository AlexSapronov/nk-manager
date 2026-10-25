/* Searchable category popup in the top layer; long NK names never size the window. */
window.CategoryFilter = (() => {
 let cleanup;
 function mount(host,categories,value,onChange){
  if(cleanup)cleanup();
  host.innerHTML=`<button id="category-filter" type="button" aria-label="Категория" aria-haspopup="listbox" aria-expanded="false"><span></span><span aria-hidden="true">▾</span></button>`;
  const trigger=host.querySelector('button'),caption=trigger.querySelector('span');
  const panel=document.createElement('div');panel.id='category-filter-panel';panel.className='category-filter-panel';panel.setAttribute('popover','auto');
  panel.innerHTML='<label class="field">Найти категорию<input id="category-filter-search" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="category-filter-options" autocomplete="off"></label><div id="category-filter-options" role="listbox" aria-label="Категории"></div><small id="category-filter-status" role="status"></small>';
  document.body.append(panel);trigger.setAttribute('aria-controls','category-filter-options');
  const search=panel.querySelector('input'),list=panel.querySelector('[role="listbox"]'),status=panel.querySelector('[role="status"]');
  const all=[{value:'',label:'Все категории'},...categories.map(c=>({value:c,label:c}))];let visible=[],active=-1;
  function label(){caption.textContent=value||'Все категории';trigger.title=value||'Все категории';}
  function position(){
   if(!trigger.isConnected){if(panel.matches(':popover-open'))panel.hidePopover();return;}
   const r=trigger.getBoundingClientRect(),width=Math.min(560,innerWidth-24),below=innerHeight-r.bottom-12,above=r.top-12;
   const up=below<180&&above>below,height=Math.max(100,Math.min(420,up?above:below));
   panel.style.width=width+'px';panel.style.maxHeight=height+'px';panel.style.left=Math.max(12,Math.min(r.left,innerWidth-width-12))+'px';
   panel.style.top=(up?Math.max(12,r.top-Math.min(panel.scrollHeight,height)-6):r.bottom+6)+'px';
  }
  function focusOption(index){
   active=index;const options=[...list.children];options.forEach((o,i)=>o.classList.toggle('active',i===active));
   if(options[active]){search.setAttribute('aria-activedescendant',options[active].id);options[active].scrollIntoView({block:'nearest'});}else search.removeAttribute('aria-activedescendant');
  }
  function close(focus=false){if(panel.matches(':popover-open'))panel.hidePopover();if(focus)trigger.focus();}
  function choose(item){value=item.value;label();close(true);onChange(value);}
  function render(){
   const query=search.value.trim().toLocaleLowerCase();visible=all.filter(c=>!query||c.label.toLocaleLowerCase().includes(query));list.replaceChildren();
   visible.forEach((item,index)=>{const option=document.createElement('button');option.type='button';option.tabIndex=-1;option.id='category-filter-option-'+index;option.setAttribute('role','option');option.setAttribute('aria-selected',String(item.value===value));option.textContent=item.label;option.onclick=()=>choose(item);list.append(option);});
   status.textContent=visible.length?'Найдено: '+visible.length:'Категории не найдены';focusOption(-1);if(panel.matches(':popover-open'))position();
  }
  trigger.onclick=()=>{if(panel.matches(':popover-open')){close();return;}search.value='';render();panel.showPopover();position();search.focus();};
  trigger.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(!panel.matches(':popover-open'))trigger.click();focusOption(e.key==='ArrowDown'?0:visible.length-1);}};
  // beforetoggle runs synchronously, unlike the queued/coalesced toggle event.
  panel.addEventListener('beforetoggle',event=>trigger.setAttribute('aria-expanded',String(event.newState==='open')));
  search.oninput=render;
  panel.onkeydown=e=>{
   if(e.key==='Escape'){e.preventDefault();close(true);}
   if(e.key==='Tab')close(true);
   if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(visible.length)focusOption(active<0?(e.key==='ArrowDown'?0:visible.length-1):(active+(e.key==='ArrowDown'?1:-1)+visible.length)%visible.length);}
   if(e.key==='Enter'&&active>=0){e.preventDefault();choose(visible[active]);}
  };
  window.addEventListener('resize',position);window.addEventListener('scroll',position,true);
  cleanup=()=>{window.removeEventListener('resize',position);window.removeEventListener('scroll',position,true);panel.remove();};label();
 }
 return {mount};
})();
