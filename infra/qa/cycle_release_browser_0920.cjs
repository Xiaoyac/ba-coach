// Real login and read-only goal/history checks; credentials never printed.
const {chromium}=require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const out=path.resolve('work/full-cycle-acceptance-0920/post-release-20260920T031500Z');

(async()=>{
  const actor=process.argv[2]||'D';
  const cred=JSON.parse(await fs.readFile(path.join(out,actor,'credentials.local-only.json'),'utf8'));
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  const results=[];
  try {
    for(const width of [1440,390]) {
      const ctx=await browser.newContext({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
      const page=await ctx.newPage();
      const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto('https://bacoach.xyz/',{waitUntil:'domcontentloaded'});
      await page.locator('input[autocomplete="username"]').fill(cred.username);
      await page.locator('input[type="password"]').fill(cred.password);
      await page.locator('button[type="submit"]').click();
      await page.getByRole('button',{name:/账号菜单/}).waitFor({timeout:30000});
      await page.getByRole('textbox',{name:'Message',exact:true}).waitFor();
      await page.getByPlaceholder('慢慢说…',{exact:true}).waitFor({timeout:30000});
      await page.screenshot({path:path.join(out,`chat-${actor}-${width}.png`),scale:'css'});
      const fit=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
      assert.equal(fit.width,fit.scrollWidth);
      const goals=page.getByRole('button',{name:'我的目标',exact:true});
      if(!await goals.isVisible()) await page.getByRole('button',{name:'切换对话侧栏',exact:true}).click();
      await goals.click();
      const dialog=page.getByRole('dialog',{name:'我的目标',exact:true});
      await dialog.waitFor();
      await dialog.getByText('正在整理目标档案…',{exact:true}).waitFor({state:'hidden'});
      const count=await dialog.locator('[data-goal-id]').count();
      await page.screenshot({path:path.join(out,`goals-${actor}-${width}.png`),scale:'css'});
      if(count) {
        await dialog.locator('[data-goal-id]').first().click();
        await dialog.getByText(/历史资料只读/).waitFor();
        await dialog.getByText('正在加载历史…',{exact:true}).waitFor({state:'hidden'});
        await dialog.getByRole('button',{name:/执行与复盘/}).click();
        await page.screenshot({path:path.join(out,`cycles-${actor}-${width}.png`),scale:'css'});
      }
      const goalText=await dialog.innerText();
      await page.getByRole('button',{name:'关闭目标总览'}).click();
      await dialog.waitFor({state:'hidden'});
      await page.reload({waitUntil:'domcontentloaded'});
      await page.getByRole('button',{name:/账号菜单/}).waitFor();
      await page.getByPlaceholder('慢慢说…',{exact:true}).waitFor({timeout:30000});
      results.push({width,login:true,refreshSession:true,goals:count,fit,errors,goalText});
      await ctx.close();
    }
  } finally {await browser.close();}
  await fs.writeFile(path.join(out,`browser-${actor}.json`),JSON.stringify(results,null,2));
  console.log(JSON.stringify(results));
})().catch(e=>{console.error(e.message);process.exitCode=1;});
