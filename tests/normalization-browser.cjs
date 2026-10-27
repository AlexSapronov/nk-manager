const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{createServer}=require('node:net'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),python=process.env.NK_TEST_PYTHON||path.join(root,'.venv/bin/python'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'nk-normalization-'));
let child,browser,logs='';const report={scope:'Synthetic mixed drafts/statuses, actual HTTP/SQLite; no NK calls',checks:[],errors:[]};
async function main(){
 const socket=createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));const origin=`http://127.0.0.1:${port}`;
 child=spawn(python,['-m','tests.normalization_browser_server'],{cwd:root,env:{...process.env,NK_DATA:path.join(dir,'data'),NK_PUBLIC_URL:origin,NK_TEST_PORT:String(port)},stdio:['ignore','ignore','pipe']});child.stderr.on('data',d=>logs+=d.toString());
 for(let i=0;i<100;i++){try{if((await fetch(origin+'/health')).ok)break;}catch(_){}if(child.exitCode!==null)throw new Error(logs);await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),args:process.env.CHROMIUM_ARGS?JSON.parse(process.env.CHROMIUM_ARGS):[]});
 const page=await browser.newPage({viewport:{width:1366,height:900},colorScheme:'dark'});page.on('pageerror',e=>report.errors.push(e.message));
 await page.goto(origin);await page.locator('#login-username').fill('tester');await page.locator('#login-password').fill('isolated-test-admin-password');await page.locator('#login-form button[type="submit"]').click();await page.locator('#normalize-drafts').waitFor();
 const before=await(await page.request.get(origin+'/api/cards')).json();
 await page.locator('#normalize-drafts').click();await page.locator('#normalize-apply').waitFor();assert.equal(await page.locator('[data-normalize-select]').count(),5);assert.equal(await page.locator('#normalize-apply').isEnabled(),true);
 assert((await page.locator('#dialog').innerText()).includes('Не указан PN'));
 const rows=page.locator('#dialog tbody tr');const dupRows=rows.filter({hasText:'SAME'});await dupRows.locator('[data-normalize-select]').first().uncheck();await dupRows.locator('[data-normalize-select]').last().uncheck();assert.equal(await page.locator('#normalize-apply').isEnabled(),true);
 await rows.filter({hasText:'PN-TAIL'}).locator('summary').first().click();assert((await rows.filter({hasText:'PN-TAIL'}).innerText()).includes('PN-TAIL Разъём на кабель'));
 for(const width of [1366,900,390]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>{const d=document.querySelector('#dialog');return d.scrollWidth<=d.clientWidth+1&&d.getBoundingClientRect().width<=innerWidth;}),'Overflow '+width);}
 await page.setViewportSize({width:1366,height:1000});await page.screenshot({path:path.join(root,'docs/screenshots/normalization-preview.png')});
 await page.locator('#normalize-apply').click();await page.waitForFunction(()=>document.querySelector('#dialog h2')?.textContent==='Результат приведения черновиков');assert((await page.locator('#dialog').innerText()).includes('Приведено: 3 из 3'));
 const after=await(await page.request.get(origin+'/api/cards')).json();
 assert.equal(after.find(c=>c.name==='PN-TAIL Разъём на кабель').pn,'PN-TAIL');assert.equal(after.find(c=>c.name==='PN-HEAD Разъём на кабель').pn,'PN-HEAD');
 const untouched=before.filter(c=>/ON-MODERATION|PUBLISHED|OTHER-OWNER|ARCHIVED|BUSY/.test(c.name));for(const c of untouched){const changed=after.find(x=>x.id===c.id);assert.equal(changed.revision,c.revision);assert.deepEqual(changed.attributes,c.attributes);}
 assert.equal(await page.evaluate(()=>state.selected.size),2);
 await page.screenshot({path:path.join(root,'docs/screenshots/normalization-result.png')});report.checks.push('Before/after preview, exact recovered PN, unresolved fields, status/owner/archive/busy exclusion, partial apply, only complete cards selected');
 await page.locator('#normalize-again').click();await page.locator('#normalize-apply').waitFor();assert.equal(await page.locator('[data-normalize-select]').count(),2);assert.equal(await page.locator('#normalize-apply').isEnabled(),true);await page.locator('#normalize-apply').click();await page.waitForFunction(()=>document.querySelector('#dialog h2')?.textContent==='Результат приведения черновиков');assert((await page.locator('#dialog').innerText()).includes('Приведено: 2 из 2'));report.checks.push('Re-running is idempotent; local exact duplicates do not require an explanation');
 assert.deepEqual(report.errors,[]);report.status='passed';
}
main().catch(e=>{report.status='failed';report.error=e.stack;report.logs=logs.slice(-2000);console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(child)child.kill('SIGTERM');fs.writeFileSync(path.join(root,'docs/normalization-browser-report.json'),JSON.stringify(report,null,2)+'\n');fs.rmSync(dir,{recursive:true,force:true});console.log(JSON.stringify(report,null,2));});
