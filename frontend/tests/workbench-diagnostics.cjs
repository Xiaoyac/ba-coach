// Render the real diagnostics component with synthetic telemetry. No browser,
// model, backend, or new test dependency is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const filename = path.resolve(__dirname, '../components/TestRunDiagnostics.tsx');
const javascript = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const component = { exports: {} };
vm.runInNewContext(javascript, { module: component, exports: component.exports, require: createRequire(filename) }, { filename });
const diagnostics = (telemetry) => renderToStaticMarkup(React.createElement(component.exports.default, { metrics: { telemetry } }));
const timing = (metrics) => renderToStaticMarkup(React.createElement(component.exports.RunTimingSummary, { metrics }));

const old = diagnostics({ prompt_version: 'legacy-combined-hash', answer_validator: { status: 'passed' } });
assert.match(old, /没有实际调用时间线/);
assert.match(old, /没有生成前 Router 数据/);
assert.match(old, /旧记录仅有组合指纹：legacy-combined-hash/);
assert.doesNotMatch(old, /模型回复原样展示|本轮暂停流程推进/);
const oldTiming = timing({ duration_ms: 500, telemetry: { time_to_first_content_token_ms: 7 } });
assert.match(oldTiming, /500 ms/);
assert.doesNotMatch(oldTiming, /7 ms/); // A generated token is not a display measurement.
assert.match(oldTiming, /未记录/);
assert.match(timing({ duration_ms: 0, telemetry: { time_to_first_visible_content_ms: 0 } }), /0 ms/);

const unchanged = diagnostics({ answer_validator: { status: 'review', display_source: 'original_model_reply', llm_calls: 0 } });
assert.match(unchanged, /模型回复原样展示/);
assert.doesNotMatch(unchanged, /模型生成的原文|用户实际看到的文字/);
assert.doesNotMatch(unchanged, /本轮暂停流程推进/);

const changed = diagnostics({ answer_validator: {
  status: 'corrected', original_reply: '<script>original</script>', replacement_reply: '实际显示文字',
  replacement_source: 'safety_check', progression_held: false,
  findings: [{ code: 'clinical_claim_needs_review', severity: 'block' }],
} });
assert.match(changed, /模型生成的原文/);
assert.match(changed, /用户实际看到的文字/);
assert.match(changed, /safety_check/);
assert.match(changed, /回复检查未暂停流程推进/);
assert.doesNotMatch(changed, /本轮暂停流程推进|<script>/);
assert.match(changed, /&lt;script&gt;/);
assert.match(diagnostics({ answer_validator: { status: 'corrected', progression_held: true } }), /本轮暂停流程推进/);

const traced = diagnostics({
  execution_timeline: [
    { stage: 'clinical_extraction', start_ms: 900, duration_ms: 30, status: 'completed', after_display: true },
    { stage: 'main_generation', start_ms: 100, duration_ms: 600, status: 'completed' },
    { stage: 'retrieval', start_ms: 20, duration_ms: 90, status: 'timeout', retry_count: 1 },
  ],
  router_pre_reply: { input_sources: ['当前用户输入', '已提交状态'], selected_module: 'module_2', database_module: 'module_1' },
  prompt_sources: [{ source: 'global', version: 'global-0924' }, { source: 'module_2', version: 'm2-0924' }],
});
assert.ok(traced.indexOf('知识检索') < traced.indexOf('主回复生成'));
assert.ok(traced.indexOf('主回复生成') < traced.indexOf('事实抽取'));
assert.match(traced, /回复展示后继续处理/);
assert.match(traced, /超时/);
assert.match(traced, /重试次数：1/);
assert.match(traced, /M2 \/ M1/);
assert.match(traced, /当前用户输入、已提交状态/);
assert.match(traced, /global-0924/);
assert.match(traced, /m2-0924/);

const normalFields = diagnostics({ extraction: { status: 'completed', candidate: { value: 'synthetic fact' } } });
assert.doesNotMatch(normalFields, /<details[^>]*\sopen=/);
const failedFields = diagnostics({ database_write: { status: 'rejected', reason: 'stale_cycle' } });
assert.match(failedFields, /<details[^>]*\sopen=/);
assert.match(failedFields, /stale_cycle/);
console.log('PASS: legacy unknowns, real timeline ordering, post-display work, prompt/router sources, conditional reply comparison, actual progression hold, and diagnostic expansion');

const actualInput = diagnostics({ main_input: { system: '<system>observed</system>', messages: [{ role: 'user', content: '真实输入' }] } });
assert.match(actualInput, /主模型实际输入/);
assert.match(actualInput, /&lt;system&gt;/);
assert.doesNotMatch(actualInput, /<details[^>]*\sopen=/);
assert.doesNotMatch(old, /主模型实际输入/);
assert.match(timing({telemetry: {first_visible_measurement:'server_sse_release',time_to_first_visible_content_ms:10}}), /服务端首次发送可见回复/);

const rawTrace = diagnostics({ reply_trace: { raw_model_reply: '<think>internal</think>{"chat_reply":"hi"}', normalized_reply: 'hi', visible_reply: 'hi', display_source: 'normalized_model_reply' } });
assert.match(rawTrace, /原始输出与展示结果/);
assert.match(rawTrace, /模型原始输出/);
assert.match(rawTrace, /格式整理后的回复/);
assert.match(rawTrace, /实际展示内容/);
assert.match(rawTrace, /&lt;think&gt;/);
assert.doesNotMatch(rawTrace, /<think>|<details[^>]*\sopen=/);
assert.doesNotMatch(old, /原始输出与展示结果/);
