const assert = require('node:assert/strict');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport:{width,height:900}});
      const errors=[]; page.on('pageerror',error=>errors.push(error.message));
      let reasoning = null;
      const raw='{"decision":"not_needed","selections":[],"note":"本轮无需新知识 <script>window.injected=true</script>"}';
      await page.route('**/api/**',route=>route.fulfill({json:{snapshot_version:2,title:'中介调试验收',created_at:'2026-10-08T10:00:00Z',messages:[{
        id:1,role:'assistant',content:'合成回复。',knowledge_references:{
          available:true,module:'module_2',recalled:[{id:'one',source:'BA',text:'合成知识'}],provided:[],
          mediator_status:'completed',mediator_reason:'not_needed',mediator_note:'本轮无需新知识',
          mediator_duration_ms:1500,mediator_model:'deepseek-v4.1-flash',
          mediator_response_content:raw,mediator_reasoning_content:reasoning}
      }]}}));
      const open = async () => {
        await page.getByRole('button',{name:'调试详情',exact:true}).click().catch(async error => { console.error('PAGE',page.url(),(await page.locator('body').innerText()).slice(0,2000),errors); throw error; });
        await page.getByRole('button',{name:'中介节点使用建议内容',exact:true}).click();
      };
      await page.goto((process.env.MEDIATOR_TEST_URL || 'http://127.0.0.1:3152')+'/share/synthetic-hybrid');
      await open();
      await page.getByText(raw,{exact:true}).waitFor();
      assert.equal(await page.getByText('中介思考（已保存）',{exact:true}).count(),0);
      assert.equal(await page.evaluate(()=>window.injected),undefined);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:`/tmp/hybrid-mediator-${width}.png`,fullPage:true});
      reasoning='合成供应商思考内容';
      await page.reload(); await open();
      await page.getByText('中介思考（已保存）',{exact:true}).click();
      await page.getByText(reasoning,{exact:true}).waitFor();
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PASS: saved output visible, optional reasoning separate, no script execution, desktop/mobile fit');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
