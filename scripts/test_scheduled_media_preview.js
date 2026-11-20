/* Exercise the actual scheduled-message hover preview with an isolated DOM. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.handlers = {};
    this.offsetWidth = 200;
    this.offsetHeight = 100;
  }
  set innerHTML(value) {
    this.children = [];
    for (const tag of value.matchAll(/<(img|video)\b/g)) this.appendChild(new Element(tag[1]));
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; if (name === "src") this.src = ""; }
  contains(element) { return element === this || this.children.some(child => child.contains(element)); }
  querySelector(selector) {
    const match = child => selector[0] === "." ? child.className === selector.slice(1) : child.tagName === selector;
    return this.children.find(match) || this.children.map(child => child.querySelector(selector)).find(Boolean) || null;
  }
  getBoundingClientRect() { return {left: 20, top: 20, bottom: 40}; }
  pause() { this.paused = true; }
  load() {}
  play() { throw new Error("A preview must not play video"); }
}

const modal = new Element("div");
const entries = new Element("div");
const pending = [];
const revoked = [];
const requests = [];
let nextUrl = 0;
const context = {
  document: {
    getElementById: id => id === "scheduledMessageContentModal" ? modal : (id === "scheduledMessageEntries" ? entries : null),
    querySelector: selector => modal.querySelector(selector),
    createElement: tag => new Element(tag),
  },
  window: {innerWidth: 800, innerHeight: 600},
  scheduledMessageDraft: [],
  panelToken: "test-token",
  ensurePanelToken: async () => {},
  withActiveQQBot: url => url + "?bot_id=test-bot",
  scheduledImageDataSize: () => 1,
  URL: {
    createObjectURL: () => "blob:test-" + ++nextUrl,
    revokeObjectURL: url => revoked.push(url),
  },
  fetch: (url, options) => {
    requests.push({url, options});
    return new Promise(resolve => pending.push(resolve));
  },
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../panel/web/app.js"), "utf8");
const start = source.indexOf("function ensureScheduledImagePreview(");
const end = source.indexOf("function newScheduledMessage(", start);
assert(start >= 0 && end > start, "preview functions must remain available");
vm.runInContext(source.slice(start, end), context);
const listenerStart = source.indexOf('  const scheduledEntries = document.getElementById("scheduledMessageEntries");');
const listenerEnd = source.indexOf('  document.addEventListener("pointerdown"', listenerStart);
assert(listenerStart >= 0 && listenerEnd > listenerStart, "preview event handlers must remain available");
vm.runInContext(source.slice(listenerStart, listenerEnd), context);
const renderStart = source.indexOf('    const attachment = scheduledMessageAttachment(message);', source.indexOf('function renderScheduledMessageEditor('));
const renderEnd = source.indexOf('    const imageRemove =', renderStart);
assert(renderStart >= 0 && renderEnd > renderStart, "attachment name rendering must remain available");
vm.runInContext('function renderPreviewName(entry, message) {\n' + source.slice(renderStart, renderEnd) + '\n}', context);

function anchor(index) {
  const node = new Element("span");
  node.dataset.messageIndex = String(index);
  node.closest = selector => selector === ".scheduled-message-image-name.has-image" ? node : null;
  return node;
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

async function run() {
  context.scheduledMessageDraft = [
    {attachment_id: "picture id", attachment_type: "image", attachment_name: "photo.png"},
    {attachment_id: "video id", attachment_type: "video", attachment_name: "clip.mp4"},
    {attachment_id: "audio id", attachment_type: "audio", attachment_name: "song.mp3"},
    {image_data: "data:image/png;base64,AA==", image_name: "legacy.png"},
  ];
  function renderName(message) {
    const name = new Element("span");
    name.classList = {toggle: (key, enabled) => { name.previewEnabled = key === "has-image" && enabled; }};
    context.renderPreviewName({querySelector: () => name}, message);
    return name;
  }
  assert.equal(renderName(context.scheduledMessageDraft[0]).previewEnabled, true);
  const renderedVideo = renderName(context.scheduledMessageDraft[1]);
  assert.equal(renderedVideo.previewEnabled, true, "video filename participates in hover/focus preview");
  assert.equal(renderedVideo.tabIndex, 0, "video filename is keyboard focusable");
  assert.equal(renderName(context.scheduledMessageDraft[2]).previewEnabled, false);
  const imageAnchor = anchor(0);
  entries.handlers.pointerover({target: imageAnchor, relatedTarget: null});
  await settle();
  assert.equal(requests[0].url, "/api/scheduled-message-media/picture%20id?bot_id=test-bot");
  assert.equal(requests[0].options.headers["X-WRelay-Token"], "test-token");
  pending.shift()({ok: true, blob: async () => ({})});
  await settle();
  const preview = modal.querySelector(".scheduled-message-image-preview");
  const image = preview.querySelector("img");
  const video = preview.querySelector("video");
  assert.equal(image.src, "blob:test-1");
  image.onload();
  assert.equal(preview.hidden, false);
  entries.handlers.pointerout({target: imageAnchor, relatedTarget: null});
  assert.equal(preview.hidden, true);
  assert(revoked.includes("blob:test-1"));
  assert.equal(image.src, "");

  const videoAnchor = anchor(1);
  entries.handlers.focusin({target: videoAnchor});
  await settle();
  assert.equal(requests[1].url, "/api/scheduled-message-media/video%20id?bot_id=test-bot");
  assert.equal(requests[1].options.headers["X-WRelay-Token"], "test-token");
  pending.shift()({ok: true, blob: async () => ({})});
  await settle();
  assert.equal(video.src, "blob:test-2");
  assert.equal(video.muted, true);
  assert.notEqual(video.autoplay, true);
  const ready = video.onloadeddata;
  ready();
  assert.equal(preview.hidden, false);
  entries.handlers.focusout({target: videoAnchor, relatedTarget: null});
  assert.equal(preview.hidden, true);
  assert.equal(video.src, "");
  assert.equal(video.paused, true);
  assert(revoked.includes("blob:test-2"));
  ready();
  assert.equal(preview.hidden, true, "stale load cannot reopen a closed preview");

  context.openScheduledImagePreview(videoAnchor);
  await settle();
  context.closeScheduledImagePreview();
  pending.shift()({ok: true, blob: async () => ({})});
  await settle();
  assert.equal(preview.hidden, true);
  assert(revoked.includes("blob:test-3"), "late fetch releases its object URL");

  context.openScheduledImagePreview(videoAnchor);
  await settle();
  pending.shift()({ok: true, blob: async () => ({})});
  await settle();
  video.onerror();
  assert.equal(preview.hidden, true, "decoding errors close the preview");
  assert(revoked.includes("blob:test-4"));

  context.openScheduledImagePreview(anchor(2));
  assert.equal(requests.length, 4, "audio does not request a visual preview");
  context.openScheduledImagePreview(anchor(3));
  assert.equal(image.src, "data:image/png;base64,AA==", "legacy inline images remain supported");
  image.onload();
  assert.equal(preview.hidden, false);
  context.closeScheduledImagePreview();
  assert.equal(preview.hidden, true);
}

run().then(() => console.log("PASS - scheduled message media previews"))
  .catch(error => { console.error(error); process.exitCode = 1; });
