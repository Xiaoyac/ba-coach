const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const file = path.resolve(__dirname, '../lib/plan-display.ts');
const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context, { filename: file });
const format = value => JSON.parse(JSON.stringify(context.exports.copingPlanDisplayValue(value)));
const none = { status: 'not_applicable', source_message_id: 42, source_quote: '暂时没有什么困难' };

assert.equal(format([none]), '暂无需要应对的困难');
for (const missing of [null, undefined, '', '  ', [], {}, { status: 'unknown' }]) {
  assert.equal(format(missing), null, 'unknown is still unknown, not no barriers');
}
for (const invalid of [
  { ...none, source_message_id: 0 }, { ...none, source_message_id: true },
  { ...none, source_message_id: '42' }, { ...none, source_quote: '' },
  { status: 'not_applicable' },
]) assert.equal(format([invalid]), null, 'unverified marker cannot assert no barriers');
assert.equal(format(none), null, 'only the agreed single-item list is an explicit N/A record');
assert.equal(format([none, none]), null, 'duplicate marker is not the canonical sourced state');
assert.equal(format([[none]]), null, 'a nested marker is not the canonical sourced state');
assert.equal(format([{ ...none, barrier: '下雨', plan: '在家走动' }]), null, 'conflicting N/A cannot assert no barriers');
const real = { barrier: '下雨', plan: '在家走动' };
assert.deepEqual(format([real]), [real], 'normal coping plans preserve their existing rendering');
assert.deepEqual(format([none, real]), [real], 'a real barrier is not concealed by an inconsistent N/A item');
assert.deepEqual(format([{ ...real, source_message_id: 10, source_quote: '私有溯源文字' }]), [real], 'evidence fields do not appear in the card');
assert.equal(format('感觉累了就减少时长'), '感觉累了就减少时长', 'legacy plain text is preserved');

// Render the real archive PlanCard, so helper correctness cannot hide a missed
// integration or evidence fields exposed by the component's generic renderer.
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const componentFile = path.resolve(__dirname, '../components/GoalOverview.tsx');
const componentCode = ts.transpileModule(fs.readFileSync(componentFile, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const component = { exports: {}, require(name) {
  if (name === '@/lib/plan-display') return context.exports;
  if (name === '@/lib/program') return { scheduleKindLabel: { unspecified: '安排待明确' } };
  if (name.startsWith('@/')) return {};
  return require(name);
} };
vm.runInNewContext(`${componentCode}\nexports.testPlanCard = PlanCard;`, component, { filename: componentFile });
const htmlFor = coping => renderToStaticMarkup(React.createElement(component.exports.testPlanCard, {
  plan: { id: 'plan-1', version_no: 1, record_status: 'confirmed', activity_content: '散步',
    created_at: '2026-10-03T00:00:00Z', updated_at: '2026-10-03T00:00:00Z',
    barrier_coping_plan: coping },
}));
const explicitHtml = htmlFor([none]);
assert.ok(explicitHtml.includes('暂无需要应对的困难'));
for (const privateText of ['source_message_id', 'source_quote', 'not_applicable', none.source_quote]) {
  assert.equal(explicitHtml.includes(privateText), false, 'source metadata is not rendered');
}
const unknownHtml = htmlFor(null);
assert.equal(unknownHtml.includes('暂无需要应对的困难'), false);
assert.match(unknownHtml, /应对办法<\/dt><dd[^>]*>尚未记录<\/dd>/);
const realHtml = htmlFor([real]);
assert.ok(realHtml.includes('困难：下雨；应对：在家走动'));
console.log('PASS: sourced N/A, unknown and invalid markers, real plans, legacy text, real archive render, evidence privacy');
