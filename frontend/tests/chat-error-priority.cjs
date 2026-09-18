// Exercise the real TypeScript SSE client without a server, account or model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/api.ts'), 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
}).outputText;
async function run(frames) {
  const exports = {};
  const errors = []; let completed = 0; let reply = '';
  const sandbox = {exports, process: {env: {}}, TextDecoder,
    require: () => ({apiHeaders: () => ({}), checkAuthentication: () => {}}),
    fetch: async () => new Response(frames.map(([event, data]) =>
      `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`).join(''))};
  vm.runInNewContext(source, sandbox);
  let thrown;
  try { await exports.streamChat({message: 'synthetic'}, {
    onError: e => errors.push(e), onDone: () => completed++, onDelta: s => reply += s,
  }); } catch (e) { thrown = e.message; }
  return {errors, completed, reply, thrown};
}
(async () => {
  const upstream = '豆包模型已达到安全体验模式的推理额度上限，服务已暂停。';
  const error = ['error', {detail: upstream}];
  const failed = ['persisted', {saved: false}];
  const delta = ['delta', {text: '部分回复'}];
  let r = await run([error, failed]);
  assert.deepEqual(r.errors, [upstream]); assert.equal(r.completed, 1);
  r = await run([failed]); assert.match(r.errors[0], /未返回回复正文/);
  r = await run([['reasoning_delta', {text: '只有思考'}], failed]);
  assert.match(r.errors[0], /未返回回复正文/);
  r = await run([delta, failed]); assert.match(r.errors[0], /保存失败/);
  r = await run([delta, error, failed]);
  assert.ok(r.errors.at(-1).includes(upstream)); assert.match(r.errors.at(-1), /保存失败/);
  r = await run([error]); assert.equal(r.thrown, upstream);
  r = await run([delta, ['persisted', {saved: true}]]);
  assert.deepEqual(r.errors, []); assert.equal(r.completed, 1); assert.equal(r.reply, '部分回复');
  r = await run([delta, error, ['persisted', {saved: true}]]);
  assert.deepEqual(r.errors, [upstream]);
  console.log('PASS: 8 SSE error-priority cases (quota, empty, reasoning-only, save failure, partial failure, EOF, success, saved partial).');
})().catch(e => {console.error(e); process.exitCode = 1;});
