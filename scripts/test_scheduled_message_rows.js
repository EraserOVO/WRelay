const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../panel/web/app.js"), "utf8");
const elements = new Map();
const alerts = [];
const saves = [];
const modal = {style: {display: "none"}};
let confirmDelete;
let focused;
let uploads = 0;

function getElementById(id) {
  if (id === "scheduledMessageContentModal") return modal;
  if (!elements.has(id)) elements.set(id, {innerHTML: "", textContent: "", dataset: {}});
  return elements.get(id);
}

const context = vm.createContext({
  document: {
    getElementById,
    querySelector: () => ({focus() { focused = draft()[0]?.content; }}),
  },
  settings: {qq_group_openids: []},
  activeQQBotId: "bot-A",
  workspaceEpoch: 1,
  SVG_GRIP: "", SVG_PEN: "", SVG_TRASH: "", SVG_PLAY: "", SVG_PAUSE: "",
  mcIcon: () => "",
  escapeHtml: value => String(value),
  groupInActiveQQBotWorkspace: () => true,
  syncHeaderAll: () => {},
  scheduledLocalDateTimeValue: () => "2030-01-01T09:00",
  renderScheduledMessageEditor: () => {},
  readScheduledMessageEditor: () => structuredClone(draft()),
  closeScheduledImagePreview: () => {},
  closeAllScheduledDatePickers: () => {},
  showAlert: (...args) => alerts.push(args.join(" ")),
  showConfirmDlg: (_title, _body, callback) => { confirmDelete = callback; },
  saveScheduledMessageGroups: async () => { saves.push(structuredClone(groups())); return true; },
  uploadScheduledMessageAttachment: async () => { uploads += 1; return {attachment: {}}; },
});

function evaluate(code) { return vm.runInContext(code, context); }

function load(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  assert(first >= 0 && last > first, "Could not load application section: " + start);
  evaluate(source.slice(first, last));
}

const handlers = {};
for (const id of ["scheduledGroupMessageList", "scheduledMessageEntries", "scheduledMessageAllEnabled"]) {
  getElementById(id).addEventListener = (event, handler) => { handlers[id + ":" + event] = handler; };
}
load("const SCHEDULED_TARGET_MODES", "function pillSwitchMarkup(");
load("function renderScheduledMessageWorkspace(", "async function saveScheduledMessageGroups(");
load("function scheduledMessageAttachment(", "function openScheduledImagePreview(");
load("function newScheduledMessage(", "function scheduledOptionMarkup(");
load("function openScheduledMessageEditor(", "async function uploadScheduledMessageAttachment(");
load("function preserveScheduledMessageAttachments(", "// ---------- 转发组");
load('  const messageList = document.getElementById("scheduledGroupMessageList");', '  const targetList = document.getElementById("scheduledGroupTargetList");');
load('  document.getElementById("scheduledMessageEntries").addEventListener("change", async', '  document.getElementById("scheduledMessageContentConfirm").addEventListener');

function draft() { return evaluate("scheduledMessageDraft"); }
function groups() { return evaluate("scheduledMessageGroupsDraft"); }

function setGroups(data, active = "g1") {
  Object.assign(context, {nextGroups: structuredClone(data), nextActiveGroup: active});
  evaluate("scheduledMessageGroupsDraft = nextGroups; activeScheduledMessageGroupId = nextActiveGroup; renderScheduledMessageWorkspace();");
}

function message(id, content, attachmentMiB) {
  const entry = {id, content, schedule_type: "interval", interval_minutes: 2};
  if (attachmentMiB) Object.assign(entry, {
    attachment_id: id + ".mp4", attachment_type: "video", attachment_mime: "video/mp4",
    attachment_name: id + ".mp4", attachment_size: attachmentMiB * 1024 * 1024,
  });
  return entry;
}

function fixture(firstAttachmentMiB, secondAttachmentMiB) {
  return [
    {id: "g1", name: "Group 1", messages: [message("m1", "测试111", firstAttachmentMiB), message("m2", "777", secondAttachmentMiB)]},
    {id: "g2", name: "Group 2", messages: [message("m2", "same ID in another group")]},
  ];
}

function dataset(tag) {
  return Object.fromEntries(Array.from(tag.matchAll(/\bdata-([\w-]+)="([^"]*)"/g),
    ([, name, value]) => [name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), value]));
}

async function clickRow(index, action) {
  const rows = getElementById("scheduledGroupMessageList").innerHTML.match(/<tr\b[^>]*>.*?<\/tr>/gs);
  const row = rows[index];
  const className = "scheduled-group-message-" + action;
  const buttonTag = Array.from(row.matchAll(/<button\b[^>]*>/g), match => match[0]).find(tag => tag.includes(className));
  assert(buttonTag, "No " + action + " button for message row " + index);
  const rowElement = {dataset: dataset(row.match(/^<tr\b[^>]*>/)[0])};
  const button = {
    dataset: dataset(buttonTag),
    classList: {contains: name => name === className},
    closest: selector => selector.startsWith("tr") ? rowElement : null,
  };
  await handlers["scheduledGroupMessageList:click"]({
    target: {closest: selector => selector.includes(className) ? button : button.closest(selector)},
  });
}

async function run() {
  setGroups(fixture());
  await clickRow(1, "edit");
  assert.deepEqual(Array.from(draft(), entry => entry.id), ["m2"]);
  assert.equal(focused, "777");
  draft()[0].content = "edited second message";
  const reordered = fixture().reverse();
  reordered[1].messages.reverse();
  reordered[1].messages[0].enabled = false;
  setGroups(reordered, "g2");
  await evaluate("confirmScheduledMessageEditor()");
  assert.deepEqual(saves[0].map(group => group.messages.map(entry => entry.content)), [
    ["same ID in another group"], ["edited second message", "测试111"],
  ]);
  assert.equal(saves[0][1].messages[0].enabled, false, "A refreshed enable status must survive the edit");

  setGroups(fixture());
  await clickRow(1, "remove");
  const refreshed = fixture().reverse();
  refreshed[1].messages.reverse();
  setGroups(refreshed, "g2");
  await confirmDelete();
  assert.deepEqual(saves[1].map(group => group.messages.map(entry => entry.id)), [["m2"], ["m1"]]);

  setGroups(fixture());
  await clickRow(1, "edit");
  const missing = fixture();
  missing[0].messages.pop();
  setGroups(missing);
  await evaluate("confirmScheduledMessageEditor()");
  assert.equal(saves.length, 2, "Removing the edited row must invalidate its editor");

  setGroups(fixture());
  await clickRow(1, "remove");
  setGroups(missing);
  await confirmDelete();
  assert.equal(saves.length, 2, "A missing target must not cause deletion of a different row");
  assert.deepEqual(structuredClone(groups()), missing);

  setGroups(fixture());
  await clickRow(1, "remove");
  context.activeQQBotId = "bot-B";
  context.workspaceEpoch += 1;
  setGroups(fixture());
  await confirmDelete();
  assert.equal(saves.length, 2, "An old confirmation must not delete in a different account");
  assert.deepEqual(structuredClone(groups()), fixture());

  setGroups(fixture(29, 1));
  await clickRow(1, "edit");
  const entry = {dataset: {index: "0"}};
  const fileInput = {
    dataset: {field: "media_file_input"},
    files: [{name: "replacement.mp4", size: 2 * 1024 * 1024}],
    closest: () => entry, value: "replacement.mp4",
  };
  await handlers["scheduledMessageEntries:change"]({target: fileInput});
  assert.equal(uploads, 0, "The other row's 29 MiB attachment counts against the upload limit");
  assert(alerts.some(alert => alert.includes("30 MiB")));
  draft()[0].attachment_size = 2 * 1024 * 1024;
  await evaluate("confirmScheduledMessageEditor()");
  assert.equal(saves.length, 2, "The other row's attachment also counts against the save limit");

  setGroups(fixture());
  evaluate('openScheduledMessageEditor({groupId: "g1", messageId: null, epoch: workspaceEpoch})');
  assert.equal(draft()[0].content, "");
  draft()[0].content = "new message";
  setGroups(fixture(), "g2");
  await evaluate("confirmScheduledMessageEditor()");
  assert.deepEqual(saves[2].map(group => group.messages.map(entry => entry.content)), [
    ["测试111", "777", "new message"], ["same ID in another group"],
  ]);

  const oldMessages = [{id: "m1", image_name: "legacy.png", image_data: "data:image/png;base64,legacy"},
    message("m2", "old video", 1)];
  const statusMessages = [{id: "m3", content: "new first row"}, {id: "m1", image_name: "legacy.png"},
    {id: "m2", content: "video removed"}];
  const previous = {message_groups: [{id: "g1", messages: oldMessages}]};
  const status = {message_groups: [{id: "g1", messages: statusMessages}]};
  const merged = context.preserveScheduledMessageAttachments(status, previous).message_groups[0].messages;
  assert.equal(merged[0].image_data, undefined, "A newly inserted row must not inherit the old first row's image");
  assert.equal(merged[1].image_data, oldMessages[0].image_data);
  assert.equal(merged[2].attachment_id, undefined, "A removed attachment must not reappear after refresh");
  console.log("PASS - scheduled messages edit, delete, refresh, workspace and attachment limits");
}

run().catch(error => { console.error(error); process.exitCode = 1; });
