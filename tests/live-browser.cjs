// Actual HTTP service, SQLite, sessions and XLSX. Test data is isolated in /tmp.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const {createServer}=require('node:net');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const python=process.env.NK_TEST_PYTHON||path.join(root,'.venv/bin/python');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'nk-live-browser-'));
const shots=path.join(root,'docs/screenshots');
const report={scope:'real local HTTP/SQLite service, isolated synthetic test input; no NK integration',checks:[],screenshots:[],errors:[]};
const check=name=>report.checks.push({name,status:'passed'});
let child,browser,logs='';
async function main(){
 const listener=createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));const port=listener.address().port;await new Promise(r=>listener.close(r));
 const origin=`http://127.0.0.1:${port}`;
 const environment={...process.env,NK_DATA:path.join(dir,'data')};
 const setup=spawnSync(python,['-c',"import os; from nk_manager.store import Store; Store(os.environ['NK_DATA']).setup('tester','isolated-test-admin-password')"],{cwd:root,env:environment,encoding:'utf8'});
 assert.equal(setup.status,0,setup.stderr);
 child=spawn(python,['scripts/run_wsl.py','--port',String(port)],{cwd:root,env:environment,stdio:['ignore','pipe','pipe']});
 child.stderr.on('data',d=>logs+=d.toString());
 for(let i=0;i<100;i++){try{const r=await fetch(origin+'/health');if(r.ok)break;}catch(_){}if(child.exitCode!==null)throw new Error(logs);await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.CHROMIUM_ARGS?JSON.parse(process.env.CHROMIUM_ARGS):[]});
 const context=await browser.newContext({viewport:{width:1366,height:768},colorScheme:'light',acceptDownloads:true});
 const page=await context.newPage();page.on('pageerror',e=>report.errors.push(e.message));
 const visible=selector=>page.locator(selector).waitFor({state:'visible'});
 const shot=async name=>{await page.screenshot({path:path.join(shots,name+'.png')});report.screenshots.push(name+'.png');};
 await page.goto(origin);await visible('#login-screen');
 await page.locator('#login-username').fill('tester');await page.locator('#login-password').fill('isolated-test-admin-password');await page.locator('#login-form button[type="submit"]').click();await visible('.registry');
 assert.equal(await page.locator('#rows tbody tr').count(),0);assert((await page.locator('.empty').innerText()).includes('Каталог пуст'));
 assert.equal(await page.locator('nav a[href="#admin"]').count(),1);check('Password-protected login and empty real database');
 await page.locator('#create').click();await visible('#creation-pn');
 await page.locator('#creation-pn').fill('BROWSER-ALPHA');await page.locator('#creation-tnved').fill('8536901000');await page.locator('#creation-brand').fill('Hsuan Mao');
 const dictionary=(await(await page.request.get(origin+'/api/creation/catalog')).json()).kinds;
 const cable=dictionary.find(k=>k.tnved==='8536901000'&&k.name==='Разъём на кабель');
 await page.locator('#creation-kind').selectOption(cable.id);assert.equal(await page.locator('#creation-name').innerText(),'BROWSER-ALPHA Разъём на кабель');
 for(const width of [1366,900,390]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>{const d=document.querySelector('#dialog');return d.scrollWidth<=d.clientWidth+1&&d.getBoundingClientRect().width<=innerWidth;}),'Creation dialog overflow '+width);}
 await page.setViewportSize({width:1366,height:900});await shot('creation-wizard');
 await page.locator('#creation-prepare').click();await visible('#field-pn');assert.equal(await page.locator('#field-name').inputValue(),'BROWSER-ALPHA Разъём на кабель');assert(await page.locator('#field-name').getAttribute('readonly')!==null);
 await page.locator('#field-pn').fill('BROWSER-BETA');assert.equal(await page.locator('#field-name').inputValue(),'BROWSER-BETA Разъём на кабель');await page.locator('#field-pn').fill('BROWSER-ALPHA');
 await page.locator('#save-card').click();await page.waitForFunction(()=>document.querySelector('.drawer-head')?.innerText.includes('Ревизия 1'));await page.locator('#close-drawer').click();
 await page.reload();await visible('.registry');assert.equal(await page.locator('#rows tbody tr').count(),1);check('PN/TN VED/brand/kind wizard, derived name updates with PN, local card retained after reload');
 // A genuine XLSX is written as test input, never added to the user's database.
 const xlsx=path.join(dir,'catalog.xlsx');
 const fixture="from openpyxl import Workbook; import sys; b=Workbook(); w=b.active; w.append(['PN','GTIN','ТН ВЭД','Категория','Тип карточки','Наименование','Статус карточки','Страна','Общий признак','Материал']); w.append(['CONNECTOR777','04640000000011','8536901000','Соединители','Единичный','Соединитель','Опубликована','','Общий','']); b.save(sys.argv[1])";
 const generated=spawnSync(python,['-c',fixture,xlsx],{encoding:'utf8'});assert.equal(generated.status,0,generated.stderr);
 await page.locator('nav a[href="#import"]').click();await visible('#import-file');await page.locator('#import-file').setInputFiles(xlsx);await page.locator('#import-next').click();await visible('#sheet-select');
 await page.locator('#import-next').click();await page.waitForFunction(()=>document.querySelector('#import-body h2')?.textContent==='Результаты проверки');
 assert(!(await page.locator('#import-body').innerText()).includes('неверный формат'));await page.locator('#import-next').click();await visible('#confirm-import');await page.locator('#confirm-import').check();await page.locator('#import-next').click();await visible('#dialog[open]');
 assert((await page.locator('#dialog').innerText()).includes('1 записей сохранено'));await page.locator('#dialog [data-close]').first().click();
 await page.locator('nav a[href="#cards"]').click();await visible('.registry');assert.equal(await page.locator('#rows tbody tr').count(),2);await shot('live-registry');check('Real XLSX mapping, preview, confirmation and import');
 await page.locator('#search').fill('BROWSER-ALPHA');await page.locator('[data-open]').click();
 await page.locator('#choose-product-kind').click();await visible('#creation-pn');assert.equal(await page.locator('#creation-brand').inputValue(),'Hsuan Mao');
 const replacement=dictionary.find(k=>k.tnved==='8536901000'&&k.id!==cable.id);
 await page.locator('#creation-kind').selectOption(replacement.id);await page.locator('#creation-prepare').click();await page.locator('#dialog').waitFor({state:'hidden'});assert.equal(await page.locator('#field-name').inputValue(),'BROWSER-ALPHA '+replacement.name);
 await page.locator('#save-card').click();await page.waitForFunction(()=>document.querySelector('.drawer-head')?.innerText.includes('Ревизия 2'));await page.locator('#close-drawer').click();
 const afterKind=(await(await page.request.get(origin+'/api/cards')).json()).find(c=>c.pn==='BROWSER-ALPHA');
 assert.equal(afterKind.attributes['Товарный знак [#2504]'],'Hsuan Mao');assert.equal(afterKind.attributes['Код ОКПД2 [#3961]'],'27.33.13.110');
 check('Existing local card can change kind; full name and identity update while other filled defaults persist');
 await page.locator('#search').fill('CONNECTOR777');await page.locator('[data-open]').click();await page.locator('#claim-card').click();await page.waitForFunction(()=>document.querySelector('.drawer-head')?.innerText.includes('Ревизия 2'));
 await page.locator('[data-tab="attributes"]').click();await visible('#toggle-attribute-fields');await page.locator('#toggle-attribute-fields').click();await visible('[data-attribute="Страна"]');await page.locator('[data-attribute="Страна"]').fill('Китай');await page.locator('#save-card').click();await page.waitForFunction(()=>document.querySelector('.drawer-head')?.innerText.includes('Ревизия 3'));await shot('live-editor');
 await page.locator('#close-drawer').click();await page.reload();await visible('.registry');await page.locator('#search').fill('CONNECTOR777');await page.locator('[data-open]').click();await page.locator('[data-tab="attributes"]').click();assert.equal(await page.locator('[data-attribute="Страна"]').inputValue(),'Китай');await page.locator('#close-drawer').click();check('Claim and persistent edit of imported source attributes');
 await page.locator('nav a[href="#templates"]').click();await visible('#export-catalog');const downloadPromise=page.waitForEvent('download');await page.locator('#export-catalog').click();const downloaded=await downloadPromise;const zip=path.join(dir,'analysis.zip');await downloaded.saveAs(zip);
 const inspect=spawnSync(python,['-c',"from zipfile import ZipFile; import json,sys; z=ZipFile(sys.argv[1]); j=json.loads(z.read('catalog.json')); assert len(j['cards'])==2; assert any(c['attributes'].get('Страна')=='Китай' for c in j['cards']); assert 'summary.xlsx' in z.namelist()",zip],{encoding:'utf8'});assert.equal(inspect.status,0,inspect.stderr);check('Download analysis ZIP containing actual saved cards and XLSX');
 await page.locator('nav a[href="#batches"]').click();await visible('[data-result]');await page.locator('[data-result]').click();await visible('#dialog[open]');await page.locator('#dialog [data-close]').first().click();
 await page.locator('#logout').click();await visible('#login-screen');await page.locator('#login-username').fill('tester');await page.locator('#login-password').fill('isolated-test-admin-password');await page.locator('#login-form button[type="submit"]').click();await visible('nav a[href="#admin"]');await page.locator('nav a[href="#admin"]').click();await visible('#new-user');await shot('live-admin');check('Real batch results, logout and admin password');
 await page.locator('#new-user').fill('onboarding');await page.locator('#add-user').click();await visible('#user-setup-code');
 const code=await page.locator('#user-setup-code').inputValue();
 assert(code.length>16);await page.locator('#dialog [data-close]').first().click();
 await page.locator('#logout').click();await visible('#login-screen');
 await page.locator('#login-username').fill('onboarding');await page.locator('#login-password').fill(code);await page.locator('#login-form button[type="submit"]').click();await visible('#setup-screen');
 await page.locator('#setup-password').fill('password-of-choice');await page.locator('#setup-password-confirm').fill('password-of-choice');
 await page.locator('#setup-form button[type="submit"]').click();await visible('.registry');
 assert.equal(await page.locator('nav a[href="#admin"]').count(),0);
 check('Administrator onboarding code, mandatory password confirmation, employee permissions');
 for(const width of [1366,1100,900]){await page.setViewportSize({width,height:768});assert(await page.evaluate(()=>{const side=document.querySelector('.sidebar').getBoundingClientRect();return [...document.querySelectorAll('.sidebar nav a')].every(a=>a.getBoundingClientRect().right<=side.right+1&&a.scrollWidth<=a.clientWidth+1);}),'Admin navigation must fit inside sidebar at '+width);}
 await page.setViewportSize({width:1366,height:768});

 await page.locator('nav a[href="#templates"]').click();await visible('#import-template');
 assert.equal(await page.locator('[data-preset-create]').count(),11);
 const preset=page.locator('.template-card').filter({has:page.getByRole('heading',{name:'Эталон опубликованных — 8536901000',exact:true})});
 await preset.locator('[data-preset-create]').click();await visible('#creation-pn');assert.equal(await page.locator('#creation-tnved').inputValue(),'8536901000');
 await page.locator('#creation-pn').fill('PRESET-NEW');await page.locator('#creation-brand').fill('Hsuan Mao');await page.locator('#creation-kind').selectOption(cable.id);await page.locator('#creation-prepare').click();await visible('#field-pn');assert.equal(await page.locator('#field-name').inputValue(),'PRESET-NEW Разъём на кабель');
 await page.locator('[data-tab="attributes"]').click();assert.equal(await page.locator('[data-attribute="Код ОКПД2 [#3961]"]').inputValue(),'27.33.13.110');
 page.once('dialog',d=>d.accept());await page.locator('#close-drawer').click();check('11 built-in presets available and create local draft with modal OKPD2');
 await page.locator('#import-template').click();
 const template={schema_version:1,name:'Шаблон соединителей',tnved:'8536901000',category:'Соединители',card_type:'Единичный',rules:[{attribute:'Материал',mode:'fill_if_empty',value:'Металл'}]};
 await page.locator('#template-json').fill(JSON.stringify(template));await page.locator('#save-template').click();await visible('[data-approve]');page.once('dialog',d=>d.accept());await page.locator('[data-approve]').click();await page.waitForFunction(()=>document.querySelector('.template-card .badge')?.textContent.includes('Активен локально'));
 await page.locator('nav a[href="#cards"]').click();await visible('.registry');await page.locator('#search').fill('CONNECTOR777');await page.locator('[data-select]').check();await page.locator('#apply-templates').click();await visible('#apply-plan');assert((await page.locator('#dialog').innerText()).includes('Материал')&&(await page.locator('#dialog').innerText()).includes('Металл'));await page.locator('#apply-plan').click();await page.waitForFunction(()=>document.querySelector('#dialog h2')?.textContent==='Результат заполнения');await page.locator('#dialog [data-close]').first().click();
 await page.locator('[data-open]').click();await page.locator('[data-tab="attributes"]').click();await visible('[data-attribute="Материал"]');assert.equal(await page.locator('[data-attribute="Материал"]').inputValue(),'Металл');await page.locator('#close-drawer').click();check('Import, approve and apply a real local category template via browser');
 await page.locator('#search').fill('');await page.locator('#theme-toggle').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');await shot('live-dark');
 for(const [width,height] of [[1440,900],[1920,1080],[390,844]]){await page.setViewportSize({width,height});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot(`live-${width}x${height}`);}
 check('Shared themes and desktop/mobile layouts of the real service');
 assert.deepEqual(report.errors,[]);report.status='passed';report.browser=browser.version();
}
main().catch(error=>{report.status='failed';report.failure=error.stack;report.serverLogs=logs.slice(-4000);process.exitCode=1;console.error(error);}).finally(async()=>{if(browser)await browser.close();if(child)child.kill('SIGTERM');fs.mkdirSync(shots,{recursive:true});fs.writeFileSync(path.join(root,'docs/live-browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));fs.rmSync(dir,{recursive:true,force:true});});
