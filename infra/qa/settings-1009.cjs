// Synthetic API fixtures only; exercise the actual Next.js routes and forms.
const {chromium}=require('playwright');const assert=require('node:assert/strict');const fs=require('node:fs/promises');const path=require('node:path');
const {installFixtures}=require('./goal-formulation-1003.cjs');
const base=process.argv[2]||'http://127.0.0.1:3170';const out=path.resolve('.test-tmp/settings-1009/browser');
(async()=>{await fs.mkdir(out,{recursive:true});const browser=await chromium.launch({headless:true,channel:'chrome'});const results=[];
try{for(const [width,role] of [[1440,'user'],[390,'user'],[360,'user'],[1440,'admin'],[390,'admin']]){
 const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce',serviceWorkers:'block'});
 await context.addInitScript(({fixture,role})=>{
  const old=localStorage.getItem('psy-theme');(0,eval)(`(${fixture})`)({role});if(old)localStorage.setItem('psy-theme',old);
  const f=window.settingsFixture={writes:[],failProfile:true,failSave:true,failPassword:true,account:{username:'settings-demo',nickname:'小安',display_id:'小安#12345',tag:'12345',role,profile_uuid:'settings-demo',email:'old@example.test',email_verified:false,email_delivery_available:true,email_required:false,birth_date_required:false},profile:{nickname:'小安',tag:'12345',display_id:'小安#12345',birth_date:'2000-01-01',age:26,physical_condition:[],behavior_taboo:[],content_taboo:[],supporters:[],can_manage_models:false}};
  const original=window.fetch;window.fetch=async(input,options={})=>{
   const url=new URL(typeof input==='string'?input:input.url,location.href);const p=url.pathname;const method=options.method||'GET';const json=(value,status=200)=>Response.json(value,{status});
   if(p==='/api/auth/me')return json(f.account);
   if(p==='/api/profile'){
    if(method==='GET')return f.failProfile?json({detail:'模拟档案读取失败'},503):json(f.profile);
    f.writes.push({path:p,body:JSON.parse(options.body)});if(f.failSave)return json({detail:'模拟保存失败'},503);Object.assign(f.profile,JSON.parse(options.body));f.account.nickname=f.profile.nickname;return json(f.profile);
   }
   if(p==='/api/auth/email'){const body=JSON.parse(options.body);f.writes.push({path:p,body});f.account.email=body.email;return json(f.account);}
   if(p==='/api/auth/email/verification/resend'){f.writes.push({path:p});return json({message:'验证邮件已发送'});}
   if(p==='/api/auth/password'){f.writes.push({path:p,body:JSON.parse(options.body)});return f.failPassword?json({detail:'当前密码不正确'},400):json({message:'ok'});}
   if(p==='/api/auth/logout'){f.writes.push({path:p});return json({message:'ok'});}
   return original(input,options);
  };
 },{fixture:installFixtures.toString(),role});
 const escaped=[],errors=[];await context.route('**/api/**',r=>{escaped.push(r.request().url());return r.fulfill({status:599,body:'{}'});});
 const page=await context.newPage();page.setDefaultTimeout(12000);page.setDefaultNavigationTimeout(60000);page.on('pageerror',e=>errors.push(e.message));
 const shot=async name=>{assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await page.evaluate(()=>Array.from(document.querySelectorAll('[aria-label="设置页面"] input')).filter(e=>e.getClientRects().length).every(e=>{const b=e.getBoundingClientRect();return b.x>=0&&b.right<=innerWidth+1;})));
  await page.screenshot({path:path.join(out,`${name}-${role}-${width}.png`)});};
 const nav=page.getByRole('navigation',{name:'设置导航',exact:true});
 try{
 await page.goto(base);const input=page.getByRole('textbox',{name:'Message',exact:true});await input.fill('尚未发送的聊天草稿');await input.blur();
 if(!await page.locator('#chat-header-tools').isVisible())await page.locator('button[aria-controls="chat-header-tools"]').click();
 await page.getByRole('button',{name:/账号菜单/}).click();
 const menu=page.getByRole('menu');assert.equal(await menu.getByRole('menuitem',{name:'我的档案',exact:true}).count(),0);assert.equal(await menu.getByRole('menuitem',{name:'退出登录',exact:true}).count(),0);
 const settings=menu.getByRole('menuitem',{name:'设置',exact:true});assert.equal(await settings.locator('svg').count(),1);await settings.click();await nav.waitFor();assert.equal(new URL(page.url()).pathname,'/settings');
 for(const [theme,name] of [['porcelain','青花瓷'],['github','GitHub Dark'],['warm','温暖浅色'],['dark','黑金夜色']]){
  await page.getByRole('radio',{name:new RegExp(name)}).click();await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>localStorage.getItem('psy-theme')),theme);
  if(theme==='porcelain'||theme==='github')await shot(theme);
 }
 assert.equal(await nav.getByRole('button',{name:'外观',exact:true}).locator('svg circle').count(),1);
 const navBox=await nav.boundingBox();const contentBox=await page.locator('#settings-appearance').boundingBox();assert(navBox.x+navBox.width<=contentBox.x);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await nav.getByRole('button',{name:'我的档案',exact:true}).click();await page.getByText('档案服务暂时不可用，请稍后重新加载。',{exact:true}).waitFor();
 await page.evaluate(()=>window.settingsFixture.failProfile=false);await page.getByRole('button',{name:/重新加载/}).click();const nickname=page.getByRole('textbox',{name:'公开昵称',exact:true});await nickname.fill('修改后的昵称');
 await nav.getByRole('button',{name:'邮箱与找回密码',exact:true}).click();await page.getByRole('textbox',{name:'邮箱',exact:true}).fill('new@example.test');
 await nav.getByRole('button',{name:'我的档案',exact:true}).click();assert.equal(await nickname.inputValue(),'修改后的昵称');await page.getByRole('button',{name:'保存修改',exact:true}).click();await page.getByText('档案服务暂时不可用，请稍后重新加载。',{exact:true}).waitFor();assert.equal(await nickname.inputValue(),'修改后的昵称');await page.evaluate(()=>window.settingsFixture.failSave=false);await page.getByRole('button',{name:'保存修改',exact:true}).click();await page.getByRole('button',{name:'已保存',exact:true}).waitFor();await shot('profile');
 await nav.getByRole('button',{name:'邮箱与找回密码',exact:true}).click();assert.equal(await page.getByRole('textbox',{name:'邮箱',exact:true}).inputValue(),'new@example.test');await page.getByRole('button',{name:'保存邮箱',exact:true}).click();await page.getByText(/邮箱已保存；/).waitFor();await page.getByRole('button',{name:'重新发送验证邮件',exact:true}).click();await page.getByText('验证邮件已发送',{exact:true}).waitFor();await shot('email');
 await nav.getByRole('button',{name:'修改密码',exact:true}).click();await page.getByLabel('当前密码',{exact:true}).fill('synthetic-old');await page.getByLabel(/^新密码/).fill('synthetic-new');await page.getByLabel('再输入一次新密码',{exact:true}).fill('synthetic-new');await page.getByRole('button',{name:'确认修改',exact:true}).click();await page.getByText('当前密码不正确',{exact:true}).waitFor();await page.evaluate(()=>window.settingsFixture.failPassword=false);await page.getByRole('button',{name:'确认修改',exact:true}).click();await page.getByText(/密码已更新/).waitFor();
 await page.getByRole('link',{name:'返回对话',exact:true}).click();await input.waitFor();assert.equal(await input.inputValue(),'尚未发送的聊天草稿');if(role==='admin'){
  if(!await page.locator('#chat-header-tools').isVisible())await page.locator('button[aria-controls="chat-header-tools"]').click();
  const sidebar=page.locator('aside');if(!await sidebar.getByRole('link',{name:'设置',exact:true}).isVisible())await page.getByRole('button',{name:'切换对话侧栏',exact:true}).click();
  await sidebar.getByRole('link',{name:'设置',exact:true}).click();await nav.waitFor();
 }else{await page.goBack();await nav.waitFor();}
 await page.reload();await nav.waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('psy-theme')),'dark');
 await nav.getByRole('button',{name:'退出登录',exact:true}).click();assert.equal(await page.evaluate(()=>window.settingsFixture.writes.length),0);await page.getByRole('button',{name:'退出当前账号',exact:true}).click();await nav.waitFor({state:'hidden'});assert.equal(await page.evaluate(()=>localStorage.getItem('psy-auth-token')),null);
 assert.deepEqual(escaped,[]);assert.deepEqual(errors,[]);results.push({width,role,route:true,leftNavigation:true,fiveSections:true,sunIcon:true,profileRetryAndSave:true,emailSaveAndResend:true,passwordRetryAndSave:true,draftPreserved:true,refresh:true,logout:true});
 }catch(e){await shot('failure');throw e;}finally{await context.close();}
}
const guest=await browser.newContext({serviceWorkers:'block'});const guestPage=await guest.newPage();
 await guest.route('**/api/**',r=>r.fulfill({status:401,body:'{}'}));await guestPage.goto(base+'/settings');await guestPage.locator('button[type=submit]').filter({hasText:'登录'}).waitFor();assert.equal(await guestPage.getByRole('navigation',{name:'设置导航',exact:true}).count(),0);await guest.close();
 await fs.writeFile(path.join(out,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
