// Render the real attribute editor with deterministic API replies; no DOM or NK writes.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const state={allAttributeFields:false};let hasReference=true;
const context={window:{},state,URLSearchParams,esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),api:async url=>url.startsWith('/api/nk/field-profile')?{has_reference:hasReference,attribute_ids:['13914']}:{schema:[]}};
vm.runInNewContext(fs.readFileSync('web/nk.js','utf8'),context);
(async()=>{
 const card={id:'x',tnved:'8536699008',attributes:{'Модель [#13914]':'','Пустое [#999]':'','Заполнено [#998]':'value','Ноль [#997]':'0','Единица [#996]':'','Единица [#996]::unit':'В'}};
 const original=JSON.stringify(card);
 let html=await context.window.NkUI.attributesHtml(card,true);
 assert(html.includes('data-attribute="Модель [#13914]"'));
 assert(!html.includes('data-attribute="Пустое [#999]"'));
 for(const name of ['Заполнено [#998]','Ноль [#997]','Единица [#996]'])assert(html.includes(`data-attribute="${name}"`));
 assert(html.includes('Показать все поля'));
 state.allAttributeFields=true;
 html=await context.window.NkUI.attributesHtml(card,true);assert(html.includes('data-attribute="Пустое [#999]"'));
 state.allAttributeFields=false;hasReference=false;
 html=await context.window.NkUI.attributesHtml(card,true);assert(html.includes('data-attribute="Пустое [#999]"'));
 assert.equal(JSON.stringify(card),original);
 console.log('Published field visibility: passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
