/** Browser regression with explicit synthetic API fixtures. No model or production calls.
 * Run against a LOCAL built frontend; backend behavior is covered by pytest separately.
 * Requires Playwright (bundled Codex runtime or a development installation).
 */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { mkdir, readFile } = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const base = process.env.WORKBENCH_TEST_URL || "http://127.0.0.1:3108";
  assert.equal(new URL(base).hostname, "127.0.0.1", "Only a loopback test server is allowed");
  const output = path.resolve(process.env.WORKBENCH_TEST_OUTPUT || "../.test-tmp/workbench-browser");
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.WORKBENCH_BROWSER_CHANNEL ? {channel:process.env.WORKBENCH_BROWSER_CHANNEL} : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], cases = [], runs = [];
  const actual = process.env.WORKBENCH_WORKBOOK_RESULT ? JSON.parse(await readFile(process.env.WORKBENCH_WORKBOOK_RESULT,"utf8")) : null;
  let reviews = [], executed = 0;
  page.on("pageerror", e => errors.push(e.message));
  await page.addInitScript(() => localStorage.setItem("psy-auth-token", "synthetic-ui-test-token"));
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url()), method = req.method();
    if (url.hostname !== "127.0.0.1") return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const p = url.pathname.replace("/api/admin/evaluations", "");
    const body = method === "POST" || method === "PUT" ? req.postDataJSON() : null;
    const json = (data, status = 200) => route.fulfill({status, contentType:"application/json", body:JSON.stringify(data)});
    if (url.pathname === "/api/auth/me") return json({username:"fixture",nickname:"测试同事",role:"admin",profile_uuid:"synthetic",
      email:"fixture@example.invalid",email_required:false,email_verified:true,display_id:"测试同事#10001"});
    if (url.pathname === "/api/conversations") return json(method === "GET" ? [] : {session_id:"ui-only",title:"测试",updated_at:new Date().toISOString(),messages:[],next_module:"module_1"});
    if (url.pathname.endsWith("/revision")) return json({revision:1});
    if (url.pathname.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:""});
    if (url.pathname === "/api/modules") return json({modules:["module_1","module_2","module_3","module_4"]});
    if (p === "/cases" && method === "GET") return json({cases});
    if (p === "/cases" && method === "POST") {
      cases.push({...body,id:randomUUID(),revision:1,updated_at:new Date().toISOString()}); return json(cases.at(-1),201);
    }
    if (p.startsWith("/cases/") && method === "PUT") {
      const i = cases.findIndex(c => c.id === p.split("/")[2]);
      cases[i] = {...cases[i],...body,revision:body.revision+1}; return json(cases[i]);
    }
    if (p.endsWith("/runs") && method === "POST") {
      const c = cases.find(c => c.id === p.split("/")[2]);
      const parent = runs.find(r => r.id === body.parent_run_id);
      const input = body.message || c.user_input;
      const reply = "【界面测试模拟回答】可以从你愿意尝试的一件小事开始，先安排五分钟，再看看感受。";
      const run = {id:body.request_id,case_id:c.id,parent_run_id:parent?.id || null,status:"completed",case_snapshot:parent?.case_snapshot || {...c},
        provider:body.provider,model:"ui-fixture-not-real-model",input_text:input,reply,created_at:new Date().toISOString(),error_code:null,
        transcript:[...(parent?.transcript || []),{role:"user",content:input},{role:"assistant",content:reply}],
        metrics:{duration_ms:1200,usage:{input_tokens:30,output_tokens:20},retrieved:[],telemetry:{answer_validator:{status:"passed",version:"answer-rules-v1",duration_ms:0.1,findings:[]},retrieval:{gate:{retrieve:true,reason:"substantive_or_unknown"},returned:0}}}};
      runs.unshift(run); executed++; return json(run,202);
    }
    if (p === "/runs") return json({runs:runs.map(r=>({...r,latest_verdict:reviews.find(v=>v.run_id===r.id)?.verdict || "unreviewed"})),next_offset:null});
    if (p.startsWith("/runs/") && p.endsWith("/reviews")) {
      const review = {...body,id:randomUUID(),run_id:p.split("/")[2],reviewer_id:1,created_at:new Date().toISOString()};
      reviews.unshift(review); return json({id:review.id},201);
    }
    if (p.startsWith("/runs/")) return json({...runs.find(r => r.id === p.split("/")[2]),reviews:reviews.filter(r => r.run_id === p.split("/")[2])});
    if (p.endsWith(".csv")) return route.fulfill({contentType:"text/csv",body:"\uFEFFCase ID,实际回答\nM1-001,模拟导出\n"});
    if (p === "/import/preview" && actual && body.filename.endsWith(".xlsx")) return json(actual.preview);
    if (p === "/import/preview") return json({sheets:["CSV"],selected_sheet:"CSV",errors:[],cases:[{
      case_code:"M2-001",module:"module_2",user_type:"低动力型",scenario:"希望先设定一个小目标",user_input:"我愿意试试，但不知道该做什么。",
      expected_behavior:"协助选择一个具体且可执行的目标",extra_columns:{}}]});
    if (p === "/import") { cases.push(...body.cases.map(c => ({...c,id:randomUUID(),revision:1,updated_at:new Date().toISOString()}))); return json({imported:body.cases.length},201); }
    return json({});
  });
  try {
    await page.goto(base);
    await page.getByRole("button",{name:"测试工作台",exact:true}).click();
    await page.getByRole("button",{name:"新增用例"}).click();
    for (const [label,value] of [["Case ID","M1-001"],["用户类型","积极型：愿意尝试行动"],["测试场景","主动表达情绪困扰，希望找到解决方法"],
      ["用户起始输入","我最近总是提不起劲，但还挺想改变的，不知道从哪里开始。"],["AI 应达到的目标","共情；聚焦一个具体事件；完成 ABC 梳理；清楚解释 BA 原理。"]]) {
      await page.getByLabel(label,{exact:true}).fill(value);
    }
    await page.getByRole("button",{name:"保存用例",exact:true}).click();
    await page.getByRole("cell",{name:"M1-001",exact:true}).waitFor();
    await page.getByRole("button",{name:"编辑",exact:true}).click();
    await page.getByLabel("测试场景",{exact:true}).fill("主动表达情绪困扰，希望找到解决方法（修订）");
    await page.getByRole("button",{name:"保存用例",exact:true}).click();
    await page.getByText("主动表达情绪困扰，希望找到解决方法（修订）",{exact:true}).waitFor();
    await page.locator('input[type="file"]').setInputFiles({name:"评测集.csv",mimeType:"text/csv",buffer:Buffer.from("Case ID,模块,用户类型,测试场景,用户起始输入,AI应达到的目标\nM2-001,M2,低动力型,小目标,愿意试试,协助选择目标\n")});
    await page.getByRole("button",{name:"确认导入",exact:true}).click();
    await page.getByRole("cell",{name:"M2-001",exact:true}).waitFor();
    assert.equal(cases[0].revision,2);
    await page.getByLabel("筛选模块").selectOption("module_2");
    assert.equal(await page.getByRole("cell",{name:"M1-001",exact:true}).count(),0);
    await page.getByLabel("筛选模块").selectOption("all");
    await page.screenshot({path:path.join(output,"cases-desktop.png"),fullPage:true});
    await page.evaluate(() => document.documentElement.classList.add("theme-warm"));
    await page.screenshot({path:path.join(output,"cases-desktop-warm.png"),fullPage:true,animations:"disabled"});
    await page.evaluate(() => document.documentElement.classList.remove("theme-warm"));
    await page.getByLabel("选中当前筛选前20条").check();
    await page.getByRole("button",{name:"运行选中 (2/20)",exact:true}).click();
    await page.getByText("已执行 2 条。执行状态和质量结论请在运行记录中查看、评审。",{exact:true}).waitFor();
    await page.getByRole("button",{name:"查看 / 评审",exact:true}).nth(1).waitFor();
    assert.equal(executed,2);
    await page.getByLabel("筛选运行状态").selectOption("failed");
    await page.getByRole("heading",{name:"没有匹配的结果"}).waitFor();
    await page.getByRole("button",{name:"重置筛选",exact:true}).click();
    await page.getByRole("button",{name:"查看 / 评审",exact:true}).first().click();
    await page.getByText("程序校验通过（不等于语义正确）",{exact:true}).waitFor();
    await page.getByLabel("评审结论").selectOption("pass");
    await page.getByLabel("评审备注").fill("界面测试：验收目标逐项核对，记录依据。");
    await page.getByRole("button",{name:"保存评审",exact:true}).click();
    await page.getByText("界面测试：验收目标逐项核对，记录依据。",{exact:true}).waitFor();
    await page.getByLabel("继续追问",{exact:true}).fill("那我今天先走五分钟可以吗？");
    await page.getByRole("button",{name:"发送追问并自动记录",exact:true}).click();
    await page.getByText("那我今天先走五分钟可以吗？",{exact:true}).waitFor();
    assert.equal(runs[0].transcript.length,4);
    await page.screenshot({path:path.join(output,"run-desktop.png"),fullPage:true});
    await page.getByRole("button",{name:"返回列表",exact:true}).click();
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button",{name:"导出记录",exact:true}).click();
    assert.equal((await downloaded).suggestedFilename(),"测试记录.csv");
    await page.getByRole("button",{name:"评测集 (2)",exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    const bounds = await page.getByRole("dialog").boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await page.screenshot({path:path.join(output,"cases-mobile.png"),fullPage:true});
    if (actual) {
      assert(process.env.WORKBENCH_XLSX,"Original workbook path required");
      cases.splice(0,cases.length,...actual.saved_cases);
      runs.splice(0,runs.length,actual.run);
      await page.setViewportSize({width:1600,height:1000});
      await page.getByRole("button",{name:"刷新",exact:true}).click();
      await page.getByRole("button",{name:"评测集 (41)",exact:true}).waitFor();
      await page.screenshot({path:path.join(output,"actual-workbook-cases.png"),fullPage:true});
      await page.locator('input[type="file"]').setInputFiles(process.env.WORKBENCH_XLSX);
      await page.getByText("41 条有效用例，6 处错误。确认后统一保存，不覆盖已有 Case ID。",{exact:true}).waitFor();
      assert(await page.getByRole("button",{name:"确认导入",exact:true}).isDisabled());
      await page.screenshot({path:path.join(output,"actual-workbook-validation.png"),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:"passed",originalWorkbookChecks:actual ? ["41 persisted case fixtures displayed","original XLSX upload","6 validation errors visibly block confirmation"] : [],checks:["admin entry","six-column create/edit","import preview/confirm","module filter","batch two runs","human review","followup history","CSV download","mobile viewport","no browser exceptions"],screenshots:output}));
  } catch (e) {
    await page.screenshot({path:path.join(output,"failure.png"),fullPage:true});
    console.error((await page.locator("body").innerText()).slice(-9000));
    throw e;
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
