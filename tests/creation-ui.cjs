// Shared identity contract: the real UI must reflect the name that will be saved.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{}};vm.runInNewContext(fs.readFileSync('web/creation.js','utf8'),context);
const ui=context.window.CreationUI;
const c={pn:'LM50-23B24R2',name:'old',attributes:{'Вид товара [#12]':'НЕТ В СПРАВОЧНИКЕ','Вид товара (при отсутствии в справочнике) [#23828]':'AC/DC преобразователь','Товарный знак [#2504]':'MORNSUN','Модель / артикул производителя [#13914]':'LM50-23B24R2'}};
ui.syncName(c);assert.equal(c.name,'LM50-23B24R2 AC/DC преобразователь');assert.equal(c.attributes['Полное наименование товара [#2478]'],c.name);
c.pn='LM50-23B24R3';ui.syncName(c);assert.equal(c.name,'LM50-23B24R3 AC/DC преобразователь');assert.equal(c.attributes['Модель / артикул производителя [#13914]'],c.pn);
c.attributes['Модель / артикул производителя [#13914]']='MORNSUN';c.attributes['Модель / артикул производителя [#13914] · 6']='OLD';
ui.syncName(c);assert.equal(c.attributes['Модель / артикул производителя [#13914]'],'MORNSUN');assert.equal(c.attributes['Модель / артикул производителя [#13914] · 6'],c.pn);
const imported={pn:'PN-X',attributes:{'Вид товара':'НЕТ В СПРАВОЧНИКЕ','Вид товара (при отсутствии в справочнике)':'Разъём','Полное наименование товара':'old'}};
ui.syncName(imported);assert.equal(imported.name,'PN-X Разъём');assert.equal(imported.attributes['Полное наименование товара'],imported.name);
console.log('Creation identity UI contract: passed');
