/* Exercise Steam source registration, draft saving and conflict recovery in an isolated DOM. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.handlers = {};
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = '';
    this._text = '';
    this._html = '';
    this.value = '';
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.options = tag === 'select' ? [] : undefined;
    this.parentElement = null;
    this.scrollLeft = 0;
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => name && !names.includes(name)).join(' '); },
      contains: name => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !this.classList.contains(name) : !!force;
        this.classList[enabled ? 'add' : 'remove'](name);
        return enabled;
      },
    };
  }
  set textContent(value) { this._text = String(value); this._html = ''; this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { this._html = String(value); this._text = ''; this.children = []; }
  get innerHTML() { return this._html; }
  get childElementCount() { return this.children.length; }
  get firstChild() { return this.children[0] || null; }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
      if (this.options) this.options.push(child);
      if (this.tagName === 'select' && !this.value && child.tagName === 'option') this.value = child.value;
    }
  }
  replaceChildren(...children) {
    this.children = [];
    if (this.options) this.options = [];
    this._text = '';
    this._html = '';
    this.append(...children);
  }
  addEventListener(name, callback) { (this.handlers[name] ||= []).push(callback); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus() {}
  select() {}
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(item => item.trim());
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return descendants.filter(element => selectors.some(item => {
      if (item === '*') return true;
      const tag = item.match(/^[a-zA-Z]+/)?.[0];
      const className = item.match(/\.([\w-]+)/)?.[1];
      const dataKey = item.match(/\[data-([\w-]+)\]/)?.[1];
      return (!tag || element.tagName === tag)
        && (!className || element.classList.contains(className))
        && (!dataKey || element.dataset[dataKey.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] !== undefined);
    }));
  }
  async fire(name, event = {}) {
    for (const callback of this.handlers[name] || []) {
      await callback(Object.assign({target: this, preventDefault() {}, stopPropagation() {}}, event));
    }
  }
}

const ids = {};
for (const id of [
  'steamSourceBody', 'steamGroups', 'steamFgTabs', 'steamSourceAllEnabled', 'steamGroupAllEnabled',
  'steamRegisterSource', 'steamAddRegisteredSource', 'steamAddGroupTarget', 'steamSyncGroups',
  'steamRegion', 'steamPollInterval', 'steamDraftActions', 'steamDraftWarning', 'steamRetryDraft',
  'steamResetDraft', 'steamStatus', 'steamBadge', 'steamClearPaused', 'btnSteamProtection',
  'confirmBody', 'confirmTitle', 'confirmModal',
]) ids[id] = new Element(id.includes('Region') || id.includes('Interval') ? 'select' : 'div');

const settings = {
  _profile_bot: '111', _revision: '1', steam_region: 'CN', steam_poll_interval: 900,
  steam_sources: [],
  steam_forwarding_groups: [{id: 'g1', name: 'Test group', enabled: true, sources: [], groups: []}],
  qq_group_openids: [{openid: 'G1', name: 'Test group', enabled: true, bot_id: '111'}],
};
let saved = null, failSave = false, alerts = [], confirmation = null, selectPrompt = null;
let timerSequence = 0;
const timers = new Map();
const ctx = {
  document: {
    getElementById: id => ids[id],
    createElement: tag => new Element(tag),
    createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; },
    addEventListener() {},
    activeElement: null,
  },
  settings, activePage: 'steam', currentQQBotId: () => '111',
  groupInActiveQQBotWorkspace: () => true,
  renderIdCell: value => String(value), mcIcon: () => '', URL,
  confirmAction: null,
  setConfirmButtons: () => {},
  mountImageCardOptions: () => () => ({}),
  setInterval: () => 0,
  setTimeout: callback => { const id = ++timerSequence; timers.set(id, callback); return id; },
  clearTimeout: id => timers.delete(id),
  showAlert: (title, text) => alerts.push([title, text]),
  showConfirmDlg: (title, text, action) => { confirmation = action; },
  showConfirmSelect: (title, items, labelFor, onSelect) => { selectPrompt = {title, items, labelFor, onSelect}; },
  jget: async () => ({ok: true, bot_running: true, queue_pending: 0, queue_paused: 0}),
  jpost: async () => ({ok: true, removed: 1}),
  saveAndReload: async () => {
    if (failSave) return false;
    saved = JSON.parse(JSON.stringify(ctx.settings));
    ctx.settings._revision = String(Number(ctx.settings._revision) + 1);
    return true;
  },
  console, JSON, Date, Math, Map, Set, Number,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'panel', 'web', 'steam-page.js'), 'utf8'), ctx);

function all(root) { return [root, ...root.children.flatMap(all)]; }
function labeled(root, label) {
  const item = all(root).find(element => element.tagName === 'label' && element.children[0]?.textContent === label);
  assert(item, label);
  return item.children[1];
}
function checked(root, label) {
  const item = all(root).find(element => element.tagName === 'label' && element.textContent === label);
  assert(item, label);
  return item.children[0];
}
function titled(root, title) {
  const item = all(root).find(element => element.title === title);
  assert(item, title);
  return item;
}
async function flushTimers() {
  while (timers.size) {
    const [id, callback] = timers.entries().next().value;
    timers.delete(id);
    await callback();
  }
}
function selectAll() {
  assert(selectPrompt, 'expected a selection dialog');
  const prompt = selectPrompt;
  selectPrompt = null;
  prompt.onSelect(prompt.items);
}

(async () => {
  await ids.steamRegisterSource.fire('click');
  const appid = labeled(ids.confirmBody, 'AppID 或 Steam 游戏地址');
  appid.value = '620';
  labeled(ids.confirmBody, '名称（可留空）').value = 'Portal 2';
  await ctx.confirmAction();
  await ids.steamAddRegisteredSource.fire('click');
  selectAll();
  await ids.steamAddGroupTarget.fire('click');
  selectAll();
  await flushTimers();

  assert.equal(saved.steam_sources[0].value, '620');
  assert.equal(saved.steam_sources[0].name, 'Portal 2');
  assert.equal(saved.steam_sources[0].translation_enabled, true);
  assert.deepEqual(saved.steam_forwarding_groups[0].sources, [saved.steam_sources[0].id]);
  assert.deepEqual(saved.steam_forwarding_groups[0].groups, ['G1']);

  await titled(ids.steamSourceBody, '内容筛选').fire('click');
  const dlc = checked(ids.confirmBody, '折扣包含 DLC');
  dlc.checked = true;
  await dlc.fire('change');
  const bundles = checked(ids.confirmBody, '折扣包含捆绑包');
  bundles.checked = true;
  await bundles.fire('change');
  await ctx.confirmAction();
  await flushTimers();
  assert.equal(saved.steam_sources[0].include_dlc, true);
  assert.equal(saved.steam_sources[0].include_bundles, true);

  await titled(ids.steamSourceBody, '编辑名称与来源地址').fire('click');
  labeled(ids.confirmBody, '名称（可留空）').value = 'Unfinished';
  await ctx.confirmAction();
  await titled(ids.steamSourceBody, '编辑名称与来源地址').fire('click');
  assert.equal(labeled(ids.confirmBody, '名称（可留空）').value, 'Unfinished');

  settings.steam_sources[0].name = 'Other edit';
  settings._revision = '5';
  ctx.renderSteamPage();
  assert.equal(ids.steamDraftActions.hidden, false);
  assert(ids.steamDraftWarning.textContent.includes('已在其他位置更改'));
  await titled(ids.steamSourceBody, '编辑名称与来源地址').fire('click');
  assert.equal(labeled(ids.confirmBody, '名称（可留空）').value, 'Unfinished');

  await ids.steamResetDraft.fire('click');
  assert(confirmation);
  await confirmation();
  await titled(ids.steamSourceBody, '编辑名称与来源地址').fire('click');
  assert.equal(labeled(ids.confirmBody, '名称（可留空）').value, 'Other edit');

  labeled(ids.confirmBody, '名称（可留空）').value = 'Not saved';
  await ctx.confirmAction();
  failSave = true;
  await flushTimers();
  assert.equal(settings.steam_sources[0].name, 'Other edit');
  assert.equal(ids.steamDraftActions.hidden, false);
  await titled(ids.steamSourceBody, '编辑名称与来源地址').fire('click');
  assert.equal(labeled(ids.confirmBody, '名称（可留空）').value, 'Not saved');
  console.log('Steam source registration, draft save and conflict checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
