// Tests only the labelled static prototype. No mocks of production NK endpoints.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {createRequire} = require('node:module');
let playwright;
try { playwright = require('playwright'); }
catch(error) {
  if (!process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES) throw error;
  playwright = createRequire(path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'package.json'))('playwright');
}
const root = path.resolve(__dirname, '..');
const screenshots = path.join(root, 'docs/screenshots');
fs.mkdirSync(screenshots, {recursive:true});
const evidence = {scope:'static UX prototype only',started:new Date().toISOString(),checks:[],screenshots:[],externalRequests:[]};
const check = name => evidence.checks.push({name,status:'passed'});
const server = http.createServer((req,res)=>{
  const pathname = decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  const file = path.resolve(root, '.' + (pathname.endsWith('/') ? pathname+'index.html' : pathname));
  if(!file.startsWith(root+path.sep)){res.writeHead(403);return res.end();}
  fs.readFile(file,(err,data)=>{
    if(err){res.writeHead(404);return res.end();}
    const type={'.js':'text/javascript','.css':'text/css','.html':'text/html'}[path.extname(file)]||'application/octet-stream';
    res.writeHead(200,{'Content-Type':type+'; charset=utf-8'});res.end(data);
  });
});
async function main(){
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 const browser = await playwright.chromium.launch({headless:true,
   ...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),
   args: process.env.CHROMIUM_ARGS?JSON.parse(process.env.CHROMIUM_ARGS):[]});
 const errors=[];
 try {
 const context=await browser.newContext({viewport:{width:1366,height:768}});
 const page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(!r.url().startsWith(origin))evidence.externalRequests.push(r.url());});
 await page.goto(origin+'/prototype/');
 const visible = async(selector)=>{await page.locator(selector).waitFor({state:'visible'});assert(await page.locator(selector).isVisible(),selector+' visible');};
 const shot = async(name)=>{await page.screenshot({path:path.join(screenshots,name+'.png'),fullPage:false});evidence.screenshots.push(name+'.png');};
 await visible('.registry');assert.equal(await page.locator('tbody tr').count(),12);await visible('.prototype-bar');
 assert.equal(await page.locator('nav a[href="#admin"]').count(),0);check('Initial registry and employee navigation');
 for(const [width,height] of [[1366,768],[1440,900],[1920,1080],[390,844]]){
   await page.setViewportSize({width,height});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page overflow at '+width);
   await shot(`registry-${width}x${height}`);
   await page.locator('[data-open="1"]').click();await visible('#drawer');
   const footer=await page.locator('.drawer-footer').boundingBox();assert(footer.y+footer.height<=height+1,'Drawer actions remain visible');
   await shot(`drawer-${width}x${height}`);await page.locator('#close-drawer').click();
 }
 check('Four required viewport sizes, screenshots and visible drawer actions');
 await page.setViewportSize({width:1093,height:614});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot('registry-1366-at-125-percent-space');
 check('125 percent space approximation, not native browser zoom');
 await page.setViewportSize({width:1366,height:768});
 await page.locator('#search').fill('L-132XYD');assert.equal(await page.locator('tbody tr').count(),9);
 await page.locator('[data-open="1"]').click();await page.locator('#expand-drawer').click();
 assert(await page.locator('#drawer').evaluate(el=>el.classList.contains('expanded')));
 await page.locator('#field-name').fill('Локальная проверка наименования');
 await page.locator('[data-tab="attributes"]').click();
 await page.locator('[data-tab="main"]').click();assert.equal(await page.locator('#field-name').inputValue(),'Локальная проверка наименования');
 await page.locator('#save-card').click();await page.locator('#close-drawer').click();
 assert.equal(await page.locator('#search').inputValue(),'L-132XYD');
 await page.locator('[data-open="1"]').click();assert.equal(await page.locator('#field-name').inputValue(),'Локальная проверка наименования');
 await page.locator('#next-card').click();assert((await page.locator('.drawer-head h2').innerText()).includes('-1'));
 await page.locator('#close-drawer').click();await page.locator('#search').fill('');
 check('Search preserved; expand, input across tabs, save and next card');
 await page.locator('[data-open="1"]').click();await page.locator('#field-name').fill('Несохранённый ввод');
 page.once('dialog',d=>d.dismiss());await page.locator('#close-drawer').click();await visible('#drawer');
 assert.equal(await page.locator('#field-name').inputValue(),'Несохранённый ввод');
 page.once('dialog',d=>d.accept());await page.locator('#close-drawer').click();check('Unsaved input guard allows cancel and explicit discard');
 await page.locator('[data-select="1"]').check();await page.locator('#next-page').click();
 await page.locator('tbody input[type="checkbox"]').first().check();assert((await page.locator('#bulk strong').innerText()).includes('2'));
 await page.locator('#bulk-template').click();await visible('#apply-preview');
 assert((await page.locator('#dialog').innerText()).includes('2 карточек'));await shot('bulk-template-preview');
 await page.locator('#apply-preview').click();await page.locator('#bulk-send').click();assert((await page.locator('#dialog').innerText()).includes('ровно 2'));
 assert(await page.locator('#dialog button[disabled]').isDisabled());await page.locator('#dialog [data-close]').first().click();
 check('Selection persists across pages; exact count in template and sending previews');
 await page.locator('#bulk-check').click();await page.locator('#confirm-duplicate').click();await visible('#duplicate-error');
 assert((await page.locator('#duplicate-error').innerText()).length>0);await shot('duplicate-review');
 await page.locator('#duplicate-reason').fill('Разные исполнения по документации поставщика');await page.locator('#confirm-duplicate').click();
 check('Duplicate decision requires a reason');await page.locator('#clear-selection').click();
 await page.locator('nav a[href="#import"]').click();
 for(let i=0;i<3;i++)await page.locator('#import-next').click();await visible('#import-confirm');
 await page.locator('#import-confirm').check();await page.locator('#import-next').click();await shot('excel-confirmation');check('Four-step Excel review flow');
 await page.locator('nav a[href="#templates"]').click();await page.locator('#template-diff').click();
 assert((await page.locator('#dialog').innerText()).includes('Автозаполнение отклонить'));await shot('template-diff');await page.locator('#dialog [data-close]').first().click();
 await page.locator('nav a[href="#batches"]').click();await page.locator('#batch-unknown').click();
 assert((await page.locator('#dialog').innerText()).includes('заблокирована'));await page.locator('#dialog [data-close]').first().click();await shot('batches');
 check('Template diff and unknown mutation outcome explanation');
 await page.locator('#role-switch').click();await page.locator('nav a[href="#admin"]').click();await visible('#admin-content');await shot('admin');
 await page.locator('[data-admin="Подпись"]').click();assert((await page.locator('#admin-content').innerText()).includes('Нет подключения'));
 await page.locator('#role-switch').click();assert.equal(await page.locator('nav a[href="#admin"]').count(),0);
 check('Admin preview navigation, signer unavailable and employee menu separation');
 await page.locator('#scenarios').click();await page.locator('[data-scenario="empty"]').click();await visible('.empty');
 await page.locator('#scenarios').click();await page.locator('[data-scenario="offline"]').click();
 await page.locator('[data-open="1"]').click();await page.locator('#field-name').fill('Сохранить при отсутствии сети');await page.locator('#save-card').click();
 assert.equal(await page.locator('#field-name').inputValue(),'Сохранить при отсутствии сети');
 page.once('dialog',d=>d.accept());await page.locator('#close-drawer').click();
 await page.locator('#scenarios').click();await page.locator('[data-scenario="conflict"]').click();await visible('#resolve-conflict');
 await page.locator('#resolve-conflict').click();assert((await page.locator('#dialog').innerText()).includes('Ваше значение'));await shot('revision-conflict');
 await page.locator('#dialog [data-close]').first().click();await page.locator('#close-drawer').click();
 check('Empty, offline input preservation and revision conflict views');
 await page.locator('#scenarios').click();await page.locator('[data-scenario="normal"]').click();
 await page.locator('[data-select="1"]').check();await page.locator('#bulk-stop').click();await page.locator('#confirm-stop').click();
 assert((await page.locator('#stop-error').innerText()).length>0);await page.locator('#stop-reason').fill('Товар больше не планируется');await page.locator('#confirm-stop').click();
 await page.locator('[data-filter="archive"]').click();assert(await page.locator('[data-open="1"]').isVisible());check('Local stop requires a reason and retains card in archive');
 await page.goto(origin+'/prototype/preview.html');await visible('.registry');
 assert.equal(evidence.externalRequests.length,0);assert.deepEqual(errors,[]);check('Standalone HTML loads without external requests or JavaScript errors');
 evidence.status='passed';evidence.browser=browser.version();evidence.finished=new Date().toISOString();
 }finally{await browser.close();}
}
main().catch(error=>{evidence.status='failed';evidence.error=error.stack;process.exitCode=1;console.error(error);}).finally(()=>{
 fs.writeFileSync(path.join(root,'docs/browser-report.json'),JSON.stringify(evidence,null,2)+'\n');server.close();console.log(JSON.stringify(evidence,null,2));
});
