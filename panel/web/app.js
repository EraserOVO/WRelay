const SVG_FUNNEL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></svg>';
const SVG_TRANSLATE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="M14 18h7"/><path d="M17.5 14l3.5 7"/><path d="M14 21l3.5-7"/></svg>';
const SVG_ORIGINAL_ON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
const SVG_ORIGINAL_OFF = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
const SVG_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>';
const SVG_GRIP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>';
// 统一笔图标：空心笔、笔尖朝左下角（转发组重命名 / 频道名称编辑共用）
const SVG_PEN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
const SVG_PAUSE = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>';
const SVG_PLAY = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
// 主题按钮图标：实心月亮（夜间）/ 太阳（日间），跟随按钮文字色
const SVG_MOON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
const SVG_SUN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';

// 关于作者 —— 社交媒体链接（QQ / Bilibili 链接留待填写；GitHub 固定）
const ABOUT_LINKS = {
  qq:       "https://qm.qq.com/q/KOaCzqYuES",
  bilibili: "https://space.bilibili.com/437986248",
  github:   "https://github.com/EraserOVO",
};
function openAboutLink(url, label) {
  if (!url) {
    showAlert("关于作者", label + " 链接还未填写，请在 app.js 顶部的 ABOUT_LINKS 中配置。");
    return;
  }
  window.open(url, "_blank");
}

let settings = null;
// 主题偏好：夜间/日间模式，持久化到 localStorage，默认日间（仅首次使用/未保存偏好时生效）
const THEME_KEY = "wrelay_theme";
const THEME_DARK = "dark";
const THEME_LIGHT = "light";
// 面板默认主题：日间。只影响"没有保存过主题偏好"的首次打开；
// 用户已保存的 dark/light 偏好始终照常恢复，不会被默认值覆盖
const THEME_DEFAULT = THEME_LIGHT;
function applyTheme(theme) {
  document.body.classList.toggle("theme-light", theme === THEME_LIGHT);
  const btn = document.getElementById("btnTheme");
  if (btn) {
    btn.innerHTML = theme === THEME_LIGHT ? SVG_SUN : SVG_MOON;
  }
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
}
function toggleTheme() {
  const next = document.body.classList.contains("theme-light") ? THEME_DARK : THEME_LIGHT;
  applyTheme(next);
}
(function initTheme() {
  let t = THEME_DEFAULT;
  try { t = localStorage.getItem(THEME_KEY) || THEME_DEFAULT; } catch (e) {}
  if (t !== THEME_LIGHT && t !== THEME_DARK) t = THEME_DEFAULT;
  applyTheme(t);
})();
// Discord 频道真实名称映射 {频道ID: 真实名}，由 /api/channel-names 拉取（仅展示，不写入 settings）
let channelNames = {};
// Discord 频道服务器 ID 映射 {频道ID: 服务器ID}，仅用于生成频道跳转链接
let channelGuildIds = {};
// 转发组机制：Discord、X、QQ→QQ 各自一套上下文（组列表字段 / 成员字段 / active
// 状态 / id 前缀等），组相关函数全部按 ctx 参数化复用，三者交互一致。
// active 组 id 各自持久化到 localStorage，刷新后恢复且互不影响；
// 组内成员随 settings 整体保存。
const MAX_FG = 10;
const FG_DISCORD = {
  key: "discord",
  field: "forwarding_groups",
  memberField: {chan: "channels", group: "groups"},
  emptyMembers: ["channels", "groups"],
  storageKey: "wrelay_active_fg",
  idPrefix: "fg_",
  tabsId: "fgTabs",
  addBtnId: "btnAddFg",
  activeId: null,
};
const FG_X = {
  key: "x",
  field: "x_forwarding_groups",
  memberField: {source: "sources", group: "groups"},
  emptyMembers: ["sources", "groups"],
  storageKey: "wrelay_active_xfg",
  idPrefix: "xfg_",
  tabsId: "xFgTabs",
  addBtnId: "btnAddXFg",
  activeId: null,
};
const FG_QQ = {
  key: "qq",
  field: "qq_forwarding_groups",
  memberField: {group: "groups"},
  emptyMembers: ["groups"],
  storageKey: "wrelay_active_qqfg",
  idPrefix: "qqfg_",
  tabsId: "qqFgTabs",
  addBtnId: "btnAddQQFg",
  activeId: null,
};
const FG_CTX = {discord: FG_DISCORD, x: FG_X, qq: FG_QQ};
try { FG_DISCORD.activeId = localStorage.getItem(FG_DISCORD.storageKey); } catch (e) { FG_DISCORD.activeId = null; }
try { FG_X.activeId = localStorage.getItem(FG_X.storageKey); } catch (e) { FG_X.activeId = null; }
try { FG_QQ.activeId = localStorage.getItem(FG_QQ.storageKey); } catch (e) { FG_QQ.activeId = null; }
// X source 字符串 → 去重状态 key（与 bot 侧 config._x_source_key 同规则）：
// screen_name 去 @；完整 URL 时提取 /twitter/user/{handle}，提取不到用整个 URL
function xSourceKey(raw) {
  raw = String(raw || "").trim();
  if (/^https?:\/\//.test(raw)) {
    const m = raw.match(/\/twitter\/user\/([^/?#]+)/);
    return m ? m[1] : raw;
  }
  return raw.replace(/^@+/, "");
}

function discordChannelUrl(channelId) {
  const id = String(channelId || "").trim();
  const guildId = String(channelGuildIds[id] || "").trim();
  if (!/^\d+$/.test(id) || !/^\d+$/.test(guildId)) return "";
  return "https://discord.com/channels/" + guildId + "/" + id;
}

// X source 字符串 → X 账号主页地址；兼容账号名、X/Twitter 主页 URL 和 RSSHub URL。
function xSourceProfileUrl(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  let handle = xSourceKey(value);
  if (/^https?:\/\//i.test(value)) {
    try {
      const url = new URL(value);
      const rssMatch = url.pathname.match(/\/twitter\/user\/([^/?#]+)/i);
      if (rssMatch) {
        handle = decodeURIComponent(rssMatch[1]);
      } else if (/^(?:www\.)?(?:x|twitter)\.com$/i.test(url.hostname)) {
        const firstPath = url.pathname.split("/").filter(Boolean)[0];
        if (firstPath) handle = firstPath;
      } else {
        return "";
      }
    } catch (e) {
      return "";
    }
  }
  handle = String(handle || "").replace(/^@+/, "").trim();
  if (!handle || /[/?#\s]/.test(handle)) return "";
  return "https://x.com/" + encodeURIComponent(handle);
}

// 只翻译用户可见的错误提示；运行日志区域 renderStatus() 保留原文，便于排查。
function localizeUserError(message, fallback) {
  const text = String(message || "").trim();
  if (!text) return fallback || "操作失败";
  const statusMatch = text.match(/\bHTTP(?:\s+Error)?\s+(\d{3})\b/i);
  const status = statusMatch ? statusMatch[1] : "";
  const lower = text.toLowerCase();
  if (lower.includes("no_available_workers") || lower.includes("no available workers")) {
    const prefix = text.includes("AI API 返回 HTTP") && status
      ? "AI API 返回 HTTP " + status + "："
      : status ? "接口返回 HTTP " + status + "：" : "";
    return prefix + "服务商暂时不可用，没有可用的服务节点（所有线路均已熔断或不健康），请稍后重试";
  }
  const replacements = [
    [/Error from provider self-deploy:?/gi, "服务商自托管服务错误："],
    [/No available workers \(all circuits open or unhealthy\)/gi, "没有可用的服务节点（所有线路均已熔断或不健康）"],
    [/Service Unavailable/gi, "服务不可用"],
    [/Bad Gateway/gi, "网关错误"],
    [/Gateway Timeout/gi, "网关超时"],
    [/Unauthorized/gi, "未授权"],
    [/Forbidden/gi, "禁止访问"],
    [/Not Found/gi, "未找到资源"],
    [/Bad Request/gi, "请求参数错误"],
    [/model is not supported/gi, "模型不受支持"],
    [/invalid model/gi, "模型无效"],
    [/invalid api key/gi, "API 密钥无效"],
    [/authentication failed/gi, "认证失败"],
    [/connection refused/gi, "连接被拒绝"],
    [/connection reset/gi, "连接被重置"],
    [/read timed out/gi, "读取超时"],
    [/ReadTimeout/gi, "读取超时"],
    [/timed out/gi, "请求超时"],
    [/timeout/gi, "超时"],
    [/connection error/gi, "连接错误"],
    [/ConnectError/gi, "连接错误"],
    [/ConnectionError/gi, "连接错误"],
    [/JSONDecodeError/gi, "响应 JSON 解析失败"],
    [/No such file or directory/gi, "文件或目录不存在"],
    [/Permission denied/gi, "没有权限"],
    [/Request failed/gi, "请求失败"],
    [/HTTP Error/gi, "HTTP 错误"],
  ];
  let translated = text;
  replacements.forEach(function (item) {
    translated = translated.replace(item[0], item[1]);
  });
  const untranslated = translated.replace(/\b(?:AI|API|HTTP|TMT)\b/gi, "");
  if (translated.includes("AI API 返回 HTTP") && /[A-Za-z]/.test(untranslated)) {
    return "AI API 返回 HTTP " + (status || "未知") + "：服务商返回了无法识别的错误，请检查配置或稍后重试";
  }
  if (/[A-Za-z]/.test(untranslated)) {
    const prefixMatch = translated.match(/^(启动失败|启动重启进程失败|执行失败|请求异常|调用失败|保存失败|恢复失败)/);
    if (prefixMatch) return prefixMatch[1] + "，请稍后重试";
  }
  if (!/[\u3400-\u9fff]/.test(translated) && /[A-Za-z]/.test(translated)) {
    return status ? "接口返回 HTTP " + status : (fallback || "请求失败");
  }
  return translated;
}

// ---------------- 主要界面切换（Discord → QQ / X → QQ / QQ → QQ） ----------------
// 每种转发组合一个独立页面，页面选择持久化到 localStorage（与主题/转发组同方式），
// 刷新后恢复；默认进入 Discord → QQ（不改变原有使用习惯）。
// 三个页面共用同一份 settings 对象（整体保存、整体拉取），切换只改可见性，
// 不会丢失任何已保存/未保存的修改。
const PAGE_STORAGE_KEY = "wrelay_active_page";
let activePage = "discord";
try {
  const savedPage = localStorage.getItem(PAGE_STORAGE_KEY);
  if (savedPage === "x" || savedPage === "qq") activePage = savedPage;
} catch (e) {}

function setActivePage(page) {
  activePage = page === "x" || page === "qq" ? page : "discord";
  try { localStorage.setItem(PAGE_STORAGE_KEY, activePage); } catch (e) {}
  document.getElementById("pageDiscord").style.display = activePage === "discord" ? "" : "none";
  document.getElementById("pageX").style.display = activePage === "x" ? "" : "none";
  document.getElementById("pageQQ").style.display = activePage === "qq" ? "" : "none";
  // 顶部第二张卡按页面切换：Discord → 离线补发；X → X RSS 状态；QQ → 不显示
  const backfillCard = document.getElementById("backfillCard");
  if (backfillCard) backfillCard.style.display = activePage === "discord" ? "" : "none";
  const xrssCard = document.getElementById("xrssCard");
  if (xrssCard) xrssCard.style.display = activePage === "x" ? "" : "none";
  const scheduledCard = document.getElementById("scheduledMessageCard");
  if (scheduledCard) scheduledCard.style.display = activePage === "qq" ? "" : "none";
  document.querySelectorAll('#pageTabs [data-kind="pageTab"]').forEach(function (btn) {
    btn.classList.toggle("active", btn.dataset.page === activePage);
  });
}

let panelToken = "";

async function ensurePanelToken() {
  if (panelToken) return panelToken;
  const r = await fetch("/api/auth/bootstrap", {
    headers: {"Accept": "application/json"},
  });
  const data = await r.json();
  if (!r.ok || !data.token) throw new Error(data.msg || "面板认证失败");
  panelToken = data.token;
  return panelToken;
}

async function jget(url) {
  await ensurePanelToken();
  const r = await fetch(url, {
    headers: {"X-WRelay-Token": panelToken},
  });
  return r.json();
}
async function jpost(url, body) {
  await ensurePanelToken();
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WRelay-Token": panelToken,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

function openBackupRestore() {
  document.getElementById("backupRestoreModal").style.display = "flex";
}
function closeBackupRestore() {
  document.getElementById("backupRestoreModal").style.display = "none";
}
async function exportBackup() {
  await ensurePanelToken();
  const runtime = document.getElementById("backupIncludeRuntime").checked;
  const r = await fetch("/api/backup/export?include_runtime_state=" + (runtime ? "true" : "false"), {
    headers: {"X-WRelay-Token": panelToken},
  });
  if (!r.ok) throw new Error("导出失败");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = "wrelay-backup.zip"; a.click();
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}
async function restoreBackup() {
  const file = document.getElementById("backupRestoreFile").files[0];
  if (!file) { showAlert("恢复配置", "请先选择 ZIP 备份文件"); return; }
  const runtime = document.getElementById("backupIncludeRuntime").checked;
  if (runtime && !confirm("风险提示：恢复游标或重试队列可能造成重复投递、漏发或改变待补发状态。确定继续吗？")) return;
  await ensurePanelToken();
  const r = await fetch("/api/backup/restore?include_runtime_state=" + (runtime ? "true" : "false") + "&confirm_runtime_state=" + (runtime ? "true" : "false"), {
    method: "POST", headers: {"Content-Type": "application/zip", "X-WRelay-Token": panelToken}, body: file,
  });
  const data = await r.json();
  if (!r.ok || !data.ok) throw new Error(data.msg || "恢复失败");
  closeBackupRestore();
  await refreshSettings();
  showAlert("恢复配置", data.msg || "恢复完成");
}

// 运行日志自动跟随：仅当用户已在日志底部时新日志才滚到底部，向上翻阅时暂停，滚回底部自动恢复
var logFollow = true;
document.getElementById("log").addEventListener("scroll", function () {
  const el = document.getElementById("log");
  logFollow = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
});

function renderStatus(st) {
  const b = document.getElementById("badge");
  if (st.running) {
    document.getElementById("btnStart").disabled = true;
    document.getElementById("btnStop").disabled = false;
    document.getElementById("btnRestart").disabled = false;
    const h = st.health;
    if (activePage === "x" || activePage === "qq") {
      // X → QQ / QQ → QQ 页面只关心 QQ 连接状态，不显示无关的 Discord 状态。
      if (h && h.qq) {
        b.textContent = "运行中";
        b.className = "badge on";
      } else if (h) {
        b.textContent = "运行中（QQ 未连接）";
        b.className = "badge warn";
      } else {
        b.textContent = "运行中（检测中…）";
        b.className = "badge warn";
      }
    } else if (h && h.discord && h.qq) {
      b.textContent = "运行中";
      b.className = "badge on";
    } else if (h) {
      b.textContent = h.discord ? "运行中（QQ 未连接）" : h.qq ? "运行中（Discord 未连接）" : "运行中（未连接）";
      b.className = "badge warn";
    } else {
      b.textContent = "运行中（检测中…）";
      b.className = "badge warn";
    }
  } else {
    b.textContent = "已停止";
    b.className = "badge off";
    document.getElementById("btnStart").disabled = false;
    document.getElementById("btnStop").disabled = true;
    document.getElementById("btnRestart").disabled = true;
  }
  document.getElementById("pidinfo").textContent = st.pid ? ("PID: " + st.pid) : "";
  const stats = st.stats || {};
  const retry = (st.health && st.health.retry) || {};
  const retryText = retry.pending_entries
    ? " · 待重试 " + retry.pending_entries + " 条"
    : "";
  const statsEl = document.getElementById("stats");
  statsEl.textContent = (stats.total_forwarded || stats.total_failed || retry.pending_entries)
    ? ("今日转发 " + (stats.today_forwarded || 0) + " 条 · 累计 " + (stats.total_forwarded || 0) + " 条 · 失败 " + (stats.total_failed || 0) + " 条" + retryText)
    : "";
  const lvlEl = document.getElementById("logLevel");
  if (lvlEl && st.level) lvlEl.value = st.level;
  const hint = document.getElementById("managedHint");
  hint.textContent = st.running && !st.managed
    ? "机器人由其他方式启动，面板无法完全控制；请先手动停止后再接管。"
    : "";
  const el = document.getElementById("log");
  const stick = logFollow;
  el.textContent = st.log || "（暂无日志）";
  if (stick) el.scrollTop = el.scrollHeight;
  renderTranslationRuntimeStatus(st);
  renderAutostart(document.getElementById("btnAutostart"), st.autostart);
}

function renderTranslationRuntimeStatus(st) {
  const health = (st && st.health) || {};
  const runtime = health.translation || {};
  let text = st && st.running ? "当前工作模型：尚未成功翻译" : "当前工作模型：机器人未运行";
  if (runtime.provider === "ai" && runtime.model) {
    text = "当前工作模型：" + runtime.model;
  } else if (runtime.provider === "tencent_tmt") {
    text = "当前翻译服务：腾讯 TMT";
  }
  ["translationRuntimeStatus", "xTranslationRuntimeStatus"].forEach(function (id) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  });
}

// 开机自启按钮：√ / × 用 SVG 图标绘制（不使用文字字符），状态记录在 data-on 上供点击切换判断
function renderAutostart(btn, on) {
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  var icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>开机自启</span>";
}

// 离线补发开关按钮：与开机自启同款（√/× SVG + 文字），data-on 记录状态供点击切换
function renderBackfillToggle(on) {
  const btn = document.getElementById("btnBackfillToggle");
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  var icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>启用补发</span>";
}

function renderFailureNotificationToggle(on) {
  const btn = document.getElementById("btnFailureNotificationToggle");
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  const icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>失败通知</span>";
}

let scheduledMessageDraft = null;
let scheduledMessageId = 0;

function renderScheduledMessageToggle(on) {
  const btn = document.getElementById("btnScheduledMessageToggle");
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  const icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>启用定时发送</span>";
}

function scheduledLocalDateTimeValue() {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60000).toISOString().slice(0, 16);
}

function newScheduledMessage() {
  scheduledMessageId += 1;
  return {
    id: "message-" + Date.now() + "-" + scheduledMessageId,
    content: "",
    schedule_type: "interval",
    interval_days: 1,
    interval_hours: 0,
    interval_minutes: 0,
    anchor_at: scheduledLocalDateTimeValue(),
    calendar_type: "weekly",
    weekdays: [0],
    day_of_month: 1,
    month: 1,
    day: 1,
    send_time: "09:00",
  };
}

function normalizeScheduledMessages(config) {
  const value = config || {};
  if (Array.isArray(value.messages)) {
    return value.messages.map(function (raw, index) {
      const message = Object.assign(newScheduledMessage(), raw || {});
      message.id = String(message.id || "message-" + (index + 1));
      message.content = String(message.content || "");
      message.schedule_type = message.schedule_type === "calendar" ? "calendar" : "interval";
      message.interval_days = Math.max(0, Math.min(3650, Number(message.interval_days) || 0));
      message.interval_hours = Math.max(0, Math.min(23, Number(message.interval_hours) || 0));
      message.interval_minutes = Math.max(0, Math.min(59, Number(message.interval_minutes) || 0));
      message.anchor_at = typeof message.anchor_at === "string" ? message.anchor_at : scheduledLocalDateTimeValue();
      message.calendar_type = ["weekly", "monthly", "yearly"].includes(message.calendar_type)
        ? message.calendar_type : "weekly";
      message.weekdays = Array.isArray(message.weekdays)
        ? message.weekdays.map(Number).filter(function (day) { return day >= 0 && day <= 6; })
        : [0];
      if (!message.weekdays.length) message.weekdays = [0];
      message.day_of_month = Math.max(1, Math.min(31, Number(message.day_of_month) || 1));
      message.month = Math.max(1, Math.min(12, Number(message.month) || 1));
      message.day = Math.max(1, Math.min(31, Number(message.day) || 1));
      message.send_time = /^([01]\d|2[0-3]):[0-5]\d$/.test(message.send_time || "")
        ? message.send_time : "09:00";
      return message;
    });
  }
  if (String(value.content || "").trim()) {
    const legacy = newScheduledMessage();
    legacy.content = value.content;
    legacy.send_time = /^([01]\d|2[0-3]):[0-5]\d$/.test(value.send_time || "")
      ? value.send_time : "09:00";
    if (value.schedule_mode === "weekly") {
      legacy.schedule_type = "calendar";
      legacy.calendar_type = "weekly";
      legacy.weekdays = Array.isArray(value.weekdays) && value.weekdays.length ? value.weekdays : [0];
    } else if (value.schedule_mode === "monthly") {
      legacy.schedule_type = "calendar";
      legacy.calendar_type = "monthly";
      legacy.day_of_month = Number(value.anchor_date && value.anchor_date.slice(-2)) || 1;
    } else {
      legacy.interval_days = Math.max(1, Number(value.every_days) || 1);
      legacy.anchor_at = value.anchor_date ? value.anchor_date + "T" + legacy.send_time : legacy.anchor_at;
    }
    return [legacy];
  }
  return [];
}

function scheduledOptionMarkup(message, index) {
  const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  return '<div class="scheduled-message-entry" data-index="' + index + '">' +
    '<div class="scheduled-message-entry-header"><strong>消息 ' + (index + 1) + '</strong>' +
    '<button type="button" class="rowicon sec scheduled-message-remove" title="删除消息" aria-label="删除消息">×</button></div>' +
    '<label class="scheduled-message-entry-label">消息正文' +
    '<textarea class="scheduled-message-entry-content" data-field="content" maxlength="20000" placeholder="输入消息正文"></textarea></label>' +
    '<div class="scheduled-message-entry-row">' +
    '<label>发送方式<select class="scheduled-message-entry-select" data-field="schedule_type">' +
    '<option value="interval">按间隔发送</option><option value="calendar">按日历发送</option></select></label>' +
    '</div>' +
    '<div class="scheduled-message-interval-fields">' +
    '<label>基准日期时间<input type="datetime-local" lang="zh-CN" data-field="anchor_at"></label>' +
    '<div class="scheduled-message-interval-row"><span>每隔</span>' +
    '<label><input type="number" data-field="interval_days" min="0" max="3650" step="1"> 日</label>' +
    '<label><input type="number" data-field="interval_hours" min="0" max="23" step="1"> 时</label>' +
    '<label><input type="number" data-field="interval_minutes" min="0" max="59" step="1"> 分</label></div>' +
    '</div>' +
    '<div class="scheduled-message-calendar-fields">' +
    '<div class="scheduled-message-entry-row"><label>日历周期<select class="scheduled-message-entry-select" data-field="calendar_type">' +
    '<option value="weekly">每周</option><option value="monthly">每月</option><option value="yearly">每年</option></select></label>' +
    '<label>发送时间<input type="time" data-field="send_time"></label></div>' +
    '<div class="scheduled-message-calendar-panel" data-calendar-type="weekly"><span>星期</span><div class="scheduled-message-weekdays">' +
    weekdays.map(function (label, day) {
      return '<label><input type="checkbox" data-field="weekday" value="' + day + '">' + label + '</label>';
    }).join("") + '</div></div>' +
    '<div class="scheduled-message-calendar-panel" data-calendar-type="monthly">' +
    '<label>每月 <input type="number" data-field="day_of_month" min="1" max="31" step="1"> 号</label></div>' +
    '<div class="scheduled-message-calendar-panel" data-calendar-type="yearly">' +
    '<label>每年 <select data-field="month">' + Array.from({length: 12}, function (_, month) {
      return '<option value="' + (month + 1) + '">' + (month + 1) + ' 月</option>';
    }).join("") + '</select></label>' +
    '<label><input type="number" data-field="day" min="1" max="31" step="1"> 号</label></div>' +
    '</div></div>';
}

function updateScheduledMessageEntryVisibility(entry) {
  const type = entry.querySelector('[data-field="schedule_type"]');
  const interval = entry.querySelector(".scheduled-message-interval-fields");
  const calendar = entry.querySelector(".scheduled-message-calendar-fields");
  const isCalendar = type && type.value === "calendar";
  if (interval) interval.style.display = isCalendar ? "none" : "flex";
  if (calendar) calendar.style.display = isCalendar ? "flex" : "none";
  const calendarType = entry.querySelector('[data-field="calendar_type"]');
  entry.querySelectorAll(".scheduled-message-calendar-panel").forEach(function (panel) {
    panel.style.display = isCalendar && calendarType && panel.dataset.calendarType === calendarType.value ? "flex" : "none";
  });
}

function renderScheduledMessageEditor() {
  const container = document.getElementById("scheduledMessageEntries");
  if (!container) return;
  const messages = scheduledMessageDraft || [];
  container.innerHTML = messages.map(scheduledOptionMarkup).join("");
  container.querySelectorAll(".scheduled-message-entry").forEach(function (entry, index) {
    const message = messages[index];
    entry.querySelector('[data-field="content"]').value = message.content || "";
    entry.querySelector('[data-field="schedule_type"]').value = message.schedule_type;
    ["anchor_at", "interval_days", "interval_hours", "interval_minutes", "day_of_month", "month", "day", "send_time", "calendar_type"].forEach(function (key) {
      const field = entry.querySelector('[data-field="' + key + '"]');
      if (field && message[key] !== undefined && message[key] !== null) field.value = message[key];
    });
    entry.querySelectorAll('[data-field="weekday"]').forEach(function (field) {
      field.checked = message.weekdays.includes(Number(field.value));
    });
    updateScheduledMessageEntryVisibility(entry);
  });
}

function readScheduledMessageEditor() {
  const container = document.getElementById("scheduledMessageEntries");
  if (!container) return [];
  return Array.from(container.querySelectorAll(".scheduled-message-entry")).map(function (entry, index) {
    const get = function (key) { return entry.querySelector('[data-field="' + key + '"]'); };
    const type = get("schedule_type").value;
    const message = {
      id: (scheduledMessageDraft[index] && scheduledMessageDraft[index].id) || ("message-" + (index + 1)),
      content: get("content").value,
      schedule_type: type,
      interval_days: Number(get("interval_days").value) || 0,
      interval_hours: Number(get("interval_hours").value) || 0,
      interval_minutes: Number(get("interval_minutes").value) || 0,
      anchor_at: get("anchor_at").value,
      calendar_type: get("calendar_type").value,
      weekdays: Array.from(entry.querySelectorAll('[data-field="weekday"]:checked')).map(function (field) { return Number(field.value); }),
      day_of_month: Number(get("day_of_month").value) || 1,
      month: Number(get("month").value) || 1,
      day: Number(get("day").value) || 1,
      send_time: get("send_time").value || "09:00",
    };
    return message;
  });
}

function openScheduledMessageEditor() {
  const modal = document.getElementById("scheduledMessageContentModal");
  if (!modal) return;
  scheduledMessageDraft = normalizeScheduledMessages(settings && settings.scheduled_message);
  renderScheduledMessageEditor();
  modal.style.display = "flex";
  const first = document.querySelector(".scheduled-message-entry-content");
  if (first) first.focus();
}

async function confirmScheduledMessageEditor() {
  const messages = readScheduledMessageEditor();
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (!message.content.trim()) {
      showAlert("定时消息", "消息 " + (index + 1) + " 的正文不能为空");
      return;
    }
    if (message.schedule_type === "interval" && !message.interval_days && !message.interval_hours && !message.interval_minutes) {
      showAlert("定时消息", "消息 " + (index + 1) + " 的间隔至少需要 1 分钟");
      return;
    }
    if (message.schedule_type === "calendar" && message.calendar_type === "weekly" && !message.weekdays.length) {
      showAlert("定时消息", "消息 " + (index + 1) + " 至少选择一个星期");
      return;
    }
  }
  settings.scheduled_message = Object.assign({}, settings.scheduled_message || {}, {
    enabled: messages.length ? (settings.scheduled_message || {}).enabled === true : false,
    content: messages.length ? messages[0].content : "",
    messages: messages,
  });
  const saved = await saveAndReload();
  if (saved) {
    scheduledMessageDraft = null;
    document.getElementById("scheduledMessageContentModal").style.display = "none";
  }
}

function initScheduledPickers() {
  const editButton = document.getElementById("btnScheduledMessageEdit");
  if (editButton) {
    editButton.innerHTML = SVG_PEN;
    editButton.addEventListener("click", openScheduledMessageEditor);
  }
  document.getElementById("btnAddScheduledMessage").addEventListener("click", function () {
    scheduledMessageDraft = readScheduledMessageEditor();
    scheduledMessageDraft.push(newScheduledMessage());
    renderScheduledMessageEditor();
  });
  document.getElementById("scheduledMessageEntries").addEventListener("click", function (event) {
    const remove = event.target.closest(".scheduled-message-remove");
    if (!remove) return;
    const entry = remove.closest(".scheduled-message-entry");
    if (!entry) return;
    scheduledMessageDraft = readScheduledMessageEditor();
    const index = Number(entry.dataset.index);
    scheduledMessageDraft.splice(index, 1);
    renderScheduledMessageEditor();
  });
  document.getElementById("scheduledMessageEntries").addEventListener("change", function (event) {
    const entry = event.target.closest(".scheduled-message-entry");
    if (entry && (event.target.dataset.field === "schedule_type" || event.target.dataset.field === "calendar_type")) {
      updateScheduledMessageEntryVisibility(entry);
    }
  });
  document.getElementById("scheduledMessageContentConfirm").addEventListener("click", confirmScheduledMessageEditor);
}

initScheduledPickers();

function scheduledMessageEditorFocused() {
  const modal = document.getElementById("scheduledMessageContentModal");
  return !!(modal && modal.style.display !== "none");
}

function renderScheduledMessage(config) {
  const value = config || {};
  const messages = normalizeScheduledMessages(value);
  const summary = document.getElementById("scheduledMessageSummary");
  if (summary) {
    summary.textContent = messages.length
      ? "已配置 " + messages.length + " 条消息"
      : "尚未添加消息";
  }
  const groupSummary = document.getElementById("scheduledMessageGroupSummary");
  if (groupSummary) {
    const selected = (value.target_groups || []).map(function (id) { return String(id); });
    const groups = settings && Array.isArray(settings.qq_group_openids) ? settings.qq_group_openids : [];
    const byId = {};
    groups.forEach(function (group) { byId[String(group.openid || "")] = group; });
    const rows = selected.map(function (id) { return byId[id]; }).filter(Boolean);
    if (!rows.length) {
      groupSummary.textContent = "当前未添加目标群";
    } else {
      groupSummary.innerHTML = rows.map(function (group) {
        const id = String(group.openid || "");
        const name = group.name || group.remark || identityName("groups", id) || id;
        const suffix = group.enabled === true ? "" : "（未启用）";
        return '<span class="scheduled-message-group-summary-item">' + escapeHtml(name + suffix) + '</span>';
      }).join("、");
    }
  }
  renderScheduledMessageToggle(value.enabled === true);
}

async function saveScheduledMessageSettings(enabled) {
  const current = settings.scheduled_message || {};
  const messages = normalizeScheduledMessages(current);
  const config = Object.assign({}, current, {messages: messages});
  if (enabled !== undefined) config.enabled = enabled;
  if (config.enabled && !messages.length) {
    showAlert("定时消息", "请先添加至少一条消息");
    return false;
  }
  if (config.enabled && !(config.target_groups || []).length) {
    showAlert("定时消息", "请至少选择一个已审核群");
    return false;
  }
  settings.scheduled_message = config;
  return await saveAndReload();
}

// ---------- 转发组（Discord / X / QQ→QQ 共用机制，按 ctx 区分） ----------
function getFgList(ctx) {
  return (settings && settings[ctx.field]) || [];
}
function getActiveFg(ctx) {
  const list = getFgList(ctx);
  if (!list.length) return null;
  let fg = null;
  if (ctx.activeId) {
    for (let i = 0; i < list.length; i++) {
      if (list[i].id === ctx.activeId) { fg = list[i]; break; }
    }
  }
  if (!fg) {
    // 当前 id 失效（组被删除/首次访问）：回退第一个组并固定记录
    fg = list[0];
    ctx.activeId = fg.id;
    try { localStorage.setItem(ctx.storageKey, ctx.activeId); } catch (e) {}
  }
  return fg;
}
function setActiveFg(ctx, id) {
  ctx.activeId = id;
  try { localStorage.setItem(ctx.storageKey, id); } catch (e) {}
  renderSettings();
}
function toggleFgMember(fg, id, field, on) {
  // 只修改当前组的成员集合（channels/sources/groups）；id 统一转字符串存储
  if (!fg) return;
  const idStr = String(id);
  const arr = fg[field] || [];
  const idx = arr.indexOf(idStr);
  if (on && idx < 0) arr.push(idStr);
  if (!on && idx >= 0) arr.splice(idx, 1);
}
function removeFgMember(ctx, id, field) {
  // 只把实体从当前转发组移除（脱组）：不删全局实体、不影响其他组
  const fg = getActiveFg(ctx);
  if (!fg) return;
  const idStr = String(id);
  const arr = fg[field] || [];
  const idx = arr.indexOf(idStr);
  if (idx >= 0) arr.splice(idx, 1);
  saveAndReload();
}
function fgCandidate(kind) {
  // kind → 已注册实体列表 / 条目 id / 展示文案（各转发组共用）
  if (kind === "group") {
    return {
      all: settings.qq_group_openids,
      idOf: function (g) { return String(g.openid); },
      labelOf: function (g) {
        const gname = g.name || g.remark || identityName("groups", g.openid) || "";
        return (gname + " " + g.openid).trim();
      },
    };
  }
  if (kind === "chan") {
    return {
      all: settings.discord_channels,
      idOf: function (c) { return String(c.id); },
      labelOf: function (c) { return ((c.name || "") + " " + c.id).trim(); },
    };
  }
  // source（X 转发账号）：组内以 key（去 @ handle）为标识
  return {
    all: settings.x_rss_sources,
    idOf: function (s) { return xSourceKey(s.source); },
    labelOf: function (s) {
      const handle = xSourceKey(s.source);
      return ((s.name || handle) + " @" + handle).trim();
    },
  };
}
function openFgAddPicker(ctx, kind) {
  // 「从已注册项新增」：从全局已注册实体中选择加入当前转发组
  const fg = getActiveFg(ctx);
  if (!fg) return;
  const field = ctx.memberField[kind];
  const cand = fgCandidate(kind);
  const memberSet = {};
  (fg[field] || []).forEach(function (id) { memberSet[String(id)] = true; });
  const candidates = cand.all.filter(function (item) {
    return !memberSet[cand.idOf(item)];
  });
  if (!candidates.length) {
    showAlert(
      "从已注册项新增",
      "当前转发组已包含全部已注册的" + (kind === "group" ? "群" : kind === "chan" ? "频道" : "来源") + "，没有可添加的条目"
    );
    return;
  }
  showConfirmSelect(
    "从已注册项新增",
    candidates,
    cand.labelOf,
    async function (selected) {
      selected.forEach(function (item) {
        toggleFgMember(fg, cand.idOf(item), field, true);
      });
      await saveAndReload();
    },
    {
      defaultChecked: false,
      labelLinkFn: kind === "source" ? function (item) {
        return xSourceProfileUrl(item.source);
      } : null,
      onRowDelete: function (item) {
        deleteRegisteredItem(kind, item, function () { openFgAddPicker(ctx, kind); });
      },
    }
  );
}

function openScheduledGroupPicker() {
  settings.scheduled_message = settings.scheduled_message || {};
  const selected = new Set((settings.scheduled_message.target_groups || []).map(String));
  const groups = (settings.qq_group_openids || []).filter(function (group) {
    return group && group.openid;
  });
  if (!groups.length) {
    showAlert("管理定时消息目标群", "当前没有已审核的 QQ 群");
    return;
  }
  showConfirmSelect(
    "管理定时消息目标群",
    groups,
    function (group) {
      const id = String(group.openid);
      const name = group.name || group.remark || identityName("groups", id) || "";
      const state = selected.has(id) ? "（已添加）" : "（未添加）";
      const enabled = group.enabled === true ? "" : "（当前未启用）";
      return (name + " " + id).trim() + state + enabled;
    },
    async function (picked) {
      settings.scheduled_message.target_groups = picked.map(function (group) {
        return String(group.openid);
      });
      await saveAndReload();
    },
    {
      defaultChecked: function (group) { return selected.has(String(group.openid)); },
      okText: "保存选择",
    }
  );
}

// 白名单「＋ 新增」：从已注册用户中选择加入白名单（勾选 = 放行 enabled）
function openUserAddPicker() {
  const candidates = (settings.qq_user_openids || []).filter(function (u) { return u.enabled !== true; });
  if (!candidates.length) {
    showAlert("从已注册项新增", "当前没有未加入白名单的已注册用户");
    return;
  }
  showConfirmSelect(
    "从已注册项新增",
    candidates,
    function (u) {
      const uname = u.name || u.remark || identityName("users", u.openid) || "";
      return (uname + " " + u.openid).trim();
    },
    async function (selected) {
      selected.forEach(function (u) { u.enabled = true; });
      await saveAndReload();
    },
    {
      defaultChecked: false,
      onRowDelete: function (u) { deleteRegisteredItem("user", u); },
    }
  );
}

// 「从已注册项新增」窗口行尾「×」：删除该条目的注册记录（从全局实体列表移除；
// 保存时面板 _normalize_forwarding_groups 会剔除它在各转发组中的悬空引用）。
// 与表格行内"删除"（仅移出当前转发组、保留全局实体）严格区分。删除后需重新走
// 注册流程（重新扫描 / 重新注册审核）才能再次加入转发组或白名单。
function deleteRegisteredItem(kind, item, reopenPicker) {
  const isGroup = kind === "group";
  const isChan = kind === "chan";
  const isSource = kind === "source";
  const id = String(isChan ? item.id : isSource ? xSourceKey(item.source) : item.openid);
  const name = isGroup
    ? (item.name || item.remark || identityName("groups", item.openid) || "")
    : isChan
      ? (item.name || "")
      : isSource
        ? (item.name || xSourceKey(item.source) || "")
        : (item.name || item.remark || identityName("users", item.openid) || "");
  const label = ((name || "") + " " + id).trim();
  const kindName = isGroup ? "群" : isChan ? "频道" : isSource ? "来源" : "用户";
  hideConfirm();
  showConfirmDlg("删除注册记录",
    "确定删除「" + label + "」的注册记录？\n删除后将从已注册列表移除，并从所属转发组中移出；如需使用请重新注册。",
    async function () {
      if (isGroup) {
        const idx = settings.qq_group_openids.findIndex(function (o) { return String(o.openid) === id; });
        if (idx >= 0) settings.qq_group_openids.splice(idx, 1);
      } else if (isChan) {
        const idx = settings.discord_channels.findIndex(function (o) { return String(o.id) === id; });
        if (idx >= 0) settings.discord_channels.splice(idx, 1);
      } else if (isSource) {
        const idx = settings.x_rss_sources.findIndex(function (o) { return xSourceKey(o.source) === id; });
        if (idx >= 0) settings.x_rss_sources.splice(idx, 1);
      } else {
        const idx = settings.qq_user_openids.findIndex(function (o) { return String(o.openid) === id; });
        if (idx >= 0) settings.qq_user_openids.splice(idx, 1);
      }
      await saveAndReload();
      // 删除后重开选择窗口：默认回到原页面自己的「从已注册项新增」
      if (reopenPicker) reopenPicker();
      else if (kind === "user") openUserAddPicker();
    }, "删除");
}
// 「从全部可读频道注册」：整合原「同步可读频道」——扫描 Bot 可读的频道，
// 弹窗勾选后登记为全局频道（默认未启用，只进入已注册项、不自动加入当前转发组）。
async function scanReadableChannels(btn) {
  if (btn) btn.disabled = true;
  let r;
  try {
    r = await jpost("/api/channels/refresh");
  } finally {
    if (btn) btn.disabled = false;
  }
  if (!r.ok) { showAlert("扫描失败", r.msg || "扫描失败"); return; }
  const list = (r.audit && r.audit.readable) || [];
  const known = {};
  settings.discord_channels.forEach(function (c) { known[String(c.id)] = true; });
  const pending = list.filter(function (ch) { return !known[String(ch.id)]; });
  if (!pending.length) { showAlert("注册结果", "没有发现新频道"); return; }
  showConfirmSelect("全部可读频道", pending, function (ch) { return ch.name + " (" + ch.id + ")"; }, async function (selected) {
    if (!selected.length) { showAlert("注册结果", "未选择任何频道"); return; }
    let added = 0;
    selected.forEach(function (ch) {
      const id = String(ch.id);
      if (known[id]) return;
      settings.discord_channels.push({id: id, name: String(ch.name || "").replace(/^#/, ""), enabled: false});
      known[id] = true;
      added++;
    });
    if (added) {
      await saveAndReload();
    } else {
      renderSettings();
    }
    showAlert("注册结果", "已注册 " + added + " 个新频道（默认未启用）");
  }, {defaultChecked: false, groupBy: function (ch) { return ch.guild; }});
}
function renderFgTabs(ctx) {
  const wrap = document.getElementById(ctx.tabsId);
  if (!wrap) return;
  // 重命名进行中：跳过本上下文的 tab 区重建，保留重命名输入框与已输入内容
  // （30 秒周期 refreshIdentities 等触发的 renderSettings 不会销毁输入框；
  // 其余区域照常刷新，finish 后自动恢复正常渲染）
  if (fgRenameActive && fgRenameCtxKey === ctx.key) return;
  const list = getFgList(ctx);
  getActiveFg(ctx);   // 顺带校正 activeId（失效则回退并持久化）
  wrap.innerHTML = "";
  const ordered = list.slice();
  ordered.forEach(function (fg) {
    const isActive = fg.id === ctx.activeId;
    const enabled = fg.enabled !== false;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "fgtab" + (isActive ? " active" : "") + (enabled ? "" : " paused");
    tab.dataset.kind = "fgTab";
    tab.dataset.ctx = ctx.key;
    tab.dataset.id = fg.id;
    tab.textContent = fg.name;
    wrap.appendChild(tab);
    if (isActive) {
      // 暂停/恢复、重命名/删除只对当前组可用（与 tab 并列，避免按钮嵌套）
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "fg-toggle rowicon" + (enabled ? "" : " paused");
      toggle.dataset.kind = "fgToggle";
      toggle.dataset.ctx = ctx.key;
      toggle.dataset.id = fg.id;
      toggle.title = enabled ? "暂停该转发组" : "恢复该转发组";
      toggle.setAttribute("aria-label", toggle.title);
      toggle.innerHTML = enabled ? SVG_PAUSE : SVG_PLAY;
      wrap.appendChild(toggle);
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "fgctrl rowicon";
      rename.dataset.kind = "fgRename";
      rename.dataset.ctx = ctx.key;
      rename.innerHTML = SVG_PEN;
      wrap.appendChild(rename);
      const canDelete = list.length > 1 || (ctx.key === "qq" && (fg.groups || []).length <= 1);
      if (canDelete) {
        const del = document.createElement("button");
        del.type = "button";
        del.className = "fgctrl rowicon";
        del.dataset.kind = "fgDel";
        del.dataset.ctx = ctx.key;
        del.innerHTML = SVG_TRASH;   // 与成员行内删除按钮同一白色垃圾桶图标
        wrap.appendChild(del);
      }
    }
  });
  // 达到上限(10)后直接不显示新增按钮，删除一个后自动恢复
  if (list.length < MAX_FG) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "fgtab-add";
    add.id = ctx.addBtnId;
    add.textContent = "＋ 添加转发组";
    wrap.appendChild(add);
  }
}
function addForwardingGroup(ctx) {
  const list = getFgList(ctx);
  if (list.length >= MAX_FG) { showAlert("添加转发组", "最多 " + MAX_FG + " 个转发组"); return; }
  // 空组：不复制任何成员；名称自动取未占用的「转发组N」（从 1 起找）
  let n = 1;
  const names = {};
  list.forEach(function (g) { names[g.name] = true; });
  while (names["转发组" + n]) n++;
  const fg = {
    id: ctx.idPrefix + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    name: "转发组" + n,
    enabled: true,
  };
  ctx.emptyMembers.forEach(function (f) { fg[f] = []; });
  list.push(fg);
  ctx.activeId = fg.id;
  try { localStorage.setItem(ctx.storageKey, fg.id); } catch (e) {}
  saveAndReload();
}
let fgRenameActive = false;
let fgRenameCtxKey = null;   // 正在重命名的转发组上下文 key（discord / x）
function startFgRename(ctx) {
  const fg = getActiveFg(ctx);
  // 转发组重命名
  if (!fg || fgRenameActive) return;
  const wrap = document.getElementById(ctx.tabsId);
  const tabs = Array.prototype.slice.call(wrap.querySelectorAll(".fgtab"));
  const tabBtn = tabs.find(function (b) { return b.dataset.id === fg.id; });
  if (!tabBtn) return;
  fgRenameActive = true;
  fgRenameCtxKey = ctx.key;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "fg-rename";
  input.value = fg.name;
  input.style.width = "110px";
  input.style.padding = "4px 8px";
  input.style.fontSize = "13px";
  tabBtn.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = function (commit) {
    if (done) return;
    done = true;
    fgRenameActive = false;
    fgRenameCtxKey = null;
    const v = input.value.trim();
    if (commit && v && v !== fg.name) {
      fg.name = v;   // 只改 name，id 保持不变
      saveAndReload();
    } else {
      renderSettings();
    }
  };
  input.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", function () { finish(true); });
}

function renderSettings() {
  renderAIControls();
  renderFgTabs(FG_DISCORD);
  const activeFg = getActiveFg(FG_DISCORD);
  const gb = document.getElementById("groupBody");
  gb.innerHTML = "";
  // 只渲染当前转发组的成员；「启用/测试」仍是全局实体状态
  const memberGrps = {};
  if (activeFg) {
    (activeFg.groups || []).forEach(function (o) { memberGrps[String(o)] = true; });
  }
  const grpRows = settings.qq_group_openids.filter(function (g) { return memberGrps[String(g.openid)]; });
  if (!grpRows.length) {
    gb.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增群，点击下方「新增」按钮来新增第一个群吧！</td></tr>';
  }
  grpRows.forEach((g) => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(g.openid));
    const gi = settings.qq_group_openids.indexOf(g);
    // 名称/账号：注册审核通过时写入（无注册信息的手动条目为空），
    // 名称缺省时回退显示身份库自动识别的群名
    const gname = g.name || identityName("groups", g.openid) || "";
    const gacc = g.qq_id || "";
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="group" data-i="' + gi + '"' + (g.enabled ? " checked" : "") + '></td>' +
      '<td>' +
        (gname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(gname) + '</div>' : '') +
      '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(gacc) + '</td>' +
      '<td class="mono">' + escapeHtml(g.openid) + '</td>' +
      '<td style="text-align:right"><button class="danger rowicon" data-kind="fgGroupRemove" data-id="' + escapeHtml(String(g.openid)) + '">' + SVG_TRASH + '</button></td>';
    gb.appendChild(tr);
  });
  const cb = document.getElementById("chanBody");
  cb.innerHTML = "";
  // 只渲染当前转发组的成员
  const memberChans = {};
  if (activeFg) {
    (activeFg.channels || []).forEach(function (id) { memberChans[String(id)] = true; });
  }
  const chanRows = settings.discord_channels.filter(function (c) { return memberChans[String(c.id)]; });
  if (!chanRows.length) {
    cb.innerHTML =
      '<tr class="fgempty-row"><td colspan="5" class="fgempty-msg">还未新增频道，点击下方「新增」按钮来新增第一个频道吧！</td></tr>';
  }
  chanRows.forEach((c) => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(c.id));
    const gi = settings.discord_channels.indexOf(c);
    const realName = channelNames[c.id] || "";
    const channelUrl = discordChannelUrl(c.id);
    const realNameHtml = channelUrl
      ? '<a class="discord-channel-link" href="' + escapeHtml(channelUrl) + '" target="_blank" rel="noopener noreferrer" title="打开 Discord 频道">' + escapeHtml(realName) + '</a>'
      : escapeHtml(realName);
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="chan" data-i="' + gi + '"' + (c.enabled ? " checked" : "") + '></td>' +
      '<td>' +
        '<span class="chan-name" data-kind="chanNameDisplay" data-i="' + gi + '">' +
          '<span class="chan-name-text">' + escapeHtml(c.name || "") + '</span>' +
          '<span class="chan-name-edit" data-kind="chanNameEdit" data-i="' + gi + '" title="编辑频道名称">' + SVG_PEN + '</span>' +
        '</span>' +
        (realName ? '<div style="color:var(--name-green);font-size:12px;">' + realNameHtml + '</div>' : '') +
      '</td>' +
      '<td class="mono">' + escapeHtml(c.id) + '</td>' +
      '<td><div style="display:flex;gap:4px;justify-content:flex-end;">' +
        '<button class="sec rowicon translation-toggle-icon' + (c.translation_enabled ? ' translation-active' : '') + '" title="翻译设置" aria-label="翻译设置" data-kind="chanTranslation" data-i="' + gi + '">' + SVG_TRANSLATE + '</button>' +
        '<button class="sec rowicon' + (channelFilterActive(c) ? ' filter-active' : '') + '" data-kind="chanFilter" data-i="' + gi + '">' + SVG_FUNNEL + '</button>' +
        '<button class="danger rowicon" data-kind="fgChanRemove" data-id="' + escapeHtml(String(c.id)) + '">' + SVG_TRASH + '</button>' +
      '</div></td>';
    cb.appendChild(tr);
  });
  const ub = document.getElementById("userBody");
  if (ub) {
    ub.innerHTML = "";
    if (!(settings.qq_user_openids || []).length) {
      ub.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增账号，点击下方「新增」按钮来新增第一个账号吧！</td></tr>';
    }
    (settings.qq_user_openids || []).forEach((u) => {
      const ui = settings.qq_user_openids.indexOf(u);   // 原始索引：增删改仍按 settings 数组定位
      const tr = document.createElement("tr");
      tr.setAttribute("data-id", String(u.openid));
      // 名称/账号：注册审核通过时写入（无注册信息的手动条目为空），
      // 名称缺省时回退显示身份库自动识别的昵称
      const uname = u.name || identityName("users", u.openid) || "";
      const uacc = u.qq_id || "";
      tr.innerHTML =
        '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
        '<td style="text-align:center"><input type="checkbox" data-kind="user" data-i="' + ui + '"' + (u.enabled ? " checked" : "") + '></td>' +
        '<td>' +
          (uname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(uname) + '</div>' : '') +
        '</td>' +
        '<td style="font-size:12px;">' + escapeHtml(uacc) + '</td>' +
        '<td class="mono">' + escapeHtml(u.openid) + '</td>' +
        '<td><div style="display:flex;gap:4px;justify-content:flex-end;">' +
        '<button class="sec rowicon' + (u.failure_notification ? ' filter-active' : '') + '" title="接收失败通知" aria-label="接收失败通知" data-kind="failureRecipient" data-i="' + ui + '">' + SVG_FUNNEL + '</button>' +
        '<button class="danger rowicon" data-kind="userDel" data-i="' + ui + '">' + SVG_TRASH + '</button>' +
        '</div></td>';
      ub.appendChild(tr);
    });
  }
  // 离线补发设置
  renderBackfillToggle(settings.backfill_enabled !== false);
  renderFailureNotificationToggle(!!(settings.failure_notification && settings.failure_notification.enabled));
  const bfLimit = document.getElementById("backfillLimit");
  if (bfLimit) bfLimit.value = Number(settings.backfill_limit) > 0 ? settings.backfill_limit : 10;
  renderScheduledMessage(settings.scheduled_message);
  renderRelayLinkCard();
  // 表头全选框状态仅反映「当前转发组」实际显示的成员（而非全部全局实体），
  // 避免隐藏的非本组成员导致半选误判：全部→勾选，全不→取消，部分→半选，无成员→两者均 false
  syncHeaderAll("groupAllEnabled", grpRows, "enabled");
  syncHeaderAll("chanAllEnabled", chanRows, "enabled");
  syncHeaderAll("userAllEnabled", settings.qq_user_openids || [], "enabled");
  // X → QQ / QQ → QQ 页面与 Discord 页共用同一份 settings，跟随每次保存/轮询同步刷新
  renderXPage();
  renderQQPage();
}

function renderAIControls() {
  if (!settings) return;
  const translation = settings.translation || {};
  document.getElementById("translationProvider").value = translation.provider || "tencent_tmt";
  document.getElementById("translationFallback").value = translation.fallback_provider || "";
  syncTranslationFallback("translationProvider", "translationFallback");
  renderTranslationToggle((settings.discord_channels || []).length > 0 && (settings.discord_channels || []).every(function (channel) {
    return channel.translation_enabled === true;
  }));
}

function aiProfilesForDisplay() {
  if (settings && Array.isArray(settings.ai_profiles) && settings.ai_profiles.length) {
    return settings.ai_profiles;
  }
  const ai = (settings && settings.ai) || {};
  return [{
    id: "default",
    name: "默认 AI",
    base_url: ai.base_url || "",
    model: ai.model || "",
    api_key_env: ai.api_key_env || "WRELAY_AI_API_KEY",
    timeout_seconds: Number(ai.timeout_seconds) > 0 ? ai.timeout_seconds : 25,
    max_output_tokens: Number(ai.max_output_tokens) > 0 ? ai.max_output_tokens : 800,
  }];
}

function currentAiProfileId() {
  const profiles = aiProfilesForDisplay();
  const active = settings && String(settings.ai_active_profile || "");
  return profiles.some(function (profile) { return String(profile.id) === active; })
    ? active
    : String(profiles[0].id);
}

function syncLegacyAiConfig(profile) {
  settings.ai = Object.assign({}, settings.ai || {});
  ["base_url", "model", "api_key_env", "timeout_seconds", "max_output_tokens"].forEach(function (key) {
    if (profile[key] !== undefined) settings.ai[key] = profile[key];
  });
}

function renderTranslationToggle(on) {
  renderTranslationToggleFor("btnTranslationToggle", on);
}

function renderTranslationToggleFor(buttonId, on) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  const icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>启用翻译</span>";
}

function saveAIControls() {
  settings.translation = settings.translation || {};
  settings.translation.provider = document.getElementById("translationProvider").value;
  settings.translation.fallback_provider = document.getElementById("translationFallback").value;
}

function renderXTranslationControls() {
  if (!settings) return;
  const translation = settings.x_translation || {};
  document.getElementById("xTranslationProvider").value = translation.provider || "tencent_tmt";
  document.getElementById("xTranslationFallback").value = translation.fallback_provider || "";
  syncTranslationFallback("xTranslationProvider", "xTranslationFallback");
  renderTranslationToggleFor("btnXTranslationToggle", (settings.x_rss_sources || []).length > 0 && (settings.x_rss_sources || []).every(function (source) {
    return source.translation_enabled === true;
  }));
}

function saveXTranslationControls() {
  settings.x_translation = settings.x_translation || {};
  settings.x_translation.provider = document.getElementById("xTranslationProvider").value;
  settings.x_translation.fallback_provider = document.getElementById("xTranslationFallback").value;
}

// 次选方式不允许与首选方式重复；如果切换首选后原次选被排除，
// 自动回退到“仅原文”（或第一个仍可用的选项）。
function syncTranslationFallback(primaryId, fallbackId) {
  const primary = document.getElementById(primaryId);
  const fallback = document.getElementById(fallbackId);
  if (!primary || !fallback) return;
  if (!fallback._allOptions) {
    fallback._allOptions = Array.from(fallback.options).map(function (option) {
      return option.cloneNode(true);
    });
  }
  const current = fallback.value;
  fallback.innerHTML = "";
  fallback._allOptions.forEach(function (option) {
    if (option.value !== primary.value) fallback.appendChild(option.cloneNode(true));
  });
  const available = Array.from(fallback.options);
  if (available.some(function (option) { return option.value === current; })) {
    fallback.value = current;
  } else if (available.some(function (option) { return option.value === ""; })) {
    fallback.value = "";
  } else if (available.length) {
    fallback.value = available[0].value;
  }
}


// ---------------- QQ → QQ 页面 ----------------
// QQ→QQ 使用独立的 qq_forwarding_groups；组内成员渲染沿用 Discord/X 页的
// 转发组交互，但不与它们共享组数组或路由关系。
function renderQQPage() {
  const groupBody = document.getElementById("qqFgGroupBody");
  if (!groupBody || !settings) return;
  renderFgTabs(FG_QQ);
  const qfg = getActiveFg(FG_QQ);
  const allGroups = settings.qq_group_openids || [];
  const memberGroups = {};
  if (qfg) {
    (qfg.groups || []).forEach(function (openid) { memberGroups[String(openid)] = true; });
  }
  const groupRows = allGroups.filter(function (group) {
    return memberGroups[String(group.openid)];
  });
  groupBody.innerHTML = "";
  if (!groupRows.length) {
    groupBody.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增群，点击下方「＋ 新增」来新增第一个群吧！</td></tr>';
  }
  groupRows.forEach(function (group) {
    const index = allGroups.indexOf(group);
    const name = group.name || identityName("groups", group.openid) || "";
    const account = group.qq_id || "";
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(group.openid));
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="group" data-i="' + index + '"' + (group.enabled ? " checked" : "") + '></td>' +
      '<td>' + (name ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(name) + '</div>' : '') + '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(account) + '</td>' +
      '<td class="mono">' + escapeHtml(group.openid) + '</td>' +
      '<td style="text-align:right"><button class="danger rowicon" data-kind="qqFgGroupRemove" data-id="' + escapeHtml(String(group.openid)) + '" title="移出当前转发组">' + SVG_TRASH + '</button></td>';
    groupBody.appendChild(tr);
  });
  syncHeaderAll("qqGroupAllEnabled", groupRows, "enabled");
}

// ---------------- X → QQ 页面 ----------------
// 转发组读写 settings.x_forwarding_groups（与 Discord forwarding_groups 完全
// 独立；组机制函数与 Discord 页共用，见 FG_X 上下文）；私聊白名单与 X RSS
// 状态仍读写原有全局字段。保存走现有 /api/settings/save 整体流程；
// 后端 x_rss.py 路由逻辑不在此触碰。

function renderXPage() {
  const srcBody = document.getElementById("xSourceBody");
  if (!srcBody || !settings) return;
  renderXRelayLinkCard();
  renderXTranslationControls();
  renderFgTabs(FG_X);
  const xfg = getActiveFg(FG_X);

  // X 转发账号：只渲染当前 X 转发组的账号成员；「启用」仍是全局注册源状态
  const allSources = Array.isArray(settings.x_rss_sources) ? settings.x_rss_sources : [];
  srcBody.innerHTML = "";
  const memberKeys = {};
  if (xfg) {
    (xfg.sources || []).forEach(function (k) { memberKeys[String(k)] = true; });
  }
  const srcRows = allSources.filter(function (s) { return memberKeys[xSourceKey(s.source)]; });
  if (!srcRows.length) {
    srcBody.innerHTML = '<tr class="fgempty-row"><td colspan="5" class="fgempty-msg">还未新增来源，点击下方「＋ 新增」从已注册来源中加入，或先「注册新源」。</td></tr>';
  }
  srcRows.forEach((s) => {
    const i = allSources.indexOf(s);
    const handle = xSourceKey(s.source);
    const name = s.name || handle;
    const profileUrl = xSourceProfileUrl(s.source);
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", handle);
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="xSource" data-i="' + i + '"' + (s.enabled ? " checked" : "") + '></td>' +
      '<td>' +
        '<span class="chan-name" data-kind="xSourceNameDisplay" data-i="' + i + '">' +
          '<span class="chan-name-text">' + escapeHtml(name) + '</span>' +
          '<span class="chan-name-edit" data-kind="xSourceNameEdit" data-i="' + i + '" title="编辑账号名称">' + SVG_PEN + '</span>' +
        '</span>' +
      '</td>' +
      '<td class="mono">' +
        (profileUrl
          ? '<a class="x-account-link" href="' + escapeHtml(profileUrl) + '" target="_blank" rel="noopener noreferrer" title="打开 X 主页">@' + escapeHtml(handle) + '</a>'
          : '@' + escapeHtml(handle)) +
      '</td>' +
      '<td style="text-align:right"><div style="display:flex;gap:4px;justify-content:flex-end;">' +
        '<button class="sec rowicon translation-toggle-icon' + (s.translation_enabled ? ' translation-active' : '') + '" title="翻译设置" aria-label="翻译设置" data-kind="xSourceTranslation" data-i="' + i + '">' + SVG_TRANSLATE + '</button>' +
        '<button class="sec rowicon' + (channelFilterActive(s) ? ' filter-active' : '') + '" data-kind="xSourceFilter" data-i="' + i + '">' + SVG_FUNNEL + '</button>' +
        '<button class="danger rowicon" data-kind="xFgSourceRemove" data-id="' + escapeHtml(handle) + '" title="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    srcBody.appendChild(tr);
  });
  // 表头全选框状态反映当前组成员，勾选/取消只作用于当前 X 转发组来源
  syncHeaderAll("xSourceAllEnabled", srcRows, "enabled");

  // QQ 接收群：只渲染当前 X 转发组的群成员；「启用」仍是全局群状态
  const xgBody = document.getElementById("xXGroupBody");
  const allGroups = settings.qq_group_openids || [];
  xgBody.innerHTML = "";
  const memberGrps = {};
  if (xfg) {
    (xfg.groups || []).forEach(function (o) { memberGrps[String(o)] = true; });
  }
  const grpRows = allGroups.filter(function (g) { return memberGrps[String(g.openid)]; });
  if (!grpRows.length) {
    xgBody.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增群，点击下方「＋ 新增」来新增第一个群吧！</td></tr>';
  }
  grpRows.forEach((g) => {
    const gi = allGroups.indexOf(g);
    const gname = g.name || identityName("groups", g.openid) || "";
    const gacc = g.qq_id || "";
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(g.openid));
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="group" data-i="' + gi + '"' + (g.enabled ? " checked" : "") + '></td>' +
      '<td>' +
        (gname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(gname) + '</div>' : '') +
      '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(gacc) + '</td>' +
      '<td class="mono">' + escapeHtml(g.openid) + '</td>' +
      '<td style="text-align:right"><button class="danger rowicon" data-kind="xFgGroupRemove" data-id="' + escapeHtml(String(g.openid)) + '" title="移出当前转发组">' + SVG_TRASH + '</button></td>';
    xgBody.appendChild(tr);
  });
  syncHeaderAll("xGroupAllEnabled", grpRows, "enabled");

  // 白名单：全局数据（settings.qq_user_openids），与 Discord → QQ 页
  // 共用同一份 settings 数组，任一页修改经 saveAndReload 后两边同步重渲染
  const xb = document.getElementById("xUserBody");
  if (xb) {
    xb.innerHTML = "";
    if (!(settings.qq_user_openids || []).length) {
      xb.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增账号，点击下方「新增」按钮来新增第一个账号吧！</td></tr>';
    }
    (settings.qq_user_openids || []).forEach((u) => {
      const ui = settings.qq_user_openids.indexOf(u);   // 原始索引：增删改仍按 settings 数组定位
      const tr = document.createElement("tr");
      tr.setAttribute("data-id", String(u.openid));
      const uname = u.name || identityName("users", u.openid) || "";
      const uacc = u.qq_id || "";
      tr.innerHTML =
        '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
        '<td style="text-align:center"><input type="checkbox" data-kind="xUser" data-i="' + ui + '"' + (u.enabled ? " checked" : "") + '></td>' +
        '<td>' +
          (uname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(uname) + '</div>' : '') +
        '</td>' +
        '<td style="font-size:12px;">' + escapeHtml(uacc) + '</td>' +
        '<td class="mono">' + escapeHtml(u.openid) + '</td>' +
        '<td style="text-align:right"><button class="danger rowicon" data-kind="xUserDel" data-i="' + ui + '">' + SVG_TRASH + '</button></td>';
      xb.appendChild(tr);
    });
    syncHeaderAll("xUserAllEnabled", settings.qq_user_openids || [], "enabled");
  }

  // X RSS 状态卡片的「运行中徽章 + 运行行」由 refreshXStatus 轻量轮询独立刷新；
  // 这里只同步两个配置输入框（正在编辑的输入框不覆盖，与 backfillLimit 同策略）
  const baseInput = document.getElementById("xRsshubBase");
  if (baseInput && document.activeElement !== baseInput) {
    baseInput.value = settings.x_rsshub_base || "";
  }
  const intervalInput = document.getElementById("xPollInterval");
  if (intervalInput && document.activeElement !== intervalInput) {
    const v = Number(settings.x_rss_poll_interval);
    intervalInput.value = v > 0 ? v : 300;
  }
}

// ---------------- X RSS 状态卡片（真实运行状态，轻量轮询） ----------------
// 数据来自面板 /api/xrss/status（代理 bot 的 /api/x-rss/status 运行时状态）。
// 只更新本卡片的 textContent，不重建任何表格/输入框：转发组重命名输入框
// （fgRenameCtxKey 保护）与 RSSHub/间隔输入框都不会被本刷新打断。
function xrssFmtTime(ts) {
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString("zh-CN", {hour12: false});
  return d.toDateString() === new Date().toDateString() ? time
    : d.toLocaleDateString("zh-CN") + " " + time;
}

function xrssSet(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function renderXStatus(st) {
  const badge = document.getElementById("xrssBadge");
  if (!badge || !st) return;

  const botUp = st.bot_running === true;
  const pollTs = Number(st.last_poll_ts) || 0;
  const pollText = pollTs ? xrssFmtTime(pollTs) : "—";

  // 订阅源：接口值（bot 当前实际读到）优先，面板 settings 兜底
  let srcText = "—";
  if (st.sources_total !== undefined) {
    srcText = st.sources_enabled + " / " + st.sources_total;
  } else if (settings && Array.isArray(settings.x_rss_sources)) {
    const all = settings.x_rss_sources;
    srcText = all.filter(function (s) { return s.enabled; }).length + " / " + all.length;
  }

  // 徽章：真实运行状态（写死「轮询中」已废弃）。
  // 优先级：bot 停止 > bot 接口不可用 > 轮询循环未启动（等 QQ） >
  // QQ 掉线（RSS 可能仍正常，必须与「运行中」区分）> 拉取/解析失败 > 运行中
  let badgeText, badgeCls, failed = false;
  if (!botUp) {
    badgeText = "未运行";
    badgeCls = "badge off";
  } else if (st.ok === false) {
    badgeText = localizeUserError(st.msg, "检测中…");
    badgeCls = "badge warn";
  } else if (!st.running) {
    // 轮询循环只在 QQ Bot 连接后启动：未启动即 QQ 尚未连接过
    badgeText = "等待 QQ 连接";
    badgeCls = "badge warn";
  } else if (!st.qq_connected) {
    badgeText = st.last_result === "ok" ? "RSS 正常 · QQ 未连接" : "RSSHub 请求失败 · QQ 未连接";
    badgeCls = "badge warn";
  } else if (st.last_result === "fetch_failed") {
    badgeText = "RSSHub 请求失败";
    badgeCls = "badge off";
    failed = true;
  } else if (st.last_result === "parse_failed") {
    badgeText = "RSS 解析失败";
    badgeCls = "badge off";
    failed = true;
  } else {
    badgeText = "运行中";
    badgeCls = "badge on";
  }
  badge.textContent = badgeText;
  badge.className = badgeCls;

  // 摘要行：正常态一行概况（订阅源 / 上次轮询 / 发现 / 发送）；
  // 异常态只突出错误原因，不再堆叠正常信息。
  // 「发送失败」仅在有失败时才出现，属于异常信息。
  const summary = document.getElementById("xrssSummary");
  if (summary) {
    const sendFailed = Number(st.last_failed_count) || 0;
    const err = failed || sendFailed > 0;
    summary.classList.toggle("err", err);
    if (!botUp) {
      summary.textContent = "—";
    } else if (failed) {
      summary.textContent = localizeUserError(st.last_error, "请求失败") + " · 上次轮询 " + pollText;
    } else {
      summary.textContent = "订阅源 " + srcText + " · 上次轮询 " + pollText
        + " · 发现 " + (Number(st.last_new_count) || 0) + " 条"
        + " · 发送 " + (Number(st.last_sent_count) || 0) + " 条"
        + (sendFailed > 0 ? " · 发送失败 " + sendFailed + " 条" : "");
    }
  }
}

async function refreshXStatus() {
  try {
    renderXStatus(await jget("/api/xrss/status"));
  } catch (err) { /* 面板瞬断：保留当前显示，下一轮覆盖 */ }
}

// ---------------- 消息追踪（近期投递结果，只读） ----------------
function messageTraceTime(ts) {
  const d = new Date(Number(ts || 0) * 1000);
  if (!Number.isFinite(d.getTime()) || !ts) return "—";
  const time = d.toLocaleTimeString("zh-CN", {hour12: false});
  return d.toDateString() === new Date().toDateString()
    ? time
    : d.toLocaleDateString("zh-CN") + " " + time;
}

function messageTraceSource(entry) {
  const kind = String(entry.source_kind || "other");
  const id = String(entry.source_id || entry.channel_id || "");
  if (kind === "x_rss") {
    const source = ((settings && settings.x_rss_sources) || []).find(function (item) {
      return xSourceKey(item.source) === id;
    });
    return {name: "X RSS", id: (source && source.name) || id || "未知来源"};
  }
  if (kind === "discord") {
    return {name: "Discord", id: channelNames[id] || id || "未知频道"};
  }
  return {name: "系统消息", id: "手动或定时发送"};
}

function messageTraceGroupName(openid) {
  const group = ((settings && settings.qq_group_openids) || []).find(function (item) {
    return String(item.openid) === String(openid);
  });
  return (group && (group.name || group.remark)) || identityName("groups", openid) || String(openid);
}

function messageTraceResult(entry) {
  const delivered = Number(entry.delivered_count) || 0;
  const total = Number(entry.target_count) || 0;
  const result = entry.result || "failed";
  if (result === "success") return {cls: "ok", text: "已送达 " + delivered + "/" + total};
  if (result === "partial") return {cls: "partial", text: "部分送达 " + delivered + "/" + total};
  if (result === "not_sent") return {cls: "pending", text: "未投递"};
  if (result === "skipped") return {cls: "pending", text: "已跳过"};
  return {cls: "failed", text: "投递失败 " + delivered + "/" + total};
}

function renderMessageTrace(data) {
  const body = document.getElementById("messageTraceBody");
  const summary = document.getElementById("messageTraceSummary");
  if (!body || !summary) return;

  if (!data || data.ok === false) {
    summary.textContent = localizeUserError(data && data.msg, "机器人未运行");
    body.innerHTML = '<tr><td colspan="5" class="message-trace-empty">暂时无法读取投递记录</td></tr>';
    return;
  }

  const entries = Array.isArray(data.entries) ? data.entries : [];
  summary.textContent = entries.length ? "最近 " + entries.length + " 条投递" : "暂无投递记录";
  if (!entries.length) {
    body.innerHTML = '<tr><td colspan="5" class="message-trace-empty">暂无投递记录</td></tr>';
    return;
  }

  body.innerHTML = entries.map(function (entry) {
    const source = messageTraceSource(entry);
    const groups = (entry.groups || []).map(function (group) {
      const label = messageTraceGroupName(group.id);
      return '<span class="message-trace-group' + (group.ok ? '' : ' failed') + '" title="' + escapeHtml(group.id) + '">' + escapeHtml(label) + '</span>';
    }).join("") || '<span class="pid">—</span>';
    const result = messageTraceResult(entry);
    const messageId = entry.message_id ? "消息 " + String(entry.message_id) : "未标记消息";
    const preview = entry.preview || (Number(entry.media_count) > 0 ? "包含 " + entry.media_count + " 个媒体附件" : "无文本内容");
    const note = entry.note ? '<div class="message-trace-note">' + escapeHtml(entry.note) + '</div>' : '';
    return '<tr>' +
      '<td class="message-trace-time">' + escapeHtml(messageTraceTime(entry.last_at)) + '</td>' +
      '<td><div class="message-trace-source">' + escapeHtml(source.name) + '</div><div class="message-trace-source-id">' + escapeHtml(source.id) + '</div></td>' +
      '<td><div class="message-trace-source-id">' + escapeHtml(messageId) + (Number(entry.attempts) > 1 ? ' · 重试 ' + (Number(entry.attempts) - 1) + ' 次' : '') + '</div><div class="message-trace-preview" title="' + escapeHtml(preview) + '">' + escapeHtml(preview) + '</div></td>' +
      '<td><div class="message-trace-groups">' + groups + '</div></td>' +
      '<td><span class="message-trace-result ' + result.cls + '">' + escapeHtml(result.text) + '</span>' + note + '</td>' +
      '</tr>';
  }).join("");
}

async function openMessageTrace() {
  const modal = document.getElementById("messageTraceModal");
  if (!modal) return;
  modal.style.display = "flex";
  await refreshMessageTrace();
}

function closeMessageTraceModal() {
  const modal = document.getElementById("messageTraceModal");
  if (modal) modal.style.display = "none";
}

async function refreshMessageTrace() {
  try {
    renderMessageTrace(await jget("/api/message-trace?limit=50"));
  } catch (err) { /* 面板瞬断：保留上一份追踪 */ }
}

// 「注册 X 转发账号」二级窗口：主界面的两个输入框移入此弹窗。
// 字段含义 / placeholder 说明 / 校验与错误提示 / 注册逻辑均与原主界面版本一致：
// 登记为全局 X 转发账号（默认启用），不自动加入任何转发组。
function openXSourceRegister() {
  document.getElementById("confirmTitle").textContent = "注册 X 转发账号";
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  function field(labelText, placeholder) {
    const wrap = document.createElement("div");
    wrap.style.marginBottom = "10px";
    const label = document.createElement("div");
    label.className = "bf-limit-label";
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = placeholder;
    input.style.width = "100%";
    wrap.appendChild(label);
    wrap.appendChild(input);
    body.appendChild(wrap);
    return input;
  }
  const input = field("X 账号", "");
  const nameInput = field("显示名称（可选）", "");
  setConfirmButtons("注册", true);
  confirmAction = async function () {
    const handle = input.value.trim().replace(/^@/, "");
    if (!handle) { showAlert("注册新源", "请先填写 X 账号"); return; }
    if (!Array.isArray(settings.x_rss_sources)) settings.x_rss_sources = [];
    const dup = settings.x_rss_sources.some(function (s) {
      return xSourceKey(s.source).toLowerCase() === handle.toLowerCase();
    });
    if (dup) { showAlert("注册新源", "该账号已在源列表中"); return; }
    settings.x_rss_sources.push({source: handle, name: nameInput.value.trim(), enabled: true});
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function syncHeaderAll(id, items, field) {
  const el = document.getElementById(id);
  if (!el) return;
  const on = items.filter(function (o) { return !!o[field]; }).length;
  if (on === 0) {
    el.checked = false;
    el.indeterminate = false;
  } else if (on === items.length) {
    el.checked = true;
    el.indeterminate = false;
  } else {
    el.checked = false;
    el.indeterminate = true;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
  });
}

// ---------------- 表格行拖拽排序（鼠标事件实现 + 数据数组持久化） ----------------
// 作用区域：转发组卡片的「Discord 转发频道 / QQ 接收群」
// 与「私聊权限」表格。拖拽真正重排 settings 数据并通过 saveAndReload()
// 走现有保存流程持久化（刷新后保持）；行只能在同一表格内排序。
let dndState = null;   // {body, tr, id, startX, startY} —— 正在拖拽的源行

document.addEventListener("mousedown", function (e) {
  const handle = e.target.closest(".drag-handle");
  if (!handle) return;                                    // 只允许按住手柄拖拽
  const tr = handle.closest("tr[data-id]");
  if (!tr) return;
  const body = tr.parentElement;
  if (!body || !body.id) return;
  e.preventDefault();                                     // 防止文本选择干扰拖动
  dndState = {body: body, tr: tr, id: tr.getAttribute("data-id"), startX: e.clientX, startY: e.clientY};
  tr.classList.add("drag-src");
});

document.addEventListener("mousemove", function (e) {
  if (!dndState) return;
  e.preventDefault();
  // 指针移动超过阈值才算拖拽（区分“单击手柄”）
  if (Math.abs(e.clientX - dndState.startX) < 3 && Math.abs(e.clientY - dndState.startY) < 3) return;
  const body = dndState.body;
  // 清除上次的插入指示
  body.querySelectorAll("tr.drop-before, tr.drop-after").forEach(function (r) {
    r.classList.remove("drop-before", "drop-after");
  });
  // 计算指针下的目标行（限制在同一区域）
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const tr = under ? under.closest("tr[data-id]") : null;
  if (!tr || tr.parentElement !== body || tr === dndState.tr) return;
  const r = tr.getBoundingClientRect();
  const after = e.clientY > r.top + r.height / 2;
  tr.classList.add(after ? "drop-after" : "drop-before");
});

document.addEventListener("mouseup", function (e) {
  if (!dndState) return;
  e.preventDefault();
  const st = dndState;
  dndState = null;
  st.tr.classList.remove("drag-src");
  const body = st.body;
  body.querySelectorAll("tr.drop-before, tr.drop-after").forEach(function (r) {
    r.classList.remove("drop-before", "drop-after");
  });
  // 拖动距离太小视为单击，不重排
  if (Math.abs(e.clientX - st.startX) < 3 && Math.abs(e.clientY - st.startY) < 3) return;
  if (!st.tr.isConnected) return;                          // 行已被重建，放弃
  // 计算目标行（跨区域直接忽略）
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const targetTr = under ? under.closest("tr[data-id]") : null;
  const targetBody = targetTr ? targetTr.parentElement : (under ? under.closest("tbody") : null);
  if (!targetBody || targetBody !== body) return;          // 禁止跨区域拖动
  if (targetTr && targetTr !== st.tr) {
    const r = targetTr.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    body.insertBefore(st.tr, after ? targetTr.nextSibling : targetTr);
  } else if (!targetTr && body.lastElementChild && body.lastElementChild !== st.tr) {
    body.appendChild(st.tr);                               // 拖到区域末尾空白处
  } else {
    return;                                                // 落在自己行上，无变化
  }
  // 读取拖拽后的显示顺序，重排对应 settings 数据数组并走现有保存流程
  const ids = [];
  body.querySelectorAll("tr[data-id]").forEach(function (r) { ids.push(r.getAttribute("data-id")); });
  if (!applyDndOrder(body.id, ids)) return;
  saveAndReload();                                         // 现有保存：POST settings + 重渲染
});

// 将拖拽后的显示顺序写回 settings 对应数据数组（成员按新顺序，非成员保持原相对位置）
function applyDndOrder(bodyId, newIds) {
  if (!newIds.length) return false;
  const pos = {};
  newIds.forEach(function (id, i) { pos[String(id)] = i; });
  let arr = null, memberSet = null;
  if (bodyId === "xSourceBody" || bodyId === "xXGroupBody") {
    // X 转发组成员排序：直接重排当前 X 组的 sources/groups
    // （只影响本组内的显示顺序，不重排全局注册实体）
    const xfg = getActiveFg(FG_X);
    if (!xfg) return false;
    const field = bodyId === "xSourceBody" ? "sources" : "groups";
    const members = (xfg[field] || []).map(String);
    if (members.length !== newIds.length) return false;
    const mset = {};
    members.forEach(function (id) { mset[id] = true; });
    if (!newIds.every(function (id) { return mset[String(id)]; })) return false;
    xfg[field] = newIds.map(String);
    return true;
  }
  if (bodyId === "chanBody") {
    arr = settings.discord_channels;
    memberSet = memberIdSet(getActiveFg(FG_DISCORD) && getActiveFg(FG_DISCORD).channels);
  } else if (bodyId === "groupBody") {
    arr = settings.qq_group_openids;
    memberSet = memberIdSet(getActiveFg(FG_DISCORD) && getActiveFg(FG_DISCORD).groups);
  } else if (bodyId === "userBody" || bodyId === "xUserBody") {
    // X → QQ 页的白名单表与 Discord 页共用同一份数组，全量排序
    arr = settings.qq_user_openids;
    memberSet = memberIdSet(arr && arr.map(function (u) { return u.openid; }));
  } else {
    return false;
  }
  if (!arr || !memberSet) return false;
  const getId = function (o) { return String(o.openid !== undefined ? o.openid : o.id); };
  // 校验：newIds 必须与当前可见成员一一对应（含数量一致），防止脏数据
  const members = arr.filter(function (o) { return memberSet[getId(o)]; });
  if (members.length !== newIds.length) return false;
  const byId = {};
  members.forEach(function (o) { byId[getId(o)] = o; });
  const ordered = newIds.map(function (id) { return byId[String(id)]; }).filter(Boolean);
  if (ordered.length !== newIds.length) return false;
  // 重排：成员按新顺序，非成员保持原相对位置（在原数组的成员槽位依次填入新顺序）
  let mi = 0;
  const result = [];
  arr.forEach(function (o) {
    if (memberSet[getId(o)]) result.push(ordered[mi++]);
    else result.push(o);
  });
  if (bodyId === "chanBody") settings.discord_channels = result;
  else if (bodyId === "groupBody") settings.qq_group_openids = result;
  else settings.qq_user_openids = result;
  return true;}

// {id1: true, id2: true, ...} —— 用于判断某实体是否为当前区域的成员
function memberIdSet(arr) {
  const m = {};
  (arr || []).forEach(function (id) { m[String(id)] = true; });
  return m;
}

// ---------------- 转发组 tab 左右拖拽排序（鼠标事件实现 + 数组持久化） ----------------
// 作用区域：Discord、X、QQ→QQ 页各自的转发组条（.fgtabs）。
// 按住转发组按钮（.fgtab）左右拖动调整顺序；松手后重排 settings 的
// forwarding_groups / x_forwarding_groups / qq_forwarding_groups 数组并
// saveAndReload() 持久化（刷新后保持）。
let fgDnd = null;   // {wrap, btn, startX, startY} —— 正在拖拽的源转发组按钮
function fgCtxByWrap(wrap) {
  if (wrap && wrap.id === "xFgTabs") return FG_X;
  if (wrap && wrap.id === "qqFgTabs") return FG_QQ;
  return FG_DISCORD;
}
document.addEventListener("mousedown", function (e) {
  const btn = e.target.closest(".fgtab");
  if (!btn || btn.dataset.kind !== "fgTab") return;         // 只允许拖转发组按钮
  const wrap = btn.parentElement;
  if (!wrap || !wrap.classList.contains("fgtabs")) return;
  if (fgRenameActive) return;                              // 重命名进行中不拖（避免与输入框冲突）
  e.preventDefault();                                      // 防止文本选择干扰拖动
  fgDnd = {wrap: wrap, btn: btn, startX: e.clientX, startY: e.clientY};
  btn.classList.add("drag-src");
});
document.addEventListener("mousemove", function (e) {
  if (!fgDnd) return;
  e.preventDefault();
  // 指针移动超过阈值才算拖拽（区分“单击切换转发组”）
  if (Math.abs(e.clientX - fgDnd.startX) < 3 && Math.abs(e.clientY - fgDnd.startY) < 3) return;
  const wrap = fgDnd.wrap;
  // 清除上次的插入指示
  wrap.querySelectorAll(".fgtab.drop-before, .fgtab.drop-after").forEach(function (b) {
    b.classList.remove("drop-before", "drop-after");
  });
  // 计算指针下的目标按钮（限制在同一转发组条）
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const tab = under ? under.closest(".fgtab") : null;
  if (!tab || tab.parentElement !== wrap || tab === fgDnd.btn) return;
  const r = tab.getBoundingClientRect();
  const after = e.clientX > r.left + r.width / 2;
  tab.classList.add(after ? "drop-after" : "drop-before");
});
document.addEventListener("mouseup", function (e) {
  if (!fgDnd) return;
  e.preventDefault();
  const st = fgDnd;
  fgDnd = null;
  st.btn.classList.remove("drag-src");
  const wrap = st.wrap;
  wrap.querySelectorAll(".fgtab.drop-before, .fgtab.drop-after").forEach(function (b) {
    b.classList.remove("drop-before", "drop-after");
  });
  // 拖动距离太小视为单击（切换转发组），不重排
  if (Math.abs(e.clientX - st.startX) < 3 && Math.abs(e.clientY - st.startY) < 3) return;
  if (!st.btn.isConnected) return;                          // 按钮已被重建，放弃
  // 计算目标按钮（跨转发组条直接忽略）
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const targetTab = under ? under.closest(".fgtab") : null;
  if (targetTab && targetTab.parentElement === wrap && targetTab !== st.btn) {
    const r = targetTab.getBoundingClientRect();
    const after = e.clientX > r.left + r.width / 2;
    wrap.insertBefore(st.btn, after ? targetTab.nextSibling : targetTab);
  } else if (!targetTab && wrap.lastElementChild && wrap.lastElementChild !== st.btn) {
    wrap.appendChild(st.btn);                               // 拖到条尾空白处
  } else {
    return;                                                 // 落回原位，无变化
  }
  // 读取拖拽后的显示顺序，重排对应 settings 转发组数组并持久化
  const ctx = fgCtxByWrap(wrap);
  const ids = [];
  wrap.querySelectorAll(".fgtab").forEach(function (b) { ids.push(b.dataset.id); });
  const list = getFgList(ctx);
  if (!ids.length || ids.length !== list.length) return;
  const byId = {};
  list.forEach(function (fg) { byId[fg.id] = fg; });
  const ordered = [];
  ids.forEach(function (id) { if (byId[id]) ordered.push(byId[id]); });
  if (ordered.length !== list.length) return;
  if (ctx.field === "x_forwarding_groups") settings.x_forwarding_groups = ordered;
  else if (ctx.field === "qq_forwarding_groups") settings.qq_forwarding_groups = ordered;
  else settings.forwarding_groups = ordered;
  saveAndReload();                                         // 现有保存：POST settings + 重渲染
});

// ---------------- OpenID 身份识别（辅助数据源） ----------------
// 身份库只作为「QQ 接收群」「私聊权限」表格的辅助数据源：
// 在 OpenID 旁展示已识别的群名/昵称，不提供独立的管理入口。
let identityData = {users: [], groups: []};

function identityMap(kind) {
  const m = {};
  (identityData[kind] || []).forEach(function (it) {
    m[it.openid] = it;
  });
  return m;
}

function identityName(kind, openid) {
  const it = identityMap(kind)[openid];
  return (it && it.name) || "";
}

async function refreshIdentities() {
  try {
    const r = await jget("/api/identities");
    if (r && r.ok) {
      identityData = {users: r.users || [], groups: r.groups || []};
      renderSettings();  // 用识别的昵称/群名刷新白名单表格展示
    }
  } catch (err) { /* 身份库暂不可用时忽略，仅不显示名称 */ }
}

let backfillTotal = 0;  // 当前待补发总数，供「立即补发/清空」按钮可用状态判断

function updateBackfillButtons() {
  const disable = backfillTotal <= 0;
  ["btnBackfillRun", "btnBackfillClear"].forEach(function (id) {
    const b = document.getElementById(id);
    if (b) b.disabled = disable;
  });
}

function renderBackfill(data) {
  const el = document.getElementById("backfillPending");
  if (!el) return;
  // 仅显示数字：接口不可用/未运行/无待补发均显示 0
  backfillTotal = (data && data.ok) ? (data.total || 0) : 0;
  // 超过 99 条只显示 99+，按钮可用状态仍按真实数量判断
  el.textContent = backfillTotal > 99 ? "99+" : backfillTotal;
  el.style.color = backfillTotal > 0 ? "var(--pending)" : "";
  // 无待补发时禁用「立即补发」与「清空待补发」（disabled 自带半透明）
  updateBackfillButtons();
}

async function refreshBackfill() {
  try {
    const r = await jget("/api/backfill/pending");
    renderBackfill(r);
  } catch (err) {
    renderBackfill(null);
  }
}

async function refreshChannelNames() {
  try {
    const r = await jget("/api/channel-names");
    if (r && r.names) channelNames = r.names || {};
    if (r && r.guild_ids) channelGuildIds = r.guild_ids || {};
  } catch (err) { /* ignore */ }
  renderSettings();
}

// ---------- 通用弹窗（confirmModal：alert / confirm / 勾选确认） ----------
let confirmAction = null;

function setConfirmButtons(okText, showCancel, cancelText, showOk) {
  const ok = document.getElementById("confirmOk");
  ok.textContent = okText;
  ok.style.display = showOk === false ? "none" : "";
}

function showAlert(title, message) {
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const p = document.createElement("div");
  p.style.whiteSpace = "pre-wrap";
  p.textContent = localizeUserError(message);
  body.appendChild(p);
  setConfirmButtons("确定", false);
  confirmAction = null;
  document.getElementById("confirmModal").style.display = "flex";
}

function showConfirmDlg(title, message, onOk, okText) {
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const p = document.createElement("div");
  p.style.whiteSpace = "pre-wrap";
  p.textContent = localizeUserError(message);
  body.appendChild(p);
  setConfirmButtons(okText || "确定", true);
  confirmAction = onOk;
  document.getElementById("confirmModal").style.display = "flex";
}

function showConfirmSelect(title, items, labelFn, onOkSelected, opts) {
  opts = opts || {};
  const defaultChecked = opts.defaultChecked;
  const groupBy = opts.groupBy || null;
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const rows = [];
  function renderRow(it) {
    const row = document.createElement("label");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "8px";
    row.style.cursor = "pointer";
    row.style.padding = "2px 0";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = typeof defaultChecked === "function" ? !!defaultChecked(it) : defaultChecked !== false;
    cb.style.width = "16px";
    cb.style.height = "16px";
    const span = document.createElement("span");
    span.className = "mono";
    span.style.flex = "1";
    span.style.minWidth = "0";
    const labelLink = typeof opts.labelLinkFn === "function" ? opts.labelLinkFn(it) : "";
    if (labelLink) {
      const link = document.createElement("a");
      link.className = "x-account-link";
      link.href = labelLink;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = labelFn(it);
      link.title = "打开 X 主页";
      link.addEventListener("click", function (e) { e.stopPropagation(); });
      span.appendChild(link);
    } else {
      span.textContent = labelFn(it);
    }
    row.appendChild(cb);
    row.appendChild(span);
    if (opts.onRowDelete) {
      // 「×」删除按钮：贴近窗口右边缘，删除该条目的注册记录
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "×";
      del.className = "danger rowicon";
      del.style.flex = "none";
      del.title = "删除注册记录";
      del.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        opts.onRowDelete(it);
      });
      row.appendChild(del);
    }
    body.appendChild(row);
    rows.push({it: it, cb: cb});
  }
  if (groupBy) {
    // 按分组字段聚合渲染：每个分组标题下方列出该组条目（保持组首次出现顺序）
    const order = [];
    const groups = {};
    items.forEach(function (it) {
      const g = String(groupBy(it) || "其他");
      if (!Object.prototype.hasOwnProperty.call(groups, g)) {
        groups[g] = [];
        order.push(g);
      }
      groups[g].push(it);
    });
    order.forEach(function (g) {
      const gh = document.createElement("div");
      gh.className = "sel-group-title";
      gh.textContent = g;
      body.appendChild(gh);
      groups[g].forEach(renderRow);
    });
  } else {
    items.forEach(renderRow);
  }
  setConfirmButtons(opts.okText || "确认添加", true);
  confirmAction = function () {
    const selected = rows.filter(function (b) { return b.cb.checked; }).map(function (b) { return b.it; });
    return onOkSelected(selected);
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function hideConfirm() {
  document.getElementById("confirmModal").style.display = "none";
  confirmAction = null;
  setConfirmButtons("确定", true);
}

// ---------- 注册审核弹窗（两个 QQ 同步弹窗：群 / 用户） ----------
// 自动发现的 openid 只记录不进同步名单；主动注册后才出现在这里。
// 每条申请显示 QQ号/群号 + 昵称/群名 + OpenID，右侧 √ 通过 / × 拒绝：
// 通过 → 按现有同步流程加入设置列表（默认未启用）；拒绝 → 申请消失。
let regReviewKind = null;

async function openRegistrationReview(kind) {
  regReviewKind = kind;
  // 隐藏"确定"，显示"关闭"（审核按钮在每条申请右侧）
  setConfirmButtons("", true, "关闭", false);
  document.getElementById("confirmTitle").textContent = "加载中…";
  document.getElementById("confirmBody").innerHTML = "";
  document.getElementById("confirmModal").style.display = "flex";
  let data = null;
  try {
    data = await jget("/api/registrations");
  } catch (err) { /* 接口不可用时按空列表展示 */ }
  renderRegistrationReview(data);
}

function renderRegistrationReview(data) {
  const body = document.getElementById("confirmBody");
  const items = (data && data[regReviewKind]) || [];
  document.getElementById("confirmTitle").textContent =
    (regReviewKind === "groups" ? "群注册审核" : "用户注册审核")
    + (items.length ? "（" + items.length + " 条待审）" : "");
  body.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "color:var(--muted);padding:8px 0;";
    empty.textContent = "没有待审核的注册申请";
    body.appendChild(empty);
  }
  items.forEach(function (it) {
    const label = regReviewKind === "groups"
      ? "群号 " + (it.qq_id || "?") + " · " + (it.group_name || "未命名")
      : "QQ " + (it.qq_id || "?") + " · " + (it.nickname || "未命名");
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--panel-border);";
    const info = document.createElement("div");
    info.style.cssText = "flex:1;min-width:0;";
    info.innerHTML =
      '<div>' + escapeHtml(label) + '</div>' +
      '<div class="mono" style="font-size:11px;color:var(--muted);word-break:break-all;">' + escapeHtml(it.openid) + '</div>';
    row.appendChild(info);
    const ok = document.createElement("button");
    ok.className = "sec";
    ok.textContent = "√";
    ok.style.cssText = "color:var(--name-green);border-color:var(--ok-border);";
    ok.addEventListener("click", function () { reviewRegistration(it.openid, "approve"); });
    const no = document.createElement("button");
    no.className = "danger";
    no.textContent = "×";
    no.addEventListener("click", function () { reviewRegistration(it.openid, "reject"); });
    row.appendChild(ok);
    row.appendChild(no);
    body.appendChild(row);
  });
}

async function reviewRegistration(openid, action) {
  const r = await jpost("/api/registrations/review", {kind: regReviewKind, openid: openid, action: action});
  if (!r.ok) {
    showAlert("审核失败", r.msg || "操作失败");
    return;
  }
  if (r.settings) {
    settings = r.settings;
    renderSettings();
    await refreshIdentities();
  }
  const list = await jget("/api/registrations");
  // 审核过程中弹窗被关闭（如弹出了失败提示）则不再重绘
  if (document.getElementById("confirmModal").style.display === "flex") {
    renderRegistrationReview(list);
  }
}

// ---------- 频道筛选弹窗 ----------
// 消息类型按钮（与 plugins/filter.py 的类型名一一对应，前后端保持同名单；
// Discord 视频附件当前按「文件」转发，因此没有独立的「视频」类型）
const FILTER_MESSAGE_TYPES = ["文字", "图片", "语音", "文件", "链接", "表情"];
const FILTER_X_POST_TYPES = ["原创", "转发", "引用"];

let filterEditIndex = -1;
let filterEditScope = "channel";
let filterUsernames = [];
let filterKeywords = [];
let filterMessageTypes = [];
let filterXPostTypes = [];
let translationSourceEditScope = "";
let translationSourceEditIndex = -1;

function filterItemsOf(kind) {
  return kind === "usernames" ? filterUsernames : filterKeywords;
}

function filterListIdOf(kind) {
  return kind === "usernames" ? "filterUsernameList" : "filterKeywordList";
}

function filterInputIdOf(kind) {
  return kind === "usernames" ? "filterUsernameInput" : "filterKeywordInput";
}

// 用户名 / 关键词列表渲染（每项右侧 × 可删除）
function renderFilterList(kind) {
  const wrap = document.getElementById(filterListIdOf(kind));
  const items = filterItemsOf(kind);
  wrap.innerHTML = "";
  items.forEach(function (item, i) {
    const chip = document.createElement("span");
    chip.className = "flt-chip";
    const label = document.createElement("span");
    label.textContent = item;
    label.title = item;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "flt-chip-x";
    del.textContent = "×";
    del.title = "删除";
    del.addEventListener("click", function () {
      items.splice(i, 1);
      renderFilterList(kind);
    });
    chip.appendChild(label);
    chip.appendChild(del);
    wrap.appendChild(chip);
  });
}

// 「确认」添加：去首尾空白、禁止重复（用户名/关键词匹配均不区分大小写），
// 添加后清空输入框
function addFilterItem(kind) {
  const items = filterItemsOf(kind);
  const input = document.getElementById(filterInputIdOf(kind));
  const value = input.value.trim();
  if (!value) return;
  const lower = value.toLowerCase();
  const dup = items.some(function (it) { return it.toLowerCase() === lower; });
  if (dup) {
    input.select();
    return;
  }
  items.push(value);
  input.value = "";
  renderFilterList(kind);
  input.focus();
}

// 消息类型按钮：样式参照「开机自启」（√/× SVG + 文字，data-on 记录状态）
function renderFilterTypes() {
  const wrap = document.getElementById("filterTypeList");
  wrap.innerHTML = "";
  FILTER_MESSAGE_TYPES.forEach(function (t) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sec flt-type-btn";
    btn.dataset.type = t;
    btn.title = t;
    btn.addEventListener("click", function () {
      const idx = filterMessageTypes.indexOf(t);
      if (idx >= 0) filterMessageTypes.splice(idx, 1);
      else filterMessageTypes.push(t);
      renderFilterTypeBtn(btn);
    });
    wrap.appendChild(btn);
    renderFilterTypeBtn(btn);
  });
}

function renderFilterTypeBtn(btn) {
  const on = filterMessageTypes.indexOf(btn.dataset.type) >= 0;
  btn.dataset.on = on ? "1" : "0";
  const icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>" + btn.dataset.type + "</span>";
}

function renderFilterXPostTypes() {
  const wrap = document.getElementById("filterXPostTypeList");
  if (!wrap) return;
  wrap.innerHTML = "";
  FILTER_X_POST_TYPES.forEach(function (t) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sec flt-type-btn";
    btn.dataset.type = t;
    btn.title = t;
    btn.addEventListener("click", function () {
      const idx = filterXPostTypes.indexOf(t);
      if (idx >= 0) filterXPostTypes.splice(idx, 1);
      else filterXPostTypes.push(t);
      renderFilterTypeBtnState(btn, filterXPostTypes);
    });
    wrap.appendChild(btn);
    renderFilterTypeBtnState(btn, filterXPostTypes);
  });
}

function renderFilterTypeBtnState(btn, selected) {
  const on = selected.indexOf(btn.dataset.type) >= 0;
  btn.dataset.on = on ? "1" : "0";
  const icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>" + btn.dataset.type + "</span>";
}

// 筛选配置是否生效（漏斗图标高亮）：用户名/关键词任一非空，
// 或 Discord 消息类型 / X 推文类型显式保存过且不是"全部启用"
// （缺省/全选 = 未筛选）
function filterConfigActive(c, includeAudience) {
  c = c || {};
  if (includeAudience !== false) {
    if ((c.filter_usernames || []).length) return true;
    if ((c.filter_keywords || []).length) return true;
  }
  const t = c.filter_message_types;
  if (Array.isArray(t) && t.length < FILTER_MESSAGE_TYPES.length) return true;
  const x = c.filter_post_types;
  return Array.isArray(x) && x.length > 0 && x.length < FILTER_X_POST_TYPES.length;
}

function renderManualRelayCard(cardId, filterButtonId, filterKey) {
  const btn = document.getElementById(filterButtonId);
  if (!btn) return;
  btn.innerHTML = SVG_FUNNEL;
  btn.classList.toggle("filter-active", filterConfigActive(settings && settings[filterKey], false));
}

function renderRelayLinkCard() {
  renderManualRelayCard("relayLinkCard", "btnRelayFilter", "qq_c2c_relay_filter");
}

function renderXRelayLinkCard() {
  renderManualRelayCard("xRelayLinkCard", "btnXRelayFilter", "x_c2c_relay_filter");
}

let relayChannelsEditScope = "discord";

function openRelayChannelsModal(scope) {
  relayChannelsEditScope = scope === "x" ? "x" : "discord";
  const list = document.getElementById("relayChannelsList");
  if (!list) return;
  list.innerHTML = "";
  const channels = Array.isArray(settings && (relayChannelsEditScope === "x"
    ? settings.x_rss_sources
    : settings.discord_channels))
    ? (relayChannelsEditScope === "x" ? settings.x_rss_sources : settings.discord_channels)
    : [];
  document.getElementById("relayChannelsTitle").textContent = relayChannelsEditScope === "x" ? "已注册账号" : "已注册频道";
  document.querySelector("#relayChannelsModal .relay-channels-desc").textContent = relayChannelsEditScope === "x"
    ? "这里显示全部已登记、可用于 X 手动转发链接的账号。修改备注后点击保存即可。"
    : "这里显示全部已登记、可用于 Discord 手动转发链接的频道。修改备注后点击保存即可。";
  if (!channels.length) {
    list.innerHTML = '<div class="relay-channels-empty">当前没有已注册' + (relayChannelsEditScope === "x" ? "账号" : "频道") + "</div>";
  } else {
    channels.forEach(function (channel, index) {
      const id = relayChannelsEditScope === "x"
        ? xSourceKey(channel.source)
        : String(channel.id || "");
      const row = document.createElement("div");
      row.className = "relay-channel-row";
      const info = document.createElement("div");
      info.className = "relay-channel-row-info";
      const name = document.createElement("div");
      name.className = "relay-channel-row-name";
      if (relayChannelsEditScope === "x") {
        const profileUrl = xSourceProfileUrl(channel.source);
        if (profileUrl) {
          const link = document.createElement("a");
          link.className = "x-account-link";
          link.href = profileUrl;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "@" + id;
          link.title = "打开 X 主页";
          name.appendChild(link);
        } else {
          name.textContent = "@" + id;
        }
      } else {
        const realName = channelNames[id] || "";
        const channelUrl = discordChannelUrl(id);
        if (realName && channelUrl) {
          const link = document.createElement("a");
          link.className = "discord-channel-link";
          link.href = channelUrl;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "# " + realName;
          link.title = "打开 Discord 频道";
          name.appendChild(link);
        } else {
          name.textContent = realName ? "# " + realName : "# " + (channel.name || id);
        }
      }
      name.title = name.textContent;
      const channelId = document.createElement("div");
      channelId.className = "relay-channel-row-id";
      channelId.textContent = id;
      channelId.title = id;
      info.appendChild(name);
      info.appendChild(channelId);
      const input = document.createElement("input");
      input.type = "text";
      input.value = channel.name || "";
      input.placeholder = relayChannelsEditScope === "x" ? "账号中文备注" : "频道中文备注";
      input.dataset.index = String(index);
      input.setAttribute("aria-label", relayChannelsEditScope === "x" ? "账号中文备注" : "频道中文备注");
      row.appendChild(info);
      row.appendChild(input);
      list.appendChild(row);
    });
  }
  document.getElementById("relayChannelsModal").style.display = "flex";
}

function closeRelayChannelsModal() {
  document.getElementById("relayChannelsModal").style.display = "none";
}

async function saveRelayChannels() {
  const list = document.getElementById("relayChannelsList");
  if (!list) return;
  list.querySelectorAll("input[data-index]").forEach(function (input) {
    const index = Number(input.dataset.index);
    const channel = relayChannelsEditScope === "x"
      ? settings.x_rss_sources[index]
      : settings.discord_channels[index];
    if (channel) channel.name = input.value.trim();
  });
  closeRelayChannelsModal();
  await saveAndReload();
}

function prepareFilterModal(config, label) {
  const c = config || {};
  document.getElementById("filterChanName").textContent = label;
  const audienceFields = document.getElementById("filterAudienceFields");
  if (audienceFields) audienceFields.style.display = filterEditScope === "channel" || filterEditScope === "xsource" ? "flex" : "none";
  const usernameField = document.getElementById("filterUsernameField");
  if (usernameField) usernameField.style.display = filterEditScope === "channel" ? "flex" : "none";
  const keywordField = document.getElementById("filterKeywordField");
  if (keywordField) keywordField.style.display = filterEditScope === "channel" || filterEditScope === "xsource" ? "flex" : "none";
  const keywordTitle = document.getElementById("filterKeywordTitle");
  if (keywordTitle) keywordTitle.firstChild.textContent = filterEditScope === "xsource" ? "关键词/话题 " : "关键词筛选 ";
  const keywordInput = document.getElementById("filterKeywordInput");
  if (keywordInput) keywordInput.placeholder = filterEditScope === "xsource" ? "关键词或 #话题" : "关键词";
  const messageTypeFields = document.getElementById("filterMessageTypeFields");
  if (messageTypeFields) messageTypeFields.style.display = filterEditScope === "xsource" ? "none" : "block";
  const xPostTypeFields = document.getElementById("filterXPostTypeFields");
  if (xPostTypeFields) xPostTypeFields.style.display = filterEditScope === "xsource" ? "block" : "none";
  // 兼容现有已保存的筛选配置（数组）；类型字段缺省 = 全部启用
  filterUsernames = (c.filter_usernames || []).slice();
  filterKeywords = (c.filter_keywords || []).slice();
  const storedTypes = c.filter_message_types;
  filterMessageTypes = Array.isArray(storedTypes)
    ? storedTypes.slice()
    : FILTER_MESSAGE_TYPES.slice();
  const storedXPostTypes = c.filter_post_types;
  filterXPostTypes = Array.isArray(storedXPostTypes)
    ? storedXPostTypes.slice()
    : FILTER_X_POST_TYPES.slice();
  renderFilterList("usernames");
  renderFilterList("keywords");
  renderFilterTypes();
  renderFilterXPostTypes();
  document.getElementById("filterModal").style.display = "flex";
}

function channelFilterActive(c) {
  return filterConfigActive(c);
}

function openFilterModal(index) {
  const c = settings.discord_channels[index];
  if (!c) return;
  filterEditScope = "channel";
  filterEditIndex = index;
  prepareFilterModal(c, "# " + (c.name || c.id));
}

function openXSourceFilterModal(index) {
  const source = (settings.x_rss_sources || [])[index];
  if (!source) return;
  filterEditScope = "xsource";
  filterEditIndex = index;
  prepareFilterModal(source, "@" + xSourceKey(source.source));
}

function openRelayFilterModal() {
  filterEditScope = "relay";
  filterEditIndex = -1;
  prepareFilterModal(
    settings.qq_c2c_relay_filter || {},
    "# Discord 手动转发（全部频道）"
  );
}

function openXRelayFilterModal() {
  filterEditScope = "xrelay";
  filterEditIndex = -1;
  prepareFilterModal(
    settings.x_c2c_relay_filter || {},
    "# X 手动转发（全部账号）"
  );
}

function closeFilterModal() {
  document.getElementById("filterModal").style.display = "none";
  filterEditIndex = -1;
  filterEditScope = "channel";
}

function openTranslationSourceModal(scope, index) {
  const isX = scope === "xsource";
  const item = isX
    ? (settings.x_rss_sources || [])[index]
    : (settings.discord_channels || [])[index];
  if (!item) return;
  translationSourceEditScope = isX ? "xsource" : "channel";
  translationSourceEditIndex = index;
  const label = isX
    ? "@" + xSourceKey(item.source)
    : "# " + (item.name || item.id);
  document.getElementById("translationSourceName").textContent = label;
  renderTranslationSettingButton("translationSourceEnabled", item.translation_enabled === true, "启用翻译");
  renderTranslationSettingButton("translationSourceAppendOriginal", item.translation_append_original !== false, "保留原文");
  renderTranslationSettingButton("translationSourceShowDatetime", item.translation_show_datetime === true, "显示时间日期");
  syncTranslationSourceModalButtons();
  document.getElementById("translationSourceModal").style.display = "flex";
}

function renderTranslationSettingButton(id, on, label) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  const icon = id === "translationSourceAppendOriginal"
    ? (on ? SVG_ORIGINAL_ON : SVG_ORIGINAL_OFF)
    : (on
      ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
      : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>');
  btn.innerHTML = icon + "<span>" + label + "</span>";
}

function syncTranslationSourceModalButtons() {
  const enabled = document.getElementById("translationSourceEnabled");
  const append = document.getElementById("translationSourceAppendOriginal");
  if (!enabled || !append) return;
  append.disabled = enabled.dataset.on !== "1";
}

function closeTranslationSourceModal() {
  document.getElementById("translationSourceModal").style.display = "none";
  translationSourceEditScope = "";
  translationSourceEditIndex = -1;
}

async function saveTranslationSourceModal() {
  const item = translationSourceEditScope === "xsource"
    ? (settings.x_rss_sources || [])[translationSourceEditIndex]
    : (settings.discord_channels || [])[translationSourceEditIndex];
  if (!item) return;
  item.translation_enabled = document.getElementById("translationSourceEnabled").dataset.on === "1";
  item.translation_append_original = document.getElementById("translationSourceAppendOriginal").dataset.on === "1";
  item.translation_show_datetime = document.getElementById("translationSourceShowDatetime").dataset.on === "1";
  const saved = await saveAndReload();
  if (saved) closeTranslationSourceModal();
}

document.getElementById("filterUsernameAdd").addEventListener("click", function () { addFilterItem("usernames"); });
document.getElementById("filterKeywordAdd").addEventListener("click", function () { addFilterItem("keywords"); });
document.getElementById("filterUsernameInput").addEventListener("keydown", function (e) {
  if (e.key === "Enter") { e.preventDefault(); addFilterItem("usernames"); }
});
document.getElementById("filterKeywordInput").addEventListener("keydown", function (e) {
  if (e.key === "Enter") { e.preventDefault(); addFilterItem("keywords"); }
});

document.getElementById("filterSave").addEventListener("click", async function () {
  if (filterEditScope === "relay" || filterEditScope === "xrelay") {
    const key = filterEditScope === "xrelay" ? "x_c2c_relay_filter" : "qq_c2c_relay_filter";
    settings[key] = {
      filter_message_types: filterMessageTypes.slice(),
    };
  } else {
    if (filterEditIndex < 0) return;
    const c = filterEditScope === "xsource"
      ? settings.x_rss_sources[filterEditIndex]
      : settings.discord_channels[filterEditIndex];
    if (!c) return;
    if (filterEditScope === "xsource") {
      c.filter_keywords = filterKeywords.slice();
      if (filterXPostTypes.length > 0 && filterXPostTypes.length < FILTER_X_POST_TYPES.length) {
        c.filter_post_types = filterXPostTypes.slice();
      } else {
        delete c.filter_post_types;
      }
    } else {
      c.filter_usernames = filterUsernames.slice();
      c.filter_keywords = filterKeywords.slice();
      // 全部启用 = 默认状态：不写字段（后端对缺省字段按全部启用处理），
      // 与老配置保持兼容；未全选时才落盘类型列表
      if (filterMessageTypes.length < FILTER_MESSAGE_TYPES.length) {
        c.filter_message_types = filterMessageTypes.slice();
      } else {
        delete c.filter_message_types;
      }
    }
    closeFilterModal();
    await saveAndReload();
    return;
  }
  closeFilterModal();
  await saveAndReload();
});

async function saveAndReload() {
  const result = await jpost("/api/settings/save", settings);
  if (!result.ok) {
    if (result.conflict) {
      settings = result.settings || await jget("/api/settings");
      renderSettings();
      showAlert("配置已刷新", "配置已被其他操作修改，当前页面已刷新，请重新确认本次修改。");
    } else {
      showAlert("保存失败", result.msg || "配置保存失败");
    }
    return false;
  }
  settings = await jget("/api/settings");
  renderSettings();
  await refreshChannelNames();
  await refreshStatus();
  return true;
}

// ---------- QQ Bot 配置弹窗 ----------
function closeQQConfigModal() {
  document.getElementById("qqConfigModal").style.display = "none";
}
async function openQQConfigModal() {
  document.getElementById("qqConfigAppid").value = "";
  document.getElementById("qqConfigSecret").value = "";
  document.getElementById("qqConfigModal").style.display = "flex";
  let r;
  try {
    r = await jget("/api/bot/qq-config");
  } catch (e) { r = null; }
  if (r && r.ok) {
    document.getElementById("qqConfigAppid").value = r.appid || "";
  } else {
    showAlert("读取配置失败", (r && r.msg) || "无法读取当前 QQ Bot 配置");
  }
}
async function saveQQConfig() {
  const appid = document.getElementById("qqConfigAppid").value.trim();
  const secret = document.getElementById("qqConfigSecret").value;
  if (!appid) { showAlert("QQ Bot 配置", "AppID 不能为空"); return; }
  const saveBtn = document.getElementById("qqConfigSave");
  const cancelBtn = document.getElementById("qqConfigCancel");
  saveBtn.disabled = true;
  cancelBtn.disabled = true;
  let r;
  try {
    r = await jpost("/api/bot/qq-config", { appid: appid, secret: secret });
  } catch (e) { r = { ok: false, msg: "保存失败，请重试" }; }
  if (!r || !r.ok) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    showAlert("保存失败", (r && r.msg) || "保存 QQ Bot 配置失败");
    return;
  }
  // 保存成功后再重启，使新配置生效；重启失败不回滚配置
  let rr;
  try {
    rr = await jpost("/api/bot/restart");
  } catch (e) { rr = { ok: false }; }
  if (!rr || !rr.ok) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    showAlert("配置已保存", "配置已保存，但机器人重启失败，请稍后手动重启");
    return;
  }
  closeQQConfigModal();
  await refreshStatus();
}

// ---------- 翻译服务配置弹窗 ----------
function closeTranslationConfigModal() {
  document.getElementById("translationConfigModal").style.display = "none";
  aiProfileDraft = null;
}
let aiProfileDraft = null;

function cloneAiProfileDraft() {
  const profiles = aiProfilesForDisplay().map(function (profile) { return Object.assign({}, profile); });
  const active = currentAiProfileId();
  const apiKeys = {};
  profiles.forEach(function (profile) {
    // 旧版误把密钥填进 api_key_env 时，迁移到临时密码框并恢复合法变量名；
    // 密钥只在当前页面内存中保留，保存时写入 .env.prod。
    if (profile.api_key_env && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(profile.api_key_env))) {
      apiKeys[String(profile.id)] = String(profile.api_key_env);
      profile.api_key_env = "WRELAY_AI_API_KEY";
    }
  });
  return {profiles: profiles, active: active, apiKeys: apiKeys};
}

function activeAiDraftProfile() {
  if (!aiProfileDraft) return null;
  return aiProfileDraft.profiles.find(function (profile) {
    return String(profile.id) === String(aiProfileDraft.active);
  }) || null;
}

function renderAiProfileEditor() {
  if (!aiProfileDraft) return;
  const select = document.getElementById("aiProfileSelect");
  const profile = activeAiDraftProfile();
  if (!select || !profile) return;
  select.innerHTML = aiProfileDraft.profiles.map(function (item) {
    const model = item.model ? " · " + item.model : "";
    return '<option value="' + escapeHtml(String(item.id)) + '">' + escapeHtml(String(item.name || item.id)) + escapeHtml(model) + '</option>';
  }).join("");
  select.value = String(profile.id);
  document.getElementById("aiProfileName").value = profile.name || "";
  document.getElementById("aiBaseUrl").value = profile.base_url || "";
  document.getElementById("aiModel").value = profile.model || "";
  document.getElementById("aiApiKeyEnv").value = profile.api_key_env || "WRELAY_AI_API_KEY";
  document.getElementById("aiApiKey").value = (aiProfileDraft.apiKeys || {})[String(profile.id)] || "";
  document.getElementById("aiProfileDelete").disabled = aiProfileDraft.profiles.length <= 1;
  document.getElementById("aiApiTestStatus").textContent = "";
}

function captureAiApiKeyDraft() {
  const profile = activeAiDraftProfile();
  const input = document.getElementById("aiApiKey");
  if (!profile || !input || !aiProfileDraft) return;
  aiProfileDraft.apiKeys = aiProfileDraft.apiKeys || {};
  aiProfileDraft.apiKeys[String(profile.id)] = input.value;
}

async function testAiApi() {
  const button = document.getElementById("aiApiTest");
  const status = document.getElementById("aiApiTestStatus");
  const profile = activeAiDraftProfile();
  if (!button || !status || !profile) return;
  captureAiApiKeyDraft();
  button.disabled = true;
  status.textContent = "检测中…";
  try {
    const result = await jpost("/api/translation/ai-test", {
      base_url: document.getElementById("aiBaseUrl").value.trim(),
      model: document.getElementById("aiModel").value.trim(),
      api_key_env: document.getElementById("aiApiKeyEnv").value.trim(),
      api_key: (aiProfileDraft.apiKeys || {})[String(profile.id)] || "",
      timeout_seconds: profile.timeout_seconds,
    });
    status.textContent = result.ok ? result.msg : localizeUserError(result.msg, "检测失败");
  } catch (err) {
    status.textContent = "检测请求失败";
  } finally {
    button.disabled = false;
  }
}

async function openTranslationConfigModal() {
  aiProfileDraft = cloneAiProfileDraft();
  renderAiProfileEditor();
  document.getElementById("tencentSecretId").value = "";
  document.getElementById("tencentSecretKey").value = "";
  document.getElementById("tencentConfigStatus").textContent = "TMT 配置状态读取中……";
  document.getElementById("translationConfigModal").style.display = "flex";
  let r;
  try { r = await jget("/api/translation/env"); } catch (e) { r = null; }
  const configured = r && r.ok ? (r.configured || {}) : {};
  const idReady = !!configured.WRELAY_TENCENT_SECRET_ID;
  const keyReady = !!configured.WRELAY_TENCENT_SECRET_KEY;
  const status = document.getElementById("tencentConfigStatus");
  if (!r || !r.ok) {
    status.textContent = localizeUserError((r && r.msg), "无法读取 TMT 配置状态");
  } else if (idReady && keyReady) {
    status.textContent = "TMT SecretId / SecretKey 已配置；输入框留空表示保持当前密钥。";
  } else {
    status.textContent = "TMT 密钥尚未完整配置，请填写 SecretId 和 SecretKey。";
  }
}
async function saveTranslationConfig() {
  const profile = activeAiDraftProfile();
  if (!profile) { showAlert("保存失败", "请先新增或选择一个 AI 配置"); return; }
  profile.name = document.getElementById("aiProfileName").value.trim();
  profile.base_url = document.getElementById("aiBaseUrl").value.trim();
  profile.model = document.getElementById("aiModel").value.trim();
  profile.api_key_env = document.getElementById("aiApiKeyEnv").value.trim();
  captureAiApiKeyDraft();
  const apiKey = (aiProfileDraft.apiKeys || {})[String(profile.id)] || "";
  if (!profile.name) { showAlert("保存失败", "配置名称不能为空"); return; }
  if (!profile.api_key_env) { showAlert("保存失败", "API 密钥环境变量名不能为空"); return; }
  settings.tencent_tmt = settings.tencent_tmt || {};
  const saveBtn = document.getElementById("translationConfigSave");
  const cancelBtn = document.getElementById("translationConfigCancel");
  saveBtn.disabled = true;
  cancelBtn.disabled = true;
  let envResult;
  try {
    envResult = await jpost("/api/translation/env", {
      secret_id: document.getElementById("tencentSecretId").value,
      secret_key: document.getElementById("tencentSecretKey").value,
    });
  } catch (e) { envResult = {ok: false, msg: "保存失败，请重试"}; }
  if (!envResult || !envResult.ok) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    showAlert("保存失败", (envResult && envResult.msg) || "保存腾讯 TMT 密钥失败");
    return;
  }
  let aiEnvResult;
  try {
    aiEnvResult = await jpost("/api/translation/ai-env", {
      env_name: profile.api_key_env,
      api_key: apiKey,
    });
  } catch (e) { aiEnvResult = {ok: false, msg: "保存失败，请重试"}; }
  if (!aiEnvResult || !aiEnvResult.ok) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    showAlert("保存失败", (aiEnvResult && aiEnvResult.msg) || "保存 AI API 密钥失败");
    return;
  }
  settings.ai_profiles = aiProfileDraft.profiles;
  settings.ai_active_profile = aiProfileDraft.active;
  syncLegacyAiConfig(profile);
  const saved = await saveAndReload();
  if (!saved) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    return;
  }
  // .env.prod 中的密钥需要新 Bot 进程重新加载；失败时不回滚已保存配置。
  let restartResult;
  try { restartResult = await jpost("/api/bot/restart"); } catch (e) { restartResult = {ok: false}; }
  saveBtn.disabled = false;
  cancelBtn.disabled = false;
  if (!restartResult || !restartResult.ok) {
    showAlert("配置已保存", "翻译配置已保存，但机器人重启失败，请稍后手动重启");
    return;
  }
  closeTranslationConfigModal();
  await refreshStatus();
}

// ---------- Discord Bot 配置弹窗 ----------
// 绝不在 localStorage / console 保存或输出 Token；打开时输入框留空，
// Token 留空提交表示保持当前 Token 不变（后端处理）。
function closeDiscordConfigModal() {
  document.getElementById("discordConfigModal").style.display = "none";
}
async function openDiscordConfigModal() {
  document.getElementById("discordConfigToken").value = "";
  document.getElementById("discordConfigToken").type = "password";
  document.getElementById("discordConfigModal").style.display = "flex";
  let r;
  try {
    r = await jget("/api/bot/discord-config");
  } catch (e) { r = null; }
  if (!r || !r.ok) {
    showAlert("读取配置失败", (r && r.msg) || "无法读取当前 Discord Bot 配置");
  }
}
async function saveDiscordConfig() {
  const token = document.getElementById("discordConfigToken").value;
  const saveBtn = document.getElementById("discordConfigSave");
  const cancelBtn = document.getElementById("discordConfigCancel");
  saveBtn.disabled = true;
  cancelBtn.disabled = true;
  let r;
  try {
    r = await jpost("/api/bot/discord-config", { token: token });
  } catch (e) { r = { ok: false, msg: "保存失败，请重试" }; }
  if (!r || !r.ok) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    showAlert("保存失败", (r && r.msg) || "保存 Discord Bot 配置失败");
    return;
  }
  // 保存成功后再重启，使新配置生效；重启失败不回滚配置
  let rr;
  try {
    rr = await jpost("/api/bot/restart");
  } catch (e) { rr = { ok: false }; }
  if (!rr || !rr.ok) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    showAlert("配置已保存", "配置已保存，但机器人重启失败，请稍后手动重启");
    return;
  }
  closeDiscordConfigModal();
  await refreshStatus();
}

document.addEventListener("click", async function (e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.id === "confirmOk") {
    const fn = confirmAction;
    hideConfirm();
    if (fn) await fn();
    return;
  }
  if (btn.classList.contains("modal-x") || btn.dataset.close) {
    const closeMap = {
      confirmModal: hideConfirm,
      filterModal: closeFilterModal,
      translationSourceModal: closeTranslationSourceModal,
      relayChannelsModal: closeRelayChannelsModal,
      aboutModal: function () { document.getElementById("aboutModal").style.display = "none"; },
      translationConfigModal: closeTranslationConfigModal,
      qqConfigModal: closeQQConfigModal,
      discordConfigModal: closeDiscordConfigModal,
      scheduledMessageContentModal: function () {
        scheduledMessageDraft = null;
        document.getElementById("scheduledMessageContentModal").style.display = "none";
      },
      messageTraceModal: closeMessageTraceModal,
      diagnosticModal: closeDiagnosticModal,
      backupRestoreModal: closeBackupRestore,
    };
    const closer = closeMap[btn.dataset.close];
    if (closer) closer();
    return;
  }
  if (btn.dataset.hint) {
    const tip = document.getElementById(btn.dataset.hint);
    showAlert(btn.dataset.title, (tip && tip.textContent) || "当前无附加说明。");
    return;
  }
  if (btn.id === "aiProfileAdd") {
    if (!aiProfileDraft) aiProfileDraft = cloneAiProfileDraft();
    const source = activeAiDraftProfile() || {};
    const id = "ai_" + Date.now();
    aiProfileDraft.profiles.push({
      id: id,
      name: "新 AI 配置",
      base_url: source.base_url || "",
      model: source.model || "",
      api_key_env: source.api_key_env || "WRELAY_AI_API_KEY",
      timeout_seconds: Number(source.timeout_seconds) > 0 ? source.timeout_seconds : 25,
      max_output_tokens: Number(source.max_output_tokens) > 0 ? source.max_output_tokens : 800,
    });
    aiProfileDraft.apiKeys = aiProfileDraft.apiKeys || {};
    aiProfileDraft.apiKeys[id] = "";
    aiProfileDraft.active = id;
    renderAiProfileEditor();
    return;
  }
  if (btn.id === "aiApiTest") {
    await testAiApi();
    return;
  }
  if (btn.id === "aiProfileDelete") {
    if (!aiProfileDraft || aiProfileDraft.profiles.length <= 1) return;
    const index = aiProfileDraft.profiles.findIndex(function (profile) {
      return String(profile.id) === String(aiProfileDraft.active);
    });
    if (index >= 0) aiProfileDraft.profiles.splice(index, 1);
    if (aiProfileDraft.apiKeys) delete aiProfileDraft.apiKeys[String(aiProfileDraft.active)];
    aiProfileDraft.active = String((aiProfileDraft.profiles[Math.max(0, index - 1)] || aiProfileDraft.profiles[0]).id);
    renderAiProfileEditor();
    return;
  }
  if (btn.id === "btnOpenMessageTrace") {
    btn.disabled = true;
    try {
      await openMessageTrace();
    } finally {
      btn.disabled = false;
    }
    return;
  }
  if (btn.id === "btnBackupRestore") { openBackupRestore(); return; }
  if (btn.id === "btnBackupExport") {
    try { await exportBackup(); } catch (err) { showAlert("导出备份", err.message || "导出失败"); }
    return;
  }
  if (btn.id === "btnBackupRestoreConfirm") {
    try { await restoreBackup(); } catch (err) { showAlert("恢复配置", err.message || "恢复失败"); }
    return;
  }
  if (btn.id === "btnMessageTraceRefresh") {
    btn.disabled = true;
    try {
      await refreshMessageTrace();
    } finally {
      btn.disabled = false;
    }
    return;
  }
  if (btn.id === "btnOpenDiagnostic") {
    btn.disabled = true;
    try {
      await openDiagnostic();
    } finally {
      btn.disabled = false;
    }
    return;
  }
  if (btn.id === "btnDiagnosticRefresh") {
    btn.disabled = true;
    try {
      await refreshDiagnostic();
    } catch (err) {
      showAlert("诊断失败", "暂时无法读取诊断状态，请稍后重试。");
    } finally {
      btn.disabled = false;
    }
    return;
  }
  if (btn.id === "btnFailureNotificationToggle") {
    if (!settings) return;
    settings.failure_notification = Object.assign(
      {cooldown_seconds: 900},
      settings.failure_notification || {},
      {enabled: btn.dataset.on !== "1"},
    );
    await saveAndReload();
    return;
  }
  if (btn.id === "translationSourceEnabled") {
    const on = btn.dataset.on !== "1";
    renderTranslationSettingButton("translationSourceEnabled", on, "启用翻译");
    syncTranslationSourceModalButtons();
    return;
  }
  if (btn.id === "translationSourceAppendOriginal") {
    if (btn.disabled) return;
    renderTranslationSettingButton("translationSourceAppendOriginal", btn.dataset.on !== "1", "保留原文");
    return;
  }
  if (btn.id === "translationSourceShowDatetime") {
    renderTranslationSettingButton("translationSourceShowDatetime", btn.dataset.on !== "1", "显示时间日期");
    return;
  }
  if (btn.id === "btnRelayRegisteredChannels") {
    openRelayChannelsModal("discord");
    return;
  }
  if (btn.id === "btnRelayFilter") {
    openRelayFilterModal();
    return;
  }
  if (btn.id === "btnXRelayRegisteredChannels") {
    openRelayChannelsModal("x");
    return;
  }
  if (btn.id === "btnXRelayFilter") {
    openXRelayFilterModal();
    return;
  }
  if (btn.id === "translationSourceSave") {
    await saveTranslationSourceModal();
    return;
  }
  if (btn.id === "relayChannelsSave") {
    await saveRelayChannels();
    return;
  }
  if (btn.dataset.fold) {
    const el = document.getElementById(btn.dataset.fold);
    const folded = el.style.display === "none";
    el.style.display = folded ? "" : "none";
    btn.classList.toggle("folded", !folded);
    return;
  }
  const k = btn.dataset.kind;
  if (k === "pageTab") {
    setActivePage(btn.dataset.page);
    return;
  }
  if (k === "fgTab") {
    setActiveFg(FG_CTX[btn.dataset.ctx] || FG_DISCORD, btn.dataset.id);
    return;
  }
  if (k === "fgToggle") {
    const ctx = FG_CTX[btn.dataset.ctx] || FG_DISCORD;
    const list = getFgList(ctx);
    const fg = list.find(function (item) { return item.id === btn.dataset.id; });
    if (!fg) return;
    fg.enabled = fg.enabled === false;
    await saveAndReload();
    return;
  }
  if (k === "fgRename") {
    startFgRename(FG_CTX[btn.dataset.ctx] || FG_DISCORD);
    return;
  }
  if (k === "fgDel") {
    const ctx = FG_CTX[btn.dataset.ctx] || FG_DISCORD;
    const fg = getActiveFg(ctx);
    // Discord/X 至少保留 1 个转发组；QQ→QQ 允许删除仅含一个群的最后一组。
    if (!fg) return;
    showConfirmDlg("删除转发组", "确定删除转发组「" + fg.name + "」？成员关系会移除，但" + (ctx.key === "x" ? "来源和群" : ctx.key === "qq" ? "群" : "频道和群") + "本身不会删除。", async function () {
      // 确认时基于最新 settings 解析，避免弹窗期间对象被替换
      const curList = getFgList(ctx);
      const target = curList.find(function (x) { return x.id === fg.id; });
      if (!target) return;
      curList.splice(curList.indexOf(target), 1);
      if (ctx.activeId === fg.id) {
        const next = curList[0] || null;
        ctx.activeId = next ? next.id : null;
        try { localStorage.setItem(ctx.storageKey, ctx.activeId || ""); } catch (e) {}
      }
      await saveAndReload();
    }, "删除");
    return;
  }
  if (btn.id === "btnAddFg") {
    addForwardingGroup(FG_DISCORD);
    return;
  }
  if (btn.id === "btnAddXFg") {
    addForwardingGroup(FG_X);
    return;
  }
  if (btn.id === "btnAddQQFg") {
    addForwardingGroup(FG_QQ);
    return;
  }
  if (btn.id === "btnQQConfig") {
    openQQConfigModal();
    return;
  }
  if (btn.id === "btnTranslationConfig") {
    openTranslationConfigModal();
    return;
  }
  if (btn.id === "btnXTranslationConfig") {
    openTranslationConfigModal();
    return;
  }
  if (btn.id === "translationConfigCancel") {
    closeTranslationConfigModal();
    return;
  }
  if (btn.id === "translationConfigSave") {
    saveTranslationConfig();
    return;
  }
  if (btn.id === "qqConfigCancel") {
    closeQQConfigModal();
    return;
  }
  if (btn.id === "qqConfigSave") {
    saveQQConfig();
    return;
  }
  if (btn.id === "btnDiscordConfig") {
    openDiscordConfigModal();
    return;
  }
  if (btn.id === "discordConfigCancel") {
    closeDiscordConfigModal();
    return;
  }
  if (btn.id === "discordConfigSave") {
    saveDiscordConfig();
    return;
  }
  if (btn.id === "btnAddXSource") {
    // 「注册」：打开二级窗口登记全局 X 转发账号（默认启用），不自动加入任何转发组
    openXSourceRegister();
    return;
  }
  if (btn.id === "btnAddXFgSource") {
    // 「＋ 新增」（X 转发账号）：从已注册账号中选择加入当前 X 转发组
    openFgAddPicker(FG_X, "source");
    return;
  }
  if (btn.id === "btnAddXFgGroup") {
    // 「＋ 新增」（QQ 接收群）：从已注册群中选择加入当前 X 转发组
    openFgAddPicker(FG_X, "group");
    return;
  }
  if (btn.id === "btnAddQQFgGroup") {
    // 「＋ 新增」（QQ 转发群）：从已注册群中选择加入当前 QQ→QQ 转发组
    openFgAddPicker(FG_QQ, "group");
    return;
  }
  if (btn.id === "btnSyncXGroups") {
    // 与 Discord → QQ 页共用的注册审核流程（/api/registrations）
    await openRegistrationReview("groups");
    return;
  }
  if (btn.id === "btnSyncQQGroups") {
    await openRegistrationReview("groups");
    return;
  }
  if (btn.id === "btnQQPlatformX") {
    window.open("https://q.qq.com/qqbot/dashboard/", "_blank");
    return;
  }
  if (btn.id === "btnQQPlatformQQ") {
    window.open("https://q.qq.com/qqbot/dashboard/", "_blank");
    return;
  }
  if (btn.id === "btnOpenX") {
    // 与 QQ 开放平台管理页同一新标签页跳转方式
    window.open("https://x.com/", "_blank");
    return;
  }
  if (btn.id === "btnAddXUser") {
    // 与 Discord 页完全相同的「从已注册项新增」白名单流程
    openUserAddPicker();
    return;
  }
  if (btn.id === "btnSyncXUsers") {
    await openRegistrationReview("users");
    return;
  }
  if (btn.id === "btnAddFgChan") {
    openFgAddPicker(FG_DISCORD, "chan");
    return;
  }
  if (btn.id === "btnAddFgGroup") {
    openFgAddPicker(FG_DISCORD, "group");
    return;
  }
  if (btn.id === "btnAddScheduledGroup") {
    openScheduledGroupPicker();
    return;
  }
  if (btn.id === "btnScanChannels") {
    scanReadableChannels(btn);
    return;
  }
  if (k === "xFgSourceRemove") {
    // 与转发组行内"删除"同机制：仅把来源移出当前 X 转发组（不删注册源）
    removeFgMember(FG_X, btn.dataset.id, "sources");
    return;
  }
  if (k === "xFgGroupRemove") {
    // 仅把群移出当前 X 转发组（不删全局群、不影响其他组）
    removeFgMember(FG_X, btn.dataset.id, "groups");
    return;
  }
  if (k === "qqFgGroupRemove") {
    // 仅把群移出当前 QQ→QQ 转发组（不删全局群、不影响其他组）
    removeFgMember(FG_QQ, btn.dataset.id, "groups");
    return;
  }
  if (k === "fgGroupRemove") {
    removeFgMember(FG_DISCORD, btn.dataset.id, "groups");
    return;
  } else if (k === "failureRecipient") {
    const u = settings.qq_user_openids[Number(btn.dataset.i)];
    if (u) {
      u.failure_notification = !u.failure_notification;
      await saveAndReload();
    }
    return;
  } else if (k === "userDel") {
    const idx = Number(btn.dataset.i);
    const u = settings.qq_user_openids[idx];
    const label = (u.name || "").trim() || (u.remark || "").trim() || identityName("users", u.openid) || u.openid;
    showConfirmDlg("删除用户", "确定要删除用户「" + label + "」吗？", async function () {
      settings.qq_user_openids.splice(idx, 1);
      await saveAndReload();
    }, "删除");
  } else if (k === "xUserDel") {
    // X → QQ 页白名单行内删除：与 Discord 页 userDel 同机制（同一份全局数据）
    const idx = Number(btn.dataset.i);
    const u = settings.qq_user_openids[idx];
    if (!u) return;
    const label = (u.name || "").trim() || (u.remark || "").trim() || identityName("users", u.openid) || u.openid;
    showConfirmDlg("删除用户", "确定要删除用户「" + label + "」吗？", async function () {
      settings.qq_user_openids.splice(idx, 1);
      await saveAndReload();
    }, "删除");
  } else if (k === "chanTranslation") {
    openTranslationSourceModal("channel", Number(btn.dataset.i));
  } else if (k === "xSourceTranslation") {
    openTranslationSourceModal("xsource", Number(btn.dataset.i));
  } else if (k === "chanFilter") {
    openFilterModal(Number(btn.dataset.i));
  } else if (k === "xSourceFilter") {
    openXSourceFilterModal(Number(btn.dataset.i));
  } else if (k === "fgChanRemove") {
    removeFgMember(FG_DISCORD, btn.dataset.id, "channels");
  } else if (btn.id === "btnAddUser") {
    openUserAddPicker();
  } else if (btn.id === "btnSync") {
    // 群注册审核：自动发现的 openid 只记录，注册后才进入待审名单
    await openRegistrationReview("groups");
  } else if (btn.id === "btnSyncUsers") {
    // 用户注册审核：自动发现的 openid 只记录，注册后才进入待审名单
    await openRegistrationReview("users");
  } else if (btn.id === "btnQQPlatform") {
    window.open("https://q.qq.com/qqbot/dashboard/", "_blank");
  } else if (btn.id === "btnDiscord") {
    window.open("https://discord.com/channels/@me", "_blank");
  } else if (btn.id === "btnDiscordDev") {
    window.open("https://discord.com/developers/home", "_blank");
  } else if (btn.id === "btnClearLog") {
    const r = await jpost("/api/bot/log/clear");
    if (!r.ok) showAlert("清空失败", r.msg || "清空失败");
    await refreshStatus();
  } else if (btn.id === "btnBackfillRun") {
    // 与 QQ 私聊 backfill run 一致：先查待补发缺口，无消息时直接告知，避免空触发
    btn.disabled = true;
    try {
      const p = await jget("/api/backfill/pending");
      if (p.ok && !p.total) {
        showAlert("补发", "当前无可补发的消息");
        await refreshBackfill();
      } else {
        const r = await jpost("/api/backfill/run");
        showAlert("补发", r.ok ? (r.msg || "补发已触发") : (r.msg || "补发失败"));
        await refreshBackfill();
      }
    } finally {
      updateBackfillButtons();
    }
  } else if (btn.id === "btnBackfillRefresh") {
    btn.disabled = true;
    try {
      await refreshBackfill();
      showAlert("刷新状态", "已读取 Discord 实时状态");
    } finally {
      btn.disabled = false;
    }
  } else if (btn.id === "btnBackfillClear") {
    showConfirmDlg("清空待补发", "清空待补发？未转发的旧消息将不再补发，此操作不可撤销。", async function () {
      btn.disabled = true;
      try {
        const r = await jpost("/api/backfill/clear");
        showAlert("清空结果", r.ok ? (r.cleared ? ("已清空 " + r.cleared + " 个频道的待补发") : "当前无可清空的待补发") : (r.msg || "清空失败"));
        await refreshBackfill();
      } finally {
        updateBackfillButtons();
      }
    });
  } else if (btn.id === "btnStart") {
    await jpost("/api/bot/start");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnStop") {
    await jpost("/api/bot/stop");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnRestart") {
    await jpost("/api/bot/restart");
    await sleep(1500); await refreshStatus();
  } else if (btn.id === "btnAutostart") {
    const enabled = btn.dataset.on !== "1";
    const r = await jpost("/api/autostart/set", {enabled: enabled});
    if (!r.ok) showAlert("设置失败", "设置开机自启失败");
    renderAutostart(btn, !!r.enabled);
  } else if (btn.id === "btnBackfillToggle") {
    settings.backfill_enabled = btn.dataset.on !== "1";
    await saveAndReload();
  } else if (btn.id === "btnScheduledMessageToggle") {
    await saveScheduledMessageSettings(btn.dataset.on !== "1");
  } else if (btn.id === "btnTranslationToggle") {
    const enabled = btn.dataset.on !== "1";
    (settings.discord_channels || []).forEach(function (channel) {
      channel.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnXTranslationToggle") {
    const enabled = btn.dataset.on !== "1";
    (settings.x_rss_sources || []).forEach(function (source) {
      source.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnAbout") {
    document.getElementById("aboutModal").style.display = "flex";
  } else if (btn.id === "aboutQq") {
    openAboutLink(ABOUT_LINKS.qq, "QQ");
  } else if (btn.id === "aboutBilibili") {
    openAboutLink(ABOUT_LINKS.bilibili, "Bilibili");
  } else if (btn.id === "aboutGithub") {
    openAboutLink(ABOUT_LINKS.github, "GitHub");
  } else if (btn.id === "btnRestartPanel") {
    btn.disabled = true;
    try {
      const r = await jpost("/api/panel/restart");
      if (r.ok) {
        await sleep(3000);
        location.reload();
      } else {
        btn.disabled = false;
        showAlert("重启失败", r.msg || "重启失败");
      }
    } catch (err) {
      btn.disabled = false;
      showAlert("重启失败", "请求失败，面板可能已离线");
    }
  } else if (btn.id === "btnTheme") {
    toggleTheme();
  }
});

document.addEventListener("change", async function (e) {
  const el = e.target;
  if (el.id === "aiProfileSelect") {
    if (aiProfileDraft) {
      captureAiApiKeyDraft();
      aiProfileDraft.active = el.value;
      renderAiProfileEditor();
    }
    return;
  }
  if (el.id === "translationProvider" || el.id === "translationFallback") {
    if (el.id === "translationProvider") syncTranslationFallback("translationProvider", "translationFallback");
    saveAIControls();
    await saveAndReload();
    return;
  }
  if (el.id === "xTranslationProvider" || el.id === "xTranslationFallback") {
    if (el.id === "xTranslationProvider") syncTranslationFallback("xTranslationProvider", "xTranslationFallback");
    saveXTranslationControls();
    await saveAndReload();
    return;
  }
  if (el.id === "backfillLimit") {
    let v = parseInt(el.value, 10);
    if (!(v > 0)) v = 10;
    if (v > 30) v = 30;
    settings.backfill_limit = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (el.id === "logLevel") {
    const r = await jpost("/api/bot/loglevel", {level: el.value});
    if (!r.ok) showAlert("设置失败", r.msg || "设置失败");
    await refreshStatus();
    return;
  }
  if (el.id === "xSourceAllEnabled") {
    const xfg = getActiveFg(FG_X);
    const sourceSet = memberIdSet(xfg && xfg.sources);
    (settings.x_rss_sources || []).forEach(function (s) {
      if (sourceSet[xSourceKey(s.source)]) s.enabled = el.checked;
    });
    await saveAndReload();
    return;
  }
  if (el.id === "xRsshubBase") {
    settings.x_rsshub_base = el.value.trim();
    await saveAndReload();
    return;
  }
  if (el.id === "xPollInterval") {
    let v = parseInt(el.value, 10);
    // 下限 30 秒（与后端 get_x_rss_poll_interval 一致，防误配打爆 RSSHub）
    if (!(v > 0)) v = 300;
    if (v < 30) v = 30;
    settings.x_rss_poll_interval = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (el.id === "xUserAllEnabled") {
    settings.qq_user_openids.forEach(function (u) { u.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "groupAllEnabled" || el.id === "xGroupAllEnabled" || el.id === "qqGroupAllEnabled") {
    const ctx = el.id === "xGroupAllEnabled" ? FG_X : el.id === "qqGroupAllEnabled" ? FG_QQ : FG_DISCORD;
    const fg = getActiveFg(ctx);
    const groupSet = memberIdSet(fg && fg.groups);
    settings.qq_group_openids.forEach(function (g) {
      if (groupSet[String(g.openid)]) g.enabled = el.checked;
    });
    await saveAndReload();
    return;
  }
  if (el.id === "chanAllEnabled") {
    const fg = getActiveFg(FG_DISCORD);
    const channelSet = memberIdSet(fg && fg.channels);
    settings.discord_channels.forEach(function (c) {
      if (channelSet[String(c.id)]) c.enabled = el.checked;
    });
    await saveAndReload();
    return;
  }
  if (el.id === "userAllEnabled") {
    settings.qq_user_openids.forEach(function (u) { u.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (!el.dataset.kind) return;
  if (el.dataset.kind === "xSource") {
    const s = (settings.x_rss_sources || [])[Number(el.dataset.i)];
    if (s) {
      s.enabled = el.checked;
      await saveAndReload();
    }
    return;
  }
  if (el.dataset.kind === "xUser") {
    const u = settings.qq_user_openids[Number(el.dataset.i)];
    if (u) {
      u.enabled = el.checked;
      await saveAndReload();
    }
    return;
  }
  if (el.dataset.kind === "group") {
    settings.qq_group_openids[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "chan") {
    settings.discord_channels[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "user") {
    settings.qq_user_openids[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  }
});

document.addEventListener("blur", async function (e) {
  const el = e.target;
  if (el.dataset.kind === "chanName") {
    settings.discord_channels[Number(el.dataset.i)].name = el.value;
    await saveAndReload();
  } else if (el.dataset.kind === "xSourceName") {
    const source = (settings.x_rss_sources || [])[Number(el.dataset.i)];
    if (source) {
      source.name = el.value;
      await saveAndReload();
    }
  }
}, true);

// 频道名称：正常状态显示「名称 ✎」，点击 ✎ 才显示输入框进入编辑；
// 失焦后沿用上方既有 chanName 保存逻辑（保存并重渲染，自动恢复为「名称 ✎」）。
function startChanNameEdit(i) {
  const c = settings.discord_channels[Number(i)];
  if (!c) return;
  const holder = document.querySelector('.chan-name[data-kind="chanNameDisplay"][data-i="' + i + '"]');
  if (!holder) return;
  const input = document.createElement("input");
  input.type = "text";
  input.value = c.name || "";
  input.dataset.kind = "chanName";
  input.dataset.i = String(i);
  holder.replaceWith(input);
  input.focus();
  input.select();
}
function startXSourceNameEdit(i) {
  const source = (settings.x_rss_sources || [])[Number(i)];
  if (!source) return;
  const holder = document.querySelector('.chan-name[data-kind="xSourceNameDisplay"][data-i="' + i + '"]');
  if (!holder) return;
  const input = document.createElement("input");
  input.type = "text";
  input.value = source.name || "";
  input.dataset.kind = "xSourceName";
  input.dataset.i = String(i);
  holder.replaceWith(input);
  input.focus();
  input.select();
}
document.addEventListener("click", function (e) {
  const edit = e.target.closest('[data-kind="chanNameEdit"]');
  if (edit) {
    startChanNameEdit(edit.dataset.i);
    return;
  }
  const xEdit = e.target.closest('[data-kind="xSourceNameEdit"]');
  if (xEdit) {
    startXSourceNameEdit(xEdit.dataset.i);
  }
});

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

let lastBackfillSeq = 0;
let latestStatus = null;

function renderDiagnostic(st) {
  const body = document.getElementById("diagnosticBody");
  if (!body) return;
  const health = (st && st.health) || {};
  const retry = health.retry || {};
  const rows = [
    ["机器人进程", st && st.running ? "运行中" : "未运行", !!(st && st.running)],
    ["Discord 连接", health.discord ? "已连接" : "未连接", !!health.discord],
    ["QQ 连接", health.qq ? "已连接" : "未连接", !!health.qq],
    ["待重试投递", Number(retry.pending_entries || 0) ? String(retry.pending_entries) + " 条" : "无", !Number(retry.pending_entries || 0)],
  ];
  body.innerHTML = "";
  rows.forEach(function (row) {
    const item = document.createElement("div");
    item.className = "diagnostic-row";
    const label = document.createElement("span");
    label.textContent = row[0];
    const value = document.createElement("span");
    value.className = "diagnostic-result " + (row[2] ? "ok" : "warn");
    value.textContent = row[1];
    item.append(label, value);
    body.appendChild(item);
  });
}

async function refreshDiagnostic() {
  const st = await jget("/api/status");
  latestStatus = st;
  renderStatus(st);
  renderDiagnostic(st);
}

async function openDiagnostic() {
  const modal = document.getElementById("diagnosticModal");
  if (!modal) return;
  modal.style.display = "flex";
  if (latestStatus) renderDiagnostic(latestStatus);
  try {
    await refreshDiagnostic();
  } catch (err) {
    const body = document.getElementById("diagnosticBody");
    if (body) body.textContent = "暂时无法读取诊断状态，请稍后刷新。";
  }
}

function closeDiagnosticModal() {
  const modal = document.getElementById("diagnosticModal");
  if (modal) modal.style.display = "none";
}

async function refreshStatus() {
  try {
    const st = await jget("/api/status");
    latestStatus = st;
    renderStatus(st);
    // 同步补发开关/上限（QQ 私聊指令可能改动 settings.json）；
    // 正在编辑的控件不覆盖，避免打断用户输入
    if (st.backfill_enabled !== undefined) {
      renderBackfillToggle(st.backfill_enabled !== false);
    }
    const bfLimit = document.getElementById("backfillLimit");
    if (
      bfLimit
      && st.backfill_limit !== undefined
      && document.activeElement !== bfLimit
    ) {
      bfLimit.value = Number(st.backfill_limit) > 0 ? st.backfill_limit : 10;
    }
    if (st.scheduled_message !== undefined && settings && !scheduledMessageEditorFocused()) {
      settings.scheduled_message = st.scheduled_message || {};
      renderScheduledMessage(settings.scheduled_message);
    }
    // 补发/清除成功（含 QQ 私聊触发）后立即刷新待补发数量
    if (st.backfill_seq !== lastBackfillSeq) {
      lastBackfillSeq = st.backfill_seq;
      await refreshBackfill();
    }
  } catch (err) {
    document.getElementById("badge").textContent = "面板连接断开";
  }
}

let pollingStarted = false;

async function init() {
  // 防重复启动：无论 init() 被调用多少次（重复加载/脚本二次执行），
  // 轮询定时器只创建一套，避免 /api/status → /api/health 出现多重轮询。
  if (pollingStarted) return;
  pollingStarted = true;
  // 恢复上一次选择的界面（默认 Discord → QQ）
  setActivePage(activePage);
  try {
    settings = await jget("/api/settings");
    renderSettings();
  } catch (err) { /* ignore */ }
  await refreshChannelNames();
  await refreshStatus();
  setInterval(refreshStatus, 3000);
  await refreshBackfill();
  setInterval(refreshBackfill, 300000);
  await refreshIdentities();
  setInterval(refreshIdentities, 30000);
  // X RSS 状态卡片：独立轻量轮询（只更新卡片文本，10 秒足够）
  await refreshXStatus();
  setInterval(refreshXStatus, 10000);
  await refreshMessageTrace();
  setInterval(refreshMessageTrace, 10000);
}

init();
