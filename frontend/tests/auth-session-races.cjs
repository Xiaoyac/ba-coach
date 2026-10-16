const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const path = require('node:path');
const source = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../lib/auth.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function fixture() {
  const storage = new Map([['psy-auth-token', 'a']]), calls = [];
  const context = { exports: {}, process: { env: {} }, localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) }, fetch: (url, options) => { const d = deferred(); calls.push({ url, options, ...d }); return d.promise; }, require: () => ({ clearLocalPush: async () => {} }) };
  vm.runInNewContext(source, context); return { auth: context.exports, storage, calls };
}
(async () => {
  {
    const f = fixture(); const request = f.auth.fetchMe(); f.storage.set('psy-auth-token', 'b');
    f.calls[0].resolve({ status: 401 }); assert.equal(await request, null); assert.equal(f.auth.getToken(), 'b', 'late expired account cannot erase new credential');
  }
  {
    const f = fixture(); const request = f.auth.fetchMe(); f.calls[0].resolve({ status: 401 }); await request;
    assert.equal(f.auth.getToken(), null, 'current expired credential is removed');
  }
  {
    const f = fixture(); const request = f.auth.logout(); f.storage.set('psy-auth-token', 'b');
    await new Promise(r => setImmediate(r)); assert.equal(f.calls[0].options.headers.Authorization, 'Bearer a', 'logout is bound to initiating account');
    f.calls[0].resolve({ status: 200 }); await request; assert.equal(f.auth.getToken(), 'b');
  }
  {
    const f = fixture(); const request = f.auth.logout(); await new Promise(r => setImmediate(r)); f.calls[0].resolve({ status: 200 }); await request;
    assert.equal(f.auth.getToken(), null);
  }
  console.log('4 auth credential race cases passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
