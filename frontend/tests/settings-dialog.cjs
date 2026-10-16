/* Node-only component/helper regression checks. No browser, network or real APIs.
 * The fake native dialog checks our lifecycle decisions, not browser focus trapping. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const postcss = require('postcss');
const root = path.resolve(__dirname, '..');
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }

function loader(overrides = {}, globals = {}) {
  const cache = new Map();
  const stubs = new Map();
  const stub = name => {
    if (!stubs.has(name)) {
      const Component = () => null;
      Component.displayName = name;
      stubs.set(name, { default: Component });
    }
    return stubs.get(name);
  };
  function load(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(code, { ...globals, exports: module.exports, module, require(name) {
      if (Object.hasOwn(overrides, name)) return overrides[name];
      if (name === 'react') return React;
      if (name === 'react/jsx-runtime') return require('react/jsx-runtime');
      if (name === 'next/navigation') return { useRouter: () => ({ replace() {} }) };
      if (name === 'next/link') return { default: props => React.createElement('a', props, props.children) };
      if (name === '@/components/icons') return new Proxy({}, { get: () => () => null });
      if (['@/components/OverallStyleSelector', '@/lib/overallStyle', '@/lib/theme'].includes(name)) {
        return load(name.slice(2) + (name.includes('/components/') ? '.tsx' : '.ts'));
      }
      if (name.startsWith('@/components/')) return stub(name);
      if (name === '@/lib/settingsDialog') return load('lib/settingsDialog.ts');
      throw new Error(`Unexpected import: ${name}`);
    } }, { filename: file });
    return module.exports;
  }
  return { load, stubs };
}

class Element {
  constructor({ connected = true, visible = true, blocked = false, inside = false, disabled = false } = {}) {
    Object.assign(this, { isConnected: connected, visible, blocked, inside, disabled, focusCalls: [] });
  }
  matches(selector) { assert.equal(selector, ':disabled'); return this.disabled; }
  closest() { return this.blocked ? {} : null; }
  getClientRects() { return this.visible ? [{}] : []; }
  focus(options) { this.focusCalls.push(options); }
}
class Dialog extends Element {
  constructor() { super(); this.open = false; this.shown = 0; this.closed = 0; }
  showModal() { assert.equal(this.open, false); this.open = true; this.shown++; }
  close() { assert.equal(this.open, true); this.open = false; this.closed++; }
  contains(element) { return element === this || element.inside; }
}
function nativeFixture(previous = new Element(), candidates = []) {
  const document = { activeElement: previous, body: new Element(), documentElement: new Element(), querySelectorAll: () => candidates };
  const { showSettingsDialog } = loader({}, { document, HTMLElement: Element }).load('lib/settingsDialog.ts');
  return { document, dialog: new Dialog(), previous, showSettingsDialog };
}

check('native helper opens once, closes and restores opener without scrolling', () => {
  const f = nativeFixture(); const close = f.showSettingsDialog(f.dialog);
  assert.equal(f.dialog.shown, 1); close();
  assert.equal(f.dialog.closed, 1);
  assert.equal(f.previous.focusCalls[0].preventScroll, true);
});
check('already-open dialog is not shown twice; effect cleanup/replay can reopen', () => {
  const f = nativeFixture(); f.dialog.open = true;
  f.showSettingsDialog(f.dialog)(); assert.equal(f.dialog.shown, 0);
  f.showSettingsDialog(f.dialog)(); assert.equal(f.dialog.shown, 1);
  assert.equal(f.dialog.closed, 2);
});
check('disappeared menu opener falls back past hidden, inert, disabled and dialog controls', () => {
  const excluded = [new Element({ visible: false }), new Element({ blocked: true }), new Element({ inside: true }), new Element({ disabled: true })];
  const trigger = new Element();
  const f = nativeFixture(new Element({ connected: false }), [...excluded, trigger]);
  f.showSettingsDialog(f.dialog)();
  assert.equal(f.previous.focusCalls.length, 0);
  for (const element of excluded) assert.equal(element.focusCalls.length, 0);
  assert.equal(trigger.focusCalls.length, 1);
});
check('direct-load body or document focus falls back to a usable workspace control', () => {
  for (const initial of ['body', 'documentElement']) {
    const disabledComposer = new Element({ disabled: true }), button = new Element();
    const f = nativeFixture(null, [disabledComposer, button]);
    f.document.activeElement = f.document[initial];
    f.showSettingsDialog(f.dialog)();
    assert.equal(f.document[initial].focusCalls.length, 0);
    assert.equal(disabledComposer.focusCalls.length, 0);
    assert.equal(button.focusCalls.length, 1);
  }
});
check('no usable focus target is harmless on logout/unmount', () => {
  const f = nativeFixture(null); assert.doesNotThrow(() => f.showSettingsDialog(f.dialog)());
});

// Hook harness executes the real component callbacks/effects without emulating
// browser DOM or mounting child forms. React SSR separately checks actual markup.
function hooks(initialState = value => value) {
  const slots = []; let cursor = 0; let pending = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const api = { ...React,
    useState(initial) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: initialState(typeof initial === 'function' ? initial() : initial) };
      return [slots[i].value, next => { slots[i].value = typeof next === 'function' ? next(slots[i].value) : next; }];
    },
    useRef(initial) { const i = cursor++; slots[i] ??= { current: initial }; return slots[i]; },
    useCallback(fn) { cursor++; return fn; },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !same(slots[i].deps, deps)) pending.push(() => {
        slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: fn() };
      });
    },
  };
  api.useLayoutEffect = api.useEffect;
  return { api, render(Component, props) { cursor = 0; pending = []; return Component(props); },
    flush() { pending.forEach(effect => effect()); pending = []; } };
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object' || !tree.props) return [];
  return [tree, ...nodes(tree.props.children)];
}
function find(tree, predicate) {
  const found = nodes(tree).find(predicate); assert.ok(found, 'expected component node'); return found;
}
const account = { profile_uuid: 'account-a', username: '预览账号', role: 'admin', email_required: false, birth_year_required: false };
const props = { account, active: true, onAccountRefresh: async () => {}, onLogout: async () => {} };

check('real SSR has labelled native dialog, close button, navigation, role-gated choices', () => {
  const Settings = loader().load('components/SettingsPage.tsx').default;
  for (const role of ['user', 'admin']) {
    const html = renderToStaticMarkup(React.createElement(Settings, { ...props, account: { ...account, role } }));
    assert.match(html, /^<dialog[^>]+aria-labelledby="settings-dialog-title"/);
    assert.match(html, /id="settings-dialog-title"/);
    assert.match(html, /aria-label="关闭设置，返回对话"/);
    assert.match(html, /aria-label="设置导航"/);
    // 默认, 简约 and 被试建议1 are administrator-only choices.
    assert.equal((html.match(/name="overall-style"/g) || []).length, role === 'admin' ? 4 : 0);
    assert.equal(html.includes('overall-style-heading'), role === 'admin');
    assert.doesNotMatch(html.slice(0, html.indexOf('>')), /\sopen(?:=|\s|$)/);
  }
});
check('close, Escape and true backdrop click replace route without scrolling; drags do not close', () => {
  const h = hooks(); const calls = [];
  const Settings = loader({ react: h.api, 'next/navigation': { useRouter: () => ({ replace: (...args) => calls.push(args) }) } }).load('components/SettingsPage.tsx').default;
  const tree = h.render(Settings, props);
  find(tree, n => n.props['aria-label'] === '关闭设置，返回对话').props.onClick();
  let prevented = false; tree.props.onCancel({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  const backdrop = {}, content = {};
  tree.props.onPointerDown({ target: content, currentTarget: backdrop });
  tree.props.onClick({ target: backdrop, currentTarget: backdrop });
  assert.equal(calls.length, 2, 'drag from content cannot dismiss');
  tree.props.onPointerDown({ target: backdrop, currentTarget: backdrop });
  tree.props.onClick({ target: backdrop, currentTarget: backdrop });
  assert.equal(calls.length, 3);
  for (const [url, options] of calls) { assert.equal(url, '/'); assert.equal(options.scroll, false); }
});
check('settings active transition opens/closes once and retains visited embedded forms', () => {
  const h = hooks(); let shown = 0, closed = 0;
  const Settings = loader({ react: h.api, '@/lib/settingsDialog': { showSettingsDialog() { shown++; return () => { closed++; }; } } }).load('components/SettingsPage.tsx').default;
  let tree = h.render(Settings, props); tree.props.ref.current = {}; h.flush();
  find(tree, n => n.props['aria-label'] === '我的档案').props.onClick();
  tree = h.render(Settings, props); h.flush();
  assert.equal(find(tree, n => n.props.id === 'settings-profile').props.hidden, false);
  find(tree, n => n.props['aria-label'] === '外观').props.onClick();
  tree = h.render(Settings, { ...props, active: false }); h.flush();
  const profile = find(tree, n => n.props.id === 'settings-profile');
  assert.equal(profile.props.hidden, true); assert.equal(profile.props.children.props.embedded, true);
  tree = h.render(Settings, props); h.flush();
  assert.ok(find(tree, n => n.props.id === 'settings-profile').props.children);
  assert.equal(shown, 2); assert.equal(closed, 1);
});

function authFixture(nextAccount, pathname = '/settings') {
  const h = hooks(value => value && typeof value === 'object' && 'checking' in value
    ? { checking: false, account: nextAccount } : value);
  const location = { pathname };
  const modules = loader({ react: h.api, 'next/navigation': { usePathname: () => location.pathname },
    '@/lib/auth': {}, '@/lib/overallStyle': { bindOverallStyleAccount() {} } });
  const Gate = modules.load('components/AuthGate.tsx').default;
  return { location, render: () => h.render(Gate), stubs: modules.stubs };
}
check('direct settings load keeps chat visible and stable while route changes preserve settings', () => {
  const f = authFixture(account); let tree = f.render();
  const workspace = find(tree, n => Object.hasOwn(n.props, 'data-chat-workspace'));
  assert.equal(workspace.props.hidden, undefined); assert.equal(workspace.props.inert, true);
  assert.doesNotMatch(workspace.props.className, /\bhidden\b/);
  const chat = workspace.props.children;
  assert.equal(find(tree, n => n.type.displayName === '@/components/SettingsPage').props.active, true);
  f.location.pathname = '/'; tree = f.render();
  const restored = find(tree, n => Object.hasOwn(n.props, 'data-chat-workspace'));
  assert.equal(restored.props.inert, false);
  assert.equal(restored.props.children.type, chat.type); assert.equal(restored.props.children.key, chat.key);
  assert.equal(find(tree, n => n.type.displayName === '@/components/SettingsPage').props.active, false);
});
check('required-email form is not eclipsed by native modal; guest and birthday gates remain', () => {
  const email = authFixture({ ...account, email_required: true }).render();
  assert.equal(find(email, n => n.type.displayName === '@/components/SettingsPage').props.active, false);
  find(email, n => n.type.displayName === '@/components/RequiredEmailModal');
  const birthday = authFixture({ ...account, birth_year_required: true }).render();
  assert.equal(birthday.type.displayName, '@/components/RequiredBirthYearModal');
  const guest = authFixture(null).render();
  find(guest, n => n.type.displayName === '@/components/AuthScreen');
  assert.equal(nodes(guest).some(n => n.type.displayName === '@/components/SettingsPage'), false);
});
check('settings links retain URL navigation, menu callback and scroll position', () => {
  const Link = loader().load('components/SettingsLink.tsx').default; const onClick = () => {};
  const tree = Link({ onClick, menu: true });
  assert.equal(tree.props.href, '/settings'); assert.equal(tree.props.scroll, false);
  assert.equal(tree.props.onClick, onClick); assert.equal(tree.props.role, 'menuitem');
  assert.equal(tree.props['data-settings-trigger'], true);
});
check('closed dialog remains hidden across responsive rules; content has independent scrolling', () => {
  const css = postcss.parse(fs.readFileSync(path.join(root, 'app/settings.css'), 'utf8'));
  let hidden = false, desktop = false, mobile = false, scroll = false;
  css.walkRules(rule => {
    rule.walkDecls(decl => {
      if (rule.selector === '.settings-window:not([open])' && decl.prop === 'display' && decl.value === 'none') hidden = true;
      if (rule.selector === '.settings-window' && decl.prop === 'width' && decl.value.includes('1180px')) desktop = true;
      if (rule.selector === '.settings-window' && rule.parent.params === '(max-width: 480px)' && decl.prop === 'height' && decl.value === '100dvh') mobile = true;
      if (rule.selector === '.settings-content' && decl.prop === 'overflow-y' && decl.value === 'auto') scroll = true;
    });
  });
  assert.ok(hidden && desktop && mobile && scroll);
  assert.match(fs.readFileSync(path.join(root, 'app/globals.css'), 'utf8'), /@import "\.\/settings\.css"/);
});
console.log(`${checks} settings checks passed. Native browser focus trapping, rendering and pointer behavior are not verified by this Node suite.`);
