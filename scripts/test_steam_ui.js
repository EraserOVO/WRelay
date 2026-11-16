/* Exercise the Steam page with a small isolated DOM and fake panel API. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.handlers = {}; this._text = ''; this.value = ''; this.checked = false; this.hidden = false; this.disabled = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  async fire(name) { if (this.handlers[name]) await this.handlers[name]({target: this}); }
}
const ids = {};
for (const id of ['steamSources','steamGroups','steamOptions','steamAddGame','steamAddCreator','steamAddGroup','steamSave','steamResetDraft','steamDraftWarning','steamClearPaused','steamStatus']) ids[id] = new Element('div');
const settings = {_profile_bot:'111', _revision:'1', steam_sources:[], steam_forwarding_groups:[],
  qq_group_openids:[{openid:'G1', name:'Test group', enabled:true, bot_id:'111'}]};
let saved = null, failSave = false, alerts = [], confirmation = null;
const ctx = {document: {getElementById:id => ids[id], createElement:tag => new Element(tag), createTextNode:text => {const n=new Element('#text');n.textContent=text;return n;}},
  settings, activePage:'steam', currentQQBotId:()=>'111', groupInActiveQQBotWorkspace:()=>true,
  setInterval:()=>0, showAlert:(title,text)=>alerts.push([title,text]),
  showConfirmDlg:(title,text,action)=>{confirmation=action;},
  jget:async()=>({ok:true,bot_running:true,queue_pending:0,queue_paused:0}),
  jpost:async()=>({ok:true,removed:1}),
  saveAndReload:async()=>{if (failSave) return false; saved=JSON.parse(JSON.stringify(ctx.settings)); ctx.settings._revision=String(Number(ctx.settings._revision)+1); return true;},
  console, JSON, Date, Math, Map, Set, Number};
ctx.window=ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'..','panel','web','steam-page.js'),'utf8'),ctx);

function all(root) { return [root,...root.children.flatMap(all)]; }
function labeled(root, label) { const item=all(root).find(x=>x.tagName==='label' && x.children[0]?.textContent===label); assert(item, label); return item.children[1]; }
function check(root, label) { const item=all(root).find(x=>x.tagName==='label' && x.textContent===label); assert(item,label); return item.children[0]; }

(async()=>{
  await ids.steamAddGame.fire('click');
  const appid=labeled(ids.steamSources,'AppID 或 Steam 游戏地址'); appid.value='620'; await appid.fire('input');
  const dlc=check(ids.steamSources,'折扣包含 DLC'); dlc.checked=true; await dlc.fire('change');
  const bundles=check(ids.steamSources,'折扣包含捆绑包'); bundles.checked=true; await bundles.fire('change');
  await ids.steamAddGroup.fire('click');
  const source=check(ids.steamGroups,'620'); source.checked=true; await source.fire('change');
  const group=check(ids.steamGroups,'Test group'); group.checked=true; await group.fire('change');
  await ids.steamSave.fire('click');
  assert.equal(saved.steam_sources[0].value,'620');
  assert.equal(saved.steam_sources[0].include_dlc,true);
  assert.equal(saved.steam_sources[0].include_bundles,true);
  assert.deepEqual(saved.steam_forwarding_groups[0].sources,[saved.steam_sources[0].id]);
  assert.deepEqual(saved.steam_forwarding_groups[0].groups,['G1']);

  // Unrelated settings refresh keeps an in-progress draft.
  const field=labeled(ids.steamSources,'名称（可留空）'); field.value='Unfinished'; await field.fire('input');
  ctx.settings._revision='3'; ctx.renderSteamPage();
  assert.equal(labeled(ids.steamSources,'名称（可留空）').value,'Unfinished');
  assert.equal(ids.steamDraftWarning.hidden,true);
  ctx.settings.steam_sources[0].name='Other edit'; ctx.settings._revision='4'; ctx.renderSteamPage();
  assert.equal(ids.steamSave.disabled,true);
  assert.equal(labeled(ids.steamSources,'名称（可留空）').value,'Unfinished');
  await ids.steamResetDraft.fire('click'); assert(confirmation); await confirmation();
  assert.equal(labeled(ids.steamSources,'名称（可留空）').value,'Other edit');

  // A failed save must not leak the draft into the shared settings object.
  const changed=labeled(ids.steamSources,'名称（可留空）'); changed.value='Not saved'; await changed.fire('input');
  failSave=true; await ids.steamSave.fire('click');
  assert.equal(ctx.settings.steam_sources[0].name,'Other edit');
  assert.equal(labeled(ids.steamSources,'名称（可留空）').value,'Not saved');
  console.log('Steam page checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
