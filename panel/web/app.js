const SVG_FUNNEL = mcIcon("filter-2", 14);
const SVG_FAILURE_NOTIFICATION = mcIcon("warning", 18);
const SVG_ORIGINAL_ON = mcIcon("check", 13);
const SVG_ORIGINAL_OFF = mcIcon("close", 13);
const SVG_TRASH = mcIcon("delete-2", 14);
const SVG_GRIP = mcIcon("menu", 14);
const SVG_SCHEDULE_CALENDAR = '<svg class="scheduled-date-picker-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5.5" width="16" height="15" rx="2"/><path d="M8 3.5v4M16 3.5v4M4 9.5h16M8 13h.01M12 13h.01M16 13h.01M8 17h.01M12 17h.01M16 17h.01"/></svg>';
const SVG_SCHEDULE_CLOCK = '<svg class="scheduled-date-picker-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/></svg>';
// 统一笔图标：空心笔、笔尖朝左下角（转发组重命名 / 频道名称编辑共用）
const SVG_PEN = mcIcon("edit-2", 12);
const SVG_PAUSE = mcIcon("pause", 13);
const SVG_PLAY = mcIcon("play", 13);
// 更新检查偏好：启动后是否自动检查一次（仅提示，不自动安装）
const AUTO_UPDATE_CHECK_KEY = "wrelay_auto_update_check";
function isAutoUpdateCheckEnabled() {
  try { return localStorage.getItem(AUTO_UPDATE_CHECK_KEY) !== "0"; } catch (e) { return true; }
}
function applyAutoUpdateCheckPreference(enabled) {
  const button = document.getElementById("btnAutoUpdateCheck");
  if (button) {
    button.dataset.on = enabled ? "1" : "0";
    button.setAttribute("aria-checked", String(enabled));
  }
}
(function initUpdatePreferences() {
  applyAutoUpdateCheckPreference(isAutoUpdateCheckEnabled());
})();

let settings = null;
// 主题偏好：夜间/日间模式，持久化到 localStorage，首次使用默认夜间。
const THEME_KEY = "wrelay_theme";
const THEME_DARK = "dark";
const THEME_LIGHT = "light";
const STATUS_SHEEN_KEY = "wrelay_status_sheen";
function isStatusSheenEnabled() {
  try { return localStorage.getItem(STATUS_SHEEN_KEY) !== "0"; } catch (e) { return true; }
}
function applyStatusSheenPreference(enabled) {
  document.body.classList.toggle("status-sheen-disabled", !enabled);
}
applyStatusSheenPreference(isStatusSheenEnabled());
// 面板默认主题：夜间。只影响"没有保存过主题偏好"的首次打开；
// 用户已保存的 dark/light 偏好始终照常恢复，不会被默认值覆盖
const THEME_DEFAULT = THEME_DARK;
function applyTheme(theme) {
  document.documentElement.dataset.startupTheme = theme === THEME_LIGHT ? THEME_LIGHT : THEME_DARK;
  document.body.classList.toggle("theme-light", theme === THEME_LIGHT);
  const themeMode = document.getElementById("themeMode");
  if (themeMode) themeMode.value = theme === THEME_LIGHT ? THEME_LIGHT : THEME_DARK;
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
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
// 转发组机制：Discord、X、Bilibili、QQ→QQ 各自一套上下文（组列表字段 / 成员字段 / active
// 状态 / id 前缀等），组相关函数全部按 ctx 参数化复用，四者交互一致。
// active 组 id 各自持久化到 localStorage，刷新后恢复且互不影响；
// 组内成员随 settings 整体保存。
const MAX_FG = 30;
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
const FG_BILIBILI = {
  key: "bilibili",
  field: "bilibili_forwarding_groups",
  memberField: {biliSource: "sources", source: "sources", group: "groups"},
  emptyMembers: ["sources", "groups"],
  storageKey: "wrelay_active_bilibili_fg",
  idPrefix: "bilibili_fg_",
  tabsId: "bilibiliFgTabs",
  addBtnId: "btnAddBilibiliFg",
  activeId: null,
};
const FG_YOUTUBE = {
  key: "youtube",
  field: "youtube_forwarding_groups",
  memberField: {youtubeSource: "sources", source: "sources", group: "groups"},
  emptyMembers: ["sources", "groups"],
  storageKey: "wrelay_active_youtube_fg",
  idPrefix: "youtube_fg_",
  tabsId: "youtubeFgTabs",
  addBtnId: "btnAddYoutubeFg",
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
const FG_CTX = {discord: FG_DISCORD, x: FG_X, bilibili: FG_BILIBILI, youtube: FG_YOUTUBE, qq: FG_QQ};
try { FG_DISCORD.activeId = localStorage.getItem(FG_DISCORD.storageKey); } catch (e) { FG_DISCORD.activeId = null; }
try { FG_X.activeId = localStorage.getItem(FG_X.storageKey); } catch (e) { FG_X.activeId = null; }
try { FG_BILIBILI.activeId = localStorage.getItem(FG_BILIBILI.storageKey); } catch (e) { FG_BILIBILI.activeId = null; }
try { FG_YOUTUBE.activeId = localStorage.getItem(FG_YOUTUBE.storageKey); } catch (e) { FG_YOUTUBE.activeId = null; }
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

// ---------------- 主要界面切换 ----------------
// 桌面端每次启动进入运行总览；普通网页面板恢复上次选择的页面。
// 三个页面共用同一份 settings 对象（整体保存、整体拉取），切换只改可见性，
// 不会丢失任何已保存/未保存的修改。
const IS_DESKTOP_HOST = window.location.protocol === "file:" || new URLSearchParams(window.location.search).get("desktop") === "1";
if (IS_DESKTOP_HOST) document.body.classList.add("desktop-host");
const PAGE_STORAGE_KEY = IS_DESKTOP_HOST ? "wrelay_desktop_active_page" : "wrelay_active_page";
let activePage = IS_DESKTOP_HOST ? "overview" : "discord";
try {
  const savedPage = localStorage.getItem(PAGE_STORAGE_KEY);
  if (!IS_DESKTOP_HOST && ["overview", "discord", "x", "bilibili", "youtube", "steam", "qq", "settings"].includes(savedPage)) activePage = savedPage;
} catch (e) {}

function setActivePage(page) {
  const pageInfo = {
    overview: ["总览", "查看各转发链路与机器人运行状态"],
    discord: ["Discord → QQ", "管理频道、接收群与转发组"],
    x: ["X → QQ", "管理 X 来源、接收群与转发组"],
    bilibili: ["Bilibili → QQ", "管理 Bilibili 来源、接收群与转发组"],
    youtube: ["YouTube → QQ", "管理 YouTube 频道、接收群与转发组"],
    steam: ["Steam → QQ", "管理 Steam 来源、接收群与转发组"],
    qq: ["QQ → QQ", "管理授权用户的手动转发目标"],
    settings: ["设置", "应用偏好、连接与维护"],
  };
  activePage = Object.hasOwn(pageInfo, page) ? page : "discord";
  const chainDetailModal = document.getElementById("chainDetailModal");
  if (chainDetailModal?.style.display === "flex") closeChainDetailModal();
  document.body.dataset.activePage = activePage;
  try { localStorage.setItem(PAGE_STORAGE_KEY, activePage); } catch (e) {}
  document.getElementById("activePageTitle").textContent = pageInfo[activePage][0];
  document.getElementById("activePageDescription").textContent = pageInfo[activePage][1];
  document.getElementById("pageOverview").style.display = activePage === "overview" ? "" : "none";
  document.getElementById("pageDiscord").style.display = activePage === "discord" ? "" : "none";
  document.getElementById("pageX").style.display = activePage === "x" ? "" : "none";
  document.getElementById("pageBilibili").style.display = activePage === "bilibili" ? "" : "none";
  document.getElementById("pageYoutube").style.display = activePage === "youtube" ? "" : "none";
  document.getElementById("pageSteam").style.display = activePage === "steam" ? "" : "none";
  document.getElementById("pageQQ").style.display = activePage === "qq" ? "" : "none";
  document.getElementById("pageSettings").style.display = activePage === "settings" ? "" : "none";
  const runtimeCards = document.querySelector(".cardrow");
  if (runtimeCards) runtimeCards.style.display = activePage === "settings" ? "none" : "";
  document.getElementById("recentMessageCard").style.display = activePage === "overview" ? "" : "none";
  if (latestMessageTraceData) renderRecentMessage(latestMessageTraceData);
  // 桌面版将 RSS 与 Steam 详情卡收在状态栏入口内；普通网页仍在对应链路页显示。
  const showInlineChainDetails = !IS_DESKTOP_HOST;
  const xrssCard = document.getElementById("xrssCard");
  if (xrssCard) xrssCard.style.display = showInlineChainDetails && activePage === "x" ? "" : "none";
  const bilibiliCard = document.getElementById("bilibiliCard");
  if (bilibiliCard) bilibiliCard.style.display = showInlineChainDetails && activePage === "bilibili" ? "" : "none";
  const youtubeCard = document.getElementById("youtubeCard");
  if (youtubeCard) youtubeCard.style.display = showInlineChainDetails && activePage === "youtube" ? "" : "none";
  const steamCard = document.getElementById("steamStatusCard");
  if (steamCard) steamCard.style.display = showInlineChainDetails && activePage === "steam" ? "" : "none";
  const scheduledCard = document.getElementById("scheduledMessageCard");
  if (scheduledCard) scheduledCard.style.display = activePage === "qq" ? "" : "none";
  document.querySelectorAll('#pageTabs [data-kind="pageTab"]').forEach(function (btn) {
    btn.classList.toggle("active", btn.dataset.page === activePage);
    if (btn.dataset.page === activePage) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
  renderOverviewCounts();
  if (latestStatus) renderStatus(latestStatus);
  renderChainStatusShortcut();
  renderPageCredentialShortcut();
  renderFailureNotificationShortcut();
  if (activePage === "steam" && window.renderSteamPage) window.renderSteamPage();
  if (settings) {
    if (activePage === "overview") void refreshWatchedChainStatuses();
    else if (activePage === "youtube") void refreshYoutubeStatus();
    else if (activePage === "steam") void refreshSteamStatus();
  }
}

function renderPageCredentialShortcut() {
  const discord = document.getElementById("discordConfigShortcut");
  const youtube = document.getElementById("youtubeApiKeyShortcut");
  if (!discord || !youtube) return;
  const detailOpen = document.getElementById("chainDetailModal")?.style.display === "flex";
  discord.hidden = activePage !== "discord" || !detailOpen;
  youtube.hidden = activePage !== "youtube" || !detailOpen;
  const discordHealthy = !!(latestStatus && latestStatus.running === true
    && latestStatus.health && latestStatus.health.discord === true);
  discord.dataset.state = discordHealthy ? "on" : "neutral";
  discord.title = discordHealthy ? "Discord Bot 凭据正常 · 已连接" : "配置 Discord Bot 凭据";
  discord.setAttribute("aria-label", discordHealthy ? "配置 Discord Bot 凭据：已连接" : "配置 Discord Bot 凭据");
  const youtubeHealthy = !!(latestYoutubeStatus && latestYoutubeStatus.data_api_healthy === true);
  youtube.dataset.state = youtubeHealthy ? "on" : "neutral";
  youtube.title = youtubeHealthy ? "YouTube Data API Key 正常工作" : "配置 YouTube Data API Key";
  youtube.setAttribute("aria-label", youtubeHealthy ? "配置 YouTube Data API Key：工作正常" : "配置 YouTube Data API Key");
}

function renderChainStatusShortcut() {
  const button = document.getElementById("chainStatusShortcut");
  if (!button) return;

  const pages = {
    discord: {kind: "discord", name: "Discord", target: "discordUpstreamCard"},
    x: {kind: "rss", name: "X", badge: "xrssBadge", summary: "xrssSummary", target: "xrssCard"},
    bilibili: {kind: "rss", name: "Bilibili", badge: "bilibiliBadge", summary: "bilibiliSummary", target: "bilibiliCard"},
    youtube: {kind: "rss", name: "YouTube", badge: "youtubeBadge", summary: "youtubeSummary", target: "youtubeCard"},
    steam: {kind: "steam", name: "Steam", icon: "rss", status: "steamStatus", target: "steamStatusCard"},
  };
  const page = pages[activePage];
  if (!page) {
    button.hidden = true;
    return;
  }

  button.hidden = false;
  button.dataset.target = page.target;
  button.setAttribute("aria-controls", page.target);
  if (page.kind === "discord") {
    const badge = document.getElementById("discordUpstreamBadge");
    const health = latestStatus?.health;
    const state = !latestStatus || !latestStatus.running ? "neutral" : !health ? "warn" : health.discord ? "on" : "warn";
    const label = !latestStatus ? "状态未知" : !latestStatus.running ? "机器人未运行" : !health ? "等待连接状态" : health.discord ? "Gateway 已连接" : health.discord_connecting ? "Gateway 连接中" : "Gateway 未连接";
    badge.className = "badge " + state;
    badge.textContent = label;
    const summary = document.getElementById("discordUpstreamSummary");
    if (summary) {
      const channels = settings && Array.isArray(settings.discord_channels) ? enabledTotal("discord_channels") : null;
      summary.textContent = channels ? "启用频道 " + channels.enabled + " / 配置 " + channels.total + " · 未逐频道检测" : "频道配置尚未读取";
    }
    button.dataset.state = state;
    button.title = "Discord 上游 · " + label;
  } else if (page.kind === "steam") {
    const status = document.getElementById(page.status);
    const badge = document.getElementById("steamBadge");
    const statusText = [badge && badge.textContent.trim(), status && status.textContent.trim()].filter(Boolean).join("；") || "等待检查";
    button.dataset.state = ["on", "off", "warn", "pending", "neutral"].find(function (state) {
      return badge && badge.classList.contains(state);
    }) || status?.dataset.state || "neutral";
    button.innerHTML = mcIcon("cloud-download", 18);
    button.title = page.name + "：" + statusText + " · 点击查看状态和设置";
    button.setAttribute("aria-label", "打开 Steam 运行状态详情");
  } else {
    const badge = document.getElementById(page.badge);
    const summary = document.getElementById(page.summary);
    const badgeText = badge ? badge.textContent.trim() : "检测中";
    let state = "neutral";
    if (badge && badge.classList.contains("on")) {
      state = "on";
    } else if (badge && badge.classList.contains("off")) {
      state = "off";
    } else if (badge && badge.classList.contains("warn")) {
      state = "warn";
    }
    button.dataset.state = state;
    button.innerHTML = mcIcon("cloud-download", 18);
    const detail = [page.name + " 上游：" + badgeText, badge && (badge.title || badge.dataset.wrelayTitle), summary && summary.textContent.trim()]
      .filter(Boolean).join("；");
    button.title = detail + " · 点击查看状态和设置";
    button.setAttribute("aria-label", "打开" + page.name + " 上游状态详情");
  }
  if (page.kind !== "discord" && button.dataset.state === "on") {
    const summary = document.getElementById(page.summary || page.status);
    if (!summary || !summary.textContent.includes("上次轮询") || summary.textContent.includes("上次轮询 —")) {
      button.dataset.state = "pending";
      button.title = page.name + " 上游 · 等待首次轮询";
    }
  }
  button.innerHTML = mcIcon("cloud-download", 18);
  button.setAttribute("aria-label", page.name + " 上游状态：" + (button.title || "状态未知"));
}

let chainDetailPlaceholder = null;
let chainDetailCard = null;
let chainDetailDisplay = "";

function openChainDetailModal(cardId) {
  const card = document.getElementById(cardId);
  const overlay = document.getElementById("chainDetailModal");
  const body = document.getElementById("chainDetailBody");
  const title = document.getElementById("chainDetailTitle");
  if (!card || !overlay || !body || !title) return;

  renderChainStatusShortcut();
  const heading = card.querySelector(":scope > h2");
  title.textContent = heading ? heading.textContent.trim() : "链路详情";
  chainDetailPlaceholder = document.createComment("Chain detail card position");
  card.before(chainDetailPlaceholder);
  chainDetailCard = card;
  chainDetailDisplay = card.style.display;
  card.style.display = "";
  body.replaceChildren(card);
  overlay.style.display = "flex";
  renderPageCredentialShortcut();
  overlay.querySelector(".modal-x")?.focus();
  if (cardId === "steamStatusCard") void refreshSteamStatus();
  else if (cardId === "youtubeCard") void refreshYoutubeStatus();
}

document.getElementById("chainDetailModal")?.addEventListener("click", function (event) {
  if (event.target === this) closeChainDetailModal();
});

function closeChainDetailModal() {
  const overlay = document.getElementById("chainDetailModal");
  const body = document.getElementById("chainDetailBody");
  if (!overlay) return;
  overlay.style.display = "none";
  if (chainDetailCard && chainDetailPlaceholder?.parentNode) {
    chainDetailPlaceholder.replaceWith(chainDetailCard);
    chainDetailCard.style.display = chainDetailDisplay;
  } else if (chainDetailCard && body) {
    body.append(chainDetailCard);
  }
  chainDetailCard = null;
  chainDetailPlaceholder = null;
  renderPageCredentialShortcut();
}

const chainStatusShortcut = document.getElementById("chainStatusShortcut");
if (chainStatusShortcut) {
  chainStatusShortcut.addEventListener("click", function () {
    const target = chainStatusShortcut.dataset.target;
    if (target) openChainDetailModal(target);
  });
}

function openYoutubeApiKeyModal() {
  const modal = document.getElementById("youtubeApiKeyModal");
  const input = document.getElementById("youtubeApiKeyInput");
  if (!modal) return;
  if (input) {
    input.value = "";
    input.type = "password";
  }
  setYoutubeApiKeyVisible(false);
  modal.style.display = "flex";
  refreshYoutubeApiKeyState();
  modal.querySelector(".modal-x")?.focus();
}

function closeYoutubeApiKeyModal() {
  const modal = document.getElementById("youtubeApiKeyModal");
  const input = document.getElementById("youtubeApiKeyInput");
  if (modal) modal.style.display = "none";
  if (input) input.value = "";
  setYoutubeApiKeyVisible(false);
}

async function refreshYoutubeApiKeyState() {
  const status = document.getElementById("youtubeApiKeyStatus");
  if (!status) return;
  status.textContent = "检查配置中…";
  try {
    const result = await jget("/api/youtube/api-key");
    status.textContent = result.ok
      ? (result.configured ? "已配置；密钥不会显示" : "尚未配置")
      : "无法读取配置状态";
  } catch (_error) {
    status.textContent = "无法读取配置状态";
  }
}

function setYoutubeApiKeyVisible(visible) {
  const input = document.getElementById("youtubeApiKeyInput");
  const toggle = input && input.closest(".secret-input-wrap")?.querySelector(".secret-input-toggle");
  if (!input || !toggle) return;
  input.type = visible ? "text" : "password";
  toggle.setAttribute("aria-pressed", String(visible));
  toggle.setAttribute("aria-label", visible ? "隐藏 API Key" : "显示 API Key");
  toggle.title = visible ? "隐藏 API Key" : "显示 API Key";
  toggle.innerHTML = mcIcon(visible ? "eye-close" : "eye", 18);
}

function secretToggleLabel(input, visible) {
  const name = input.id === "youtubeApiKeyInput" ? "API Key" : "密钥";
  return (visible ? "隐藏" : "显示") + " " + name;
}

function enhanceSecretInput(input) {
  if (!input || input.type !== "password" || input.closest(".secret-input-wrap") || !input.parentNode) return;
  const parent = input.parentNode;
  const wrap = document.createElement("span");
  wrap.className = "secret-input-wrap";
  parent.insertBefore(wrap, input);
  wrap.appendChild(input);

  const existingToggle = input.id === "youtubeApiKeyInput"
    ? document.getElementById("youtubeApiKeyToggle") : null;
  const toggle = existingToggle && existingToggle.parentNode === parent
    ? existingToggle : document.createElement("button");
  toggle.type = "button";
  toggle.className = "secret-input-toggle";
  toggle.setAttribute("aria-label", secretToggleLabel(input, false));
  toggle.title = secretToggleLabel(input, false);
  toggle.setAttribute("aria-pressed", "false");
  toggle.innerHTML = mcIcon("eye", 18);
  wrap.appendChild(toggle);
}

function enhanceSecretInputs(root) {
  if (root.nodeType === Node.ELEMENT_NODE && root.matches('input[type="password"]')) enhanceSecretInput(root);
  if (root.querySelectorAll) root.querySelectorAll('input[type="password"]').forEach(enhanceSecretInput);
}

enhanceSecretInputs(document);
document.addEventListener("click", function (event) {
  const toggle = event.target.closest(".secret-input-toggle");
  if (!toggle) return;
  const input = toggle.closest(".secret-input-wrap")?.querySelector("input");
  if (!input) return;
  const visible = input.type === "password";
  input.type = visible ? "text" : "password";
  toggle.setAttribute("aria-pressed", String(visible));
  toggle.setAttribute("aria-label", secretToggleLabel(input, visible));
  toggle.title = secretToggleLabel(input, visible);
  toggle.innerHTML = mcIcon(visible ? "eye-close" : "eye", 18);
});
new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
  if (node.nodeType === Node.ELEMENT_NODE) enhanceSecretInputs(node);
}))).observe(document.body, {childList: true, subtree: true});

document.getElementById("youtubeApiKeySave")?.addEventListener("click", async function () {
  const input = document.getElementById("youtubeApiKeyInput");
  const status = document.getElementById("youtubeApiKeyStatus");
  const button = this;
  const apiKey = input ? input.value.trim() : "";
  if (!apiKey) {
    if (status) status.textContent = "请输入新 Key；留空会保留当前密钥";
    input?.focus();
    return;
  }
  button.disabled = true;
  if (status) status.textContent = "正在保存…";
  try {
    const saved = await jpost("/api/youtube/api-key", {api_key: apiKey});
    if (!saved.ok) {
      if (status) status.textContent = saved.msg || "保存失败，请重试";
      return;
    }
    if (latestYoutubeStatus) latestYoutubeStatus = {...latestYoutubeStatus, data_api_healthy: false};
    renderPageCredentialShortcut();
    if (input) input.value = "";
    setYoutubeApiKeyVisible(false);
    if (status) status.textContent = "已保存，正在重启机器人…";
    let restarted;
    try { restarted = await postBotLifecycle("/api/bot/restart"); } catch (_error) { restarted = {ok: false}; }
    if (!restarted?.ok && status) status.textContent = "Key 已保存；机器人重启失败，请从总览手动重启。";
    else if (status) status.textContent = "Key 已保存，机器人正在重启。";
  } catch (_error) {
    if (status) status.textContent = "保存失败，请检查面板连接后重试。";
  } finally {
    button.disabled = false;
  }
});

function enabledTotal(field) {
  const items = settings && Array.isArray(settings[field]) ? settings[field] : [];
  return {
    enabled: items.filter(function (item) { return item && item.enabled !== false; }).length,
    total: items.length,
  };
}

function routedEnabledTotal(itemsField, groupsField, memberField, itemKey) {
  const items = settings && Array.isArray(settings[itemsField]) ? settings[itemsField] : [];
  const activeTargets = new Set(((settings && Array.isArray(settings.qq_group_openids)) ? settings.qq_group_openids : [])
    .filter(groupInActiveQQBotWorkspace)
    .filter(function (group) { return group.enabled !== false; })
    .map(function (group) { return String(group.openid); }));
  const routedKeys = new Set();
  const groups = settings && Array.isArray(settings[groupsField]) ? settings[groupsField] : [];
  groups.forEach(function (group) {
    if (!group || group.enabled === false || !Array.isArray(group.groups)
        || !group.groups.some(function (id) { return activeTargets.has(String(id)); })) return;
    (Array.isArray(group[memberField]) ? group[memberField] : []).forEach(function (id) {
      routedKeys.add(String(id));
    });
  });
  const routedItems = items.filter(function (item) {
    return item && routedKeys.has(String(itemKey(item) || ""));
  });
  return {
    enabled: routedItems.filter(function (item) { return item.enabled !== false; }).length,
    total: routedItems.length,
  };
}

function renderOverviewCounts() {
  const activeGroups = ((settings && settings.qq_group_openids) || []).filter(groupInActiveQQBotWorkspace);
  const groups = {enabled: activeGroups.filter(function (item) { return item.enabled !== false; }).length, total: activeGroups.length};
  const users = enabledTotal("qq_user_openids");
  const routes = [
    ["overviewDiscordMeta", routedEnabledTotal("discord_channels", "forwarding_groups", "channels", function (item) { return item.id; }), "频道"],
    ["overviewXMeta", routedEnabledTotal("x_rss_sources", "x_forwarding_groups", "sources", function (item) { return xSourceKey(item.source); }), "来源"],
    ["overviewBilibiliMeta", routedEnabledTotal("bilibili_sources", "bilibili_forwarding_groups", "sources", function (item) { return item.id || bilibiliSourceId(item.kind, item.value); }), "来源"],
    ["overviewYoutubeMeta", routedEnabledTotal("youtube_sources", "youtube_forwarding_groups", "sources", function (item) { return item.id || youtubeSourceId(item.value); }), "来源"],
    ["overviewSteamMeta", routedEnabledTotal("steam_sources", "steam_forwarding_groups", "sources", function (item) { return item.id; }), "来源"],
  ];
  routes.forEach(function (route) {
    const el = document.getElementById(route[0]);
    if (el) el.textContent = "启用" + route[2] + " " + route[1].enabled + "/" + route[1].total
      + " · 接收群 " + groups.enabled + "/" + groups.total;
  });
  const qqMeta = document.getElementById("overviewQQMeta");
  if (qqMeta) qqMeta.textContent = "授权用户 " + users.enabled + "/" + users.total
    + " · 目标群 " + groups.enabled + "/" + groups.total;
}

async function loadAppInfo() {
  const versionEl = document.getElementById("appVersion");
  if (!versionEl) return;
  try {
    const info = await jget("/api/app/info");
    versionEl.textContent = info && info.version && info.version !== "未知" ? "v" + info.version : "版本未知";
  } catch (err) {
    versionEl.textContent = "版本信息不可用";
  }
}

const UPDATE_CHECK_HINT = "检查 GitHub 官方稳定版，不会自动下载安装。";
const UPDATE_RELEASES_URL = "https://github.com/EraserOVO/WRelay/releases/latest";
// 已检查到可安装的新版本时，按钮改为「立即更新」
let pendingUpdate = null;
let updateInstalling = false;

function setUpdateButton(label, disabled) {
  const button = document.getElementById("btnCheckUpdates");
  if (!button) return;
  button.textContent = label;
  button.disabled = Boolean(disabled);
}

async function checkForUpdates() {
  const status = document.getElementById("updateStatus");
  if (!status) return;
  setUpdateButton("检查中…", true);
  status.textContent = "正在连接 GitHub 官方发布页。";
  try {
    const result = await jget("/api/app/update-check");
    if (!result || !result.ok) throw new Error((result && result.msg) || "暂时无法检查更新");
    const current = String(result.current_version || "未知");
    const latest = String(result.latest_version || "未知");
    if (result.update_available) {
      const fallback = UPDATE_RELEASES_URL;
      const url = String(result.release_url || "");
      pendingUpdate = {
        latest: latest,
        release_url: /^https:\/\/github\.com\/EraserOVO\/WRelay\/releases\//i.test(url) ? url : fallback,
      };
      status.textContent = "发现新版本 v" + latest + "（当前 v" + current + "），可立即更新。";
      setUpdateButton("立即更新", false);
    } else {
      pendingUpdate = null;
      status.textContent = "当前已是最新版本 v" + current + "。";
      setUpdateButton("检查更新", false);
    }
  } catch (err) {
    pendingUpdate = null;
    status.textContent = (err && err.message) || "请检查网络后重试。";
    setUpdateButton("检查更新", false);
  }
}

function describeInstallStage(state) {
  const stage = String((state && state.stage) || "");
  const percent = Number(state && state.percent);
  const hasPercent = Number.isFinite(percent) && percent > 0;
  const labels = {
    preparing: "准备下载…",
    downloading: "正在下载安装包…",
    verifying: "正在校验安装包…",
    launching: "正在启动安装程序…",
    done: "安装程序已启动",
  };
  let text = labels[stage] || "正在更新…";
  if (hasPercent && (stage === "downloading" || stage === "verifying")) {
    text += " " + Math.min(100, Math.round(percent)) + "%";
  }
  return text;
}

async function pollUpdateProgress() {
  const status = document.getElementById("updateStatus");
  while (updateInstalling) {
    let state = null;
    try {
      state = await jget("/api/app/update-progress");
    } catch (err) {
      state = null;
    }
    if (state && state.ok) {
      const text = describeInstallStage(state);
      if (status) status.textContent = text;
      const percent = Number(state.percent);
      setUpdateButton(
        Number.isFinite(percent) && percent > 0
          ? "更新中 " + Math.min(100, Math.round(percent)) + "%"
          : "更新中…",
        true
      );
      if (state.stage === "done" || state.stage === "failed") {
        updateInstalling = false;
        if (state.stage === "done") {
          if (status) status.textContent = "安装程序已启动，请按向导完成更新。";
          setUpdateButton("更新中…", true);
        } else {
          if (status) status.textContent = state.error || "更新失败，请稍后重试。";
          setUpdateButton("立即更新", false);
        }
        return;
      }
    }
    await new Promise(function (resolve) { setTimeout(resolve, 800); });
  }
}

async function startUpdate() {
  const status = document.getElementById("updateStatus");
  if (!pendingUpdate || updateInstalling) return;
  updateInstalling = true;
  setUpdateButton("准备中…", true);
  if (status) status.textContent = "正在准备下载安装包…";
  try {
    const result = await jpost("/api/app/update-install");
    if (!result || !result.ok) throw new Error((result && result.msg) || "无法开始更新");
  } catch (err) {
    updateInstalling = false;
    if (status) status.textContent = (err && err.message) || "无法开始更新，请稍后重试。";
    setUpdateButton("立即更新", false);
    return;
  }
  await pollUpdateProgress();
}

async function runUpdateAction() {
  if (updateInstalling) return;
  if (pendingUpdate) {
    await startUpdate();
  } else {
    await checkForUpdates();
  }
}

// 页面刷新后若安装仍在进行，接续显示进度而不是回到「检查更新」
async function resumeUpdateProgressIfRunning() {
  let state = null;
  try {
    state = await jget("/api/app/update-progress");
  } catch (err) {
    return;
  }
  if (!state || !state.ok || !state.running) return;
  updateInstalling = true;
  if (!pendingUpdate) {
    pendingUpdate = { latest: "", release_url: "" };
  }
  await pollUpdateProgress();
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

function withActiveQQBot(url) {
  if (typeof activeQQBotId !== "undefined" && activeQQBotId && url.startsWith("/api/") && !/[?&]bot_id=/.test(url)) {
    return url + (url.includes("?") ? "&" : "?") + "bot_id=" + encodeURIComponent(activeQQBotId);
  }
  return url;
}

async function jget(url) {
  url = withActiveQQBot(url);
  await ensurePanelToken();
  const r = await fetch(url, {
    headers: {"X-WRelay-Token": panelToken},
  });
  return r.json();
}
async function jpost(url, body) {
  url = withActiveQQBot(url);
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
async function postBotLifecycle(url) {
  const result = await jpost(url);
  if (result.ok) void refreshWatchedChainStatuses(true);
  return result;
}
async function runBotAction(url, title, fallback) {
  try {
    const r = await postBotLifecycle(url);
    if (!r.ok) showAlert(title, r.msg || fallback);
  } catch (e) {
    showAlert(title, "桌面管理功能不可用，请重新打开 WRelay");
  }
}

function openBackupRestore() {
  document.getElementById("backupRestoreModal").style.display = "flex";
  refreshAutomaticBackups();
  refreshMaintenance();
}
var maintenanceEnabled = true;
const MAINTENANCE_DISMISS_MS = 10 * 60 * 1000;
const MAINTENANCE_BANNER_POSITION_KEY = "wrelay.maintenance-banner-position.v1";
let maintenanceDismissedUntil = 0;
let maintenanceBannerPositionRestored = false;
let maintenancePreview = false;
let maintenancePreviewKeys = "";
const MAINTENANCE_PREVIEW_CODE = "wrelayreminder";
let statusFailurePreview = false;
let statusFailurePreviewKeys = "";
let statusCardLastHealthy = false;
let statusCardLastFailed = false;
const STATUS_FAILURE_PREVIEW_CODE = "wrelayfail";
let clearTestPreviewKeys = "";
const CLEAR_TEST_PREVIEWS_CODE = "wrelayclear";

function clearTestPreviewStates() {
  maintenancePreviewKeys = "";
  statusFailurePreviewKeys = "";
  clearTestPreviewKeys = "";
  statusFailurePreview = false;
  if (maintenancePreview) {
    maintenancePreview = false;
    const banner = document.getElementById("maintenanceBanner");
    if (banner) banner.style.display = "none";
    refreshMaintenance();
  }
  setStatusCardAmbient(document.getElementById("badge"), statusCardLastHealthy, statusCardLastFailed);
}

function setMaintenanceBannerPosition(left, top, persist) {
  const banner = document.getElementById("maintenanceBanner");
  if (!banner || getComputedStyle(banner).display === "none") return;
  let rect = banner.getBoundingClientRect();
  const margin = 8;
  const rootRect = document.documentElement.getBoundingClientRect();
  const viewportRight = Math.min(window.innerWidth, rootRect.right);
  const sidebar = document.querySelector(".desktop-sidebar");
  const sidebarRight = sidebar && getComputedStyle(sidebar).display !== "none"
    ? sidebar.getBoundingClientRect().right : 0;
  const minLeft = Math.max(margin, sidebarRight + margin);
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  const maxWidth = Math.max(0, viewportRight - minLeft - margin);
  if (rect.width > maxWidth) {
    banner.style.width = maxWidth + "px";
    rect = banner.getBoundingClientRect();
  }
  const maxLeft = Math.max(minLeft, viewportRight - rect.width - margin);
  const maxTop = Math.max(margin, viewportHeight - rect.height - margin);
  const position = {
    left: Math.min(maxLeft, Math.max(minLeft, Number(left) || minLeft)),
    top: Math.min(maxTop, Math.max(margin, Number(top) || margin)),
  };
  banner.style.left = position.left + "px";
  banner.style.top = position.top + "px";
  banner.style.right = "auto";
  banner.style.bottom = "auto";
  if (persist) {
    try { localStorage.setItem(MAINTENANCE_BANNER_POSITION_KEY, JSON.stringify(position)); } catch (error) { /* Storage may be disabled. */ }
  }
}

function restoreMaintenanceBannerPosition() {
  if (maintenanceBannerPositionRestored) return;
  const banner = document.getElementById("maintenanceBanner");
  if (!banner || getComputedStyle(banner).display === "none") return;
  maintenanceBannerPositionRestored = true;
  let position = null;
  try {
    const saved = JSON.parse(localStorage.getItem(MAINTENANCE_BANNER_POSITION_KEY) || "null");
    if (saved && Number.isFinite(Number(saved.left)) && Number.isFinite(Number(saved.top))) {
      position = {left: Number(saved.left), top: Number(saved.top)};
    }
  } catch (error) { /* Ignore an unavailable or invalid saved position. */ }
  requestAnimationFrame(function () {
    const rect = banner.getBoundingClientRect();
    const target = position || {left: rect.left, top: rect.top};
    setMaintenanceBannerPosition(target.left, target.top, false);
  });
}

function initMaintenanceBannerDrag() {
  const banner = document.getElementById("maintenanceBanner");
  const handle = banner && banner.querySelector("h2");
  if (!banner || !handle) return;
  let drag = null;
  function moveDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    setMaintenanceBannerPosition(
      drag.startLeft + event.clientX - drag.startX,
      drag.startTop + event.clientY - drag.startY,
      false
    );
  }
  function finishDrag(event) {
    if (!drag || (event && Number.isFinite(event.pointerId) && event.pointerId !== drag.pointerId)) return;
    drag = null;
    banner.classList.remove("is-dragging");
    const rect = banner.getBoundingClientRect();
    setMaintenanceBannerPosition(rect.left, rect.top, true);
  }
  handle.addEventListener("pointerdown", function (event) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    let rect = banner.getBoundingClientRect();
    setMaintenanceBannerPosition(rect.left, rect.top, false);
    rect = banner.getBoundingClientRect();
    banner.style.left = rect.left + "px";
    banner.style.top = rect.top + "px";
    banner.style.right = "auto";
    banner.style.bottom = "auto";
    drag = {pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, startLeft: rect.left, startTop: rect.top};
    banner.classList.add("is-dragging");
    try { handle.setPointerCapture(event.pointerId); } catch (error) { /* Pointer capture is not available in every host. */ }
    event.preventDefault();
  });
  window.addEventListener("pointermove", moveDrag, true);
  window.addEventListener("pointerup", finishDrag, true);
  window.addEventListener("pointercancel", finishDrag, true);
  window.addEventListener("blur", function () { finishDrag(null); });
  window.addEventListener("resize", function () {
    if (drag || !banner.style.left || getComputedStyle(banner).display === "none") return;
    const rect = banner.getBoundingClientRect();
    setMaintenanceBannerPosition(rect.left, rect.top, true);
  });
  if (window.ResizeObserver) {
    new ResizeObserver(function () {
      if (drag || !banner.style.left || getComputedStyle(banner).display === "none") return;
      const rect = banner.getBoundingClientRect();
      setMaintenanceBannerPosition(rect.left, rect.top, false);
    }).observe(banner);
  }
}

initMaintenanceBannerDrag();
document.addEventListener("keydown", function (event) {
  const target = event.target;
  if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing ||
      (target && (target.isContentEditable || target.closest("input, textarea, select, [contenteditable='true']")))) {
    maintenancePreviewKeys = "";
    statusFailurePreviewKeys = "";
    clearTestPreviewKeys = "";
    return;
  }
  if (event.key.length !== 1 || event.repeat) return;
  const key = event.key.toLowerCase();
  maintenancePreviewKeys = (maintenancePreviewKeys + key).slice(-MAINTENANCE_PREVIEW_CODE.length);
  statusFailurePreviewKeys = (statusFailurePreviewKeys + key).slice(-STATUS_FAILURE_PREVIEW_CODE.length);
  clearTestPreviewKeys = (clearTestPreviewKeys + key).slice(-CLEAR_TEST_PREVIEWS_CODE.length);
  if (maintenancePreviewKeys === MAINTENANCE_PREVIEW_CODE) {
    maintenancePreviewKeys = "";
    maintenancePreview = true;
    const banner = document.getElementById("maintenanceBanner");
    document.getElementById("maintenanceBannerText").textContent = "测试提醒：拖动标题栏，检查位置和窗口边缘。";
    banner.style.display = "";
    restoreMaintenanceBannerPosition();
  }
  if (statusFailurePreviewKeys === STATUS_FAILURE_PREVIEW_CODE) {
    statusFailurePreviewKeys = "";
    statusFailurePreview = !statusFailurePreview;
    setStatusCardAmbient(document.getElementById("badge"), statusCardLastHealthy, statusCardLastFailed);
  }
  if (clearTestPreviewKeys === CLEAR_TEST_PREVIEWS_CODE) clearTestPreviewStates();
});
document.getElementById("btnMaintenanceDismiss").addEventListener("click", function () {
  if (maintenancePreview) {
    maintenancePreview = false;
    document.getElementById("maintenanceBanner").style.display = "none";
    refreshMaintenance();
    return;
  }
  maintenanceDismissedUntil = Date.now() + MAINTENANCE_DISMISS_MS;
  document.getElementById("maintenanceBanner").style.display = "none";
});
document.getElementById("backupRestoreFile").addEventListener("change", function () {
  document.getElementById("backupSelectedFile").textContent = this.files[0] ? "已选择：" + this.files[0].name : "尚未选择 ZIP 文件";
});
async function refreshMaintenance() {
  try {
    const st = await jget("/api/maintenance/status");
    if (!st.ok) return;
    maintenanceEnabled = st.enabled;
    const alerts = Array.isArray(st.alerts) ? st.alerts : [];
    const last = st.last_backup_ts ? new Date(st.last_backup_ts * 1000).toLocaleString() : "尚未成功备份";
    document.getElementById("maintenanceStatus").textContent = alerts.length ? "有 " + alerts.length + " 项维护提醒，请查看" : (st.enabled ? "自动备份已开启 · 最近备份 " + last : "自动备份已关闭");
    const toggle = document.getElementById("btnAutoBackupToggle");
    toggle.querySelector(".pill-switch-label").textContent = st.enabled ? "已开启" : "已关闭";
    toggle.dataset.on = st.enabled ? "1" : "0";
    toggle.setAttribute("aria-pressed", String(st.enabled));
    toggle.setAttribute("aria-checked", String(st.enabled));
    const detail = document.getElementById("maintenanceDetail");
    detail.textContent = alerts.length ? alerts.map(function (a) { return a.message; }).join("\n") : "维护检查正常。最近备份：" + last;
    detail.classList.toggle("has-alert", alerts.length > 0);
    const banner = document.getElementById("maintenanceBanner");
    if (!alerts.length) maintenanceDismissedUntil = 0;
    if (!maintenancePreview) {
      banner.style.display = alerts.length && Date.now() >= maintenanceDismissedUntil ? "" : "none";
      document.getElementById("maintenanceBannerText").textContent = alerts.length ? alerts[0].message : "";
    }
    restoreMaintenanceBannerPosition();
  } catch (error) { /* Retry on the next refresh. */ }
}
async function refreshAutomaticBackups() {
  const select = document.getElementById("autoBackupSelect");
  try {
    const st = await jget("/api/maintenance/backups");
    select.innerHTML = "";
    const entries = st.backups || [];
    if (!entries.length) select.add(new Option("暂无自动备份", ""));
    entries.forEach(function (entry) {
      const option = new Option(new Date(entry.timestamp * 1000).toLocaleString() + (entry.valid ? " · 已校验" : " · 无效"), entry.name);
      option.disabled = !entry.valid;
      select.add(option);
    });
    const valid = entries.find(function (entry) { return entry.valid; });
    if (valid) select.value = valid.name;
  } catch (error) {
    select.innerHTML = "";
    select.add(new Option("无法读取备份，请稍后重试", ""));
  }
}
async function runMaintenanceAction(button) {
  button.disabled = true;
  try {
    let result;
    if (button.id === "btnAutoBackupToggle") {
      result = await jpost("/api/maintenance/settings", {enabled: !maintenanceEnabled});
    } else if (button.id === "btnAutoBackupNow") {
      result = await jpost("/api/maintenance/backup");
    } else {
      const name = document.getElementById("autoBackupSelect").value;
      if (!name) throw new Error("请选择有效的自动备份");
      if (!confirm("恢复会将配置和运行状态退回所选时间，可能造成重复投递或漏发。程序会先校验备份、停止机器人，并保存恢复前副本。恢复成功后会恢复原来的运行状态。确定继续吗？")) return;
      result = await jpost("/api/maintenance/restore", {name: name, confirmed: true});
    }
    if (!result.ok) throw new Error(result.msg || "维护操作失败");
    if (button.id === "btnAutoBackupRestore") {
      await refreshSettings();
      showAlert("恢复备份", result.msg || "恢复完成");
    }
    await refreshMaintenance();
    await refreshAutomaticBackups();
  } catch (error) {
    showAlert("维护与备份", error.message || "操作失败，原有备份已保留");
  } finally { button.disabled = false; }
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
  if (!r.ok) {
    let data = {};
    try { data = await r.json(); } catch (e) {}
    throw new Error(data.msg || "导出失败");
  }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = "wrelay-backup.zip"; a.click();
  setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}
async function restoreBackup() {
  const file = document.getElementById("backupRestoreFile").files[0];
  if (!file) { showAlert("恢复配置", "请先选择 ZIP 备份文件"); return; }
  const runtime = document.getElementById("backupIncludeRuntime").checked;
  if (runtime && !confirm("请先停止机器人，再恢复游标或重试队列；运行中的重试队列会覆盖恢复文件。恢复运行状态仍可能造成重复投递、漏发或改变待补发状态。确定继续吗？")) return;
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
var logAutoScroll = true;
function renderLogAutoScrollToggle() {
  const button = document.getElementById("btnLogAutoScroll");
  if (!button) return;
  button.dataset.on = logAutoScroll ? "1" : "0";
  button.setAttribute("aria-pressed", logAutoScroll ? "true" : "false");
  button.setAttribute("aria-checked", logAutoScroll ? "true" : "false");
  // 只改标签文字，保留胶囊轨道（轨道由 ui-components 的装饰逻辑维持）。
  const label = button.querySelector(".pill-switch-label");
  if (label) label.textContent = "自动滚动：" + (logAutoScroll ? "开" : "关");
  else button.textContent = "自动滚动：" + (logAutoScroll ? "开" : "关");
  button.title = logAutoScroll ? "仅在日志底部时跟随最新内容" : "已暂停自动滚动";
}
renderLogAutoScrollToggle();
document.getElementById("log").addEventListener("scroll", function () {
  const el = document.getElementById("log");
  logFollow = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
});

function setStatusCardAmbient(badge, healthy, failed) {
  const card = badge && badge.closest(".card");
  statusCardLastHealthy = !!healthy;
  statusCardLastFailed = !!failed;
  if (card) {
    const showFailure = statusCardLastFailed || statusFailurePreview;
    card.classList.toggle("status-failure-active", showFailure);
    card.classList.toggle("status-ambient-active", statusCardLastHealthy && !showFailure);
  }
}

function renderStatus(st) {
  const b = document.getElementById("badge");
  const h = st.health;
  qqAggregateStatusKnown = !!(h && typeof h.qq === "boolean");
  qqAggregateConnected = qqAggregateStatusKnown && h.qq;
  qqBotStatusKnown = !!(h && Array.isArray(h.qq_bots));
  connectedQQBotIds = qqBotStatusKnown ? h.qq_bots.map(String) : [];
  renderQQBotWorkspace();
  const selectedBot = currentQQBotId();
  const selectedBotConnected = selectedBot
    ? (qqBotStatusKnown ? connectedQQBotIds.includes(selectedBot) : !!(h && h.qq))
    : !!(h && h.qq);
  let statusDetail = "已停止";
  if (st.running) {
    document.getElementById("btnStart").disabled = true;
    document.getElementById("btnStop").disabled = false;
    document.getElementById("btnRestart").disabled = false;
    if (activePage === "x" || activePage === "qq") {
      // X → QQ / QQ → QQ 页面只关心 QQ 连接状态，不显示无关的 Discord 状态。
      if (h && selectedBotConnected) {
        statusDetail = "运行中";
        b.className = "badge on";
      } else if (h) {
        statusDetail = selectedBot ? "运行中（当前 QQ Bot 未连接）" : "运行中（QQ 未连接）";
        b.className = "badge warn";
      } else {
        statusDetail = "运行中（检测中…）";
        b.className = "badge warn";
      }
    } else if (h && h.discord && selectedBotConnected) {
      statusDetail = "运行中";
      b.className = "badge on";
    } else if (h) {
      statusDetail = h.discord
        ? (selectedBot ? "运行中（当前 QQ Bot 未连接）" : "运行中（QQ 未连接）")
        : selectedBotConnected ? "运行中（Discord 未连接）" : "运行中（未连接）";
      b.className = "badge warn";
    } else {
      statusDetail = "运行中（检测中…）";
      b.className = "badge warn";
    }
  } else {
    b.className = "badge neutral";
    document.getElementById("btnStart").disabled = false;
    document.getElementById("btnStop").disabled = true;
    document.getElementById("btnRestart").disabled = true;
  }
  b.textContent = st.running ? "运行中" : "已停止";
  b.title = statusDetail;
  const pid = document.getElementById("pidinfo");
  pid.textContent = st.pid ? String(st.pid) : "—";
  pid.title = st.pid ? "PID: " + st.pid : "机器人进程尚未启动";
  // The compact RSS/QQ pages separate process state from the Bot selector's
  // connection state. Keep all backend-derived decisions and controls unchanged.
  if (document.body.classList.contains("desktop-host") && ["discord", "x", "bilibili", "youtube", "qq"].includes(activePage)) {
    b.className = st.running ? "badge on" : "badge neutral";
  }
  const needsDiscord = activePage === "discord" || activePage === "overview";
  const statusCardHealthy = st.running && selectedBotConnected && (!needsDiscord || !!(h && h.discord));
  const qqConnectionPending = !!(h && h.qq_connecting);
  const discordConnectionPending = !!(h && h.discord_connecting);
  const statusCardFailed = st.running && !!h && (
    (!selectedBotConnected && !qqConnectionPending)
    || (needsDiscord && !h.discord && !discordConnectionPending)
  );
  setStatusCardAmbient(b, statusCardHealthy, statusCardFailed);
  const stats = st.stats || {};
  const retry = (st.health && st.health.retry) || {};
  const statValues = {
    statsToday: Number(stats.today_forwarded) || 0,
    statsTotal: Number(stats.total_forwarded) || 0,
    statsFailed: Number(stats.total_failed) || 0,
  };
  Object.keys(statValues).forEach(function (id) {
    const value = document.getElementById(id);
    if (!value) return;
    value.textContent = statValues[id].toLocaleString("zh-CN");
    value.title = statValues[id].toLocaleString("zh-CN") + " 条";
  });
  const failedStat = document.getElementById("statsFailed");
  if (failedStat && Number(retry.pending_entries) > 0) {
    failedStat.title += " · 当前账号待重试 " + Number(retry.pending_entries) + " 条";
  }
  const lvlEl = document.getElementById("logLevel");
  if (lvlEl && st.level) lvlEl.value = st.level;
  const hint = document.getElementById("managedHint");
  hint.textContent = st.running && !st.managed
    ? "机器人由其他方式启动，面板无法完全控制；请先手动停止后再接管。"
    : "";
  const el = document.getElementById("log");
  const stick = logAutoScroll && logFollow;
  el.textContent = st.log || "（暂无日志）";
  if (stick) el.scrollTop = el.scrollHeight;
  renderTranslationRuntimeStatus(st);
  renderAutostart(document.getElementById("btnSettingsAutostart"), st.autostart);
  const overviewDiscord = document.getElementById("overviewDiscord");
  if (!st.running) {
    setOverviewRouteStatus(overviewDiscord, "机器人未运行", "", "neutral");
  } else if (!h) {
    setOverviewRouteStatus(overviewDiscord, "连接状态待恢复", "", "warn");
  } else if (h.discord && selectedBotConnected) {
    setOverviewRouteStatus(overviewDiscord, "已连接", "Discord → 当前 QQ Bot", "on");
  } else if (h.discord) {
    setOverviewRouteStatus(overviewDiscord, "未连接", "当前 QQ Bot", "warn");
  } else if (selectedBotConnected) {
    setOverviewRouteStatus(overviewDiscord, "未连接", "Discord", "warn");
  } else {
    setOverviewRouteStatus(overviewDiscord, "未连接", "Discord · 当前 QQ Bot", "warn");
  }
  const overviewQQ = document.getElementById("overviewQQ");
  if (!st.running) {
    setOverviewRouteStatus(overviewQQ, "机器人未运行", "", "neutral");
  } else if (!h) {
    setOverviewRouteStatus(overviewQQ, "连接状态待恢复", "", "warn");
  } else if (selectedBotConnected) {
    setOverviewRouteStatus(overviewQQ, "已连接", "授权手动转发", "on");
  } else {
    setOverviewRouteStatus(overviewQQ, "未连接", selectedBot ? "当前 QQ Bot" : "QQ Bot", "warn");
  }
  renderPageCredentialShortcut();
  renderChainStatusShortcut();
}

function setOverviewRouteStatus(target, label, detail, state) {
  if (!target) return;
  target.replaceChildren();
  const status = document.createElement("span");
  status.className = "overview-state";
  status.dataset.state = state || "";
  status.textContent = label || "";
  target.appendChild(status);
  if (detail) {
    const meta = document.createElement("span");
    meta.className = "overview-route-meta";
    meta.textContent = " · " + detail;
    target.appendChild(meta);
  }
  target.dataset.state = state || "";
  target.title = detail ? (String(label || "") + " · " + detail) : String(label || "");
}

let latestTranslationRuntime = {};

function renderTranslationRuntimeStatus(st) {
  const health = (st && st.health) || {};
  const runtime = currentQQBotId() ? ((health.translation_by_bot || {})[currentQQBotId()] || {}) : (health.translation || {});
  const nextRuntime = runtime && typeof runtime === "object" ? Object.assign({}, runtime) : {};
  const previousKey = [latestTranslationRuntime.provider, latestTranslationRuntime.profile, latestTranslationRuntime.model, latestTranslationRuntime.at].join("|");
  const nextKey = [nextRuntime.provider, nextRuntime.profile, nextRuntime.model, nextRuntime.at].join("|");
  latestTranslationRuntime = nextRuntime;
  if (previousKey !== nextKey) renderAiModelPriorityList();
}

// Settings is the sole startup preference entry; keep the existing save endpoint.
function renderAutostart(btn, on) {
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  btn.innerHTML = '<span class="settings-switch-track" aria-hidden="true"></span>';
  btn.dataset.tooltip = "开机自启：" + (on ? "已开启" : "已关闭");
  btn.removeAttribute("title");
  btn.setAttribute("aria-label", btn.dataset.tooltip);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.disabled = false;
}

function renderFailureNotificationToggle(on) {
  const btn = document.getElementById("btnFailureNotificationToggle");
  if (btn) {
    btn.dataset.on = on ? "1" : "0";
    btn.innerHTML = '<span class="settings-switch-track" aria-hidden="true"></span>';
    btn.setAttribute("role", "switch");
    btn.setAttribute("aria-checked", String(!!on));
    btn.setAttribute("aria-label", "失败通知：" + (on ? "已开启" : "已关闭"));
  }
  renderFailureNotificationShortcut();
}

function renderFailureNotificationShortcut() {
  const btn = document.getElementById("failureNotificationShortcut");
  if (!btn) return;
  const visible = activePage === "qq";
  btn.hidden = !visible;
  if (!visible) return;
  const on = !!(settings && settings.failure_notification && settings.failure_notification.enabled);
  btn.dataset.on = on ? "1" : "0";
  btn.dataset.state = on ? "on" : "neutral";
  btn.innerHTML = SVG_FAILURE_NOTIFICATION;
  btn.title = "失败通知：" + (on ? "已开启" : "已关闭") + " · 投递失败、RSS 连续失败或队列异常时提醒授权 QQ 用户 · 点击切换";
  btn.setAttribute("role", "switch");
  btn.setAttribute("aria-checked", String(on));
  btn.setAttribute("aria-label", "接收失败通知：" + (on ? "已开启" : "已关闭"));
}

function renderStatusSheenToggle(on) {
  const btn = document.getElementById("btnStatusSheenToggle");
  if (!btn) return;
  const row = btn.closest(".settings-row");
  if (row) row.classList.toggle("status-sheen-row-active", !!on);
  btn.dataset.on = on ? "1" : "0";
  btn.setAttribute("aria-checked", String(!!on));
  btn.setAttribute("aria-label", "界面动画：" + (on ? "已开启" : "已关闭"));
}

// 定时消息的目标模式：指定群 / QQ→QQ 转发组 / 全部已启用群。
const SCHEDULED_TARGET_MODES = ["groups", "forward_group", "all"];
const SCHEDULED_TARGET_LABELS = { groups: "指定群", forward_group: "转发组", all: "全部群" };

let scheduledMessageDraft = null;
let scheduledMessageEditorTarget = null;
let scheduledMessageSavePending = false;
let scheduledMessageId = 0;
let scheduledMessageGroupsDraft = [];
let activeScheduledMessageGroupId = null;
let scheduledMessageTargetPickerOpen = false;
let scheduledMessageGroupRenameActive = false;

function activeScheduledMessageGroup() {
  return scheduledMessageGroupsDraft.find(function (group) {
    return String(group.id) === String(activeScheduledMessageGroupId);
  }) || scheduledMessageGroupsDraft[0] || null;
}

function resolveScheduledMessageTarget(target) {
  if (!target || target.epoch !== workspaceEpoch) return null;
  const groups = scheduledMessageGroupsDraft.filter(group => String(group.id) === target.groupId);
  if (groups.length !== 1) return null;
  const group = groups[0];
  if (target.messageId === null) return {group, index: -1};
  const messages = group.messages || [];
  const matches = message => String(message.id) === target.messageId;
  const index = messages.findIndex(matches);
  return index >= 0 && index === messages.findLastIndex(matches) ? {group, index} : null;
}

function scheduledAttachmentBytesExcept(target) {
  return scheduledMessageGroupsDraft.reduce(function (total, group) {
    return (group.messages || []).reduce(function (sum, message, index) {
      return group === target.group && index === target.index ? sum : sum + scheduledMessageAttachmentBytes(message);
    }, total);
  }, 0);
}

function scheduledMessageGroupTargetSelection(group, qqGroups) {
  if (!group) return {ids: [], dynamicLabel: ""};
  if (group.target_mode === "all") {
    return {
      ids: qqGroups.map(function (item) { return String(item.openid); }),
      dynamicLabel: "全部已启用群（动态）",
    };
  }
  if (group.target_mode === "forward_group") {
    const wanted = new Set((group.forward_groups || []).map(String));
    const ids = [];
    (settings.qq_forwarding_groups || []).filter(function (forwardGroup) {
      return wanted.has(String(forwardGroup.id)) && forwardGroup.enabled !== false;
    }).forEach(function (forwardGroup) {
      ids.push.apply(ids, forwardGroup.groups || []);
    });
    return {ids: Array.from(new Set(ids.map(String))), dynamicLabel: "跟随 QQ 转发组（动态）"};
  }
  return {ids: (group.target_groups || []).map(String), dynamicLabel: ""};
}

function scheduledTargetEnabled(group, openid) {
  return !((group && group.disabled_target_groups) || []).map(String).includes(String(openid));
}

function setScheduledTargetEnabled(group, openid, enabled) {
  if (!group) return;
  const id = String(openid);
  const disabled = new Set((group.disabled_target_groups || []).map(String));
  if (enabled) disabled.delete(id); else disabled.add(id);
  group.disabled_target_groups = Array.from(disabled);
}

function legacyScheduledDisabledTargets(group) {
  if (!settings || !group) return [];
  let ids = (group.target_groups || []).map(String);
  if (group.target_mode === "all") {
    ids = (settings.qq_group_openids || []).map(function (item) { return String(item.openid); });
  } else if (group.target_mode === "forward_group") {
    const wanted = new Set((group.forward_groups || []).map(String));
    ids = (settings.qq_forwarding_groups || []).filter(function (item) { return wanted.has(String(item.id)); })
      .reduce(function (result, item) { return result.concat((item.groups || []).map(String)); }, []);
  }
  const disabled = new Set((settings.qq_group_openids || []).filter(function (item) { return item.enabled !== true; }).map(function (item) { return String(item.openid); }));
  return Array.from(new Set(ids.filter(function (id) { return disabled.has(id); })));
}

function normalizeScheduledMessageGroups(config) {
  const value = config || {};
  if (Array.isArray(value.message_groups) && value.message_groups.length) {
    return value.message_groups.map(function (raw, index) {
      const group = Object.assign({id: "scheduled-group-" + (index + 1), name: "消息组" + (index + 1), enabled: true, target_groups: [], messages: []}, raw || {});
      group.id = String(group.id || "scheduled-group-" + (index + 1));
      group.name = String(group.name || ("消息组" + (index + 1)));
      group.target_mode = SCHEDULED_TARGET_MODES.includes(group.target_mode) ? group.target_mode : "groups";
      group.target_groups = Array.isArray(group.target_groups) ? group.target_groups.map(String).filter(Boolean) : [];
      group.forward_groups = Array.isArray(group.forward_groups) ? group.forward_groups.map(String).filter(Boolean) : [];
      group.disabled_target_groups = Array.isArray(group.disabled_target_groups) ? group.disabled_target_groups.map(String).filter(Boolean) : legacyScheduledDisabledTargets(group);
      group.messages = normalizeScheduledMessages({messages: Array.isArray(group.messages) ? group.messages : []}).map(function (message) {
        delete message.target_mode; delete message.target_groups; delete message.forward_groups;
        return message;
      });
      return group;
    });
  }
  const legacyMessages = normalizeScheduledMessages(value);
  const buckets = new Map();
  legacyMessages.forEach(function (message) {
    // 保留旧消息引用 QQ→QQ 转发组或“全部群”的动态路由语义。
    const mode = message.target_mode || "groups";
    const key = JSON.stringify([mode, message.target_groups || [], message.forward_groups || []]);
    if (!buckets.has(key)) buckets.set(key, {id: "legacy-group-" + (buckets.size + 1), name: "消息组" + (buckets.size + 1), enabled: true, target_mode: mode, target_groups: (message.target_groups || []).slice(), forward_groups: (message.forward_groups || []).slice(), messages: []});
    const clean = Object.assign({}, message);
    delete clean.target_mode; delete clean.target_groups; delete clean.forward_groups;
    buckets.get(key).messages.push(clean);
  });
  return Array.from(buckets.values()).map(function (group) {
    group.disabled_target_groups = legacyScheduledDisabledTargets(group);
    return group;
  });
}

function pillSwitchMarkup(label, on) {
  // 胶囊开关统一外观：文字在左、轨道在右，与「启用补发」一致（✓/× 图标不再出现在按钮里）。
  return '<span class="pill-switch-label">' + label + '</span><span class="settings-switch-track" aria-hidden="true"></span>';
}

function renderScheduledMessageWorkspace() {
  const group = activeScheduledMessageGroup();
  const tabs = document.getElementById("scheduledMessageGroupTabs");
  const list = document.getElementById("scheduledGroupMessageList");
  const targets = document.getElementById("scheduledGroupTargetList");
  if (!tabs || !list || !targets) return;
  list.dataset.groupId = group ? String(group.id) : "";
  if (!scheduledMessageGroupRenameActive) {
    const groupTabs = scheduledMessageGroupsDraft.map(function (item) {
      const active = group && String(item.id) === String(group.id);
      const enabled = item.enabled !== false;
      return '<button type="button" class="fgtab' + (active ? ' active' : '') + (enabled ? '' : ' paused') + '" data-scheduled-group="' + escapeHtml(item.id) + '" title="' + escapeHtml(item.name + (enabled ? '' : '（已暂停）')) + '"><span class="fgtab-name">' + escapeHtml(item.name) + '</span>' + (enabled ? '' : '<span class="fgtab-state">暂停</span>') + '</button>';
    }).join("");
    const addGroupTab = '<button type="button" class="fgtab-add" id="btnAddScheduledGroupTab" title="添加消息组" aria-label="添加消息组">' + mcIcon("add", 16) + '</button>';
    const actionRail = group
      ? '<div class="fgtab-actions">' +
        '<button type="button" class="fg-toggle rowicon' + (group.enabled === false ? ' paused' : '') + '" data-scheduled-group-toggle title="' + (group.enabled === false ? '恢复该消息组' : '暂停该消息组') + '" aria-label="' + (group.enabled === false ? '恢复该消息组' : '暂停该消息组') + '">' + (group.enabled === false ? SVG_PLAY : SVG_PAUSE) + '</button>' +
        '<button type="button" class="fgctrl rowicon" data-scheduled-group-rename="' + escapeHtml(group.id) + '" title="重命名消息组" aria-label="重命名消息组">' + SVG_PEN + '</button>' +
        '<button type="button" class="fgctrl rowicon fg-delete scheduled-message-group-delete" data-delete-scheduled-group="' + escapeHtml(group.id) + '" title="删除消息组" aria-label="删除消息组">' + SVG_TRASH + '</button>' +
        '</div>' : '';
    tabs.innerHTML = '<div class="fgtab-list">' + groupTabs + addGroupTab + '</div>' + actionRail;
  }
  const messageCount = scheduledMessageGroupsDraft.reduce(function (sum, item) { return sum + (item.messages || []).length; }, 0);
  const summary = document.getElementById("scheduledMessageSummary");
  if (summary) summary.textContent = "共 " + scheduledMessageGroupsDraft.length + " 个消息组，" + messageCount + " 条消息";
  const allEnabled = document.getElementById("scheduledMessageAllEnabled");
  const currentMessages = group ? (group.messages || []) : [];
  if (allEnabled) {
    const count = currentMessages.filter(function (message) { return message.enabled !== false; }).length;
    allEnabled.disabled = !currentMessages.length;
    allEnabled.checked = !!currentMessages.length && count === currentMessages.length;
    allEnabled.indeterminate = count > 0 && count < currentMessages.length;
  }
  if (!group) {
    list.innerHTML = '<tr class="fgempty-row"><td colspan="5" class="fgempty-msg">暂无消息组，点击上方“＋”创建。</td></tr>';
    targets.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">选择一个消息组管理接收群。</td></tr>';
    return;
  }
  const messages = group.messages || [];
  list.innerHTML = messages.length ? messages.map(function (message, index) {
    const schedule = message.schedule_type === "once"
      ? formatScheduledDateTime(message.once_at) + (message.execution_status === "sent" ? " · 已执行" : message.execution_status === "missed" ? " · 已错过" : " · 待发送")
      : message.schedule_type === "calendar"
      ? ({weekly:"每周", monthly:"每月", yearly:"每年"}[message.calendar_type] || "日历计划") + " " + (message.send_time || "09:00")
      : "每隔 " + ([message.interval_days ? message.interval_days + "日" : "", message.interval_hours ? message.interval_hours + "时" : "", message.interval_minutes ? message.interval_minutes + "分" : ""].filter(Boolean).join(" ") || "1分");
    const attachment = scheduledMessageAttachment(message);
    const summaryText = (message.content || (attachment ? "（纯" + ({image:"图片", video:"视频", audio:"音频"}[attachment.type] || "附件") + "消息）" : "（空消息）")).replace(/\s+/g, " ").trim();
    const attachmentSummary = attachment ? ({image:"附图", video:"附视频", audio:"附音频"}[attachment.type] || "附件") : "";
    return '<tr data-id="' + escapeHtml(String(message.id)) + '"><td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td><td style="text-align:center"><input type="checkbox" class="scheduled-message-enabled" aria-label="启用消息 ' + (index + 1) + '"' + (message.enabled !== false ? ' checked' : '') + '></td><td><div class="scheduled-group-message-copy"><strong data-copy-value-override="' + escapeHtml(message.content || summaryText) + '">' + escapeHtml(summaryText) + '</strong><small>' + attachmentSummary + '</small></div></td><td class="scheduled-message-schedule">' + escapeHtml(schedule) + '</td><td><div class="table-actions"><button type="button" class="sec rowicon scheduled-group-message-edit" title="编辑消息" aria-label="编辑消息">' + SVG_PEN + '</button><button type="button" class="danger rowicon scheduled-group-message-remove" title="删除消息" aria-label="删除消息">' + SVG_TRASH + '</button></div></td></tr>';
  }).join("") : '<tr class="fgempty-row"><td colspan="5" class="fgempty-msg">这个消息组还没有定时消息。</td></tr>';

  const allQQGroups = settings && Array.isArray(settings.qq_group_openids) ? settings.qq_group_openids : [];
  const qqGroups = allQQGroups.filter(groupInActiveQQBotWorkspace);
  const targetSelection = scheduledMessageGroupTargetSelection(group, qqGroups);
  const selected = targetSelection.ids;
  const dynamicLabel = targetSelection.dynamicLabel;
  const byId = new Map(qqGroups.map(function (item) { return [String(item.openid), item]; }));
  const names = selected.map(function (id) {
    const item = byId.get(String(id));
    const name = item ? (item.name || item.remark || id) : id;
    const targetIndex = item ? allQQGroups.indexOf(item) : -1;
    const removeAction = group.target_mode === "groups"
      ? '<button type="button" class="danger rowicon scheduled-group-target-remove" data-target-openid="' + escapeHtml(String(id)) + '" title="移出当前消息组" aria-label="移出当前消息组">' + SVG_TRASH + '</button>' : '';
    const dragHandle = group.target_mode === "groups" ? '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' : '<td></td>';
    const editAction = item ? '<button type="button" class="sec rowicon" data-kind="groupEdit" data-id="' + escapeHtml(String(id)) + '" title="编辑群资料" aria-label="编辑群资料">' + SVG_PEN + '</button>' : '';
    const enabled = item && scheduledTargetEnabled(group, id);
    const enableCheckbox = '<input type="checkbox" data-kind="scheduledTargetGroup" data-openid="' + escapeHtml(String(id)) + '" aria-label="启用群 ' + escapeHtml(name) + '"' + (targetIndex < 0 ? ' disabled' : '') + (enabled ? ' checked' : '') + '>';
    return '<tr data-id="' + escapeHtml(String(id)) + '">' + dragHandle + '<td style="text-align:center">' + enableCheckbox + '</td><td>' + escapeHtml(name) + '</td><td>' + escapeHtml(item && item.qq_id || '—') + '</td><td>' + renderIdCell(String(id)) + '</td><td><div class="table-actions">' + editAction + removeAction + '</div></td></tr>';
  }).join("");
  targets.innerHTML = (dynamicLabel ? '<tr class="scheduled-group-target-mode"><td colspan="6">' + escapeHtml(dynamicLabel) + '</td></tr>' : '') + (names || '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">尚未选择 QQ 群。</td></tr>');
  syncHeaderAll("scheduledGroupTargetsAllEnabled", selected.map(function (id) { return {enabled: scheduledTargetEnabled(group, id)}; }), "enabled");
}

async function saveScheduledMessageGroups() {
  const selectedGroupId = activeScheduledMessageGroupId;
  const epoch = workspaceEpoch;
  const current = settings.scheduled_message || {};
  const groups = scheduledMessageGroupsDraft.map(function (group) {
    return Object.assign({}, group, {messages: (group.messages || []).map(function (message) {
      return Object.assign({}, message, {
        image_name: typeof message.image_data === "string" && message.image_data && typeof message.image_name === "string" ? message.image_name : "",
      });
    })});
  });
  settings.scheduled_message = Object.assign({}, current, {
    content: "",
    messages: [],
    message_groups: groups,
  });
  delete settings.scheduled_message.enabled;
  const saved = await saveAndReload();
  if (saved && epoch === workspaceEpoch) {
    scheduledMessageGroupsDraft = normalizeScheduledMessageGroups(settings.scheduled_message);
    activeScheduledMessageGroupId = scheduledMessageGroupsDraft.some(function (group) { return String(group.id) === String(selectedGroupId); })
      ? selectedGroupId : (scheduledMessageGroupsDraft[0] ? scheduledMessageGroupsDraft[0].id : null);
    renderScheduledMessageWorkspace();
  }
  return saved;
}

function scheduledLocalDateTimeValue() {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60000).toISOString().slice(0, 16);
}

function scheduledPickerPad(value) { return String(value).padStart(2, "0"); }

function scheduledPickerDateKey(date) {
  return date.getFullYear() + "-" + scheduledPickerPad(date.getMonth() + 1) + "-" + scheduledPickerPad(date.getDate());
}

function scheduledPickerParts(kind, value) {
  const now = new Date();
  const parts = {year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate(), hour: 9, minute: 0};
  const match = kind === "time"
    ? /^(\d{2}):(\d{2})$/.exec(value || "")
    : /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
  if (!match) return parts;
  if (kind === "time") {
    parts.hour = Number(match[1]);
    parts.minute = Number(match[2]);
  } else {
    parts.year = Number(match[1]);
    parts.month = Number(match[2]);
    parts.day = Number(match[3]);
    parts.hour = Number(match[4]);
    parts.minute = Number(match[5]);
  }
  return parts;
}

function formatScheduledDateTime(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
  if (match) return match[1] + "/" + match[2] + "/" + match[3] + " " + match[4] + ":" + match[5];
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  return dateOnly ? dateOnly[1] + "/" + dateOnly[2] + "/" + dateOnly[3] : "未设置时间";
}

function scheduledDatePickerMarkup(fieldName, kind, accessibleName) {
  const icon = kind === "time" ? SVG_SCHEDULE_CLOCK : SVG_SCHEDULE_CALENDAR;
  return '<div class="scheduled-date-picker" data-picker-kind="' + kind + '">' +
    '<input type="hidden" data-field="' + fieldName + '">' +
    '<button type="button" class="sec scheduled-date-picker-trigger" aria-label="' + accessibleName + '" aria-haspopup="dialog" aria-expanded="false"><span class="scheduled-date-picker-value"></span>' + icon + '</button>' +
    '<div class="scheduled-date-picker-popover" data-picker-kind="' + kind + '" role="dialog" aria-label="' + accessibleName + '" hidden></div>' +
    '</div>';
}

function updateScheduledDatePickerDisplay(picker) {
  const field = picker.querySelector('input[data-field]');
  const value = picker.querySelector(".scheduled-date-picker-value");
  if (!field || !value) return;
  value.textContent = picker.dataset.pickerKind === "time"
    ? (field.value || "选择时间")
    : formatScheduledDateTime(field.value).replace("未设置时间", "选择日期和时间");
}

function renderScheduledDatePicker(picker, focusSelector) {
  const popover = picker._scheduledDatePickerPopover || picker.querySelector(".scheduled-date-picker-popover");
  const field = picker.querySelector('input[data-field]');
  if (!popover || !field) return;
  const kind = picker.dataset.pickerKind;
  const value = field.value || "";
  const parts = scheduledPickerParts(kind, value);
  const year = picker.dataset.viewYear !== undefined ? Number(picker.dataset.viewYear) : parts.year;
  const month = picker.dataset.viewMonth !== undefined ? Number(picker.dataset.viewMonth) : parts.month - 1;
  picker.dataset.viewYear = String(year);
  picker.dataset.viewMonth = String(month);
  const selectedDateKey = kind === "datetime" && value ? value.slice(0, 10) : "";
  const todayKey = scheduledPickerDateKey(new Date());
  const monthStart = new Date(year, month, 1);
  const firstCell = new Date(year, month, 1 - ((monthStart.getDay() + 6) % 7));
  const weekdays = ["一", "二", "三", "四", "五", "六", "日"];
  const calendar = kind === "datetime"
    ? '<div class="scheduled-date-picker-calendar">' +
      '<div class="scheduled-date-picker-month"><button type="button" class="sec" data-picker-action="previous-month" aria-label="上个月">' + mcIcon("right", 14) + '</button>' +
      '<strong>' + year + "年" + (month + 1) + '月</strong>' +
      '<button type="button" class="sec" data-picker-action="next-month" aria-label="下个月">' + mcIcon("right", 14) + '</button></div>' +
      '<div class="scheduled-date-picker-weekdays">' + weekdays.map(function (day) { return "<span>" + day + "</span>"; }).join("") + '</div>' +
      '<div class="scheduled-date-picker-days">' + Array.from({length: 42}, function (_, index) {
        const date = new Date(firstCell.getFullYear(), firstCell.getMonth(), firstCell.getDate() + index);
        const dateKey = scheduledPickerDateKey(date);
        const classes = ["scheduled-date-picker-day"];
        if (date.getMonth() !== month) classes.push("outside-month");
        if (dateKey === todayKey) classes.push("is-today");
        if (dateKey === selectedDateKey) classes.push("is-selected");
        return '<button type="button" class="' + classes.join(" ") + '" data-picker-date="' + dateKey + '" aria-label="' + date.getFullYear() + "年" + (date.getMonth() + 1) + "月" + date.getDate() + '日" aria-pressed="' + String(dateKey === selectedDateKey) + '">' + date.getDate() + '</button>';
      }).join("") + '</div><div class="scheduled-date-picker-calendar-actions"><button type="button" class="sec" data-picker-action="clear-date">清除</button><button type="button" class="sec" data-picker-action="today">今天</button></div></div>'
    : '';
  const renderTimeColumn = function (part, count, selected) {
    return '<div class="scheduled-date-picker-time-column"><span>' + (part === "hour" ? "时" : "分") + '</span><div class="scheduled-date-picker-time-list" data-time-list="' + part + '">' +
      Array.from({length: count}, function (_, index) {
        const valueText = scheduledPickerPad(index);
        const active = index === selected;
        return '<button type="button" data-picker-part="' + part + '" data-picker-value="' + valueText + '" aria-pressed="' + String(active) + '"' + (active ? ' class="is-selected"' : '') + '>' + valueText + '</button>';
      }).join("") + '</div></div>';
  };
  popover.innerHTML = (kind === "time" ? '<div class="scheduled-date-picker-time-title">选择发送时间</div>' : '') +
    '<div class="scheduled-date-picker-popover-content">' + calendar +
    '<div class="scheduled-date-picker-time">' + renderTimeColumn("hour", 24, parts.hour) + renderTimeColumn("minute", 60, parts.minute) + '</div></div>';
  popover.querySelectorAll(".scheduled-date-picker-time-list").forEach(function (list) {
    const selected = list.querySelector(".is-selected");
    if (selected) list.scrollTop = Math.max(0, selected.offsetTop - (list.clientHeight - selected.offsetHeight) / 2);
  });
  if (focusSelector) {
    const target = popover.querySelector(focusSelector);
    if (target) target.focus({preventScroll: true});
  }
}

function placeScheduledDatePicker(picker) {
  const popover = picker && picker._scheduledDatePickerPopover;
  const trigger = picker && picker.querySelector(".scheduled-date-picker-trigger");
  if (!popover || !trigger) return;
  const rect = trigger.getBoundingClientRect();
  const gap = 6;
  const edge = 12;
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  const left = Math.max(edge, Math.min(rect.left, window.innerWidth - width - edge));
  let top = rect.bottom + gap;
  if (top + height > window.innerHeight - edge) top = Math.max(edge, rect.top - height - gap);
  popover.style.left = left + "px";
  popover.style.top = top + "px";
}

function closeScheduledDatePicker(picker) {
  if (!picker) return;
  const popover = picker._scheduledDatePickerPopover;
  const marker = picker._scheduledDatePickerMarker;
  picker.classList.remove("is-open");
  const trigger = picker.querySelector(".scheduled-date-picker-trigger");
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  if (popover) {
    popover.hidden = true;
    popover.style.left = "";
    popover.style.top = "";
    if (marker && marker.parentNode) {
      marker.parentNode.insertBefore(popover, marker);
      marker.remove();
    } else {
      popover.remove();
    }
  }
  picker._scheduledDatePickerPopover = null;
  picker._scheduledDatePickerMarker = null;
}

function closeAllScheduledDatePickers() {
  document.querySelectorAll(".scheduled-date-picker.is-open").forEach(closeScheduledDatePicker);
  closeAllScheduledWeekdayPickers();
}

function openScheduledDatePicker(picker) {
  if (picker.classList.contains("is-open")) {
    closeScheduledDatePicker(picker);
    return;
  }
  closeAllScheduledDatePickers();
  const popover = picker.querySelector(".scheduled-date-picker-popover");
  const trigger = picker.querySelector(".scheduled-date-picker-trigger");
  const modal = picker.closest("#scheduledMessageContentModal");
  if (!popover || !trigger || !modal) return;
  const marker = document.createComment("scheduled-date-picker");
  popover.parentNode.insertBefore(marker, popover);
  picker._scheduledDatePickerPopover = popover;
  picker._scheduledDatePickerMarker = marker;
  modal.appendChild(popover);
  picker.classList.add("is-open");
  trigger.setAttribute("aria-expanded", "true");
  const field = picker.querySelector('input[data-field]');
  const parts = scheduledPickerParts(picker.dataset.pickerKind, field && field.value);
  picker.dataset.viewYear = String(parts.year);
  picker.dataset.viewMonth = String(parts.month - 1);
  popover.hidden = false;
  if (!popover.dataset.bound) {
    popover.dataset.bound = "1";
    popover.addEventListener("click", function (event) {
      const button = event.target.closest("button[data-picker-action], button[data-picker-date], button[data-picker-part]");
      if (!button || !picker.classList.contains("is-open")) return;
      event.preventDefault();
      event.stopPropagation();
      const currentField = picker.querySelector('input[data-field]');
      const currentKind = picker.dataset.pickerKind;
      const currentValue = currentField.value || "";
      const current = scheduledPickerParts(currentKind, currentValue);
      const action = button.dataset.pickerAction;
      let focusSelector = "";
      if (action === "previous-month" || action === "next-month") {
        const shift = action === "previous-month" ? -1 : 1;
        const next = new Date(Number(picker.dataset.viewYear), Number(picker.dataset.viewMonth) + shift, 1);
        picker.dataset.viewYear = String(next.getFullYear());
        picker.dataset.viewMonth = String(next.getMonth());
        focusSelector = '[data-picker-action="' + action + '"]';
      } else if (action === "clear-date") {
        currentField.value = "";
        focusSelector = '[data-picker-action="clear-date"]';
      } else if (action === "today") {
        const today = new Date();
        const dateKey = scheduledPickerDateKey(today);
        currentField.value = dateKey + "T" + scheduledPickerPad(current.hour) + ":" + scheduledPickerPad(current.minute);
        picker.dataset.viewYear = String(today.getFullYear());
        picker.dataset.viewMonth = String(today.getMonth());
        focusSelector = '[data-picker-action="today"]';
      } else if (button.dataset.pickerDate) {
        const dateKey = button.dataset.pickerDate;
        currentField.value = dateKey + "T" + scheduledPickerPad(current.hour) + ":" + scheduledPickerPad(current.minute);
        picker.dataset.viewYear = dateKey.slice(0, 4);
        picker.dataset.viewMonth = String(Number(dateKey.slice(5, 7)) - 1);
        focusSelector = '[data-picker-date="' + dateKey + '"]';
      } else if (button.dataset.pickerPart) {
        const part = button.dataset.pickerPart;
        const chosen = Number(button.dataset.pickerValue);
        if (part === "hour") current.hour = chosen;
        if (part === "minute") current.minute = chosen;
        if (currentKind === "time") {
          currentField.value = scheduledPickerPad(current.hour) + ":" + scheduledPickerPad(current.minute);
        } else if (currentValue) {
          currentField.value = currentValue.slice(0, 10) + "T" + scheduledPickerPad(current.hour) + ":" + scheduledPickerPad(current.minute);
        } else {
          currentField.value = scheduledPickerDateKey(new Date()) + "T" + scheduledPickerPad(current.hour) + ":" + scheduledPickerPad(current.minute);
        }
        focusSelector = '[data-picker-part="' + part + '"][data-picker-value="' + scheduledPickerPad(chosen) + '"]';
      }
      updateScheduledDatePickerDisplay(picker);
      currentField.dispatchEvent(new Event("change", {bubbles: true}));
      renderScheduledDatePicker(picker, focusSelector);
      placeScheduledDatePicker(picker);
    });
  }
  renderScheduledDatePicker(picker);
  placeScheduledDatePicker(picker);
}

function refreshScheduledDatePickerDisplays(root) {
  (root || document).querySelectorAll(".scheduled-date-picker").forEach(updateScheduledDatePickerDisplay);
}

function scheduledWeekdayPickerMarkup() {
  const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  return '<div class="scheduled-message-weekday-picker">' +
    '<button type="button" class="scheduled-message-entry-select scheduled-message-weekday-trigger" aria-haspopup="dialog" aria-expanded="false"><span class="scheduled-message-weekday-value">选择星期</span>' + mcIcon("right", 14) + '</button>' +
    '<div class="scheduled-message-weekday-popover" role="dialog" aria-label="选择发送星期" hidden>' +
    '<strong>选择星期</strong><div class="scheduled-message-weekday-options">' + weekdays.map(function (label, day) {
      return '<label><input type="checkbox" data-field="weekday" value="' + day + '"><span>' + label + '</span></label>';
    }).join("") + '</div></div></div>';
}

function updateScheduledWeekdayPickerDisplay(picker) {
  const value = picker && picker.querySelector(".scheduled-message-weekday-value");
  const popover = picker && (picker._scheduledWeekdayPopover || picker.querySelector(".scheduled-message-weekday-popover"));
  if (!value || !popover) return;
  const names = Array.from(popover.querySelectorAll('[data-field="weekday"]:checked')).map(function (field) {
    return field.nextElementSibling ? field.nextElementSibling.textContent : "";
  }).filter(Boolean);
  value.textContent = names.length === 7 ? "每天" : names.length > 2 ? names.length + " 天已选" : names.join("、") || "选择星期";
  const trigger = picker.querySelector(".scheduled-message-weekday-trigger");
  if (trigger) trigger.setAttribute("aria-label", names.length ? "发送星期：" + names.join("、") : "选择发送星期");
}

function placeScheduledWeekdayPicker(picker) {
  const popover = picker && picker._scheduledWeekdayPopover;
  const trigger = picker && picker.querySelector(".scheduled-message-weekday-trigger");
  if (!popover || !trigger) return;
  const rect = trigger.getBoundingClientRect();
  const edge = 12;
  const gap = 6;
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  const left = Math.max(edge, Math.min(rect.left, window.innerWidth - width - edge));
  let top = rect.bottom + gap;
  if (top + height > window.innerHeight - edge) top = Math.max(edge, rect.top - height - gap);
  popover.style.left = left + "px";
  popover.style.top = top + "px";
}

function closeScheduledWeekdayPicker(picker) {
  if (!picker) return;
  const popover = picker._scheduledWeekdayPopover;
  const marker = picker._scheduledWeekdayMarker;
  picker.classList.remove("is-open");
  picker.querySelector(".scheduled-message-weekday-trigger")?.setAttribute("aria-expanded", "false");
  if (popover) {
    popover.hidden = true;
    popover.style.left = "";
    popover.style.top = "";
    if (marker && marker.parentNode) {
      marker.parentNode.insertBefore(popover, marker);
      marker.remove();
    } else {
      popover.remove();
    }
  }
  picker._scheduledWeekdayPopover = null;
  picker._scheduledWeekdayMarker = null;
}

function closeAllScheduledWeekdayPickers() {
  document.querySelectorAll(".scheduled-message-weekday-picker.is-open").forEach(closeScheduledWeekdayPicker);
}

function openScheduledWeekdayPicker(picker) {
  if (!picker) return;
  if (picker.classList.contains("is-open")) {
    closeScheduledWeekdayPicker(picker);
    return;
  }
  closeAllScheduledDatePickers();
  const popover = picker.querySelector(".scheduled-message-weekday-popover");
  const trigger = picker.querySelector(".scheduled-message-weekday-trigger");
  const modal = picker.closest("#scheduledMessageContentModal");
  if (!popover || !trigger || !modal) return;
  const marker = document.createComment("scheduled-weekday-picker");
  popover.parentNode.insertBefore(marker, popover);
  picker._scheduledWeekdayPopover = popover;
  picker._scheduledWeekdayMarker = marker;
  modal.appendChild(popover);
  picker.classList.add("is-open");
  trigger.setAttribute("aria-expanded", "true");
  popover.hidden = false;
  updateScheduledWeekdayPickerDisplay(picker);
  if (!popover.dataset.bound) {
    popover.dataset.bound = "1";
    popover.addEventListener("change", function (event) {
      if (event.target.dataset.field !== "weekday") return;
      updateScheduledWeekdayPickerDisplay(picker);
      scheduledMessageDraft = readScheduledMessageEditor();
    });
  }
  placeScheduledWeekdayPicker(picker);
}

function ensureScheduledImagePreview() {
  const modal = document.getElementById("scheduledMessageContentModal");
  if (!modal) return null;
  let preview = modal.querySelector(".scheduled-message-image-preview");
  if (!preview) {
    preview = document.createElement("div");
    preview.className = "scheduled-message-image-preview";
    preview.setAttribute("role", "tooltip");
    preview.hidden = true;
    preview.innerHTML = '<img alt="" hidden><video muted playsinline preload="auto" hidden></video>';
    preview.addEventListener("pointerout", function (event) {
      if (preview.contains(event.relatedTarget) || (preview._anchor && preview._anchor.contains(event.relatedTarget))) return;
      closeScheduledImagePreview();
    });
    modal.appendChild(preview);
  }
  return preview;
}

function placeScheduledImagePreview(anchor, preview) {
  if (!anchor || !preview) return;
  const rect = anchor.getBoundingClientRect();
  const edge = 12;
  const gap = 8;
  const width = preview.offsetWidth;
  const height = preview.offsetHeight;
  const left = Math.max(edge, Math.min(rect.left, window.innerWidth - width - edge));
  let top = rect.bottom + gap;
  if (top + height > window.innerHeight - edge) top = Math.max(edge, rect.top - height - gap);
  preview.style.left = left + "px";
  preview.style.top = top + "px";
}

function scheduledMessageAttachment(message) {
  if (!message) return null;
  if (message.attachment_id) {
    return {
      id: message.attachment_id,
      type: message.attachment_type || "image",
      name: message.attachment_name || "已附加文件",
      size: Number(message.attachment_size) || 0,
      mime: message.attachment_mime || "",
    };
  }
  if (message.image_data) {
    return {id: "", type: "image", name: message.image_name || "已附加图片", size: scheduledImageDataSize(message.image_data), data: message.image_data};
  }
  return null;
}

function scheduledMessageAttachmentBytes(message) {
  const attachment = scheduledMessageAttachment(message);
  return attachment ? (attachment.size || 0) : 0;
}

function openScheduledImagePreview(anchor) {
  const index = Number(anchor && anchor.dataset.messageIndex);
  const message = scheduledMessageDraft && scheduledMessageDraft[index];
  const attachment = scheduledMessageAttachment(message);
  if (!attachment || !["image", "video"].includes(attachment.type)) return;
  const preview = ensureScheduledImagePreview();
  const image = preview && preview.querySelector("img");
  const video = preview && preview.querySelector("video");
  if (!preview || !image || !video) return;
  closeScheduledImagePreview();
  const requestId = (preview._requestId || 0) + 1;
  preview._requestId = requestId;
  preview._anchor = anchor;
  const display = function (src) {
    if (preview._requestId !== requestId || preview._anchor !== anchor) {
      if (src.startsWith("blob:")) URL.revokeObjectURL(src);
      return;
    }
    preview._objectUrl = src.startsWith("blob:") ? src : "";
    const media = attachment.type === "video" ? video : image;
    media.hidden = false;
    const ready = function () {
      if (preview._requestId !== requestId || preview._anchor !== anchor) return;
      preview.hidden = false;
      placeScheduledImagePreview(anchor, preview);
    };
    media.onerror = function () {
      if (preview._requestId === requestId) closeScheduledImagePreview();
    };
    if (attachment.type === "video") {
      video.onloadeddata = ready;
      video.muted = true;
    } else {
      image.onload = ready;
      image.alt = attachment.name || "定时消息图片预览";
    }
    media.src = src;
  };
  if (attachment.data) {
    display(attachment.data);
    return;
  }
  (async function () {
    try {
      await ensurePanelToken();
      const response = await fetch(withActiveQQBot("/api/scheduled-message-media/" + encodeURIComponent(attachment.id)), {
        headers: {"X-WRelay-Token": panelToken},
      });
      if (!response.ok) throw new Error("媒体预览读取失败");
      display(URL.createObjectURL(await response.blob()));
    } catch (_error) {
      if (preview._requestId === requestId) closeScheduledImagePreview();
    }
  })();
}

function closeScheduledImagePreview() {
  const preview = document.querySelector(".scheduled-message-image-preview");
  if (!preview) return;
  const image = preview.querySelector("img");
  const video = preview.querySelector("video");
  if (image) {
    image.onload = null;
    image.onerror = null;
    image.removeAttribute("src");
    image.alt = "";
    image.hidden = true;
  }
  if (video) {
    video.onloadeddata = null;
    video.onerror = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.hidden = true;
  }
  preview._requestId = (preview._requestId || 0) + 1;
  if (preview._objectUrl) URL.revokeObjectURL(preview._objectUrl);
  preview._objectUrl = "";
  preview.hidden = true;
  preview.style.left = "";
  preview.style.top = "";
  preview._anchor = null;
}

function newScheduledMessage() {
  scheduledMessageId += 1;
  return {
    id: "message-" + Date.now() + "-" + scheduledMessageId,
    enabled: true,
    content: "",
    image_data: "",
    image_name: "",
    // 目标：未显式设置时沿用顶部「默认目标」（target_groups 为空 + groups 模式）。
    target_mode: "groups",
    target_groups: [],
    forward_groups: [],
    schedule_type: "interval",
    once_at: scheduledLocalDateTimeValue(),
    completed_at: "",
    execution_status: "",
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
      message.id = String(raw?.id || "message-" + (index + 1));
      message.content = String(message.content || "");
      message.image_name = typeof message.image_data === "string" && message.image_data && typeof message.image_name === "string" ? message.image_name : "";
      message.target_mode = SCHEDULED_TARGET_MODES.includes(message.target_mode)
        ? message.target_mode : "groups";
      message.target_groups = Array.isArray(message.target_groups)
        ? message.target_groups.map(String).filter(Boolean) : [];
      message.forward_groups = Array.isArray(message.forward_groups)
        ? message.forward_groups.map(String).filter(Boolean) : [];
      message.schedule_type = ["interval", "calendar", "once"].includes(message.schedule_type) ? message.schedule_type : "interval";
      message.interval_days = Math.max(0, Math.min(3650, Number(message.interval_days) || 0));
      message.interval_hours = Math.max(0, Math.min(23, Number(message.interval_hours) || 0));
      message.interval_minutes = Math.max(0, Math.min(59, Number(message.interval_minutes) || 0));
      message.anchor_at = typeof message.anchor_at === "string" ? message.anchor_at : scheduledLocalDateTimeValue();
      message.once_at = typeof message.once_at === "string" ? message.once_at : scheduledLocalDateTimeValue();
      message.completed_at = typeof message.completed_at === "string" ? message.completed_at : "";
      message.execution_status = ["sent", "missed"].includes(message.execution_status) ? message.execution_status : "";
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
    legacy.id = "message-1";
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
  const dayOptions = Array.from({length: 31}, function (_, index) { return index + 1; });
  return '<div class="scheduled-message-entry" data-index="' + index + '">' +
    '<div class="scheduled-message-entry-body">' +
    '<label class="scheduled-message-entry-label">消息正文' +
    '<textarea class="scheduled-message-entry-content" data-field="content" maxlength="20000" placeholder="输入消息正文"></textarea></label>' +
    '<div class="scheduled-message-image-row">' +
    '<input type="file" data-field="media_file_input" accept=".png,.jpg,.jpeg,.gif,.webp,.mp4,.webm,.mp3,.wav,.ogg,.m4a,image/png,image/jpeg,image/gif,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,audio/ogg,audio/mp4" hidden>' +
    '<button type="button" class="sec rowicon scheduled-message-image-pick" title="上传附件" aria-label="上传附件">' + mcIcon("upload", 16) + '</button>' +
    '<button type="button" class="sec rowicon scheduled-message-image-remove" title="移除附件" aria-label="移除附件" hidden>' + SVG_TRASH + '</button>' +
    '<span class="scheduled-message-image-name" data-message-index="' + index + '" tabindex="0"></span>' +
    '</div>' +
    '<div class="scheduled-message-timing-row">' +
    '<div class="scheduled-message-timing-mode">' +
    '<label>发送方式<select class="scheduled-message-entry-select" data-field="schedule_type">' +
    '<option value="interval">按间隔发送</option><option value="calendar">按日历发送</option><option value="once">按时点发送</option></select></label>' +
    '</div>' +
    '<div class="scheduled-message-interval-fields">' +
    '<label>基准日期时间' + scheduledDatePickerMarkup("anchor_at", "datetime", "选择基准日期和时间") + '</label>' +
    '<div class="scheduled-message-interval-row"><span>每隔</span>' +
    '<label><input type="number" data-field="interval_days" min="0" max="3650" step="1"> 日</label>' +
    '<label><input type="number" data-field="interval_hours" min="0" max="23" step="1"> 时</label>' +
    '<label><input type="number" data-field="interval_minutes" min="0" max="59" step="1"> 分</label></div>' +
    '</div>' +
    '<div class="scheduled-message-calendar-fields">' +
    '<label>日历周期<select class="scheduled-message-entry-select" data-field="calendar_type">' +
    '<option value="weekly">每周</option><option value="monthly">每月</option><option value="yearly">每年</option></select></label>' +
    '<div class="scheduled-message-calendar-panel scheduled-message-weekday-panel" data-calendar-type="weekly"><label>星期</label>' + scheduledWeekdayPickerMarkup() + '</div>' +
    '<div class="scheduled-message-calendar-panel" data-calendar-type="monthly">' +
    '<label>每月日期<select data-field="day_of_month">' + dayOptions.map(function (day) { return '<option value="' + day + '">' + day + ' 号</option>'; }).join("") + '</select></label></div>' +
    '<div class="scheduled-message-calendar-panel" data-calendar-type="yearly">' +
    '<label>月份<select data-field="month">' + Array.from({length: 12}, function (_, month) {
      return '<option value="' + (month + 1) + '">' + (month + 1) + ' 月</option>';
    }).join("") + '</select></label>' +
    '<label>日期<select data-field="day">' + dayOptions.map(function (day) { return '<option value="' + day + '">' + day + ' 日</option>'; }).join("") + '</select></label></div>' +
    '<label>发送时间' + scheduledDatePickerMarkup("send_time", "time", "选择发送时间") + '</label>' +
    '</div>' +
    '<div class="scheduled-message-once-fields"><label>发送日期时间' + scheduledDatePickerMarkup("once_at", "datetime", "选择发送日期和时间") + '</label></div>' +
    '</div>' +
    '</div></div>';
}

function updateScheduledMessageEntryVisibility(entry) {
  const type = entry.querySelector('[data-field="schedule_type"]');
  const interval = entry.querySelector(".scheduled-message-interval-fields");
  const calendar = entry.querySelector(".scheduled-message-calendar-fields");
  const isCalendar = type && type.value === "calendar";
  const isOnce = type && type.value === "once";
  if (interval) interval.style.display = !isCalendar && !isOnce ? "grid" : "none";
  if (calendar) calendar.style.display = isCalendar ? "grid" : "none";
  const once = entry.querySelector(".scheduled-message-once-fields");
  if (once) once.style.display = isOnce ? "flex" : "none";
  const calendarType = entry.querySelector('[data-field="calendar_type"]');
  entry.querySelectorAll(".scheduled-message-calendar-panel").forEach(function (panel) {
    panel.style.display = isCalendar && calendarType && panel.dataset.calendarType === calendarType.value ? "flex" : "none";
  });
  const weekdayPicker = entry.querySelector(".scheduled-message-weekday-picker");
  if (weekdayPicker && (!isCalendar || !calendarType || calendarType.value !== "weekly")) closeScheduledWeekdayPicker(weekdayPicker);
}

function scheduledTargetSummary(message) {
  const mode = message.target_mode || "groups";
  const qqGroups = (settings && Array.isArray(settings.qq_group_openids)) ? settings.qq_group_openids.filter(groupInActiveQQBotWorkspace) : [];
  const qqByOpenid = {};
  qqGroups.forEach(function (group) { qqByOpenid[String(group.openid || "")] = group; });
  const groupName = function (id) {
    const group = qqByOpenid[String(id)];
    if (!group) return String(id);
    return group.name || group.remark || identityName("groups", String(id)) || String(id);
  };
  if (mode === "all") {
    return "全部已启用群（" + qqGroups.filter(function (group) { return group.enabled === true; }).length + " 个）";
  }
  if (mode === "forward_group") {
    const forwardGroups = (settings && Array.isArray(settings.qq_forwarding_groups)) ? settings.qq_forwarding_groups : [];
    const byId = {};
    forwardGroups.forEach(function (group) { byId[String(group.id || "")] = group; });
    const picked = (message.forward_groups || []).map(function (id) {
      const group = byId[String(id)];
      if (!group) return String(id);
      const name = String(group.name || group.id || "");
      return name + (group.enabled === false ? "（已暂停）" : "");
    });
    return picked.length ? "转发组：" + picked.join("、") : "未选择转发组（点击管理选择）";
  }
  if (!(message.target_groups || []).length) {
    const defaults = ((settings && settings.scheduled_message && settings.scheduled_message.target_groups) || []).length;
    return defaults ? "默认目标 " + defaults + " 个群" : "未选择群（点击管理选择）";
  }
  return (message.target_groups || []).map(groupName).join("、");
}

function updateScheduledMessageTargetVisibility(entry, message) {
  const modeField = entry.querySelector('[data-field="target_mode"]');
  const mode = modeField ? modeField.value : (message && message.target_mode) || "groups";
  const manage = entry.querySelector(".scheduled-message-target-manage");
  const summary = entry.querySelector(".scheduled-message-target-summary");
  if (manage) manage.style.display = mode === "all" ? "none" : "";
  if (summary) {
    summary.textContent = scheduledTargetSummary(Object.assign({}, message || {}, { target_mode: mode }));
  }
}

function renderScheduledMessageEditor() {
  const container = document.getElementById("scheduledMessageEntries");
  if (!container) return;
  closeAllScheduledDatePickers();
  closeScheduledImagePreview();
  const messages = scheduledMessageDraft || [];
  container.innerHTML = messages.length ? messages.map(scheduledOptionMarkup).join("")
    : '<p class="compact-empty-state">暂无定时消息；请返回主面板点「＋ 新增」添加。</p>';
  container.querySelectorAll(".scheduled-message-entry").forEach(function (entry, index) {
    const message = messages[index];
    entry.querySelector('[data-field="content"]').value = message.content || "";
    const attachment = scheduledMessageAttachment(message);
    const imageName = entry.querySelector(".scheduled-message-image-name");
    if (imageName) {
      const kindLabel = attachment ? ({image:"图片", video:"视频", audio:"音频"}[attachment.type] || "附件") : "附件";
      imageName.textContent = attachment ? (attachment.name || "已附加" + kindLabel) : "未附加" + kindLabel;
      const canPreview = !!attachment && (attachment.type === "image" || attachment.type === "video");
      imageName.classList.toggle("has-image", canPreview);
      imageName.removeAttribute("title");
      imageName.tabIndex = canPreview ? 0 : -1;
      imageName.setAttribute("aria-label", canPreview ? "悬停或聚焦预览" + kindLabel + "：" + attachment.name : (attachment ? "已附加" + kindLabel + "：" + attachment.name : "未附加附件"));
    }
    const imageRemove = entry.querySelector(".scheduled-message-image-remove");
    if (imageRemove) imageRemove.hidden = !attachment;
    const imagePick = entry.querySelector(".scheduled-message-image-pick");
    if (imagePick) {
      const label = attachment ? "更换附件" : "上传附件";
      imagePick.title = label;
      imagePick.setAttribute("aria-label", label);
    }
    entry.querySelector('[data-field="schedule_type"]').value = message.schedule_type;
    ["anchor_at", "once_at", "interval_days", "interval_hours", "interval_minutes", "day_of_month", "month", "day", "send_time", "calendar_type"].forEach(function (key) {
      const field = entry.querySelector('[data-field="' + key + '"]');
      if (field && message[key] !== undefined && message[key] !== null) field.value = message[key];
    });
    refreshScheduledDatePickerDisplays(entry);
    entry.querySelectorAll('[data-field="weekday"]').forEach(function (field) {
      field.checked = message.weekdays.includes(Number(field.value));
    });
    updateScheduledWeekdayPickerDisplay(entry.querySelector(".scheduled-message-weekday-picker"));
    updateScheduledMessageEntryVisibility(entry);
  });
}

function readScheduledMessageEditor() {
  const container = document.getElementById("scheduledMessageEntries");
  if (!container) return [];
  return Array.from(container.querySelectorAll(".scheduled-message-entry")).map(function (entry, index) {
    const get = function (key) { return entry.querySelector('[data-field="' + key + '"]'); };
    const weekdayPicker = entry.querySelector(".scheduled-message-weekday-picker");
    const weekdayPopover = weekdayPicker && (weekdayPicker._scheduledWeekdayPopover || weekdayPicker.querySelector(".scheduled-message-weekday-popover"));
    const type = get("schedule_type").value;
    const message = {
      id: (scheduledMessageDraft[index] && scheduledMessageDraft[index].id) || ("message-" + (index + 1)),
      enabled: !(scheduledMessageDraft[index] && scheduledMessageDraft[index].enabled === false),
      content: get("content").value,
      image_data: (scheduledMessageDraft[index] && scheduledMessageDraft[index].image_data) || "",
      image_name: (scheduledMessageDraft[index] && scheduledMessageDraft[index].image_name) || "",
      attachment_id: (scheduledMessageDraft[index] && scheduledMessageDraft[index].attachment_id) || "",
      attachment_type: (scheduledMessageDraft[index] && scheduledMessageDraft[index].attachment_type) || "",
      attachment_mime: (scheduledMessageDraft[index] && scheduledMessageDraft[index].attachment_mime) || "",
      attachment_name: (scheduledMessageDraft[index] && scheduledMessageDraft[index].attachment_name) || "",
      attachment_size: Number(scheduledMessageDraft[index] && scheduledMessageDraft[index].attachment_size) || 0,
      schedule_type: type,
      once_at: get("once_at").value,
      completed_at: "",
      execution_status: "",
      interval_days: Number(get("interval_days").value) || 0,
      interval_hours: Number(get("interval_hours").value) || 0,
      interval_minutes: Number(get("interval_minutes").value) || 0,
      anchor_at: get("anchor_at").value,
      calendar_type: get("calendar_type").value,
      weekdays: Array.from(weekdayPopover ? weekdayPopover.querySelectorAll('[data-field="weekday"]:checked') : []).map(function (field) { return Number(field.value); }),
      day_of_month: Number(get("day_of_month").value) || 1,
      month: Number(get("month").value) || 1,
      day: Number(get("day").value) || 1,
      send_time: get("send_time").value || "09:00",
    };
    const previous = scheduledMessageDraft[index] || {};
    if (!message.image_data) message.image_name = "";
    if (type === "once" && previous.schedule_type === "once" && message.once_at === previous.once_at) {
      message.completed_at = previous.completed_at || "";
      message.execution_status = previous.execution_status || "";
    }
    return message;
  });
}

// 单条定时消息的目标选择：指定群（多个 QQ 群）或转发组（QQ→QQ 转发组）。
function openScheduledTargetPicker(index) {
  const message = scheduledMessageDraft && scheduledMessageDraft[index];
  if (!message) return;
  const mode = message.target_mode || "groups";
  if (mode === "all") {
    showAlert("发送目标", "「全部群」会发送到当前账号已启用的 QQ 群，无需选择。");
    return;
  }
  if (mode === "forward_group") {
    const forwardGroups = (settings && Array.isArray(settings.qq_forwarding_groups))
      ? settings.qq_forwarding_groups : [];
    if (!forwardGroups.length) {
      showAlert("发送目标", "当前没有 QQ→QQ 转发组，请先在「QQ → QQ」页创建转发组");
      return;
    }
    const selected = new Set((message.forward_groups || []).map(String));
    showConfirmSelect(
      "消息 " + (index + 1) + "：选择转发组",
      forwardGroups,
      function (group) {
        const id = String(group.id || "");
        const owned = new Set(((settings && settings.qq_group_openids) || []).filter(groupInActiveQQBotWorkspace).map(function (item) { return String(item.openid); }));
        const members = Array.isArray(group.groups) ? group.groups.filter(function (id) { return owned.has(String(id)); }).length : 0;
        const state = group.enabled === false ? "（已暂停）" : "";
        return (group.name || id) + state + " · " + members + " 个群";
      },
      function (picked) {
        message.forward_groups = picked.map(function (group) { return String(group.id); });
        renderScheduledMessageEditor();
      },
      {
        defaultChecked: function (group) { return selected.has(String(group.id || "")); },
        okText: "保存选择",
      }
    );
    return;
  }
  const groups = (settings && Array.isArray(settings.qq_group_openids))
    ? settings.qq_group_openids.filter(groupInActiveQQBotWorkspace)
    : [];
  if (!groups.length) {
    showAlert("发送目标", "当前没有已审核的 QQ 群");
    return;
  }
  const selected = new Set((message.target_groups || []).map(String));
  showConfirmSelect(
    "消息 " + (index + 1) + "：选择目标群",
    groups,
    function (group) {
      const id = String(group.openid);
      const name = group.name || group.remark || identityName("groups", id) || "";
      const state = selected.has(id) ? "（已选）" : "（未选）";
      const enabled = group.enabled === true ? "" : "（当前未启用）";
      return (name + " " + id).trim() + state + enabled;
    },
    function (picked) {
      message.target_groups = picked.map(function (group) { return String(group.openid); });
      renderScheduledMessageEditor();
    },
    {
      defaultChecked: function (group) { return selected.has(String(group.openid)); },
      okText: "保存选择",
    }
  );
}

function openScheduledMessageEditor(target) {
  const modal = document.getElementById("scheduledMessageContentModal");
  const resolved = resolveScheduledMessageTarget(target);
  if (!modal || !resolved || scheduledMessageSavePending) return;
  scheduledMessageEditorTarget = target;
  scheduledMessageDraft = [resolved.index < 0 ? newScheduledMessage() : {...resolved.group.messages[resolved.index]}];
  renderScheduledMessageEditor();
  modal.style.display = "flex";
  const first = document.querySelector(".scheduled-message-entry-content");
  if (first) first.focus();
}

function closeScheduledMessageEditor() {
  scheduledMessageEditorTarget = null;
  scheduledMessageDraft = null;
  closeScheduledImagePreview();
  document.getElementById("scheduledMessageContentModal").style.display = "none";
}

async function saveScheduledMessageRows(group, updatedMessages) {
  const originalMessages = group.messages;
  const originalSettings = settings;
  const originalConfig = settings.scheduled_message;
  group.messages = updatedMessages;
  scheduledMessageSavePending = true;
  let saved = false;
  try {
    saved = await saveScheduledMessageGroups();
    return saved;
  } finally {
    if (!saved) {
      group.messages = originalMessages;
      if (settings === originalSettings) settings.scheduled_message = originalConfig;
    }
    scheduledMessageSavePending = false;
  }
}

async function confirmScheduledMessageEditor() {
  if (scheduledMessageSavePending) return;
  if (scheduledAttachmentUploadPending) {
    showAlert("定时消息", "请等待附件上传完成后再保存");
    return;
  }
  const target = scheduledMessageEditorTarget;
  const resolved = resolveScheduledMessageTarget(target);
  if (!resolved) {
    showAlert("保存失败", "这条消息或消息组已变化，请重新打开编辑窗口。");
    return;
  }
  const [message] = readScheduledMessageEditor();
  if (!message) return;
  const {group, index: targetIndex} = resolved;
  const previous = group.messages?.[targetIndex];
  if (previous) {
    message.enabled = previous.enabled !== false;
    if (previous.schedule_type === "once" && message.schedule_type === "once" && previous.once_at === message.once_at) {
      message.completed_at = previous.completed_at || "";
      message.execution_status = previous.execution_status || "";
    }
  }
  if (!message.content.trim() && !scheduledMessageAttachment(message)) {
    showAlert("定时消息", "请填写正文或添加附件");
    return;
  }
  if (scheduledAttachmentBytesExcept(resolved) + scheduledMessageAttachmentBytes(message) > 30 * 1024 * 1024) {
    showAlert("定时消息", "所有定时消息附件合计不能超过 30 MiB");
    return;
  }
  if (message.schedule_type === "interval" && !message.interval_days && !message.interval_hours && !message.interval_minutes) {
    showAlert("定时消息", "发送间隔至少需要 1 分钟");
    return;
  }
  if (message.schedule_type === "once") {
    const selectedAt = new Date(message.once_at);
    if (!message.once_at || Number.isNaN(selectedAt.getTime())) {
      showAlert("定时消息", "请选择有效的发送时间");
      return;
    }
    if (!message.completed_at && selectedAt <= new Date()) {
      showAlert("定时消息", "发送时间必须晚于当前时间");
      return;
    }
  }
  if (message.schedule_type === "calendar" && message.calendar_type === "weekly" && !message.weekdays.length) {
    showAlert("定时消息", "至少选择一个星期");
    return;
  }
  if (message.schedule_type === "calendar" && message.calendar_type === "yearly" &&
      message.day > new Date(2000, message.month, 0).getDate()) {
    showAlert("定时消息", "年度日期不存在，请选择该月的有效日期");
    return;
  }
  const updatedMessages = (group.messages || []).slice();
  if (targetIndex < 0) updatedMessages.push(message);
  else updatedMessages[targetIndex] = message;
  const saved = await saveScheduledMessageRows(group, updatedMessages);
  if (saved && scheduledMessageEditorTarget === target) closeScheduledMessageEditor();
}

let scheduledAttachmentUploadPending = false;

function scheduledImageDataSize(dataUrl) {
  if (typeof dataUrl !== "string" || !dataUrl) return 0;
  const comma = dataUrl.indexOf(",");
  if (comma < 0) return 0;
  const encoded = dataUrl.slice(comma + 1);
  const padding = encoded.endsWith("==") ? 2 : (encoded.endsWith("=") ? 1 : 0);
  return Math.max(0, Math.floor(encoded.length * 3 / 4) - padding);
}

async function uploadScheduledMessageAttachment(file) {
  const url = withActiveQQBot("/api/scheduled-message-media");
  await ensurePanelToken();
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-File-Name": encodeURIComponent(file.name || "attachment"),
      "X-WRelay-Token": panelToken,
    },
    body: file,
  });
  let result = {};
  try { result = await response.json(); } catch (_error) {}
  if (!response.ok || !result.ok || !result.attachment) {
    throw new Error(result.msg || "附件上传失败");
  }
  return result;
}

function startScheduledMessageGroupRename(groupId) {
  const target = {groupId, messageId: null, epoch: workspaceEpoch};
  const group = resolveScheduledMessageTarget(target)?.group;
  const tabs = document.getElementById("scheduledMessageGroupTabs");
  if (!group || !tabs || scheduledMessageGroupRenameActive) return;
  const tab = Array.prototype.find.call(tabs.querySelectorAll("[data-scheduled-group]"), function (item) {
    return String(item.dataset.scheduledGroup) === String(group.id);
  });
  if (!tab) return;
  scheduledMessageGroupRenameActive = true;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "fg-rename";
  input.value = group.name || "";
  input.setAttribute("aria-label", "消息组名称");
  tab.replaceWith(input);
  input.focus();
  input.select();
  let finished = false;
  const finish = async function (commit) {
    if (finished) return;
    finished = true;
    scheduledMessageGroupRenameActive = false;
    const name = input.value.trim();
    const current = resolveScheduledMessageTarget(target)?.group;
    if (commit && current && name && name !== current.name) {
      current.name = name;
      renderScheduledMessageWorkspace();
      await saveScheduledMessageGroups();
    } else {
      renderScheduledMessageWorkspace();
    }
  };
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    else if (event.key === "Escape") { event.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", function () { finish(true); });
}

function initScheduledPickers() {
  const editButton = document.getElementById("btnScheduledMessageEdit");
  if (editButton) {
    editButton.addEventListener("click", function () {
      const group = activeScheduledMessageGroup();
      if (group) openScheduledMessageEditor({groupId: String(group.id), messageId: null, epoch: workspaceEpoch});
    });
  }
  const addGroup = async function () {
    const suffix = scheduledMessageGroupsDraft.length + 1;
    const group = {id: "scheduled-group-" + Date.now() + "-" + suffix, name: "消息组" + suffix, enabled: true, target_mode: "groups", target_groups: [], forward_groups: [], disabled_target_groups: [], messages: []};
    scheduledMessageGroupsDraft.push(group);
    activeScheduledMessageGroupId = group.id;
    renderScheduledMessageWorkspace();
    await saveScheduledMessageGroups();
  };
  const groupTabs = document.getElementById("scheduledMessageGroupTabs");
  if (groupTabs) groupTabs.addEventListener("click", async function (event) {
    const add = event.target.closest("#btnAddScheduledGroupTab");
    if (add) { await addGroup(); return; }
    const toggle = event.target.closest("[data-scheduled-group-toggle]");
    if (toggle) {
      const group = activeScheduledMessageGroup();
      if (!group) return;
      group.enabled = group.enabled === false;
      renderScheduledMessageWorkspace();
      await saveScheduledMessageGroups();
      return;
    }
    const rename = event.target.closest("[data-scheduled-group-rename]");
    if (rename) { startScheduledMessageGroupRename(rename.dataset.scheduledGroupRename); return; }
    const remove = event.target.closest("[data-delete-scheduled-group]");
    if (remove) {
      const target = {groupId: remove.dataset.deleteScheduledGroup, messageId: null, epoch: workspaceEpoch};
      if (!resolveScheduledMessageTarget(target)) return;
      showConfirmDlg("删除消息组", "删除该消息组及其中的定时消息？保存后生效。", function () {
        const current = resolveScheduledMessageTarget(target);
        if (!current) return;
        scheduledMessageGroupsDraft.splice(scheduledMessageGroupsDraft.indexOf(current.group), 1);
        if (activeScheduledMessageGroupId === target.groupId) {
          activeScheduledMessageGroupId = scheduledMessageGroupsDraft[0]?.id || null;
        }
        renderScheduledMessageWorkspace();
        saveScheduledMessageGroups();
      }, "删除");
      return;
    }
    const tab = event.target.closest("[data-scheduled-group]");
    if (!tab) return;
    activeScheduledMessageGroupId = String(tab.dataset.scheduledGroup);
    renderScheduledMessageWorkspace();
  });
  const manageTargets = document.getElementById("btnScheduledGroupTargets");
  if (manageTargets) manageTargets.addEventListener("click", function () {
    const group = activeScheduledMessageGroup();
    if (!group) return;
    const groupId = String(group.id);
    const groups = (settings && Array.isArray(settings.qq_group_openids) ? settings.qq_group_openids : []).filter(groupInActiveQQBotWorkspace);
    if (!groups.length) { showAlert("QQ 接收群", "当前没有已审核的 QQ 群"); return; }
    const selected = new Set((group.target_groups || []).map(String));
    showConfirmSelect("选择消息组接收群", groups, function (item) {
      const name = item.name || item.remark || item.openid;
      return name + (item.enabled === true ? "（已启用）" : "（未启用）");
    }, async function (picked) {
      const currentGroup = scheduledMessageGroupsDraft.find(function (item) {
        return String(item.id) === groupId;
      });
      if (!currentGroup) {
        showAlert("保存失败", "当前消息组已发生变化，请重新选择接收群。");
        return;
      }
      currentGroup.target_mode = "groups";
      currentGroup.target_groups = picked.map(function (item) { return String(item.openid); });
      const targetSet = new Set(currentGroup.target_groups);
      currentGroup.disabled_target_groups = (currentGroup.disabled_target_groups || []).map(String).filter(function (id) { return targetSet.has(id); });
      currentGroup.forward_groups = [];
      renderScheduledMessageWorkspace();
      await saveScheduledMessageGroups();
    }, {
      defaultChecked: function (item) {
        if (group.target_mode === "all") return item.enabled === true;
        if (group.target_mode === "forward_group") {
          const wanted = new Set((group.forward_groups || []).map(String));
          return (settings.qq_forwarding_groups || []).some(function (fg) { return wanted.has(String(fg.id)) && fg.enabled !== false && (fg.groups || []).map(String).includes(String(item.openid)); });
        }
        return selected.has(String(item.openid));
      },
      okText: "保存选择",
    });
    scheduledMessageTargetPickerOpen = true;
  });
  const messageList = document.getElementById("scheduledGroupMessageList");
  const targetForRow = function (control) {
    const row = control?.closest("tr[data-id]");
    return row && {groupId: messageList.dataset.groupId, messageId: row.dataset.id, epoch: workspaceEpoch};
  };
  const allEnabled = document.getElementById("scheduledMessageAllEnabled");
  if (allEnabled) allEnabled.addEventListener("change", async function () {
    const group = activeScheduledMessageGroup();
    if (!group) return;
    (group.messages || []).forEach(function (message) { message.enabled = allEnabled.checked; });
    renderScheduledMessageWorkspace();
    await saveScheduledMessageGroups();
  });
  if (messageList) messageList.addEventListener("change", async function (event) {
    const checkbox = event.target.closest(".scheduled-message-enabled");
    const target = resolveScheduledMessageTarget(targetForRow(checkbox));
    if (!target) return;
    target.group.messages[target.index].enabled = checkbox.checked;
    renderScheduledMessageWorkspace();
    await saveScheduledMessageGroups();
  });
  if (messageList) messageList.addEventListener("click", function (event) {
    const action = event.target.closest(".scheduled-group-message-edit, .scheduled-group-message-remove");
    if (!action || scheduledMessageSavePending) return;
    const target = targetForRow(action);
    const resolved = resolveScheduledMessageTarget(target);
    if (!resolved) {
      showAlert("定时消息", "无法定位这条消息，请刷新后重试。");
      return;
    }
    if (action.classList.contains("scheduled-group-message-edit")) {
      openScheduledMessageEditor(target);
      return;
    }
    const message = resolved.group.messages[resolved.index];
    const label = String(message.content || scheduledMessageAttachment(message)?.name || "附件消息").trim().slice(0, 60);
    showConfirmDlg("删除定时消息", "删除「" + label + "」？保存后生效。", async function () {
      if (scheduledMessageSavePending) return;
      const current = resolveScheduledMessageTarget(target);
      if (!current) {
        showAlert("删除失败", "这条消息或消息组已变化，请刷新后重试。");
        return;
      }
      const messages = current.group.messages.slice();
      messages.splice(current.index, 1);
      await saveScheduledMessageRows(current.group, messages);
    }, "删除");
  });
  const targetList = document.getElementById("scheduledGroupTargetList");
  if (targetList) targetList.addEventListener("click", function (event) {
    const remove = event.target.closest(".scheduled-group-target-remove");
    if (!remove) return;
    const group = activeScheduledMessageGroup();
    if (!group || group.target_mode !== "groups") return;
    group.target_groups = (group.target_groups || []).filter(function (id) { return String(id) !== String(remove.dataset.targetOpenid); });
    group.disabled_target_groups = (group.disabled_target_groups || []).map(String).filter(function (id) { return id !== String(remove.dataset.targetOpenid); });
    renderScheduledMessageWorkspace();
    saveScheduledMessageGroups();
  });
  document.getElementById("scheduledMessageEntries").addEventListener("click", function (event) {
    if (scheduledAttachmentUploadPending) return;
    const weekdayTrigger = event.target.closest(".scheduled-message-weekday-trigger");
    if (weekdayTrigger) {
      event.preventDefault();
      event.stopPropagation();
      openScheduledWeekdayPicker(weekdayTrigger.closest(".scheduled-message-weekday-picker"));
      return;
    }
    const pickerTrigger = event.target.closest(".scheduled-date-picker-trigger");
    if (pickerTrigger) {
      event.preventDefault();
      event.stopPropagation();
      openScheduledDatePicker(pickerTrigger.closest(".scheduled-date-picker"));
      return;
    }
    const manage = event.target.closest(".scheduled-message-target-manage");
    if (manage) {
      const entry = manage.closest(".scheduled-message-entry");
      const index = entry ? Number(entry.dataset.index) : -1;
      if (index < 0) return;
      scheduledMessageDraft = readScheduledMessageEditor();
      openScheduledTargetPicker(index);
      return;
    }
    const imagePick = event.target.closest(".scheduled-message-image-pick");
    if (imagePick) {
      const entry = imagePick.closest(".scheduled-message-entry");
      const input = entry && entry.querySelector('[data-field="media_file_input"]');
      if (input) input.click();
      return;
    }
    const imageRemove = event.target.closest(".scheduled-message-image-remove");
    if (imageRemove) {
      const entry = imageRemove.closest(".scheduled-message-entry");
      if (!entry) return;
      scheduledMessageDraft = readScheduledMessageEditor();
      const message = scheduledMessageDraft[Number(entry.dataset.index)];
      if (message) {
        message.image_data = "";
        message.image_name = "";
        message.attachment_id = "";
        message.attachment_type = "";
        message.attachment_mime = "";
        message.attachment_name = "";
        message.attachment_size = 0;
        renderScheduledMessageEditor();
      }
      return;
    }
  });
  const scheduledEntries = document.getElementById("scheduledMessageEntries");
  scheduledEntries.addEventListener("pointerover", function (event) {
    const name = event.target.closest(".scheduled-message-image-name.has-image");
    if (!name || name.contains(event.relatedTarget)) return;
    openScheduledImagePreview(name);
  });
  scheduledEntries.addEventListener("pointerout", function (event) {
    const name = event.target.closest(".scheduled-message-image-name.has-image");
    if (!name || name.contains(event.relatedTarget)) return;
    const preview = document.querySelector(".scheduled-message-image-preview");
    if (!preview || !preview.contains(event.relatedTarget)) closeScheduledImagePreview();
  });
  scheduledEntries.addEventListener("focusin", function (event) {
    const name = event.target.closest(".scheduled-message-image-name.has-image");
    if (name) openScheduledImagePreview(name);
  });
  scheduledEntries.addEventListener("focusout", function (event) {
    const name = event.target.closest(".scheduled-message-image-name.has-image");
    if (name && !name.contains(event.relatedTarget)) closeScheduledImagePreview();
  });
  document.addEventListener("pointerdown", function (event) {
    if (event.target.closest(".scheduled-date-picker-trigger, .scheduled-date-picker-popover, .scheduled-message-weekday-trigger, .scheduled-message-weekday-popover")) return;
    closeAllScheduledDatePickers();
    closeScheduledImagePreview();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const picker = document.querySelector(".scheduled-date-picker.is-open, .scheduled-message-weekday-picker.is-open");
    if (!picker) {
      closeScheduledImagePreview();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (picker.matches(".scheduled-date-picker")) {
      closeScheduledDatePicker(picker);
      picker.querySelector(".scheduled-date-picker-trigger")?.focus();
    } else {
      closeScheduledWeekdayPicker(picker);
      picker.querySelector(".scheduled-message-weekday-trigger")?.focus();
    }
  });
  window.addEventListener("resize", function () {
    closeAllScheduledDatePickers();
    closeScheduledImagePreview();
  });
  document.getElementById("scheduledMessageEntries").addEventListener("change", function (event) {
    if (event.target.dataset.field !== "target_mode") return;
    const entry = event.target.closest(".scheduled-message-entry");
    if (!entry) return;
    scheduledMessageDraft = readScheduledMessageEditor();
    const message = scheduledMessageDraft[Number(entry.dataset.index)];
    if (message) {
      message.target_mode = SCHEDULED_TARGET_MODES.includes(event.target.value)
        ? event.target.value : "groups";
      updateScheduledMessageTargetVisibility(entry, message);
    }
  });
  document.getElementById("scheduledMessageEntries").addEventListener("change", async function (event) {
    const entry = event.target.closest(".scheduled-message-entry");
    if (entry && event.target.dataset.field === "media_file_input") {
      if (scheduledAttachmentUploadPending) {
        event.target.value = "";
        return;
      }
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const extension = String(file.name || "").split(".").pop().toLowerCase();
      const allowedExtensions = ["png", "jpg", "jpeg", "gif", "webp", "mp4", "webm", "mp3", "wav", "ogg", "m4a"];
      if (!allowedExtensions.includes(extension)) {
        showAlert("定时消息", "支持 PNG、JPEG、GIF、WebP 图片，MP4、WebM 视频，以及 MP3、WAV、OGG、M4A 音频");
        event.target.value = "";
        return;
      }
      if (file.size <= 0 || file.size > 30 * 1024 * 1024) {
        showAlert("定时消息", "单个媒体文件大小必须大于 0 且不超过 30 MiB");
        event.target.value = "";
        return;
      }
      scheduledMessageDraft = readScheduledMessageEditor();
      const index = Number(entry.dataset.index);
      const target = scheduledMessageEditorTarget;
      const resolved = resolveScheduledMessageTarget(target);
      if (!resolved) {
        showAlert("定时消息", "这条消息或消息组已变化，请重新打开编辑窗口。");
        event.target.value = "";
        return;
      }
      const usedByOthers = scheduledAttachmentBytesExcept(resolved);
      if (usedByOthers + file.size > 30 * 1024 * 1024) {
        showAlert("定时消息", "所有定时消息附件合计不能超过 30 MiB");
        event.target.value = "";
        return;
      }
      scheduledAttachmentUploadPending = true;
      try {
        const response = await uploadScheduledMessageAttachment(file);
        if (scheduledMessageEditorTarget !== target || !resolveScheduledMessageTarget(target)) return;
        scheduledMessageDraft = readScheduledMessageEditor();
        const latest = scheduledMessageDraft[index];
        Object.assign(latest, response.attachment, {image_data: "", image_name: ""});
        renderScheduledMessageEditor();
      } catch (error) {
        if (scheduledMessageEditorTarget === target && resolveScheduledMessageTarget(target)) {
          scheduledMessageDraft = readScheduledMessageEditor();
          showAlert("定时消息", error && error.message ? error.message : "附件上传失败，请重新选择");
        }
      } finally {
        scheduledAttachmentUploadPending = false;
        event.target.value = "";
      }
      return;
    }
    if (entry && event.target.dataset.field === "weekday") {
      updateScheduledWeekdayPickerDisplay(entry.querySelector(".scheduled-message-weekday-picker"));
    }
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
  scheduledMessageGroupsDraft = normalizeScheduledMessageGroups(value);
  if (!activeScheduledMessageGroupId || !scheduledMessageGroupsDraft.some(function (group) { return String(group.id) === String(activeScheduledMessageGroupId); })) {
    activeScheduledMessageGroupId = scheduledMessageGroupsDraft[0] ? scheduledMessageGroupsDraft[0].id : null;
  }
  renderScheduledMessageWorkspace();
}

function preserveScheduledMessageAttachments(statusConfig, currentConfig) {
  if (statusConfig && Array.isArray(statusConfig.message_groups)) {
    const currentGroups = Array.isArray(currentConfig && currentConfig.message_groups) ? currentConfig.message_groups : [];
    const oldGroups = new Map(currentGroups.map(function (group, index) {
      return [String(group.id || "scheduled-group-" + (index + 1)), group];
    }));
    const next = Object.assign({}, statusConfig);
    next.message_groups = statusConfig.message_groups.map(function (group, groupIndex) {
      const oldGroup = oldGroups.get(String(group.id || "scheduled-group-" + (groupIndex + 1)));
      const oldMessages = new Map(((oldGroup && oldGroup.messages) || []).map(function (message, index) {
        return [String(message.id || "message-" + (index + 1)), message];
      }));
      return Object.assign({}, group, {messages: (group.messages || []).map(function (message, index) {
        const previous = oldMessages.get(String(message.id || "message-" + (index + 1)));
        if (!message.attachment_id && !message.image_data && previous?.image_data) {
          message = Object.assign({}, message, {image_data: previous.image_data, image_name: previous.image_name || ""});
        }
        return message;
      })});
    });
    return next;
  }
  const currentMessages = normalizeScheduledMessages(currentConfig);
  const currentById = new Map(currentMessages.map(function (message) {
    return [message.id, message];
  }));
  const next = Object.assign({}, statusConfig || {});
  next.messages = normalizeScheduledMessages(statusConfig).map(function (message) {
    const previous = currentById.get(message.id);
    if (!message.attachment_id && !message.image_data && previous?.image_data) {
      message.image_data = previous.image_data;
      message.image_name = previous.image_name || "";
    }
    return message;
  });
  return next;
}

// ---------- 转发组（Discord / X / QQ→QQ 共用机制，按 ctx 区分） ----------
function getFgList(ctx) {
  if (!settings) return [];
  // 新增链路的旧配置可能尚未含有对应字段；先挂回 settings，
  // 否则对临时空数组 push 后会在保存前丢失，表现为“添加转发组无反应”。
  if (!Array.isArray(settings[ctx.field])) settings[ctx.field] = [];
  return settings[ctx.field];
}
function forwardingGroupIsEmpty(ctx, fg) {
  return !!fg && ctx.emptyMembers.every(function (field) {
    const members = fg[field];
    return members == null || (Array.isArray(members) && members.length === 0);
  });
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
function fgTargetEnabled(fg, id) {
  return !((fg && fg.disabled_groups) || []).map(String).includes(String(id));
}
function setFgTargetEnabled(fg, id, enabled) {
  if (!fg) return;
  const value = String(id);
  const disabled = new Set((fg.disabled_groups || []).map(String));
  if (enabled) disabled.delete(value); else disabled.add(value);
  fg.disabled_groups = Array.from(disabled);
}
function toggleFgMember(fg, id, field, on) {
  // 只修改当前组的成员集合（channels/sources/groups）；id 统一转字符串存储
  if (!fg) return;
  const idStr = String(id);
  const arr = fg[field] || [];
  const idx = arr.indexOf(idStr);
  if (on && idx < 0) arr.push(idStr);
  if (!on && idx >= 0) arr.splice(idx, 1);
  if (field === "groups") {
    fg.disabled_groups = (fg.disabled_groups || []).map(String).filter(function (value) { return value !== idStr; });
  }
}
function removeFgMember(ctx, id, field) {
  // 只把实体从当前转发组移除（脱组）：不删全局实体、不影响其他组
  const fg = getActiveFg(ctx);
  if (!fg) return;
  const idStr = String(id);
  if (!(fg[field] || []).some(value => String(value) === idStr)) return;
  const groupId = fg.id;
  const epoch = workspaceEpoch;
  showConfirmDlg("移出当前转发组", "将此成员移出「" + fg.name + "」？注册记录及其他组不受影响。", async function () {
    if (epoch !== workspaceEpoch) return;
    const current = getFgList(ctx).find(item => item.id === groupId);
    if (!current || !(current[field] || []).some(value => String(value) === idStr)) return;
    current[field] = current[field].filter(value => String(value) !== idStr);
    if (field === "groups") current.disabled_groups = (current.disabled_groups || []).map(String).filter(value => value !== idStr);
    await saveAndReload();
  }, "移出");
}
function fgCandidate(kind) {
  // kind → 已注册实体列表 / 条目 id / 展示文案（各转发组共用）
  if (kind === "group") {
    return {
      all: settings.qq_group_openids.filter(groupInActiveQQBotWorkspace),
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
  if (kind === "biliSource") {
    return {
      all: settings.bilibili_sources || [],
      idOf: function (source) {
        return String(source.id || bilibiliSourceId(source.kind || "up", source.value || ""));
      },
      labelOf: function (source) {
        const kindText = source.kind === "up" ? "UP主" : source.kind === "tag" ? "标签" : "关键词";
        return ((source.value || "") + " · " + kindText).trim();
      },
    };
  }
  if (kind === "youtubeSource") {
    return {
      all: settings.youtube_sources || [],
      idOf: function (source) {
        return String(source.id || youtubeSourceId(source.value));
      },
      labelOf: function (source) {
        return ((source.name || source.value || "") + (source.name && source.value ? " · " + source.value : "")).trim();
      },
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
  const field = ctx.memberField[kind] || ctx.memberField.source;
  const cand = fgCandidate(kind);
  const memberSet = {};
  (fg[field] || []).forEach(function (id) { memberSet[String(id)] = true; });
  const candidates = cand.all.filter(function (item) {
    return !memberSet[cand.idOf(item)];
  });
  if (!candidates.length) {
    showAlert(
      "从已注册项新增",
      "暂无可添加的项目",
      {emptyState: true}
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
      cardRows: true,
      detailFn: function (item) {
        if (kind === "chan") return String(item.id || "");
        if (kind === "group") return String(item.openid || "");
        if (kind === "source") return "@" + xSourceKey(item.source);
        return "";
      },
      rowDeleteIcon: SVG_TRASH,
      onRowEdit: kind === "group" ? function (item) { editGroupDetails(item); } : null,
      labelLinkFn: kind === "source"
        ? function (item) { return xSourceProfileUrl(item.source); }
        : kind === "youtubeSource"
          ? function (item) { return youtubeChannelUrl(item.value); }
          : null,
      onRowDelete: function (item) {
        deleteRegisteredItem(kind, item, function () { openFgAddPicker(ctx, kind); });
      },
    }
  );
}

// 白名单「＋ 新增」：从已注册用户中选择加入白名单（勾选 = 放行 enabled）
function openUserAddPicker() {
  const candidates = (settings.qq_user_openids || []).filter(function (u) { return u.enabled !== true; });
  if (!candidates.length) {
    showAlert("从已注册项新增", "暂无可添加的用户", {emptyState: true});
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
      cardRows: true,
      detailFn: function (u) { return String(u.openid || ""); },
      onRowDelete: function (u) { deleteRegisteredItem("user", u); },
    }
  );
}

// 「从已注册项新增」窗口行尾垃圾桶：删除该条目的注册记录（从全局实体列表移除；
// 保存时面板 _normalize_forwarding_groups 会剔除它在各转发组中的悬空引用）。
// 与表格行内"删除"（仅移出当前转发组、保留全局实体）严格区分。删除后需重新走
// 注册流程（重新扫描 / 重新注册审核）才能再次加入转发组或白名单。
function deleteRegisteredItem(kind, item, reopenPicker) {
  const isGroup = kind === "group";
  const isChan = kind === "chan";
  const isSource = kind === "source";
  const isBilibiliSource = kind === "biliSource";
  const isYoutubeSource = kind === "youtubeSource";
  const id = String(
    isChan ? item.id
      : isSource ? xSourceKey(item.source)
        : isBilibiliSource ? (item.id || bilibiliSourceId(item.kind || "up", item.value || ""))
          : isYoutubeSource ? (item.id || youtubeSourceId(item.value))
            : item.openid
  );
  const name = isGroup
    ? (item.name || item.remark || identityName("groups", item.openid) || "")
    : isChan
      ? (item.name || "")
      : isSource
        ? (item.name || xSourceKey(item.source) || "")
        : isBilibiliSource
          ? (item.value || "")
          : isYoutubeSource
            ? (item.name || item.value || "")
            : (item.name || item.remark || identityName("users", item.openid) || "");
  const label = ((name || "") + " " + id).trim();
  const kindName = isGroup ? "群" : isChan ? "频道" : isSource || isBilibiliSource || isYoutubeSource ? "来源" : "用户";
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
      } else if (isBilibiliSource) {
        const idx = (settings.bilibili_sources || []).findIndex(function (o) {
          return String(o.id || bilibiliSourceId(o.kind || "up", o.value || "")) === id;
        });
        if (idx >= 0) settings.bilibili_sources.splice(idx, 1);
      } else if (isYoutubeSource) {
        const idx = (settings.youtube_sources || []).findIndex(function (o) {
          return String(o.id || youtubeSourceId(o.value)) === id;
        });
        if (idx >= 0) settings.youtube_sources.splice(idx, 1);
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
  if (!pending.length) { showAlert("注册结果", "暂无可注册的新频道", {emptyState: true}); return; }
  showConfirmSelect("全部可读频道", pending, function (ch) { return ch.name + " (" + ch.id + ")"; }, async function (selected) {
    if (!selected.length) { showAlert("注册结果", "未选择任何频道"); return; }
    let added = 0;
    selected.forEach(function (ch) {
      const id = String(ch.id);
      if (known[id]) return;
      settings.discord_channels.push({
        id: id, name: String(ch.name || "").replace(/^#/, ""), enabled: false,
        translation_enabled: true, translation_append_original: true, translation_show_datetime: false,
      });
      known[id] = true;
      added++;
    });
    if (added) {
      await saveAndReload();
    } else {
      renderSettings();
    }
    showAlert("注册结果", "已注册 " + added + " 个新频道（默认未启用）");
  }, {defaultChecked: false, cardRows: true, detailFn: function (ch) { return String(ch.id || ""); }, groupBy: function (ch) { return ch.guild; }});
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
  const tabList = document.createElement("div");
  tabList.className = "fgtab-list";
  const actionRail = document.createElement("div");
  actionRail.className = "fgtab-actions";
  wrap.appendChild(tabList);
  const keepActionRailOutsideScrollableTabs = IS_DESKTOP_HOST && ["x", "bilibili", "youtube", "qq"].includes(ctx.key);
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
    tab.title = enabled ? fg.name : fg.name + "（已暂停）";
    const tabName = document.createElement("span");
    tabName.className = "fgtab-name";
    tabName.textContent = fg.name;
    tab.appendChild(tabName);
    if (!enabled) {
      const paused = document.createElement("span");
      paused.className = "fgtab-state";
      paused.textContent = "暂停";
      tab.appendChild(paused);
    }
    tabList.appendChild(tab);
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
      actionRail.appendChild(toggle);
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "fgctrl rowicon";
      rename.dataset.kind = "fgRename";
      rename.dataset.ctx = ctx.key;
      rename.dataset.id = fg.id;
      rename.title = "重命名转发组";
      rename.setAttribute("aria-label", rename.title);
      rename.innerHTML = SVG_PEN;
      actionRail.appendChild(rename);
      const canDelete = list.length > 1 || (ctx.key === "qq" && (fg.groups || []).length <= 1);
      if (canDelete) {
        const del = document.createElement("button");
        del.type = "button";
        del.className = "fgctrl rowicon fg-delete";
        del.dataset.kind = "fgDel";
        del.dataset.ctx = ctx.key;
        del.dataset.id = fg.id;
        del.title = "删除转发组";
        del.setAttribute("aria-label", del.title);
        del.innerHTML = SVG_TRASH;   // 与成员行内删除按钮同一白色垃圾桶图标
        actionRail.appendChild(del);
      }
    }
  });
  let addButton = null;
  // 达到上限(30)后直接不显示新增按钮，删除一个后自动恢复
  if (list.length < MAX_FG) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "fgtab-add";
    add.id = ctx.addBtnId;
    add.title = "添加转发组";
    add.setAttribute("aria-label", "添加转发组");
    add.innerHTML = mcIcon("add", 16);
    addButton = add;
  }
  if (addButton) tabList.appendChild(addButton);
  if (actionRail.childElementCount) {
    (keepActionRailOutsideScrollableTabs ? wrap : tabList).appendChild(actionRail);
  }
}
async function addForwardingGroup(ctx) {
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
    disabled_groups: [],
  };
  ctx.emptyMembers.forEach(function (f) { fg[f] = []; });
  list.push(fg);
  ctx.activeId = fg.id;
  try { localStorage.setItem(ctx.storageKey, fg.id); } catch (e) {}
  return await saveAndReload();
}
let fgRenameActive = false;
let fgRenameCtxKey = null;   // 正在重命名的转发组上下文 key（discord / x）
function startFgRename(ctx, groupId) {
  const fg = getFgList(ctx).find(item => item.id === groupId);
  // 转发组重命名
  if (!fg || fgRenameActive) return;
  const wrap = document.getElementById(ctx.tabsId);
  const tabs = Array.prototype.slice.call(wrap.querySelectorAll(".fgtab"));
  const tabBtn = tabs.find(function (b) { return b.dataset.id === fg.id; });
  if (!tabBtn) return;
  const epoch = workspaceEpoch;
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
    if (epoch !== workspaceEpoch) return;
    const current = getFgList(ctx).find(item => item.id === groupId);
    if (commit && current && v && v !== current.name) {
      current.name = v;
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

let qqBotIds = [];
let qqBotAccounts = [];
let personalQQConnections = [];
let connectedQQBotIds = [];
let qqBotStatusKnown = false;
let qqAggregateStatusKnown = false;
let qqAggregateConnected = false;
const QQ_BOT_WORKSPACE_KEY = "wrelay_active_qq_bot";
let workspaceEpoch = 0;
let activeQQBotId = "";
try { activeQQBotId = localStorage.getItem(QQ_BOT_WORKSPACE_KEY) || ""; } catch (e) {}

function currentQQBotId() {
  if (!qqBotIds.length) return "";
  if (!qqBotIds.includes(activeQQBotId)) {
    activeQQBotId = qqBotIds[0];
    try { localStorage.setItem(QQ_BOT_WORKSPACE_KEY, activeQQBotId); } catch (e) {}
  }
  return activeQQBotId;
}

function groupInActiveQQBotWorkspace(group) {
  if (!group || !group.openid) return false;
  const active = currentQQBotId();
  if (!group.bot_id && active.startsWith("personal:")) return false;
  const owner = String(group.bot_id || "");
  // 旧配置没有 Bot 归属时，沿用原有“第一个 Bot”语义，显示在第一个工作区。
  return owner === active || (!owner && active && active === qqBotIds[0]);
}

function renderQQBotWorkspace() {
  const select = document.getElementById("qqBotWorkspace");
  if (!select) return;
  const active = currentQQBotId();
  if (!qqBotIds.length) {
    select.innerHTML = '<option value="">未配置 QQ Bot</option>';
    select.disabled = true;
    return;
  }
  select.innerHTML = qqBotIds.map(function (botId) {
    const aggregateCanRepresentSingleBot = qqBotIds.length === 1 && qqAggregateStatusKnown;
    const statusKnown = qqBotStatusKnown || aggregateCanRepresentSingleBot;
    const connected = qqBotStatusKnown ? connectedQQBotIds.includes(botId) : qqAggregateConnected;
    const state = !statusKnown ? "状态未知" : connected ? "已连接" : "未连接";
    const personal = personalQQConnections.find(item => "personal:" + item.uin === botId);
    const bot = qqBotAccounts.find(item => String(item.appid) === botId);
    const personalName = personal && String(personal.name || "").trim();
    const botName = bot && String(bot.name || "").trim();
    const label = personal
      ? (personalName || ("个人 QQ · " + personal.uin)) + (personal.enabled ? "" : " · 已停用")
      : (botName || botId);
    return '<option value="' + escapeHtml(botId) + '"' + (botId === active ? ' hidden' : '') + '>' + escapeHtml(label) + " · " + state + '</option>';
  }).join("");
  select.value = active;
  select.disabled = qqBotIds.length < 2;
}

function beginPanelLoading(message, hideText) {
  const loading = document.getElementById("panelLoading");
  const loadingText = document.getElementById("panelLoadingText");
  loadingText.textContent = message;
  loadingText.classList.toggle("panel-loading-text-hidden", Boolean(hideText));
  loading.hidden = false;
}

function endPanelLoading() {
  document.getElementById("panelLoading").hidden = true;
}

const qqBotWorkspace = document.getElementById("qqBotWorkspace");
if (qqBotWorkspace) {
  qqBotWorkspace.addEventListener("change", async function (event) {
    const previous = settings?._profile_bot || activeQQBotId;
    activeQQBotId = String(event.target.value || "");
    workspaceEpoch += 1;
    document.body.classList.add("workspace-switching");
    beginPanelLoading("正在切换账号…", true);
    qqBotWorkspace.disabled = true;
    try {
      latestStatus = null;
      renderMessageTrace(null);
      ["statsToday", "statsTotal", "statsFailed"].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) { el.textContent = "—"; el.title = "正在读取当前账号"; }
      });
      const loaded = await jget("/api/settings");
      if (loaded.ok === false) throw new Error(loaded.msg || "无法读取配置");
      settings = loaded;
      try { localStorage.setItem(QQ_BOT_WORKSPACE_KEY, activeQQBotId); } catch (e) {}
      const api = window.pywebview && window.pywebview.api;
      if (IS_DESKTOP_HOST && api && api.set_last_qq_bot) {
        try {
          const saved = await api.set_last_qq_bot(activeQQBotId);
          if (!saved || saved.ok === false) throw new Error((saved && saved.msg) || "无法保存账号选择");
        } catch (error) {
          showAlert("账号选择未保存", "当前账号已切换，但下次启动可能无法恢复：" + error.message);
        }
      }
      renderSettings();
      renderOverviewCounts();
      await Promise.all([refreshStatus(), refreshXStatus(), refreshBilibiliStatus(), refreshYoutubeStatus(), refreshSteamStatus(), refreshMessageTrace()]);
    } catch (error) {
      activeQQBotId = previous;
      workspaceEpoch += 1;
      await Promise.all([refreshStatus(), refreshXStatus(), refreshBilibiliStatus(), refreshYoutubeStatus(), refreshSteamStatus(), refreshMessageTrace()]);
      showAlert("切换失败", error.message);
    } finally {
      endPanelLoading();
      document.body.classList.remove("workspace-switching");
      renderQQBotWorkspace();
    }
  });
}

function renderSettings() {
  if (window.renderSteamPage) window.renderSteamPage();
  renderAIControls();
  renderQQBotWorkspace();
  renderFgTabs(FG_DISCORD);
  const activeFg = getActiveFg(FG_DISCORD);
  const gb = document.getElementById("groupBody");
  gb.innerHTML = "";
  // 只渲染当前转发组的成员；「启用/测试」仍是全局实体状态
  const memberGrps = {};
  if (activeFg) {
    (activeFg.groups || []).forEach(function (o) { memberGrps[String(o)] = true; });
  }
  const grpRows = settings.qq_group_openids.filter(function (g) {
    return memberGrps[String(g.openid)] && groupInActiveQQBotWorkspace(g);
  });
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
      '<td style="text-align:center"><input type="checkbox" data-kind="forwardGroupTarget" data-fg="discord" data-openid="' + escapeHtml(String(g.openid)) + '"' + (fgTargetEnabled(activeFg, g.openid) ? " checked" : "") + '></td>' +
      '<td>' +
        (gname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(gname) + '</div>' : '') +
      '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(gacc) + '</td>' +
      '<td>' + renderIdCell(g.openid) + '</td>' +
      '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon" data-kind="groupEdit" data-id="' + escapeHtml(String(g.openid)) + '" title="编辑群资料" aria-label="编辑群资料">' + SVG_PEN + '</button><button class="danger rowicon" title="移出当前转发组" aria-label="移出当前转发组" data-kind="fgGroupRemove" data-id="' + escapeHtml(String(g.openid)) + '">' + SVG_TRASH + '</button></div></td>';
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
      '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增频道，点击下方「新增」按钮来新增第一个频道吧！</td></tr>';
  }
  chanRows.forEach((c) => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(c.id));
    const gi = settings.discord_channels.indexOf(c);
    const realName = channelNames[c.id] || c.name || String(c.id);
    const channelUrl = discordChannelUrl(c.id);
    const realNameHtml = channelUrl
      ? '<a class="source-link discord-channel-link" href="' + escapeHtml(channelUrl) + '" target="_blank" rel="noopener noreferrer" title="打开 Discord 频道">' + escapeHtml(realName) + '</a>'
      : escapeHtml(realName);
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="chan" data-i="' + gi + '"' + (c.enabled ? " checked" : "") + '></td>' +
      '<td><span class="chan-name-text">' + escapeHtml(c.name || "") + '</span></td>' +
      '<td class="discord-channel-real-name">' + (realName ? realNameHtml : '') + '</td>' +
      '<td>' + renderIdCell(c.id) + '</td>' +
      '<td><div class="table-actions">' +
        '<button class="sec rowicon' + (channelFilterActive(c) ? ' filter-active' : '') + '" title="内容筛选" aria-label="内容筛选" data-kind="chanFilter">' + SVG_FUNNEL + '</button>' +
        '<button class="sec rowicon" title="编辑名称与来源地址" aria-label="编辑名称与来源地址" data-kind="chanEdit">' + SVG_PEN + '</button>' +
        '<button class="danger rowicon" title="移出当前转发组" aria-label="移出当前转发组" data-kind="fgChanRemove" data-id="' + escapeHtml(String(c.id)) + '">' + SVG_TRASH + '</button>' +
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
      '<td>' + renderIdCell(u.openid) + '</td>' +
      '<td><div class="table-actions">' +
        '<button class="sec rowicon" title="编辑用户资料" aria-label="编辑用户资料" data-kind="userEdit">' + SVG_PEN + '</button>' +
        '<button class="danger rowicon" title="删除用户" aria-label="删除用户" data-kind="userDel">' + SVG_TRASH + '</button>' +
        '</div></td>';
      ub.appendChild(tr);
    });
  }
  renderFailureNotificationToggle(!!(settings.failure_notification && settings.failure_notification.enabled));
  renderStatusSheenToggle(isStatusSheenEnabled());
  renderScheduledMessage(settings.scheduled_message);
  renderUnifiedRelayFilter();
  // 表头全选框状态仅反映「当前转发组」实际显示的成员（而非全部全局实体），
  // 避免隐藏的非本组成员导致半选误判：全部→勾选，全不→取消，部分→半选，无成员→两者均 false
  syncHeaderAll("groupAllEnabled", grpRows.map(function (group) { return {enabled: fgTargetEnabled(activeFg, group.openid)}; }), "enabled");
  syncHeaderAll("chanAllEnabled", chanRows, "enabled");
  syncHeaderAll("userAllEnabled", settings.qq_user_openids || [], "enabled");
  // X / Bilibili / QQ → QQ 页面与 Discord 页共用同一份 settings，跟随保存刷新
  renderXPage();
  renderBilibiliPage();
  renderYoutubePage();
  renderQQPage();
}

function renderAIControls() {
  if (!settings) return;
  const translation = settings.translation || {};
  document.getElementById("translationProvider").value = translation.provider || "tencent_tmt";
  document.getElementById("translationFallback").value = translation.fallback_provider || "";
  syncTranslationFallback("translationProvider", "translationFallback");
  const allEnabled = function (items, defaultEnabled) {
    const list = items || [];
    return list.length ? list.every(function (item) {
      return item.translation_enabled === true;
    }) : defaultEnabled;
  };
  renderTranslationToggleFor("btnTranslationDiscord", allEnabled(settings.discord_channels, true), "Discord → QQ 翻译", !(settings.discord_channels || []).length);
  renderTranslationToggleFor("btnTranslationX", allEnabled(settings.x_rss_sources, true), "X → QQ 翻译", !(settings.x_rss_sources || []).length);
  renderTranslationToggleFor("btnTranslationBilibili", allEnabled(settings.bilibili_sources, false), "Bilibili → QQ 翻译", !(settings.bilibili_sources || []).length);
  renderTranslationToggleFor("btnTranslationYoutube", allEnabled(settings.youtube_sources, true), "YouTube → QQ 翻译", !(settings.youtube_sources || []).length);
  renderTranslationToggleFor("btnTranslationSteam", allEnabled(settings.steam_sources, true), "Steam → QQ 翻译", !(settings.steam_sources || []).length);
  renderTranslationToggleFor("btnTranslationManual", translation.enabled === true, "QQ → QQ 手动转发翻译");
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

function renderTranslationToggleFor(buttonId, on, label, noSources) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  btn.disabled = !!noSources;
  btn.title = noSources ? "暂无来源，添加来源后可批量切换" : "启用或关闭 " + label;
  btn.dataset.on = on ? "1" : "0";
  btn.setAttribute("aria-checked", on ? "true" : "false");
  btn.innerHTML = pillSwitchMarkup(label, on);
}

function saveAIControls() {
  settings.translation = settings.translation || {};
  settings.translation.provider = document.getElementById("translationProvider").value;
  settings.translation.fallback_provider = document.getElementById("translationFallback").value;
  syncLegacyXTranslationConfig();
}

function syncLegacyXTranslationConfig() {
  settings.x_translation = Object.assign({}, settings.x_translation || {});
  ["provider", "fallback_provider", "target_language", "append_original", "only_non_chinese"].forEach(function (key) {
    if (settings.translation[key] !== undefined) settings.x_translation[key] = settings.translation[key];
  });
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
    return memberGroups[String(group.openid)] && groupInActiveQQBotWorkspace(group);
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
      '<td style="text-align:center"><input type="checkbox" data-kind="forwardGroupTarget" data-fg="qq" data-openid="' + escapeHtml(String(group.openid)) + '"' + (fgTargetEnabled(qfg, group.openid) ? " checked" : "") + '></td>' +
      '<td>' + (name ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(name) + '</div>' : '') + '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(account) + '</td>' +
      '<td>' + renderIdCell(group.openid) + '</td>' +
      '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon" data-kind="groupEdit" data-id="' + escapeHtml(String(group.openid)) + '" title="编辑群资料" aria-label="编辑群资料">' + SVG_PEN + '</button><button class="danger rowicon" data-kind="qqFgGroupRemove" data-id="' + escapeHtml(String(group.openid)) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    groupBody.appendChild(tr);
  });
  syncHeaderAll("qqGroupAllEnabled", groupRows.map(function (group) { return {enabled: fgTargetEnabled(qfg, group.openid)}; }), "enabled");
}

// ---------------- X → QQ 页面 ----------------
// 转发组读写 settings.x_forwarding_groups（与 Discord forwarding_groups 完全
// 独立；组机制函数与 Discord 页共用，见 FG_X 上下文）；私聊白名单与 X RSS
// 状态仍读写原有全局字段。保存走现有 /api/settings/save 整体流程；
// 后端 x_rss.py 路由逻辑不在此触碰。

function orderedForwardingGroupItems(memberIds, items, getId, includeItem) {
  const byId = new Map();
  (items || []).forEach(function (item) { byId.set(String(getId(item)), item); });
  const seen = new Set();
  return (memberIds || []).map(function (id) { return byId.get(String(id)); }).filter(function (item) {
    if (!item) return false;
    const id = String(getId(item));
    if (seen.has(id)) return false;
    seen.add(id);
    return !includeItem || includeItem(item);
  });
}

function renderXPage() {
  const srcBody = document.getElementById("xSourceBody");
  if (!srcBody || !settings) return;
  renderFgTabs(FG_X);
  const xfg = getActiveFg(FG_X);

  // X 转发账号：只渲染当前 X 转发组的账号成员；「启用」仍是全局注册源状态
  const allSources = Array.isArray(settings.x_rss_sources) ? settings.x_rss_sources : [];
  srcBody.innerHTML = "";
  const srcRows = orderedForwardingGroupItems(
    xfg && xfg.sources, allSources, function (source) { return xSourceKey(source.source); }
  );
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
      '<td><span class="chan-name-text">' + escapeHtml(name) + '</span></td>' +
      '<td class="mono">' +
        (profileUrl
          ? '<a class="source-link x-account-link" href="' + escapeHtml(profileUrl) + '" target="_blank" rel="noopener noreferrer" title="打开 X 主页">@' + escapeHtml(handle) + '</a>'
          : '@' + escapeHtml(handle)) +
      '</td>' +
      '<td style="text-align:right"><div class="table-actions">' +
        '<button class="sec rowicon' + (xSourceFilterActive(s) ? ' filter-active' : '') + '" title="内容筛选" aria-label="内容筛选" data-kind="xSourceFilter">' + SVG_FUNNEL + '</button>' +
        '<button class="sec rowicon" title="编辑名称与来源地址" aria-label="编辑名称与来源地址" data-kind="xSourceEdit">' + SVG_PEN + '</button>' +
        '<button class="danger rowicon" data-kind="xFgSourceRemove" data-id="' + escapeHtml(handle) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    srcBody.appendChild(tr);
  });
  // 表头全选框状态反映当前组成员，勾选/取消只作用于当前 X 转发组来源
  syncHeaderAll("xSourceAllEnabled", srcRows, "enabled");

  // QQ 接收群：只渲染当前 X 转发组的群成员；「启用」仍是全局群状态
  const xgBody = document.getElementById("xXGroupBody");
  const allGroups = settings.qq_group_openids || [];
  xgBody.innerHTML = "";
  const grpRows = orderedForwardingGroupItems(
    xfg && xfg.groups, allGroups, function (group) { return group.openid; }, groupInActiveQQBotWorkspace
  );
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
      '<td style="text-align:center"><input type="checkbox" data-kind="forwardGroupTarget" data-fg="x" data-openid="' + escapeHtml(String(g.openid)) + '"' + (fgTargetEnabled(xfg, g.openid) ? " checked" : "") + '></td>' +
      '<td>' +
        (gname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(gname) + '</div>' : '') +
        (g.enabled !== true ? '<small class="help-text">全局接收未启用</small>' : '') +
      '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(gacc) + '</td>' +
      '<td>' + renderIdCell(g.openid) + '</td>' +
      '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon" data-kind="groupEdit" data-id="' + escapeHtml(String(g.openid)) + '" title="编辑群资料" aria-label="编辑群资料">' + SVG_PEN + '</button><button class="danger rowicon" data-kind="xFgGroupRemove" data-id="' + escapeHtml(String(g.openid)) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    xgBody.appendChild(tr);
  });
  syncHeaderAll("xGroupAllEnabled", grpRows.map(function (group) { return {enabled: fgTargetEnabled(xfg, group.openid)}; }), "enabled");

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
        '<td>' + renderIdCell(u.openid) + '</td>' +
        '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon" data-kind="userEdit" title="编辑用户资料" aria-label="编辑用户资料">' + SVG_PEN + '</button><button class="danger rowicon" data-kind="xUserDel" title="删除用户" aria-label="删除用户">' + SVG_TRASH + '</button></div></td>';
      xb.appendChild(tr);
    });
    syncHeaderAll("xUserAllEnabled", settings.qq_user_openids || [], "enabled");
  }

  // X RSS 状态卡片的「运行中徽章 + 运行行」由 refreshXStatus 轻量轮询独立刷新；
  // 这里只同步两个配置输入框（正在编辑的输入框不覆盖）
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

// ---------------- Bilibili → QQ 页面 ----------------
// Bilibili 使用独立的来源和转发组；来源条目本身携带 kind/value/filter 字段，
// 因此不复用 X 的账号字段，避免保存时互相覆盖。
function bilibiliKindText(source) {
  const kind = String(source && source.kind || "up").toLowerCase();
  return kind === "up" ? "UP主" : kind === "tag" ? "标签" : "关键词";
}

function bilibiliSourceUrl(source) {
  const value = String(source && (source.value || source.uid) || "").trim();
  if (!value) return "";
  const kind = String(source && source.kind || "up").toLowerCase();
  if (kind === "up" && /^\d+$/.test(value)) {
    return "https://space.bilibili.com/" + encodeURIComponent(value);
  }
  return "https://search.bilibili.com/all?keyword=" + encodeURIComponent(value);
}

function renderBilibiliPage() {
  const sourceBody = document.getElementById("bilibiliSourceBody");
  if (!sourceBody || !settings) return;
  renderFgTabs(FG_BILIBILI);
  const fg = getActiveFg(FG_BILIBILI);
  const allSources = Array.isArray(settings.bilibili_sources) ? settings.bilibili_sources : [];
  const sourceRows = orderedForwardingGroupItems(
    fg && fg.sources, allSources,
    function (source) { return String(source.id || bilibiliSourceId(source.kind || "up", source.value || "")); }
  );
  sourceBody.innerHTML = "";
  if (!sourceRows.length) {
    sourceBody.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增来源，请先「注册来源」，再加入当前转发组。</td></tr>';
  }
  sourceRows.forEach(function (source) {
    const index = allSources.indexOf(source);
    const sourceId = String(source.id || bilibiliSourceId(source.kind || "up", source.value || ""));
    const sourceUrl = bilibiliSourceUrl(source);
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", sourceId);
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="biliSource" data-i="' + index + '"' + (source.enabled ? " checked" : "") + '></td>' +
      '<td>' + escapeHtml(bilibiliKindText(source)) + '</td>' +
      '<td><span class="chan-name-text">' + escapeHtml(source.name || "") + '</span></td>' +
      '<td class="mono" title="' + escapeHtml(source.value || "") + '">' + (sourceUrl
        ? '<a class="source-link bilibili-source-link" href="' + escapeHtml(sourceUrl) + '" target="_blank" rel="noopener noreferrer" title="打开 Bilibili 来源">' + escapeHtml(source.value || source.uid || "") + '</a>'
        : escapeHtml(source.value || source.uid || "")) + '</td>' +
      '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon' + (bilibiliSourceFilterActive(source) ? ' filter-active' : '') + '" data-kind="biliSourceFilterEdit" title="内容筛选" aria-label="内容筛选">' + SVG_FUNNEL + '</button><button class="sec rowicon" data-kind="biliSourceEdit" title="编辑名称与来源地址" aria-label="编辑名称与来源地址">' + SVG_PEN + '</button><button class="danger rowicon" data-kind="biliFgSourceRemove" data-id="' + escapeHtml(sourceId) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    sourceBody.appendChild(tr);
  });
  syncHeaderAll("bilibiliSourceAllEnabled", sourceRows, "enabled");

  const groupBody = document.getElementById("bilibiliGroupBody");
  const allGroups = settings.qq_group_openids || [];
  const groupRows = orderedForwardingGroupItems(
    fg && fg.groups, allGroups, function (group) { return group.openid; }, groupInActiveQQBotWorkspace
  );
  groupBody.innerHTML = "";
  if (!groupRows.length) {
    groupBody.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增群，请加入一个 QQ 接收群。</td></tr>';
  }
  groupRows.forEach(function (group) {
    const index = allGroups.indexOf(group);
    const name = group.name || identityName("groups", group.openid) || "";
    const account = group.qq_id || "";
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(group.openid));
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="forwardGroupTarget" data-fg="bilibili" data-openid="' + escapeHtml(String(group.openid)) + '"' + (fgTargetEnabled(fg, group.openid) ? " checked" : "") + '></td>' +
      '<td>' + (name ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(name) + '</div>' : '') + '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(account) + '</td>' +
      '<td>' + renderIdCell(group.openid) + '</td>' +
      '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon" data-kind="groupEdit" data-id="' + escapeHtml(String(group.openid)) + '" title="编辑群资料" aria-label="编辑群资料">' + SVG_PEN + '</button><button class="danger rowicon" data-kind="biliFgGroupRemove" data-id="' + escapeHtml(String(group.openid)) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    groupBody.appendChild(tr);
  });
  syncHeaderAll("bilibiliGroupAllEnabled", groupRows.map(function (group) { return {enabled: fgTargetEnabled(fg, group.openid)}; }), "enabled");

  const baseInput = document.getElementById("bilibiliRsshubBase");
  if (baseInput && document.activeElement !== baseInput) baseInput.value = settings.bilibili_rsshub_base || "";
  const intervalInput = document.getElementById("bilibiliPollInterval");
  if (intervalInput && document.activeElement !== intervalInput) {
    const value = Number(settings.bilibili_poll_interval);
    intervalInput.value = value > 0 ? value : 300;
  }
  const protection = settings.bilibili_protection || {};
  const minInterval = document.getElementById("bilibiliMinSendInterval");
  if (minInterval && document.activeElement !== minInterval) {
    minInterval.value = Number.isFinite(Number(protection.min_send_interval))
      ? Math.max(0, Number(protection.min_send_interval)) : 60;
  }
  const hourlyLimit = document.getElementById("bilibiliHourlyLimit");
  if (hourlyLimit && document.activeElement !== hourlyLimit) {
    hourlyLimit.value = Number(protection.hourly_limit) > 0 ? Number(protection.hourly_limit) : 10;
  }
  const backlogLimit = document.getElementById("bilibiliBacklogLimit");
  if (backlogLimit && document.activeElement !== backlogLimit) {
    backlogLimit.value = Number(protection.backlog_limit) > 0 ? Number(protection.backlog_limit) : 10;
  }
  const overflowPolicy = document.getElementById("bilibiliOverflowPolicy");
  if (overflowPolicy && document.activeElement !== overflowPolicy) {
    overflowPolicy.value = protection.overflow_policy === "oldest" ? "oldest" : "latest";
  }
  renderBilibiliAutoResolveToggle(!!(settings.bilibili_auto_resolve && settings.bilibili_auto_resolve.enabled));
}

function renderBilibiliAutoResolveToggle(on) {
  const btn = document.getElementById("btnBilibiliAutoResolveToggle");
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  btn.setAttribute("aria-checked", on ? "true" : "false");
  btn.innerHTML = pillSwitchMarkup("启用自动解析", on);
}

// ---------------- YouTube → QQ 页面 ----------------
// 来源只有频道：UC 频道 ID 或 @handle。第一期只转发标题、封面和链接，
// 因此筛选类型固定为文字/图片/链接，没有投稿类型维度。
function normalizeYoutubeChannelValue(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (/^UC[0-9A-Za-z_-]{22}$/.test(text)) return text;
  const handlePattern = /^[0-9A-Za-z._-]{3,100}$/;
  if (text.charAt(0) === "@") {
    const handle = text.slice(1).trim();
    return handlePattern.test(handle) ? "@" + handle : "";
  }
  if (/^https?:\/\//i.test(text)) {
    let url;
    try { url = new URL(text); } catch (e) { return ""; }
    if (!/^(?:www\.|m\.)?youtube\.com$/i.test(url.hostname)) return "";
    const segments = url.pathname.split("/").filter(Boolean);
    if (segments.length >= 2 && segments[0].toLowerCase() === "channel") {
      return /^UC[0-9A-Za-z_-]{22}$/.test(segments[1]) ? segments[1] : "";
    }
    if (segments.length && segments[0].charAt(0) === "@") {
      const handle = segments[0].slice(1);
      return handlePattern.test(handle) ? "@" + handle : "";
    }
    if (segments.length >= 2 && /^(?:c|user)$/i.test(segments[0])) {
      return handlePattern.test(segments[1]) ? "@" + segments[1] : "";
    }
    return "";
  }
  return handlePattern.test(text) ? "@" + text : "";
}

function youtubeSourceId(value) {
  const normalized = normalizeYoutubeChannelValue(value);
  return normalized ? "channel:" + normalized : "";
}

function youtubeChannelUrl(value) {
  const normalized = normalizeYoutubeChannelValue(value);
  if (!normalized) return "";
  return normalized.charAt(0) === "@"
    ? "https://www.youtube.com/" + encodeURIComponent(normalized).replace(/%40/i, "@")
    : "https://www.youtube.com/channel/" + encodeURIComponent(normalized);
}

function renderYoutubePage() {
  const sourceBody = document.getElementById("youtubeSourceBody");
  if (!sourceBody || !settings) return;
  renderFgTabs(FG_YOUTUBE);
  const fg = getActiveFg(FG_YOUTUBE);
  const allSources = Array.isArray(settings.youtube_sources) ? settings.youtube_sources : [];
  const sourceRows = orderedForwardingGroupItems(
    fg && fg.sources, allSources, function (source) { return youtubeSourceId(source.value); }
  );
  sourceBody.innerHTML = "";
  if (!sourceRows.length) {
    sourceBody.innerHTML = '<tr class="fgempty-row"><td colspan="5" class="fgempty-msg">还未新增频道，请先「注册频道」，再加入当前转发组。</td></tr>';
  }
  sourceRows.forEach(function (source) {
    const index = allSources.indexOf(source);
    const sourceId = youtubeSourceId(source.value);
    const channelUrl = youtubeChannelUrl(source.value);
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", sourceId);
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="youtubeSource" data-i="' + index + '"' + (source.enabled ? " checked" : "") + '></td>' +
      '<td><span class="chan-name-text">' + escapeHtml(source.name || source.value || "") + '</span></td>' +
      '<td class="mono">' + (channelUrl
        ? '<a class="source-link youtube-source-link" href="' + escapeHtml(channelUrl) + '" target="_blank" rel="noopener noreferrer" title="打开 YouTube 频道">' + escapeHtml(source.value || "") + '</a>'
        : escapeHtml(source.value || "")) + '</td>' +
      '<td style="text-align:right"><div class="table-actions">' +
        '<button class="sec rowicon' + (youtubeSourceFilterActive(source) ? ' filter-active' : '') + '" title="内容筛选" aria-label="内容筛选" data-kind="youtubeSourceFilterEdit">' + SVG_FUNNEL + '</button>' +
        '<button class="sec rowicon" title="编辑名称与来源地址" aria-label="编辑名称与来源地址" data-kind="youtubeSourceEdit">' + SVG_PEN + '</button>' +
        '<button class="danger rowicon" data-kind="youtubeFgSourceRemove" data-id="' + escapeHtml(sourceId) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    sourceBody.appendChild(tr);
  });
  syncHeaderAll("youtubeSourceAllEnabled", sourceRows, "enabled");

  const groupBody = document.getElementById("youtubeGroupBody");
  const allGroups = settings.qq_group_openids || [];
  const groupRows = orderedForwardingGroupItems(
    fg && fg.groups, allGroups, function (group) { return group.openid; }, groupInActiveQQBotWorkspace
  );
  groupBody.innerHTML = "";
  if (!groupRows.length) {
    groupBody.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增群，请加入一个 QQ 接收群。</td></tr>';
  }
  groupRows.forEach(function (group) {
    const index = allGroups.indexOf(group);
    const name = group.name || identityName("groups", group.openid) || "";
    const account = group.qq_id || "";
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(group.openid));
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="forwardGroupTarget" data-fg="youtube" data-openid="' + escapeHtml(String(group.openid)) + '"' + (fgTargetEnabled(fg, group.openid) ? " checked" : "") + '></td>' +
      '<td>' + (name ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(name) + '</div>' : '') + '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(account) + '</td>' +
      '<td>' + renderIdCell(group.openid) + '</td>' +
      '<td style="text-align:right"><div class="table-actions"><button class="sec rowicon" data-kind="groupEdit" data-id="' + escapeHtml(String(group.openid)) + '" title="编辑群资料" aria-label="编辑群资料">' + SVG_PEN + '</button><button class="danger rowicon" data-kind="youtubeFgGroupRemove" data-id="' + escapeHtml(String(group.openid)) + '" title="移出当前转发组" aria-label="移出当前转发组">' + SVG_TRASH + '</button></div></td>';
    groupBody.appendChild(tr);
  });
  syncHeaderAll("youtubeGroupAllEnabled", groupRows.map(function (group) { return {enabled: fgTargetEnabled(fg, group.openid)}; }), "enabled");

  const intervalInput = document.getElementById("youtubePollInterval");
  if (intervalInput && document.activeElement !== intervalInput) {
    const value = Number(settings.youtube_poll_interval);
    intervalInput.value = value > 0 ? value : 300;
  }
}

function youtubeSourceFilterActive(source) {
  return sourceFilterActive(source, "youtubeSource", true);
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

// Keep diagnostic counters visible beside an RSS error, without changing the
// polling state or the existing badge's priority rules.
function appendRssCounters(summary, st, srcText, sourceLabel, includeQueue) {
  if (!document.body.classList.contains("desktop-host")) return;
  const metrics = document.createElement("span");
  metrics.className = "rss-secondary-metrics";
  metrics.textContent = sourceLabel + " " + srcText
    + " · 发现 " + (Number(st.last_new_count) || 0)
    + " · 发送 " + (Number(st.last_sent_count) || 0)
    + (includeQueue ? " · 积压 " + (Number(st.protection_queue_pending) || 0) : "");
  summary.append(metrics);
}

function conciseChainStatusError(st, chain) {
  const name = String(chain || "来源");
  if (!st) return "状态读取失败，请稍后重试。";
  if (st.config_error || st.last_result === "config_failed") return "账号配置异常，请检查配置并重启机器人。";
  if (st.protection_error || st.last_result === "protection_failed") return "投递队列异常，请检查维护与备份。";
  if (st.last_result === "parse_failed") return "RSS 内容格式异常，请检查来源地址。";
  const raw = String(st.protection_error || st.last_error || st.msg || "").toLowerCase();
  if (/\b429\b|rate.?limit|too many requests/.test(raw)) return "请求受限；稍后重试。";
  if (/\b404\b/.test(raw) && name === "YouTube") {
    return st.data_api_enabled === false
      ? "RSS 失败；可配置 API Key。"
      : "请求失败；检查频道或 API Key。";
  }
  if (/timeout|timed out|connect(?:ion|error)|disconnected|protocolerror|network|代理|proxy|dns/.test(raw)) return "网络失败；检查代理。";
  if (name === "YouTube" && st.last_result === "fetch_failed") {
    return st.data_api_enabled === false
      ? "RSS 失败；可试 API Key。"
      : "请求失败；检查 Key/配额。";
  }
  if (st.last_result === "fetch_failed") {
    return name === "X" || name === "Bilibili"
      ? "来源请求失败，请检查 RSSHub 地址与网络。"
      : name + " 请求失败，请检查来源与网络。";
  }
  if (st.ok === false) return "状态接口不可用，请检查机器人连接。";
  return name + " 检查失败，请查看运行日志。";
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
    const counts = routedEnabledTotal("x_rss_sources", "x_forwarding_groups", "sources", function (item) { return xSourceKey(item.source); });
    srcText = counts.enabled + " / " + counts.total;
  }

  // 徽章：真实运行状态（写死「轮询中」已废弃）。
  // 优先级：bot 停止 > bot 接口不可用 > 轮询循环未启动（等 QQ） >
  // QQ 掉线（RSS 可能仍正常，必须与「运行中」区分）> 拉取/解析失败 > 运行中
  let badgeText, badgeCls, failed = false;
  if (!botUp) {
    badgeText = "未运行";
    badgeCls = "badge neutral";
  } else if (st.ok === false) {
    const configFailed = !!st.config_error || st.last_result === "config_failed";
    badgeText = configFailed ? "账号配置读取失败" : "状态不可用";
    badgeCls = configFailed ? "badge off" : "badge warn";
    failed = configFailed;
  } else if (!st.running) {
    // 轮询任务在应用启动时创建；此分支表示任务尚未运行
    badgeText = "等待 QQ 连接";
    badgeCls = "badge warn";
  } else if (st.protection_error) {
    badgeText = "投递保护异常";
    badgeCls = "badge off";
    failed = true;
  } else if (!st.qq_connected) {
    if (!pollTs) badgeText = "等待 QQ 连接";
    else if (st.last_result === "ok") badgeText = "RSS 正常 · QQ 未连接";
    else if (st.last_result === "pending") badgeText = "等待 RSSHub 启动 · QQ 未连接";
    else badgeText = "RSSHub 请求失败 · QQ 未连接";
    badgeCls = "badge warn";
  } else if (st.last_result === "pending") {
    // 启动宽限期内 RSSHub 尚未就绪，不算失败
    badgeText = "等待 RSSHub 启动";
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
  const errorHint = conciseChainStatusError(st, "X");
  badge.title = failed || st.ok === false ? errorHint : badgeText;
  setStatusCardAmbient(badge, badgeCls === "badge on");
  const overviewX = document.getElementById("overviewX");
  setOverviewRouteStatus(overviewX, badgeText, "X RSS",
    badgeCls.includes("neutral") ? "neutral" : badgeCls.includes("on") ? "on" : badgeCls.includes("off") ? "off" : "warn");

  // 摘要行：正常态一行概况（订阅源 / 上次轮询 / 发现 / 发送）；
  // 异常态只突出错误原因，不再堆叠正常信息。
  // 「发送失败」仅在有失败时才出现，属于异常信息。
  const summary = document.getElementById("xrssSummary");
  if (summary) {
    const sendFailed = Number(st.last_failed_count) || 0;
    const err = failed || sendFailed > 0;
    summary.classList.toggle("err", err || st.ok === false);
    summary.title = failed || st.ok === false ? errorHint : "";
    if (!botUp) {
      summary.textContent = "—";
    } else if (failed) {
      const errorLabel = (st.config_error || st.last_result === "config_failed") ? "账号配置读取失败" : st.protection_error ? "投递保护异常"
        : st.last_result === "parse_failed" ? "解析异常" : "请求异常";
      summary.innerHTML = '<strong>' + errorLabel + '</strong><span>' + escapeHtml(errorHint) + '</span><small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    } else if (st.ok === false) {
      summary.title = errorHint;
      summary.innerHTML = '<strong>请求异常</strong><span>' + escapeHtml(errorHint) + '</span><small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    } else {
      summary.innerHTML = '<span>订阅源 ' + escapeHtml(srcText) + '</span>'
        + '<small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>'
        + '<span>发现 ' + (Number(st.last_new_count) || 0) + ' 条</span>'
        + '<span>发送 ' + (Number(st.last_sent_count) || 0) + ' 条</span>'
        + '<span>积压 ' + (Number(st.protection_queue_pending) || 0) + ' 条</span>'
        + (sendFailed > 0 ? '<span class="rss-send-fail">发送失败 ' + sendFailed + ' 条</span>' : '');
    }
    if (failed || st.ok === false) appendRssCounters(summary, st, srcText, "订阅源", true);
  }
  renderChainStatusShortcut();
}

async function refreshXStatus() {
  const owner = activeQQBotId;
  const epoch = workspaceEpoch;
  try {
    const data = await jget("/api/xrss/status");
    if (owner === activeQQBotId && epoch === workspaceEpoch) renderXStatus(data);
  } catch (err) { /* 面板瞬断：保留当前显示，下一轮覆盖 */ }
}

function renderBilibiliStatus(st) {
  const badge = document.getElementById("bilibiliBadge");
  if (!badge || !st) return;
  const botUp = st.bot_running === true;
  const pollTs = Number(st.last_poll_ts) || 0;
  const pollText = pollTs ? xrssFmtTime(pollTs) : "—";
  let enabledSources = st.sources_enabled;
  let srcText = "—";
  if (st.sources_total !== undefined) {
    srcText = st.sources_enabled + " / " + st.sources_total;
  } else if (settings && Array.isArray(settings.bilibili_sources)) {
    const counts = routedEnabledTotal("bilibili_sources", "bilibili_forwarding_groups", "sources", function (item) { return item.id || bilibiliSourceId(item.kind, item.value); });
    enabledSources = counts.enabled;
    srcText = counts.enabled + " / " + counts.total;
  }

  let badgeText, badgeCls, failed = false;
  if (!botUp) {
    badgeText = "未运行";
    badgeCls = "badge neutral";
  } else if (st.ok === false) {
    const configFailed = !!st.config_error || st.last_result === "config_failed";
    badgeText = configFailed ? "账号配置读取失败" : "状态不可用";
    badgeCls = configFailed ? "badge off" : "badge warn";
    failed = configFailed;
  } else if (!st.running) {
    badgeText = "等待 QQ 连接";
    badgeCls = "badge warn";
  } else if (st.protection_error || st.last_result === "protection_failed") {
    badgeText = "投递保护异常";
    badgeCls = "badge off";
    failed = true;
  } else if (enabledSources === 0) {
    badgeText = "无启用来源";
    badgeCls = "badge neutral";
  } else if (!st.qq_connected) {
    if (!pollTs) badgeText = "等待 QQ 连接";
    else if (st.last_result === "ok") badgeText = "RSS 正常 · QQ 未连接";
    else if (st.last_result === "pending") badgeText = "等待 RSSHub 启动 · QQ 未连接";
    else badgeText = "RSSHub 请求失败 · QQ 未连接";
    badgeCls = "badge warn";
  } else if (st.last_result === "pending") {
    // 启动宽限期内 RSSHub 尚未就绪，不算失败
    badgeText = "等待 RSSHub 启动";
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
  const errorHint = conciseChainStatusError(st, "Bilibili");
  badge.title = failed || st.ok === false ? errorHint
    : badgeText === "无启用来源" ? "请在启用的转发组中加入已启用的来源和 QQ 接收群。" : badgeText;
  setStatusCardAmbient(badge, badgeCls === "badge on");
  const overviewBilibili = document.getElementById("overviewBilibili");
  setOverviewRouteStatus(overviewBilibili, badgeText, "Bilibili RSS",
    badgeCls.includes("neutral") ? "neutral" : badgeCls.includes("on") ? "on" : badgeCls.includes("off") ? "off" : "warn");

  const summary = document.getElementById("bilibiliSummary");
  if (summary) {
    const sendFailed = Number(st.last_failed_count) || 0;
    summary.classList.toggle("err", failed || sendFailed > 0 || st.ok === false);
    summary.title = failed || st.ok === false ? errorHint : "";
    if (!botUp) {
      summary.textContent = "—";
    } else if (failed) {
      const configFailed = !!st.config_error || st.last_result === "config_failed";
      const protectionFailed = !configFailed && (!!st.protection_error || st.last_result === "protection_failed");
      const errorLabel = configFailed ? "配置异常" : protectionFailed ? "队列异常" : (st.last_result === "parse_failed" ? "解析异常" : "原因");
      summary.innerHTML = '<strong>' + errorLabel + '</strong><span>' + escapeHtml(errorHint) + '</span><small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    } else if (st.ok === false) {
      summary.title = errorHint;
      summary.innerHTML = '<strong>请求异常</strong><span>' + escapeHtml(errorHint) + '</span><small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    } else {
      summary.innerHTML = '<span>来源 ' + escapeHtml(srcText) + '</span>'
        + '<small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>'
        + '<span>发现 ' + (Number(st.last_new_count) || 0) + ' 条</span>'
        + '<span>发送 ' + (Number(st.last_sent_count) || 0) + ' 条</span>'
        + '<span>积压 ' + (Number(st.protection_queue_pending) || 0) + ' 条</span>'
        + (sendFailed > 0 ? '<span class="rss-send-fail">发送失败 ' + sendFailed + ' 条</span>' : '');
    }
    if (failed || st.ok === false) appendRssCounters(summary, st, srcText, "来源", true);
  }
  renderChainStatusShortcut();
}

async function refreshBilibiliStatus() {
  const owner = activeQQBotId;
  const epoch = workspaceEpoch;
  try {
    const data = await jget("/api/bilibili/status");
    if (owner === activeQQBotId && epoch === workspaceEpoch) renderBilibiliStatus(data);
  } catch (err) { /* 面板瞬断：保留当前显示，下一轮覆盖 */ }
}

function renderYoutubeStatus(st) {
  const badge = document.getElementById("youtubeBadge");
  if (!badge || !st) return;
  latestYoutubeStatus = st;
  const botUp = st.bot_running === true;
  const pollTs = Number(st.last_poll_ts) || 0;
  const pollText = pollTs ? xrssFmtTime(pollTs) : "—";
  const pollTotal = Number(st.poll_progress?.total) || 0;
  const pollCompleted = Array.isArray(st.poll_progress?.completed) ? st.poll_progress.completed.filter(Boolean) : [];
  let enabledSources = st.sources_enabled;
  let srcText = "—";
  if (st.sources_total !== undefined) {
    srcText = st.sources_enabled + " / " + st.sources_total;
  } else if (settings && Array.isArray(settings.youtube_sources)) {
    const counts = routedEnabledTotal("youtube_sources", "youtube_forwarding_groups", "sources", function (item) { return item.id || youtubeSourceId(item.value); });
    enabledSources = counts.enabled;
    srcText = counts.enabled + " / " + counts.total;
  }

  let badgeText, badgeCls, failed = false, progressText = "";
  if (!botUp) {
    badgeText = "未运行";
    badgeCls = "badge neutral";
  } else if (st.ok === false) {
    const configFailed = !!st.config_error || st.last_result === "config_failed";
    badgeText = configFailed ? "账号配置读取失败" : "状态不可用";
    badgeCls = configFailed ? "badge off" : "badge warn";
    failed = configFailed;
  } else if (!st.running) {
    badgeText = "等待轮询启动";
    badgeCls = "badge warn";
  } else if (st.protection_error || st.last_result === "protection_failed") {
    badgeText = "投递保护异常";
    badgeCls = "badge off";
    failed = true;
  } else if (!st.qq_connected) {
    if (!pollTs) badgeText = "等待 QQ 连接";
    else if (st.last_result === "ok") badgeText = "YouTube 正常 · QQ 未连接";
    else badgeText = "YouTube 请求失败 · QQ 未连接";
    badgeCls = "badge warn";
  } else if (pollTotal > 0) {
    badgeText = "正在检查频道";
    badgeCls = "badge warn";
    progressText = "已完成 " + pollCompleted.length + "/" + pollTotal + " 个频道";
  } else if (st.last_result === "fetch_failed") {
    badgeText = "YouTube 请求失败";
    badgeCls = "badge off";
    failed = true;
  } else if (st.last_result === "parse_failed") {
    badgeText = "RSS 解析失败";
    badgeCls = "badge off";
    failed = true;
  } else if (enabledSources === 0) {
    badgeText = "无启用来源";
    badgeCls = "badge neutral";
  } else if (!pollTs) {
    badgeText = "等待首次检查结果";
    badgeCls = "badge warn";
  } else {
    badgeText = "运行中";
    badgeCls = "badge on";
  }
  badge.textContent = badgeText;
  badge.className = badgeCls;
  const errorHint = conciseChainStatusError(st, "YouTube");
  badge.title = failed || st.ok === false ? errorHint : badgeText;
  setStatusCardAmbient(badge, badgeCls === "badge on");
  const overviewYoutube = document.getElementById("overviewYoutube");
  setOverviewRouteStatus(overviewYoutube, badgeText, progressText || "YouTube RSS",
    badgeCls.includes("neutral") ? "neutral" : badgeCls.includes("on") ? "on" : badgeCls.includes("off") ? "off" : "warn");

  const summary = document.getElementById("youtubeSummary");
  if (summary) {
    const sendFailed = Number(st.last_failed_count) || 0;
    summary.classList.toggle("err", failed || (!progressText && sendFailed > 0) || st.ok === false);
    summary.title = failed || st.ok === false ? errorHint : "";
    if (!botUp) {
      summary.textContent = "—";
    } else if (progressText) {
      const resultLabels = {
        ok: "正常",
        fetch_failed: "请求失败",
        parse_failed: "RSS 解析失败",
        protection_failed: "投递保护异常",
      };
      const failedCount = pollCompleted.filter(item => item.result !== "ok").length;
      const visibleResults = pollCompleted.slice(-3);
      const otherCount = pollCompleted.length - visibleResults.length;
      summary.innerHTML = '<strong>' + escapeHtml(progressText) + '</strong>'
        + (failedCount ? '<span class="rss-send-fail">检查失败 ' + failedCount + ' 个</span>' : '')
        + visibleResults.map(function (item) {
          const label = resultLabels[item.result] || "检查结束";
          return '<span>' + escapeHtml(item.name || item.id || "未命名频道") + ' · ' + escapeHtml(label) + '</span>';
        }).join("")
        + (otherCount ? '<span>还有 ' + otherCount + ' 个频道已完成</span>' : '');
    } else if (failed) {
      const configFailed = !!st.config_error || st.last_result === "config_failed";
      const protectionFailed = !configFailed && (!!st.protection_error || st.last_result === "protection_failed");
      const errorLabel = configFailed ? "账号配置读取失败" : protectionFailed ? "投递保护异常" : (st.last_result === "parse_failed" ? "解析异常" : "请求异常");
      summary.innerHTML = '<strong>' + errorLabel + '</strong><span>' + escapeHtml(errorHint) + '</span><small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    } else if (st.ok === false) {
      summary.title = errorHint;
      summary.innerHTML = '<strong>请求异常</strong><span>' + escapeHtml(errorHint) + '</span><small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    } else {
      summary.innerHTML = '<span>来源 ' + escapeHtml(srcText) + '</span>'
        + '<small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>'
        + '<span>发现 ' + (Number(st.last_new_count) || 0) + ' 条</span>'
        + '<span>发送 ' + (Number(st.last_sent_count) || 0) + ' 条</span>'
        + '<span>积压 ' + (Number(st.protection_queue_pending) || 0) + ' 条</span>'
        + (sendFailed > 0 ? '<span class="rss-send-fail">发送失败 ' + sendFailed + ' 条</span>' : '');
    }
    if (failed || st.ok === false) appendRssCounters(summary, st, srcText, "来源", true);
  }
  renderChainStatusShortcut();
  renderPageCredentialShortcut();
}

function renderSteamStatus(st) {
  const summary = document.getElementById("steamStatus");
  const badge = document.getElementById("steamBadge");
  const overview = document.getElementById("overviewSteam");
  const health = Array.isArray(st.source_health) ? st.source_health.filter(Boolean) : [];
  const firstSourceError = health.find(item => item.error)?.error;
  const hasError = !!(st.ok === false || st.last_error || firstSourceError);
  const botUp = st.bot_running === true || (st.bot_running === undefined && st.running === true);
  const completedAt = Number(st.last_completed_poll_ts ?? st.last_poll_ts) || 0;
  let label = "运行中", state = "on", scanProgress = "";
  if (st.bot_running === false || (!botUp && !hasError)) {
    label = "未运行";
    state = "neutral";
  } else if (st.qq_connected === false) {
    label = "等待 QQ 连接";
    state = "warn";
  } else if (st.scanning) {
    label = completedAt ? "正在检查" : "正在首次检查";
    state = "warn";
    const total = Number(st.scan_total) || 0;
    if (total > 0) {
      const scanned = Math.max(0, Math.min(total, Number(st.scanned) || 0));
      scanProgress = "已检查 " + scanned + "/" + total + " 个游戏";
    }
  } else if (hasError) {
    const partial = firstSourceError && health.some(item => !item.error);
    label = partial ? "部分来源失败" : st.running === undefined ? "状态不可用" : "检查失败";
    state = "off";
  } else if (st.sources_enabled === 0) {
    label = "无启用来源";
    state = "neutral";
  } else if (!completedAt) {
    label = "等待首次检查";
    state = "warn";
  }

  const errorHint = state === "off" ? conciseChainStatusError({
    ...st, last_error: st.last_error || firstSourceError, last_result: st.running === true ? "fetch_failed" : "",
  }, "Steam") : "";
  const source = st.scanning ? String(st.current_source || "") : "";
  const tooltip = [label, source, scanProgress, errorHint].filter(Boolean).join(" · ");
  setOverviewRouteStatus(overview, label, scanProgress || "Steam 游戏与商店监控", state);
  if (overview) overview.title = tooltip;
  if (badge) {
    badge.textContent = label;
    badge.className = "badge " + state;
    badge.title = tooltip;
  }
  if (summary) {
    summary.classList.toggle("err", state === "off");
    summary.dataset.state = state;
    summary.title = tooltip;
    const pollText = completedAt ? xrssFmtTime(completedAt) : "—";
    const lastPoll = '<small class="xrss-time">上次轮询 ' + escapeHtml(pollText) + '</small>';
    if (label === "未运行") {
      summary.textContent = "—";
    } else if (errorHint) {
      summary.innerHTML = '<strong>' + escapeHtml(label) + '</strong><span>' + escapeHtml(errorHint) + '</span>' + lastPoll;
    } else {
      const scanDetails = [source, scanProgress].filter(Boolean).join(" · ");
      summary.innerHTML = (scanDetails ? '<span>' + escapeHtml(scanDetails) + '</span>' : '')
        + '<span>来源 ' + (Number(st.sources_enabled) || 0) + ' / ' + (Number(st.sources_total) || 0) + '</span>'
        + lastPoll
        + '<span>发现 ' + (Number(st.last_new_count) || 0) + ' 条</span>'
        + '<span>发送 ' + (Number(st.last_sent_count) || 0) + ' 条</span>'
        + '<span>积压 ' + (Number(st.protection_queue_pending) || 0) + ' 条</span>';
    }
  }
  renderChainStatusShortcut();
}

const watchedChainStatusRequests = new Map();
function refreshWatchedChainStatus(service, render, force) {
  const owner = currentQQBotId();
  const epoch = workspaceEpoch;
  const previous = watchedChainStatusRequests.get(service);
  if (!force && previous?.owner === owner && previous.epoch === epoch) return previous.promise;
  const request = {owner, epoch};
  watchedChainStatusRequests.set(service, request);
  request.promise = jget("/api/" + service + "/status?bot_id=" + encodeURIComponent(owner))
    .catch(() => ({ok: false, bot_running: true}))
    .then(function (data) {
      if (watchedChainStatusRequests.get(service) === request && owner === currentQQBotId() && epoch === workspaceEpoch) {
        render(data || {ok: false, bot_running: true});
      }
    }).finally(function () {
      if (watchedChainStatusRequests.get(service) === request) watchedChainStatusRequests.delete(service);
    });
  return request.promise;
}

function refreshYoutubeStatus(force = false) {
  return refreshWatchedChainStatus("youtube", renderYoutubeStatus, force);
}

function refreshSteamStatus(force = false) {
  return refreshWatchedChainStatus("steam", renderSteamStatus, force);
}

function refreshWatchedChainStatuses(force = false) {
  return Promise.all([refreshYoutubeStatus(force), refreshSteamStatus(force)]);
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
  if (kind === "bilibili") {
    const source = ((settings && settings.bilibili_sources) || []).find(function (item) {
      return String(item.id || (String(item.kind || "up") + ":" + String(item.value || ""))) === id;
    });
    return {name: "Bilibili", id: (source && source.value) || id || "未知来源"};
  }
  if (kind === "youtube") {
    const source = ((settings && settings.youtube_sources) || []).find(function (item) {
      return youtubeSourceId(item.value) === id;
    });
    return {name: "YouTube", id: (source && (source.name || source.value)) || id || "未知频道"};
  }
  if (kind === "steam") return {name: "Steam", id: "自动投递"};
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

let latestMessageTraceData = null;

function renderMessageTrace(data) {
  latestMessageTraceData = data;
  renderRecentMessage(data);
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

function renderRecentMessage(data) {
  const badge = document.getElementById("recentMessageResult");
  const text = document.getElementById("recentMessageText");
  const time = document.getElementById("recentMessageTime");
  const entry = data && data.ok !== false && Array.isArray(data.entries) ? data.entries[0] : null;
  if (!entry) {
    badge.textContent = "暂无投递";
    badge.className = "badge neutral";
    text.textContent = data && data.ok === false ? "机器人未运行，暂时无法读取投递记录" : "暂无消息记录";
    time.textContent = "";
    return;
  }
  const result = messageTraceResult(entry);
  const source = messageTraceSource(entry);
  badge.textContent = result.text;
  badge.className = "badge " + (result.cls === "ok" ? "on" : result.cls === "failed" ? "off" : "warn");
  const preview = String(entry.preview || source.id || "消息投递");
  const summaryText = recentMessagePreview(preview);
  if (activePage === "overview") {
    const sourceLabel = document.createElement("span");
    sourceLabel.className = "recent-message-source";
    sourceLabel.textContent = source.name;
    const separator = document.createElement("span");
    separator.className = "recent-message-separator";
    separator.textContent = " · ";
    const summary = document.createElement("span");
    summary.className = "recent-message-preview";
    summary.textContent = summaryText;
    text.replaceChildren(sourceLabel, separator, summary);
  } else {
    text.textContent = source.name + " · " + summaryText;
  }
  text.title = source.name + " · " + preview;
  time.textContent = messageTraceTime(entry.last_at);
}

function recentMessagePreview(value) {
  return String(value || "").replace(/https?:\/\/[^\s]+/gi, function (url) {
    const suffixMatch = url.match(/[),.;!?，。！？）】》]+$/u);
    const suffix = suffixMatch ? suffixMatch[0] : "";
    const cleanUrl = suffix ? url.slice(0, -suffix.length) : url;
    try {
      const host = new URL(cleanUrl).hostname.replace(/^www\./i, "");
      return "链接 · " + host + suffix;
    } catch (err) {
      return "链接" + suffix;
    }
  });
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
  const owner = activeQQBotId;
  const epoch = workspaceEpoch;
  try {
    const data = await jget("/api/message-trace?limit=50");
    if (owner === activeQQBotId && epoch === workspaceEpoch) renderMessageTrace(data);
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
    settings.x_rss_sources.push({
      source: handle, name: nameInput.value.trim(), enabled: true,
      translation_enabled: true, translation_append_original: true, translation_show_datetime: false,
    });
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function bilibiliSourceId(kind, value) {
  return String(kind || "up") + ":" + String(value || "").trim();
}

function openBilibiliSourceRegister() {
  const body = document.getElementById("bilibiliSourceModalBody");
  body.innerHTML = "";
  const form = document.createElement("div");
  form.className = "bilibili-source-form-grid";

  const kindWrap = document.createElement("div");
  kindWrap.className = "bilibili-source-field";
  const kindLabel = document.createElement("div");
  kindLabel.className = "bilibili-source-field-label";
  kindLabel.textContent = "来源范围";
  const kind = document.createElement("select");
  kind.innerHTML = '<option value="up">UP 主投稿（UID）</option><option value="tag">标签搜索</option><option value="keyword">关键词搜索</option>';
  kindWrap.appendChild(kindLabel);
  kindWrap.appendChild(kind);
  form.appendChild(kindWrap);
  const valueWrap = document.createElement("div");
  valueWrap.className = "bilibili-source-field";
  const valueLabel = document.createElement("div");
  valueLabel.className = "bilibili-source-field-label";
  const valueInput = document.createElement("input");
  valueInput.type = "text";
  valueInput.autocomplete = "off";
  valueInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      document.getElementById("bilibiliSourceSave").click();
    }
  });
  valueWrap.appendChild(valueLabel);
  valueWrap.appendChild(valueInput);
  form.appendChild(valueWrap);
  function updateValuePrompt() {
    const prompts = {
      up: ["UP 主 UID", "输入数字 UID"],
      tag: ["标签名称", "输入 Bilibili 标签名称"],
      keyword: ["搜索关键词", "输入搜索关键词"],
    };
    const prompt = prompts[kind.value] || prompts.up;
    valueLabel.textContent = prompt[0];
    valueInput.placeholder = prompt[1];
  }
  kind.addEventListener("change", updateValuePrompt);
  updateValuePrompt();

  body.appendChild(form);
  async function saveSource() {
    const type = kind.value;
    const value = valueInput.value.trim();
    if (!value) { showAlert("注册 Bilibili 来源", "请填写对应的 UID、标签名称或关键词"); return; }
    if (type === "up" && !/^\d+$/.test(value)) {
      showAlert("注册 Bilibili 来源", "UP 主来源需要填写数字 UID");
      return;
    }
    if (!Array.isArray(settings.bilibili_sources)) settings.bilibili_sources = [];
    const duplicate = settings.bilibili_sources.some(function (source) {
      return String(source.kind || "up") === type && String(source.value || "").toLowerCase() === value.toLowerCase();
    });
    if (duplicate) { showAlert("注册 Bilibili 来源", "该来源已经在列表中"); return; }
    settings.bilibili_sources.push({
      id: bilibiliSourceId(type, value),
      kind: type,
      value: value,
      enabled: true,
      filter_bilibili_post_types: ["视频"],
      translation_enabled: false,
      translation_append_original: true,
      translation_show_datetime: false,
    });
    closeBilibiliSourceModal();
    await saveAndReload();
  }
  document.getElementById("bilibiliSourceModalTitle").textContent = "注册 Bilibili 来源";
  document.getElementById("bilibiliSourceSave").onclick = saveSource;
  document.getElementById("bilibiliSourceModal").style.display = "flex";
}

function closeBilibiliSourceModal() {
  document.getElementById("bilibiliSourceModal").style.display = "none";
}

function openYoutubeSourceRegister() {
  const body = document.getElementById("youtubeSourceModalBody");
  body.innerHTML = "";
  const form = document.createElement("div");
  form.className = "bilibili-source-form-grid";

  const valueWrap = document.createElement("div");
  valueWrap.className = "bilibili-source-field";
  const valueLabel = document.createElement("div");
  valueLabel.className = "bilibili-source-field-label";
  valueLabel.textContent = "频道链接、用户名或频道 ID";
  const valueInput = document.createElement("input");
  valueInput.type = "text";
  valueInput.autocomplete = "off";
  valueInput.placeholder = "例如：频道链接或 @用户名";
  valueWrap.appendChild(valueLabel);
  valueWrap.appendChild(valueInput);
  form.appendChild(valueWrap);

  const nameWrap = document.createElement("div");
  nameWrap.className = "bilibili-source-field";
  const nameLabel = document.createElement("div");
  nameLabel.className = "bilibili-source-field-label";
  nameLabel.textContent = "显示名称（可选）";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.autocomplete = "off";
  nameInput.placeholder = "留空则使用频道地址";
  nameWrap.appendChild(nameLabel);
  nameWrap.appendChild(nameInput);
  form.appendChild(nameWrap);

  body.appendChild(form);

  [valueInput, nameInput].forEach(function (input) {
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        document.getElementById("youtubeSourceSave").click();
      }
    });
  });
  async function saveSource() {
    const value = normalizeYoutubeChannelValue(valueInput.value);
    if (!value) {
      showAlert("注册 YouTube 频道", "请输入有效的频道主页链接、@开头的频道用户名或频道 ID");
      return;
    }
    if (!Array.isArray(settings.youtube_sources)) settings.youtube_sources = [];
    const sourceId = youtubeSourceId(value);
    const duplicate = settings.youtube_sources.some(function (source) {
      return String(source.id || youtubeSourceId(source.value)) === sourceId;
    });
    if (duplicate) { showAlert("注册 YouTube 频道", "该频道已经在列表中"); return; }
    settings.youtube_sources.push({
      id: sourceId,
      kind: "channel",
      value: value,
      name: nameInput.value.trim() || value,
      enabled: true,
      translation_enabled: true,
      translation_append_original: true,
      translation_show_datetime: false,
    });
    closeYoutubeSourceModal();
    await saveAndReload();
  }
  document.getElementById("youtubeSourceModalTitle").textContent = "注册 YouTube 频道";
  document.getElementById("youtubeSourceModalContext").textContent = "填写频道主页链接，或输入 @开头的频道用户名";
  document.getElementById("youtubeSourceSave").onclick = saveSource;
  document.getElementById("youtubeSourceModal").style.display = "flex";
  valueInput.focus();
}

function closeYoutubeSourceModal() {
  document.getElementById("youtubeSourceModal").style.display = "none";
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

function renderIdCell(value) {
  const id = String(value || "");
  const safe = escapeHtml(id);
  const characters = Array.from(id);
  const tailLength = characters.length > 4 ? 4 : 1;
  const prefix = escapeHtml(characters.slice(0, -tailLength).join(""));
  const suffix = escapeHtml(characters.slice(-tailLength).join(""));
  return '<div class="id-cell">' +
    '<span class="mono id-value" role="button" tabindex="0" data-copy-value="' + safe + '" aria-label="' + safe + '，右键复制；聚焦后按 Enter 或空格复制"><span class="middle-value-prefix">' + prefix + '</span><span class="middle-value-suffix">' + suffix + '</span></span>' +
    '</div>';
}

async function copyTextToClipboard(value) {
  const text = String(value || "");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const previousFocus = document.activeElement;
  const field = document.createElement("textarea");
  field.value = text;
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  let copied = false;
  try {
    field.select();
    copied = document.execCommand("copy");
  } finally {
    field.remove();
    if (previousFocus && previousFocus.isConnected && previousFocus !== document.body) {
      previousFocus.focus({preventScroll: true});
    }
  }
  if (!copied) throw new Error("复制失败");
}

async function copyForwardingValue(control) {
  try {
    await copyTextToClipboard(control.dataset.copyValue);
    if (window.showWRelayCopyFeedback) window.showWRelayCopyFeedback(control, "已复制");
  } catch (error) {
    if (window.showWRelayCopyFeedback) window.showWRelayCopyFeedback(control, "复制失败");
  }
}

// ---------------- 表格行拖拽排序（鼠标事件实现 + 数据数组持久化） ----------------
// 作用区域：Discord 频道/接收群与授权用户，以及 X、Bilibili、YouTube 当前转发组的来源/接收群。
// 拖拽重排对应的全局列表或转发组成员数组，再通过 saveAndReload() 持久化；
// 行只能在同一表格内排序。
let dndState = null;   // {body, tr, id, startX, startY} —— 正在拖拽的源行

document.addEventListener("mousedown", function (e) {
  const handle = e.target.closest(".drag-handle");
  if (!handle) return;                                    // 只允许按住手柄拖拽
  const tr = handle.closest("tr[data-id]");
  if (!tr) return;
  const body = tr.parentElement;
  if (!body || !body.id) return;
  if (body.id === "steamSourceBody" || body.id === "steamGroups") return; // Steam 两张表由草稿保存器处理排序
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
  if (bodyId === "scheduledGroupMessageList" || bodyId === "scheduledGroupTargetList") {
    const group = activeScheduledMessageGroup();
    if (!group) return false;
    if (bodyId === "scheduledGroupMessageList") {
      const messages = group.messages || [];
      const byId = new Map(messages.map(message => [String(message.id), message]));
      if (byId.size !== messages.length || new Set(newIds.map(String)).size !== messages.length ||
          newIds.length !== messages.length || !newIds.every(id => byId.has(String(id)))) return false;
      group.messages = newIds.map(id => byId.get(String(id)));
    } else {
      if (group.target_mode !== "groups") return false;
      const targets = group.target_groups || [];
      if (targets.length !== newIds.length || !newIds.every(function (id) { return targets.map(String).includes(String(id)); })) return false;
      group.target_groups = newIds.map(String);
    }
    settings.scheduled_message = Object.assign({}, settings.scheduled_message || {}, {messages: [], message_groups: scheduledMessageGroupsDraft});
    return true;
  }
  if (bodyId === "xSourceBody" || bodyId === "xXGroupBody") {
    // X 转发组成员排序：直接重排当前 X 组的 sources/groups
    // （只影响本组内的显示顺序，不重排全局注册实体）
    const xfg = getActiveFg(FG_X);
    if (!xfg) return false;
    const field = bodyId === "xSourceBody" ? "sources" : "groups";
    const members = (xfg[field] || []).map(String);
    const visibleMembers = bodyId === "xXGroupBody"
      ? members.filter(function (id) {
        const group = (settings.qq_group_openids || []).find(function (item) {
          return String(item.openid) === id;
        });
        return groupInActiveQQBotWorkspace(group);
      })
      : members;
    if (visibleMembers.length !== newIds.length) return false;
    const mset = {};
    visibleMembers.forEach(function (id) { mset[id] = true; });
    if (!newIds.every(function (id) { return mset[String(id)]; })) return false;
    if (bodyId === "xXGroupBody") {
      let index = 0;
      xfg[field] = members.map(function (id) {
        return mset[id] ? String(newIds[index++]) : id;
      });
    } else {
      xfg[field] = newIds.map(String);
    }
    return true;
  }
  if (bodyId === "bilibiliSourceBody" || bodyId === "bilibiliGroupBody") {
    const bfg = getActiveFg(FG_BILIBILI);
    if (!bfg) return false;
    const field = bodyId === "bilibiliSourceBody" ? "sources" : "groups";
    const members = (bfg[field] || []).map(String);
    const visibleMembers = bodyId === "bilibiliGroupBody"
      ? members.filter(function (id) {
        const group = (settings.qq_group_openids || []).find(function (item) {
          return String(item.openid) === id;
        });
        return groupInActiveQQBotWorkspace(group);
      })
      : members;
    if (visibleMembers.length !== newIds.length) return false;
    const mset = {};
    visibleMembers.forEach(function (id) { mset[id] = true; });
    if (!newIds.every(function (id) { return mset[String(id)]; })) return false;
    if (bodyId === "bilibiliGroupBody") {
      let index = 0;
      bfg[field] = members.map(function (id) {
        return mset[id] ? String(newIds[index++]) : id;
      });
    } else {
      bfg[field] = newIds.map(String);
    }
    return true;
  }
  if (bodyId === "youtubeSourceBody" || bodyId === "youtubeGroupBody") {
    // YouTube 转发组成员排序：直接重排当前组的 sources/groups（不动全局注册实体）
    const yfg = getActiveFg(FG_YOUTUBE);
    if (!yfg) return false;
    const field = bodyId === "youtubeSourceBody" ? "sources" : "groups";
    const members = (yfg[field] || []).map(String);
    const visibleMembers = bodyId === "youtubeGroupBody"
      ? members.filter(function (id) {
        const group = (settings.qq_group_openids || []).find(function (item) {
          return String(item.openid) === id;
        });
        return groupInActiveQQBotWorkspace(group);
      })
      : members;
    if (visibleMembers.length !== newIds.length) return false;
    const mset = {};
    visibleMembers.forEach(function (id) { mset[id] = true; });
    if (!newIds.every(function (id) { return mset[String(id)]; })) return false;
    if (bodyId === "youtubeGroupBody") {
      let index = 0;
      yfg[field] = members.map(function (id) {
        return mset[id] ? String(newIds[index++]) : id;
      });
    } else {
      yfg[field] = newIds.map(String);
    }
    return true;
  }
  if (bodyId === "chanBody") {
    arr = settings.discord_channels;
    memberSet = memberIdSet(getActiveFg(FG_DISCORD) && getActiveFg(FG_DISCORD).channels);
  } else if (bodyId === "groupBody") {
    arr = settings.qq_group_openids;
    memberSet = {};
    (getActiveFg(FG_DISCORD) && getActiveFg(FG_DISCORD).groups || []).forEach(function (id) {
      const group = (settings.qq_group_openids || []).find(function (item) {
        return String(item.openid) === String(id);
      });
      if (groupInActiveQQBotWorkspace(group)) memberSet[String(id)] = true;
    });
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
// 作用区域：各链路页的转发组条（.fgtabs）。
// 按住转发组按钮（.fgtab）左右拖动调整顺序；松手后重排对应 settings 转发组数组并
// saveAndReload() 持久化（刷新后保持）。
let fgDnd = null;   // {wrap, btn, startX, startY} —— 正在拖拽的源转发组按钮
function fgCtxByWrap(wrap) {
  if (wrap && wrap.id === "xFgTabs") return FG_X;
  if (wrap && wrap.id === "bilibiliFgTabs") return FG_BILIBILI;
  if (wrap && wrap.id === "youtubeFgTabs") return FG_YOUTUBE;
  if (wrap && wrap.id === "qqFgTabs") return FG_QQ;
  return FG_DISCORD;
}
document.addEventListener("mousedown", function (e) {
  const btn = e.target.closest(".fgtab");
  if (!btn || btn.dataset.kind !== "fgTab") return;         // 只允许拖转发组按钮
  const wrap = btn.closest(".fgtabs");
  const list = btn.closest(".fgtab-list");
  if (!wrap || !list) return;
  if (fgRenameActive) return;                              // 重命名进行中不拖（避免与输入框冲突）
  e.preventDefault();                                      // 防止文本选择干扰拖动
  fgDnd = {wrap: wrap, list: list, btn: btn, startX: e.clientX, startY: e.clientY};
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
  if (!tab || tab.closest(".fgtab-list") !== fgDnd.list || tab === fgDnd.btn) return;
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
  if (targetTab && targetTab.closest(".fgtab-list") === st.list && targetTab !== st.btn) {
    const r = targetTab.getBoundingClientRect();
    const after = e.clientX > r.left + r.width / 2;
    st.list.insertBefore(st.btn, after ? targetTab.nextSibling : targetTab);
  } else if (!targetTab && st.list.lastElementChild && st.list.lastElementChild !== st.btn) {
    st.list.appendChild(st.btn);                            // 拖到条尾空白处
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
  settings[ctx.field] = ordered;
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
  ok.classList.toggle("danger", /删除|清空|移出/.test(okText));
  ok.dataset.saveForm = String(okText === "保存");
  ok.style.display = showOk === false ? "none" : "";
  const cancel = document.getElementById("confirmCancel");
  cancel.hidden = !showCancel;
  cancel.textContent = cancelText || "取消";
}

function showAlert(title, message, opts) {
  scheduledMessageTargetPickerOpen = false;
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const p = document.createElement("div");
  p.style.whiteSpace = "pre-wrap";
  if (opts && opts.emptyState) p.className = "registration-empty-state";
  p.textContent = localizeUserError(message);
  body.appendChild(p);
  setConfirmButtons("确定", false);
  confirmAction = null;
  document.getElementById("confirmModal").style.display = "flex";
}

function showConfirmDlg(title, message, onOk, okText) {
  scheduledMessageTargetPickerOpen = false;
  const alreadyOpen = getComputedStyle(document.getElementById("confirmModal")).display !== "none";
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
  window.dismissWRelayTooltip?.();
  if (alreadyOpen) document.getElementById("confirmCancel").focus();
}

function editUserDetails(user) { editQQDetails(user, false); }
function editGroupDetails(group) { editQQDetails(group, true); }
function editQQDetails(user, isGroup) {
  const openid = String(user.openid);
  const epoch = workspaceEpoch;
  document.getElementById("confirmTitle").textContent = isGroup ? "编辑群资料" : "编辑用户资料";
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  function field(labelText, value, maxLength, inputMode) {
    const label = document.createElement("label");
    label.textContent = labelText;
    label.style.display = "block";
    const input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    input.maxLength = maxLength;
    input.style.width = "100%";
    if (inputMode) input.inputMode = inputMode;
    label.appendChild(input);
    body.appendChild(label);
    return input;
  }
  const name = field("名称", user.name, 100);
  const account = field(isGroup ? "QQ群号" : "QQ 账号", user.qq_id, 20, "numeric");
  setConfirmButtons("保存", true);
  confirmAction = async function () {
    if (epoch !== workspaceEpoch) return;
    const value = account.value.trim();
    if (value && !/^[0-9]+$/.test(value)) {
      showAlert("保存失败", (isGroup ? "QQ群号" : "QQ 账号") + "请填写数字，或留空。");
      return;
    }
    const current = (settings[isGroup ? "qq_group_openids" : "qq_user_openids"] || []).find(function (item) { return String(item.openid) === openid; });
    if (!current) {
      showAlert("保存失败", "该用户已不存在，请刷新后重试。");
      return;
    }
    current.name = name.value.trim();
    current.qq_id = value;
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
  name.focus();
}

function showConfirmSelect(title, items, labelFn, onOkSelected, opts) {
  scheduledMessageTargetPickerOpen = false;
  opts = opts || {};
  const defaultChecked = opts.defaultChecked;
  const groupBy = opts.groupBy || null;
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const rows = [];
  function renderRow(it) {
    const row = document.createElement("label");
    if (opts.cardRows) row.className = "registry-picker-row";
    else row.style.cssText = "display:flex;align-items:center;gap:8px;cursor:pointer;padding:2px 0;";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = typeof defaultChecked === "function" ? !!defaultChecked(it) : defaultChecked !== false;
    cb.style.width = "16px";
    cb.style.height = "16px";
    const span = document.createElement("span");
    span.className = opts.cardRows ? "registry-picker-info" : "mono";
    span.style.flex = "1";
    span.style.minWidth = "0";
    const labelLink = typeof opts.labelLinkFn === "function" ? opts.labelLinkFn(it) : "";
    const detail = opts.cardRows && typeof opts.detailFn === "function" ? String(opts.detailFn(it) || "") : "";
    let displayLabel = String(labelFn(it));
    if (detail && displayLabel.endsWith(detail + ")")) displayLabel = displayLabel.slice(0, -(detail.length + 2)).trim();
    else if (detail && displayLabel.endsWith(detail)) displayLabel = displayLabel.slice(0, -detail.length).replace(/\s*·\s*$/, "").trim();
    if (!displayLabel) displayLabel = detail;
    const name = document.createElement("span");
    if (opts.cardRows) name.className = "registry-picker-name";
    if (labelLink) {
      const link = document.createElement("a");
      link.className = "x-account-link";
      link.href = labelLink;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = displayLabel;
      link.title = "打开 X 主页";
      link.addEventListener("click", function (e) { e.stopPropagation(); });
      name.appendChild(link);
    } else {
      name.textContent = displayLabel;
    }
    name.title = displayLabel;
    span.appendChild(name);
    if (detail && detail !== displayLabel) {
      const secondary = document.createElement("span");
      secondary.className = "registry-picker-detail";
      secondary.textContent = detail;
      secondary.title = detail;
      span.appendChild(secondary);
    }
    row.appendChild(cb);
    row.appendChild(span);
    if (opts.onRowEdit) {
      const edit = document.createElement("button");
      edit.type = "button";
      edit.innerHTML = SVG_PEN;
       edit.className = "sec rowicon registry-edit-icon";
      edit.title = "编辑群资料";
      edit.setAttribute("aria-label", "编辑群资料");
      edit.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); opts.onRowEdit(it); });
      row.appendChild(edit);
    }
    if (opts.onRowDelete) {
      // 删除按钮贴近窗口右边缘；转发组选择窗使用垃圾桶图标。
      const del = document.createElement("button");
      del.type = "button";
      del.innerHTML = opts.rowDeleteIcon || SVG_ORIGINAL_OFF;
      del.className = "danger rowicon";
      del.style.flex = "none";
      del.title = "删除注册记录";
      del.setAttribute("aria-label", "删除注册记录");
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

function openBilibiliProtectionModal() {
  const protection = Object.assign({
    min_send_interval: 60,
    hourly_limit: 10,
    backlog_limit: 10,
    overflow_policy: "latest",
  }, (settings && settings.bilibili_protection) || {});
  const body = document.getElementById("confirmBody");
  if (!body) return;

  body.innerHTML = "";
  const form = document.createElement("div");
  form.className = "bilibili-protection-modal-form";

  function addNumberField(row, id, label, value, unit, min, max) {
    const wrapper = document.createElement("label");
    wrapper.className = "bilibili-protection-modal-field";
    const labelText = document.createElement("span");
    labelText.textContent = label;
    const control = document.createElement("span");
    const input = document.createElement("input");
    input.id = id;
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
    const unitText = document.createElement("span");
    unitText.textContent = unit;
    control.appendChild(input);
    control.appendChild(unitText);
    wrapper.appendChild(labelText);
    wrapper.appendChild(control);
    row.appendChild(wrapper);
  }

  const firstRow = document.createElement("div");
  firstRow.className = "bilibili-protection-modal-row";
  addNumberField(firstRow, "bilibiliProtectionMinSendInterval", "最短发送间隔", Number(protection.min_send_interval) >= 0 ? Number(protection.min_send_interval) : 60, "秒", 0, 86400);
  addNumberField(firstRow, "bilibiliProtectionHourlyLimit", "每小时最多", Number(protection.hourly_limit) > 0 ? Number(protection.hourly_limit) : 10, "条", 1, 10000);
  form.appendChild(firstRow);

  const secondRow = document.createElement("div");
  secondRow.className = "bilibili-protection-modal-row";
  addNumberField(secondRow, "bilibiliProtectionBacklogLimit", "每个来源积压上限", Number(protection.backlog_limit) > 0 ? Number(protection.backlog_limit) : 10, "条", 1, 1000);
  form.appendChild(secondRow);

  const policy = document.createElement("label");
  policy.className = "bilibili-protection-modal-policy";
  const policyText = document.createElement("span");
  policyText.textContent = "超出时";
  const select = document.createElement("select");
  select.id = "bilibiliProtectionOverflowPolicy";
  [
    ["latest", "保留最新、跳过旧投稿"],
    ["oldest", "保留最旧、跳过新投稿"],
  ].forEach(function (item) {
    const option = document.createElement("option");
    option.value = item[0];
    option.textContent = item[1];
    select.appendChild(option);
  });
  select.value = protection.overflow_policy === "oldest" ? "oldest" : "latest";
  policy.appendChild(policyText);
  policy.appendChild(select);
  form.appendChild(policy);

  const hint = document.createElement("div");
  hint.style.cssText = "font-size:12px;color:var(--muted);line-height:1.5;";
  hint.textContent = "保护参数按全部 Bilibili 来源合计生效；积压上限按每个来源计算。";
  form.appendChild(hint);
  body.appendChild(form);

  document.getElementById("confirmTitle").textContent = "Bilibili 转发保护";
  setConfirmButtons("保存", true);
  confirmAction = async function () {
    let minInterval = parseInt(document.getElementById("bilibiliProtectionMinSendInterval").value, 10);
    let hourlyLimit = parseInt(document.getElementById("bilibiliProtectionHourlyLimit").value, 10);
    let backlogLimit = parseInt(document.getElementById("bilibiliProtectionBacklogLimit").value, 10);
    if (!(minInterval >= 0)) minInterval = 60;
    if (minInterval > 86400) minInterval = 86400;
    if (!(hourlyLimit > 0)) hourlyLimit = 10;
    if (hourlyLimit > 10000) hourlyLimit = 10000;
    if (!(backlogLimit > 0)) backlogLimit = 10;
    if (backlogLimit > 1000) backlogLimit = 1000;
    settings.bilibili_protection = {
      min_send_interval: minInterval,
      hourly_limit: hourlyLimit,
      backlog_limit: backlogLimit,
      overflow_policy: document.getElementById("bilibiliProtectionOverflowPolicy").value === "oldest" ? "oldest" : "latest",
    };
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function openYoutubeProtectionModal() {
  const protection = Object.assign({
    min_send_interval: 60,
    hourly_limit: 10,
    backlog_limit: 10,
    overflow_policy: "latest",
  }, (settings && settings.youtube_protection) || {});
  const body = document.getElementById("confirmBody");
  if (!body) return;

  body.innerHTML = "";
  const form = document.createElement("div");
  form.className = "bilibili-protection-modal-form";

  function addNumberField(row, id, label, value, unit, min, max) {
    const wrapper = document.createElement("label");
    wrapper.className = "bilibili-protection-modal-field";
    const labelText = document.createElement("span");
    labelText.textContent = label;
    const control = document.createElement("span");
    const input = document.createElement("input");
    input.id = id;
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
    const unitText = document.createElement("span");
    unitText.textContent = unit;
    control.appendChild(input);
    control.appendChild(unitText);
    wrapper.appendChild(labelText);
    wrapper.appendChild(control);
    row.appendChild(wrapper);
  }

  const firstRow = document.createElement("div");
  firstRow.className = "bilibili-protection-modal-row";
  addNumberField(firstRow, "youtubeProtectionMinSendInterval", "最短发送间隔", Number(protection.min_send_interval) >= 0 ? Number(protection.min_send_interval) : 60, "秒", 0, 86400);
  addNumberField(firstRow, "youtubeProtectionHourlyLimit", "每小时最多", Number(protection.hourly_limit) > 0 ? Number(protection.hourly_limit) : 10, "条", 1, 10000);
  form.appendChild(firstRow);

  const secondRow = document.createElement("div");
  secondRow.className = "bilibili-protection-modal-row";
  addNumberField(secondRow, "youtubeProtectionBacklogLimit", "每个来源积压上限", Number(protection.backlog_limit) > 0 ? Number(protection.backlog_limit) : 10, "条", 1, 1000);
  form.appendChild(secondRow);

  const policy = document.createElement("label");
  policy.className = "bilibili-protection-modal-policy";
  const policyText = document.createElement("span");
  policyText.textContent = "超出时";
  const select = document.createElement("select");
  select.id = "youtubeProtectionOverflowPolicy";
  [
    ["latest", "保留最新、跳过旧视频"],
    ["oldest", "保留最旧、跳过新视频"],
  ].forEach(function (item) {
    const option = document.createElement("option");
    option.value = item[0];
    option.textContent = item[1];
    select.appendChild(option);
  });
  select.value = protection.overflow_policy === "oldest" ? "oldest" : "latest";
  policy.appendChild(policyText);
  policy.appendChild(select);
  form.appendChild(policy);

  const hint = document.createElement("div");
  hint.style.cssText = "font-size:12px;color:var(--muted);line-height:1.5;";
  hint.textContent = "保护参数按全部 YouTube 来源合计生效；积压上限按每个来源计算。";
  form.appendChild(hint);
  body.appendChild(form);

  document.getElementById("confirmTitle").textContent = "YouTube 转发保护";
  setConfirmButtons("保存", true);
  confirmAction = async function () {
    let minInterval = parseInt(document.getElementById("youtubeProtectionMinSendInterval").value, 10);
    let hourlyLimit = parseInt(document.getElementById("youtubeProtectionHourlyLimit").value, 10);
    let backlogLimit = parseInt(document.getElementById("youtubeProtectionBacklogLimit").value, 10);
    if (!(minInterval >= 0)) minInterval = 60;
    if (minInterval > 86400) minInterval = 86400;
    if (!(hourlyLimit > 0)) hourlyLimit = 10;
    if (hourlyLimit > 10000) hourlyLimit = 10000;
    if (!(backlogLimit > 0)) backlogLimit = 10;
    if (backlogLimit > 1000) backlogLimit = 1000;
    settings.youtube_protection = {
      min_send_interval: minInterval,
      hourly_limit: hourlyLimit,
      backlog_limit: backlogLimit,
      overflow_policy: document.getElementById("youtubeProtectionOverflowPolicy").value === "oldest" ? "oldest" : "latest",
    };
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function openXProtectionModal() {
  const protection = Object.assign({
    min_send_interval: 5,
    hourly_limit: 300,
    backlog_limit: 100,
    overflow_policy: "latest",
  }, (settings && settings.x_protection) || {});
  const body = document.getElementById("confirmBody");
  if (!body) return;

  body.innerHTML = "";
  const form = document.createElement("div");
  form.className = "bilibili-protection-modal-form";

  function addNumberField(row, id, label, value, unit, min, max) {
    const wrapper = document.createElement("label");
    wrapper.className = "bilibili-protection-modal-field";
    const labelText = document.createElement("span");
    labelText.textContent = label;
    const control = document.createElement("span");
    const input = document.createElement("input");
    input.id = id;
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
    const unitText = document.createElement("span");
    unitText.textContent = unit;
    control.appendChild(input);
    control.appendChild(unitText);
    wrapper.appendChild(labelText);
    wrapper.appendChild(control);
    row.appendChild(wrapper);
  }

  const firstRow = document.createElement("div");
  firstRow.className = "bilibili-protection-modal-row";
  addNumberField(firstRow, "xProtectionMinSendInterval", "最短发送间隔", Number(protection.min_send_interval) >= 0 ? Number(protection.min_send_interval) : 5, "秒", 0, 86400);
  addNumberField(firstRow, "xProtectionHourlyLimit", "每小时最多", Number(protection.hourly_limit) > 0 ? Number(protection.hourly_limit) : 300, "条", 1, 10000);
  form.appendChild(firstRow);

  const secondRow = document.createElement("div");
  secondRow.className = "bilibili-protection-modal-row";
  addNumberField(secondRow, "xProtectionBacklogLimit", "每个来源积压上限", Number(protection.backlog_limit) > 0 ? Number(protection.backlog_limit) : 100, "条", 1, 1000);
  form.appendChild(secondRow);

  const policy = document.createElement("label");
  policy.className = "bilibili-protection-modal-policy";
  const policyText = document.createElement("span");
  policyText.textContent = "超出时";
  const select = document.createElement("select");
  select.id = "xProtectionOverflowPolicy";
  [
    ["latest", "保留最新、跳过旧推文"],
    ["oldest", "保留最旧、跳过新推文"],
  ].forEach(function (item) {
    const option = document.createElement("option");
    option.value = item[0];
    option.textContent = item[1];
    select.appendChild(option);
  });
  select.value = protection.overflow_policy === "oldest" ? "oldest" : "latest";
  policy.appendChild(policyText);
  policy.appendChild(select);
  form.appendChild(policy);

  const hint = document.createElement("div");
  hint.style.cssText = "font-size:12px;color:var(--muted);line-height:1.5;";
  hint.textContent = "限速按全部 X 来源合计；积压按每个来源计算。默认 5 秒间隔、每小时 300 条、每源 100 条。";
  form.appendChild(hint);
  body.appendChild(form);

  document.getElementById("confirmTitle").textContent = "X 投递保护";
  setConfirmButtons("保存", true);
  confirmAction = async function () {
    let minInterval = parseInt(document.getElementById("xProtectionMinSendInterval").value, 10);
    let hourlyLimit = parseInt(document.getElementById("xProtectionHourlyLimit").value, 10);
    let backlogLimit = parseInt(document.getElementById("xProtectionBacklogLimit").value, 10);
    if (!(minInterval >= 0)) minInterval = 5;
    if (minInterval > 86400) minInterval = 86400;
    if (!(hourlyLimit > 0)) hourlyLimit = 300;
    if (hourlyLimit > 10000) hourlyLimit = 10000;
    if (!(backlogLimit > 0)) backlogLimit = 100;
    if (backlogLimit > 1000) backlogLimit = 1000;
    settings.x_protection = {
      min_send_interval: minInterval,
      hourly_limit: hourlyLimit,
      backlog_limit: backlogLimit,
      overflow_policy: document.getElementById("xProtectionOverflowPolicy").value === "oldest" ? "oldest" : "latest",
    };
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function hideConfirm() {
  document.getElementById("confirmModal").style.display = "none";
  scheduledMessageTargetPickerOpen = false;
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
    empty.className = "registration-empty-state";
    empty.textContent = "暂无待审核申请";
    body.appendChild(empty);
  }
  let codeFilter = null;
  let noMatch = null;
  if (items.length) {
    const filterHint = document.createElement("div");
    filterHint.style.cssText = "font-size:12px;color:var(--muted);margin:0 0 6px;";
    filterHint.textContent = "输入申请人提供的识别码，可快速定位待审核记录。";
    body.appendChild(filterHint);
    codeFilter = document.createElement("input");
    codeFilter.type = "search";
    codeFilter.setAttribute("aria-label", "按申请识别码筛选");
    codeFilter.placeholder = "粘贴申请识别码";
    codeFilter.style.cssText = "width:100%;box-sizing:border-box;margin:0 0 8px;";
    body.appendChild(codeFilter);
    noMatch = document.createElement("div");
    noMatch.className = "registration-empty-state";
    noMatch.style.display = "none";
    noMatch.textContent = "暂无匹配的申请";
    body.appendChild(noMatch);
  }
  const reviewRows = [];
  items.forEach(function (it) {
    const label = regReviewKind === "groups"
      ? (it.qq_id || it.group_name
        ? "群号 " + (it.qq_id || "未提供") + " · " + (it.group_name || "未提供群名")
        : "群注册申请（按当前群身份提交）")
      : (it.qq_id || it.nickname
        ? "QQ " + (it.qq_id || "未提供") + " · " + (it.nickname || "未提供昵称")
        : "用户注册申请（按当前账号身份提交）");
    const row = document.createElement("div");
    row.className = "registration-review-row";
    row.dataset.applicationCode = String(it.application_code || "")
      .replace(/[^a-z0-9]/gi, "").toUpperCase();
    const info = document.createElement("div");
    info.className = "registration-review-info";
    info.innerHTML =
      '<div>' + escapeHtml(label) + (it.bot_id ? '' : ' · 历史申请，归属未知，审核将加入当前账号') + '</div>' +
      '<div style="font-size:12px;color:var(--name-green);">申请识别码：' + escapeHtml(it.application_code || "旧申请需重新提交") + '</div>' +
      '<div class="mono" style="font-size:11px;color:var(--muted);word-break:break-all;">' + escapeHtml(it.openid) + '</div>';
    row.appendChild(info);
    const ok = document.createElement("button");
    ok.className = "sec rowicon";
    ok.innerHTML = SVG_ORIGINAL_ON;
    ok.title = "通过审核";
    ok.setAttribute("aria-label", "通过审核");
    ok.style.cssText = "color:var(--name-green);border-color:var(--ok-border);";
    ok.addEventListener("click", function () { reviewRegistration(it.openid, "approve"); });
    const no = document.createElement("button");
    no.className = "danger rowicon";
    no.innerHTML = SVG_ORIGINAL_OFF;
    no.title = "拒绝审核";
    no.setAttribute("aria-label", "拒绝审核");
    no.addEventListener("click", function () { reviewRegistration(it.openid, "reject"); });
    row.appendChild(ok);
    row.appendChild(no);
    body.appendChild(row);
    reviewRows.push(row);
  });
  if (codeFilter) {
    codeFilter.addEventListener("input", function () {
      const query = codeFilter.value.replace(/[^a-z0-9]/gi, "").toUpperCase();
      let visibleCount = 0;
      reviewRows.forEach(function (row) {
        const matches = !query || row.dataset.applicationCode.includes(query);
        row.hidden = !matches;
        if (matches) visibleCount += 1;
      });
      noMatch.style.display = visibleCount ? "none" : "block";
    });
  }
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
const FILTER_BILIBILI_POST_TYPES = ["视频", "专栏", "动态"];

// 各链路实际支持的消息类型（与 plugins/filter.py 的常量保持一致）。
// Discord 六类齐备；X 为文字/图片/视频；Bilibili 为文字/图片/链接/视频。
// 消息类型只渲染该链路支持的项：不支持的类型不出现，用户看不到也点不到。
// 「视频」是 X/Bilibili 的规范类型名（早期与 Discord 共用「文件」，读取时自动迁移）。
const FILTER_SUPPORTED_MESSAGE_TYPES = {
  channel: FILTER_MESSAGE_TYPES,
  relay: FILTER_MESSAGE_TYPES,
  xsource: ["文字", "图片", "视频"],
  bsource: ["文字", "图片", "链接", "视频"],
  youtubeSource: ["文字", "图片", "链接"],
  xrelay: ["文字", "图片", "视频"],
  birelay: ["文字", "图片", "链接", "视频"],
};

// 旧配置里 X/Bilibili 的「文件」= 现在的「视频」，加载时迁移，避免打开弹窗
// 显示出一个已选中的无效项。
const FILTER_MESSAGE_TYPE_LEGACY = { "文件": "视频" };

// 当前 scope 支持的消息类型；Bilibili 标签/关键词 source 的投稿集合只有视频，
// 但消息组件筛选仍保持独立（文字/封面/链接/视频可以分别控制）。
function supportedFilterTypes(scope, config) {
  if (scope === "bsource" && String((config || {}).kind || "up") !== "up") {
    return ["文字", "图片", "链接", "视频"];
  }
  return FILTER_SUPPORTED_MESSAGE_TYPES[scope] || FILTER_MESSAGE_TYPES;
}

function filterSupportedTypes() {
  return supportedFilterTypes(filterEditScope, {
    kind: filterBilibiliSourceKind,
  });
}

// 把已保存的类型名迁移为规范名（仅对「文件」→「视频」这类历史值生效）。
function migrateFilterMessageType(type) {
  const mapped = FILTER_MESSAGE_TYPE_LEGACY[type];
  return mapped && filterSupportedTypes().indexOf(mapped) >= 0 ? mapped : type;
}

let filterSourceTarget = null;
let filterEditScope = "channel";
let filterUsernames = [];
let filterKeywords = [];
let filterMessageTypes = [];
let filterXPostTypes = [];
let filterBilibiliPostTypes = [];
let filterBilibiliSourceKind = "up";

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
    del.innerHTML = SVG_ORIGINAL_OFF;
    del.title = "删除这条筛选规则";
    del.setAttribute("aria-label", del.title);
    del.addEventListener("click", function () {
      showConfirmDlg("删除筛选规则", "删除这条筛选规则？保存后生效。", function () {
        items.splice(i, 1);
        renderFilterList(kind);
      }, "删除");
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

function makeFilterCheckbox(type, selected, onChange, disabled) {
  const label = document.createElement("label");
  label.className = "content-filter-check" + (disabled ? " disabled" : "");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = selected;
  input.disabled = !!disabled;
  input.addEventListener("change", function () {
    if (onChange(input.checked) === false) input.checked = !input.checked;
  });
  label.append(input, document.createTextNode(type));
  return label;
}

// 消息类型只渲染当前链路实际支持的选项，并统一用 Steam 来源编辑中的勾选框胶囊。
function renderFilterTypes() {
  const wrap = document.getElementById("filterTypeList");
  wrap.innerHTML = "";
  filterSupportedTypes().forEach(function (t) {
    wrap.appendChild(makeFilterCheckbox(t, filterMessageTypes.indexOf(t) >= 0, function (checked) {
      const idx = filterMessageTypes.indexOf(t);
      if (checked && idx < 0) filterMessageTypes.push(t);
      else if (!checked && idx >= 0) filterMessageTypes.splice(idx, 1);
    }));
  });
}

function renderFilterXPostTypes() {
  const wrap = document.getElementById("filterXPostTypeList");
  if (!wrap) return;
  wrap.innerHTML = "";
  // 与 Bilibili 投稿类型一致：不支持的类型置灰禁用，且至少保留一种。
  const supported = filterXPostSupportedTypes();
  FILTER_X_POST_TYPES.forEach(function (t) {
    const isSupported = supported.indexOf(t) >= 0;
    wrap.appendChild(makeFilterCheckbox(t, filterXPostTypes.indexOf(t) >= 0, function (checked) {
      const idx = filterXPostTypes.indexOf(t);
      if (!checked && idx >= 0) {
        if (filterXPostTypes.length === 1) return false;
        filterXPostTypes.splice(idx, 1);
      } else if (checked && idx < 0) {
        filterXPostTypes.push(t);
      }
      return true;
    }, !isSupported));
  });
}

// X 来源支持的推文类型：三种都可筛（RSS 同时提供原创、转发与引用内容）。
function filterXPostSupportedTypes() {
  return FILTER_X_POST_TYPES;
}

function renderFilterBilibiliPostTypes() {
  const wrap = document.getElementById("filterBilibiliPostTypeList");
  if (!wrap) return;
  wrap.innerHTML = "";
  const supported = filterBilibiliSourceKind === "up" ? FILTER_BILIBILI_POST_TYPES : ["视频"];
  FILTER_BILIBILI_POST_TYPES.forEach(function (t) {
    const isSupported = supported.indexOf(t) >= 0;
    wrap.appendChild(makeFilterCheckbox(t, filterBilibiliPostTypes.indexOf(t) >= 0, function (checked) {
      const idx = filterBilibiliPostTypes.indexOf(t);
      if (!checked && idx >= 0) {
        if (filterBilibiliPostTypes.length === 1) return false;
        filterBilibiliPostTypes.splice(idx, 1);
      } else if (checked && idx < 0) {
        filterBilibiliPostTypes.push(t);
      }
      return true;
    }, !isSupported));
  });
}

// 筛选配置是否生效（漏斗图标高亮）：用户名/关键词、消息类型，或 X 推文类型
// 任一实际缩减默认支持集时高亮；缺省/全选 = 未筛选。
function selectedFilterMessageTypes(config, scope) {
  const supported = supportedFilterTypes(scope, config);
  const values = config && config.filter_message_types;
  if (!Array.isArray(values)) return supported.slice();
  if (!values.length) return [];
  const migrated = values.map(function (type) {
    return supported.indexOf("视频") >= 0 && type === "文件" ? "视频" : type;
  });
  const selected = supported.filter(function (type) {
    return migrated.indexOf(type) >= 0;
  });
  return selected.length ? selected : supported.slice();
}

function saveSourceMessageTypes(source, scope) {
  const supported = supportedFilterTypes(scope, source);
  const selected = filterMessageTypes.filter(function (type) {
    return supported.indexOf(type) >= 0;
  });
  if (selected.length < supported.length) {
    source.filter_message_types = selected;
  } else {
    delete source.filter_message_types;
  }
}

function filterConfigActive(c, includeAudience, supported, scope) {
  c = c || {};
  if (includeAudience !== false) {
    if ((c.filter_usernames || []).length) return true;
    if ((c.filter_keywords || []).length) return true;
  }
  const base = supported || FILTER_MESSAGE_TYPES;
  if (Array.isArray(c.filter_message_types)) {
    const actualTypes = supportedFilterTypes(scope, c);
    const selected = selectedFilterMessageTypes(c, scope);
    const explicit = c.filter_message_types.length === 0 || c.filter_message_types.some(function (type) { return String(type).trim(); });
    // 只含不支持的旧值时后端按未筛选处理，不点亮漏斗；显式空列表是全关筛选，应点亮。
    if (explicit && selected.length < actualTypes.length) return true;
  }
  const x = c.filter_post_types;
  return Array.isArray(x) && x.length > 0 && x.length < FILTER_X_POST_TYPES.length;
}

// 统一手动转发卡：漏斗高亮按各链路支持的类型集判定；三条链路任一有生效筛选即点亮。
const MANUAL_RELAY_FILTER_SUPPORTED = {
  qq_c2c_relay_filter: FILTER_SUPPORTED_MESSAGE_TYPES.relay,
  x_c2c_relay_filter: FILTER_SUPPORTED_MESSAGE_TYPES.xrelay,
  bilibili_c2c_relay_filter: FILTER_SUPPORTED_MESSAGE_TYPES.birelay,
};

const MANUAL_RELAY_FILTER_SCOPE = {
  qq_c2c_relay_filter: "relay",
  x_c2c_relay_filter: "xrelay",
  bilibili_c2c_relay_filter: "birelay",
};

function renderUnifiedRelayFilter() {
  const btn = document.getElementById("btnUnifiedRelayFilter");
  if (!btn) return;
  const active = Object.keys(MANUAL_RELAY_FILTER_SUPPORTED).some(function (key) {
    return filterConfigActive(
      settings && settings[key],
      false,
      MANUAL_RELAY_FILTER_SUPPORTED[key],
      MANUAL_RELAY_FILTER_SCOPE[key]
    );
  });
  btn.innerHTML = SVG_FUNNEL;
  btn.classList.toggle("filter-active", active);
}

let relayChannelsEditScope = "discord";

const MULTI_LINK_SCOPE_META = Object.freeze({
  discord: {page: "discord", label: "Discord"},
  relay: {page: "discord", label: "Discord"},
  x: {page: "x", label: "X"},
  xrelay: {page: "x", label: "X"},
  bilibili: {page: "bilibili", label: "Bilibili"},
  birelay: {page: "bilibili", label: "Bilibili"},
});

function renderMultiLinkScopeSwitch(tabs, scopeAttribute, activeScope) {
  tabs.classList.add("multi-chain-switch");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "选择转发链路");
  tabs.querySelectorAll(".fscope-tab").forEach(function (tab) {
    const scope = tab.dataset[scopeAttribute];
    const meta = MULTI_LINK_SCOPE_META[scope];
    if (!meta) return;
    const sourceIcon = document.querySelector(
      '.nav-item[data-page="' + meta.page + '"] .nav-icon svg'
    );
    if (sourceIcon) {
      const icon = sourceIcon.cloneNode(true);
      icon.setAttribute("class", "multi-link-scope-icon");
      icon.setAttribute("aria-hidden", "true");
      icon.setAttribute("focusable", "false");
      tab.replaceChildren(icon);
    }
    const active = scope === activeScope;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-label", meta.label);
    tab.setAttribute("title", meta.label);
    tab.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function renderRelayScopeTabs() {
  const tabs = document.getElementById("relayScopeTabs");
  if (!tabs) return;
  renderMultiLinkScopeSwitch(tabs, "rscope", relayChannelsEditScope);
}

function renderRelayChannelsList() {
  const list = document.getElementById("relayChannelsList");
  if (!list) return;
  list.innerHTML = "";
  const scope = relayChannelsEditScope;
  const isX = scope === "x";
  const isBilibili = scope === "bilibili";
  const saveButton = document.getElementById("relayChannelsSave");
  const doneButton = document.querySelector("#relayChannelsModal .relay-channels-actions [data-close]");
  if (saveButton) saveButton.style.display = isBilibili ? "none" : "";
  if (doneButton) doneButton.textContent = isBilibili ? "完成" : "取消";
  const items = !settings
    ? []
    : isX
      ? settings.x_rss_sources
      : isBilibili
        ? settings.bilibili_sources
        : settings.discord_channels;
  const channels = Array.isArray(items) ? items : [];
  const entryName = isX ? "账号" : isBilibili ? "来源" : "频道";
  document.getElementById("relayChannelsTitle").textContent = "已注册" + entryName;
  const description = document.querySelector("#relayChannelsModal .relay-channels-desc");
  description.style.display = isX || isBilibili ? "" : "none";
  description.textContent = isX
    ? "这里显示全部已登记的 X 账号。修改备注后点击保存即可。"
    : isBilibili
      ? "这里显示全部已登记的 Bilibili 来源；来源名称直接使用 UID、标签或关键词。"
      : "";
  if (!channels.length) {
    list.innerHTML = '<div class="relay-channels-empty">当前没有已注册' + entryName + "</div>";
  } else {
    channels.forEach(function (channel, index) {
      const id = isX
        ? xSourceKey(channel.source)
        : isBilibili
          ? String(channel.value || channel.id || "")
          : String(channel.id || "");
      const row = document.createElement("div");
      row.className = "relay-channel-row";
      const info = document.createElement("div");
      info.className = "relay-channel-row-info";
      const name = document.createElement("div");
      name.className = "relay-channel-row-name";
      if (isX) {
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
      } else if (isBilibili) {
        const kindText = channel.kind === "up" ? "UP 主" : channel.kind === "tag" ? "标签" : "关键词";
        name.textContent = kindText + " · " + id;
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
      row.appendChild(info);
      if (!isBilibili) {
        const input = document.createElement("input");
        input.type = "text";
        input.value = channel.name || "";
        input.placeholder = isX ? "账号中文备注" : "频道中文备注";
        input.dataset.index = String(index);
        input.setAttribute("aria-label", isX ? "账号中文备注" : "频道中文备注");
        row.appendChild(input);
      }
      list.appendChild(row);
    });
  }
  renderRelayScopeTabs();
}

function openUnifiedRelaySourcesModal() {
  relayChannelsEditScope = "discord";
  renderRelayChannelsList();
  document.getElementById("relayChannelsModal").style.display = "flex";
}

function switchRelayChannelsTab(scope) {
  relayChannelsEditScope = scope === "x" || scope === "bilibili" ? scope : "discord";
  renderRelayChannelsList();
}

function closeRelayChannelsModal() {
  document.getElementById("relayChannelsModal").style.display = "none";
}

function openTranslationChainsModal() {
  renderAIControls();
  document.getElementById("translationChainsModal").style.display = "flex";
}

function closeTranslationChainsModal() {
  document.getElementById("translationChainsModal").style.display = "none";
}

const FORWARDING_PANE_PAIRS = [
  ["discordChannelContent", "discordGroupContent"],
  ["xSourceContent", "xGroupContent"],
  ["bilibiliSourceContent", "bilibiliGroupContent"],
  ["youtubeSourceContent", "youtubeGroupContent"],
  ["steamSourceContent", "steamGroupContent"],
  ["qqFgGroupContent", "userContent"],
  ["scheduledGroupMessageContent", "scheduledGroupTargetContent"],
];

function setForwardingPaneFold(id, collapsed) {
  const pair = FORWARDING_PANE_PAIRS.find(items => items.includes(id));
  if (!pair) return false;
  for (const item of pair) {
    const pane = document.getElementById(item);
    const button = document.querySelector('.foldbtn[data-fold="' + item + '"]');
    if (!pane || !button) continue;
    pane.style.display = collapsed ? "none" : "";
    button.classList.toggle("folded", collapsed);
    button.setAttribute("aria-expanded", String(!collapsed));
  }
  return true;
}

function openQQWhitelistModal() {
  // 兼容旧入口/外部调用：白名单已迁到「QQ → QQ」页转发组的工作区分栏，
  // 不再使用独立弹窗，直接切到该页面。
  openQQWhitelistPane();
}

function openQQWhitelistPane() {
  setActivePage("qq");
  const pane = document.getElementById("userContent");
  const section = pane && pane.closest(".fgsection");
  if (section && section.style.display === "none") section.style.display = "";
  setForwardingPaneFold("userContent", false);
  if (section) section.scrollIntoView({block: "nearest"});
}

function closeQQWhitelistModal() {
  // 白名单已内嵌在 QQ → QQ 页，无需关闭弹窗；保留函数供旧引用安全调用。
  const modal = document.getElementById("qqWhitelistModal");
  if (modal) modal.style.display = "none";
}

async function saveRelayChannels() {
  const list = document.getElementById("relayChannelsList");
  if (!list) return;
  if (relayChannelsEditScope === "bilibili") {
    closeRelayChannelsModal();
    return;
  }
  list.querySelectorAll("input[data-index]").forEach(function (input) {
    const index = Number(input.dataset.index);
    const channel = relayChannelsEditScope === "x"
      ? settings.x_rss_sources[index]
      : relayChannelsEditScope === "bilibili"
        ? settings.bilibili_sources[index]
        : settings.discord_channels[index];
    if (channel) channel.name = input.value.trim();
  });
  closeRelayChannelsModal();
  await saveAndReload();
}

function sourceItemsForScope(scope) {
  if (scope === "channel") return settings.discord_channels || [];
  if (scope === "xsource") return settings.x_rss_sources || [];
  if (scope === "bsource") return settings.bilibili_sources || [];
  if (scope === "youtubeSource") return settings.youtube_sources || [];
  return [];
}

function sourceIdForScope(scope, source) {
  if (scope === "channel") return String(source.id || "");
  if (scope === "xsource") return xSourceKey(source.source);
  if (scope === "bsource") return String(source.id || bilibiliSourceId(source.kind || "up", source.value || ""));
  if (scope === "youtubeSource") return youtubeSourceId(source.value);
  return "";
}

function replaceForwardingSourceReference(scope, oldId, newId) {
  const config = {
    channel: [settings.forwarding_groups || [], "channels"],
    xsource: [settings.x_forwarding_groups || [], "sources"],
    bsource: [settings.bilibili_forwarding_groups || [], "sources"],
    youtubeSource: [settings.youtube_forwarding_groups || [], "sources"],
  }[scope];
  if (!config || oldId === newId) return;
  config[0].forEach(function (group) {
    if (!Array.isArray(group[config[1]])) return;
    group[config[1]] = group[config[1]].map(function (id) {
      return String(id) === String(oldId) ? newId : id;
    });
  });
}

function findSourceById(scope, sourceId) {
  if (!sourceId) return null;
  const matches = sourceItemsForScope(scope).filter(source => sourceIdForScope(scope, source) === sourceId);
  return matches.length === 1 ? matches[0] : null;
}

function openSourceContentEditor(scope, sourceId) {
  const source = findSourceById(scope, sourceId);
  const epoch = workspaceEpoch;
  if (!source) return;
  const body = document.getElementById("confirmBody");
  body.replaceChildren();
  const form = document.createElement("div");
  form.className = "source-content-form";
  const nameInput = document.createElement("input");
  const addressInput = document.createElement("input");
  function field(labelText, input, value, placeholder) {
    const label = document.createElement("label");
    label.className = "source-content-field";
    const caption = document.createElement("span");
    caption.textContent = labelText;
    input.type = "text";
    input.value = value || "";
    input.maxLength = 300;
    input.autocomplete = "off";
    if (placeholder) input.placeholder = placeholder;
    label.append(caption, input);
    form.appendChild(label);
  }
  field(scope === "bsource" ? "备注名称（可留空）" : "名称（可留空）", nameInput, source.name || (scope === "xsource" ? xSourceKey(source.source) : scope === "youtubeSource" ? source.value : ""));
  const addressLabel = scope === "channel" ? "Discord 频道 ID 或链接"
    : scope === "xsource" ? "X 账号或来源地址"
      : scope === "bsource" ? (source.kind === "up" ? "UP 主 UID" : source.kind === "tag" ? "标签名" : "关键词")
        : "YouTube 频道 ID、@handle 或地址";
  const addressValue = scope === "channel" ? source.id
    : scope === "xsource" ? source.source
      : source.value;
  const addressPlaceholder = scope === "channel" ? "频道 ID 或 Discord 频道链接"
    : scope === "xsource" ? "账号名、@账号或 RSS 地址"
      : scope === "bsource" ? (source.kind === "up" ? "数字 UID" : "来源值")
        : "UC...、@handle 或频道主页链接";
  field(addressLabel, addressInput, addressValue, addressPlaceholder);
  body.appendChild(form);
  document.getElementById("confirmTitle").textContent = "编辑来源内容";
  setConfirmButtons("保存", true);
  confirmAction = async function () {
    if (epoch !== workspaceEpoch) return;
    const source = findSourceById(scope, sourceId);
    if (!source) {
      showAlert("保存失败", "该来源已变化，请重新打开编辑窗口。");
      return;
    }
    let nextAddress = addressInput.value.trim();
    if (!nextAddress) {
      showAlert("无法保存", "来源地址不能为空。");
      return;
    }
    if (scope === "channel" && /^https?:\/\//i.test(nextAddress)) {
      try {
        const segments = new URL(nextAddress).pathname.split("/").filter(Boolean);
        nextAddress = segments.length >= 3 && segments[0].toLowerCase() === "channels" ? segments[2] : "";
      } catch (error) { nextAddress = ""; }
      if (!/^\d{15,22}$/.test(nextAddress)) {
        showAlert("无法保存", "请输入有效的 Discord 频道 ID 或频道链接。");
        return;
      }
    } else if (scope === "channel" && !/^\d{15,22}$/.test(nextAddress)) {
      showAlert("无法保存", "请输入有效的 Discord 频道 ID 或频道链接。");
      return;
    } else if (scope === "xsource") {
      if (/^@/.test(nextAddress)) nextAddress = nextAddress.replace(/^@+/, "");
      if (/\s/.test(nextAddress)) {
        showAlert("无法保存", "X 来源地址中不能包含空格。");
        return;
      }
    } else if (scope === "bsource" && source.kind === "up" && !/^\d+$/.test(nextAddress)) {
      showAlert("无法保存", "UP 主来源地址必须是数字 UID。");
      return;
    } else if (scope === "youtubeSource") {
      nextAddress = normalizeYoutubeChannelValue(nextAddress);
      if (!nextAddress) {
        showAlert("无法保存", "请输入有效的 YouTube 频道 ID、@handle 或频道地址。");
        return;
      }
    }

    const nextId = scope === "channel" ? nextAddress
      : scope === "xsource" ? xSourceKey(nextAddress)
        : scope === "bsource" ? bilibiliSourceId(source.kind || "up", nextAddress)
          : youtubeSourceId(nextAddress);
    const duplicate = sourceItemsForScope(scope).some(function (item) {
      if (item === source) return false;
      if (scope === "bsource") {
        return String(item.kind || "up") === String(source.kind || "up") && String(item.value || "").toLowerCase() === nextAddress.toLowerCase();
      }
      const candidate = sourceIdForScope(scope, item);
      return scope === "xsource" ? candidate.toLowerCase() === nextId.toLowerCase() : candidate === nextId;
    });
    if (duplicate) {
      showAlert("无法保存", "该来源地址已存在。");
      return;
    }

    source.name = nameInput.value.trim();
    if (scope === "channel") source.id = nextAddress;
    else if (scope === "xsource") source.source = nextAddress;
    else if (scope === "bsource") {
      source.value = nextAddress;
      source.id = nextId;
    } else {
      source.value = nextAddress;
      source.id = nextId;
      source.kind = "channel";
      if (!source.name) source.name = nextAddress;
    }
    replaceForwardingSourceReference(scope, sourceId, nextId);
    await saveAndReload();
  };
  document.getElementById("confirmModal").style.display = "flex";
  nameInput.focus();
}

function syncFilterTranslationInputs() {
  const enabled = document.getElementById("filterTranslationEnabled");
  const append = document.getElementById("filterTranslationAppendOriginal");
  const datetime = document.getElementById("filterTranslationShowDatetime");
  if (!enabled || !append || !datetime) return;
  append.disabled = !enabled.checked;
  datetime.disabled = !enabled.checked;
  append.closest(".content-filter-check")?.classList.toggle("disabled", append.disabled);
  datetime.closest(".content-filter-check")?.classList.toggle("disabled", datetime.disabled);
}

function mountImageCardOptions(container) {
  container.replaceChildren(document.getElementById("imageCardOptionsTemplate").content.cloneNode(true));
  const enabled = container.querySelector('[data-image-card="enabled"]');
  const light = container.querySelector('[data-image-card="light"]');
  const initialEnabled = settings?.image_card_enabled !== false;
  const initialLight = settings?.image_card_theme === "light";
  enabled.checked = initialEnabled;
  light.checked = initialLight;
  const sync = () => {
    light.closest("label").style.display = enabled.checked ? "" : "none";
    light.disabled = !enabled.checked;
  };
  enabled.addEventListener("change", sync);
  sync();
  return () => {
    const changes = {};
    if (enabled.checked !== initialEnabled) changes.image_card_enabled = enabled.checked;
    if (light.checked !== initialLight) changes.image_card_theme = light.checked ? "light" : "dark";
    return changes;
  };
}

let readFilterImageCardOptions = () => ({});

function prepareFilterModal(config, label) {
  const c = config || {};
  document.getElementById("filterChanName").textContent = label;
  // 受众筛选（用户名/关键词）只在 Discord 频道与 X/Bilibili/YouTube 来源弹窗出现。
  const showAudienceFields = ["channel", "xsource", "bsource", "youtubeSource"].includes(filterEditScope);
  const audienceFields = document.getElementById("filterAudienceFields");
  if (audienceFields) audienceFields.style.display = showAudienceFields ? "flex" : "none";
  const usernameField = document.getElementById("filterUsernameField");
  if (usernameField) usernameField.style.display = filterEditScope === "channel" ? "flex" : "none";
  const keywordField = document.getElementById("filterKeywordField");
  if (keywordField) keywordField.style.display = showAudienceFields ? "flex" : "none";
  const keywordTitle = document.getElementById("filterKeywordTitle");
  if (keywordTitle) keywordTitle.firstChild.textContent = filterEditScope === "xsource" ? "关键词/话题 " : "关键词筛选 ";
  const keywordInput = document.getElementById("filterKeywordInput");
  if (keywordInput) keywordInput.placeholder = filterEditScope === "xsource" ? "关键词或 #话题" : "关键词";
  // 消息类型筛选对所有 scope 都可用：Discord 频道与手动转发用六类型集合，
  // X 用文字/图片/视频，Bilibili 用文字/图片/链接/视频。
  // 曾误写成只允许 channel/xsource/bsource，导致手动转发（relay/xrelay/birelay）
  // 弹窗整块消息类型区域被隐藏，只剩标题和按钮。
  const messageTypeFields = document.getElementById("filterMessageTypeFields");
  if (messageTypeFields) {
    messageTypeFields.style.display = "block";
    // 没有受众筛选区时它就是内容区第一块，顶部间距交给 .modal-body 的 padding；
    // 否则会再叠加 14px margin，标题与内容之间多出一段空白。
    messageTypeFields.style.marginTop = showAudienceFields ? "14px" : "0";
  }
  const xPostTypeFields = document.getElementById("filterXPostTypeFields");
  if (xPostTypeFields) xPostTypeFields.style.display = filterEditScope === "xsource" ? "block" : "none";
  const bilibiliPostTypeFields = document.getElementById("filterBilibiliPostTypeFields");
  if (bilibiliPostTypeFields) bilibiliPostTypeFields.style.display = filterEditScope === "bsource" ? "block" : "none";
  const translationFields = document.getElementById("filterTranslationFields");
  const isSourceFilter = ["channel", "xsource", "bsource", "youtubeSource"].includes(filterEditScope);
  if (translationFields) translationFields.style.display = isSourceFilter ? "block" : "none";
  document.getElementById("filterTranslationEnabled").checked = c.translation_enabled === true;
  document.getElementById("filterTranslationAppendOriginal").checked = c.translation_append_original !== false;
  document.getElementById("filterTranslationShowDatetime").checked = c.translation_show_datetime === true;
  readFilterImageCardOptions = mountImageCardOptions(document.getElementById("filterImageCardFields"));
  syncFilterTranslationInputs();
  filterBilibiliSourceKind = String(c.kind || "up");
  // 手动转发统一弹窗：用 Tab 分屏编辑 Discord / X / Bilibili 三套筛选。
  renderFilterScopeTabs();
  // 兼容现有已保存的筛选配置（数组）；类型字段缺省 = 该链路全部支持类型启用。
  // 存量配置可能带着本链路不支持的类型（X/Bilibili 早期与 Discord 共用六类型），
  // 这里按支持集过滤，避免打开弹窗就显示出一堆无效的已选项。
  // 过滤后为空要区分两种情况：用户明确取消勾选全部（空数组，保持不转发），
  // 与老配置只存了不支持的类型（回退为全部支持类型，避免转发意外全失效）。
  filterUsernames = (c.filter_usernames || []).slice();
  filterKeywords = (c.filter_keywords || []).slice();
  const supportedMessageTypes = filterSupportedTypes();
  const storedTypes = c.filter_message_types;
  if (Array.isArray(storedTypes)) {
    // 先迁移历史名，再按当前 scope 的支持集规范化；来源弹窗只处理消息类型，
    // Discord 频道与手动 relay 的旧逻辑保持原样。
    const migrated = storedTypes.map(migrateFilterMessageType);
    const kept = supportedMessageTypes.filter(function (t) {
      return migrated.indexOf(t) >= 0;
    });
    filterMessageTypes = kept.length || !storedTypes.length
      ? kept
      : supportedMessageTypes.slice();
  } else {
    filterMessageTypes = supportedMessageTypes.slice();
  }
  const storedXPostTypes = c.filter_post_types;
  filterXPostTypes = Array.isArray(storedXPostTypes)
    ? storedXPostTypes.slice()
    : FILTER_X_POST_TYPES.slice();
  const storedBilibiliPostTypes = c.filter_bilibili_post_types;
  filterBilibiliPostTypes = Array.isArray(storedBilibiliPostTypes)
    ? storedBilibiliPostTypes.filter(function (type) { return FILTER_BILIBILI_POST_TYPES.includes(type); })
    : ["视频"];
  if (!filterBilibiliPostTypes.length || filterBilibiliSourceKind !== "up") {
    filterBilibiliPostTypes = ["视频"];
  }
  renderFilterList("usernames");
  renderFilterList("keywords");
  renderFilterTypes();
  renderFilterXPostTypes();
  renderFilterBilibiliPostTypes();
  renderFilterScopeTabs();
  document.getElementById("filterModal").style.display = "flex";
}

// 来源级内容筛选按钮在关键词、类型或翻译选项偏离默认值时高亮。
function sourceTranslationActive(source) {
  return source.translation_enabled === true || source.translation_append_original === false || source.translation_show_datetime === true;
}

function sourceFilterActive(source, scope, includeAudience) {
  if (!source) return false;
  const supported = FILTER_SUPPORTED_MESSAGE_TYPES[scope] || FILTER_MESSAGE_TYPES;
  if (sourceTranslationActive(source)) return true;
  if (filterConfigActive(source, includeAudience !== false, supported, scope)) return true;
  if (scope === "xsource") {
    const types = source.filter_post_types;
    return Array.isArray(types) && types.length > 0 && types.length < FILTER_X_POST_TYPES.length;
  }
  if (scope === "bsource") {
    const supportedPosts = source.kind === "up" ? FILTER_BILIBILI_POST_TYPES : ["视频"];
    const posts = Array.isArray(source.filter_bilibili_post_types)
      ? source.filter_bilibili_post_types.filter(function (type) { return supportedPosts.indexOf(type) >= 0; })
      : supportedPosts;
    return posts.length < supportedPosts.length;
  }
  return false;
}

function channelFilterActive(c) {
  return !!c && (sourceTranslationActive(c) || filterConfigActive(c));
}

function xSourceFilterActive(source) {
  return sourceFilterActive(source, "xsource", true);
}

function bilibiliSourceFilterActive(source) {
  return sourceFilterActive(source, "bsource", true);
}

function openSourceFilterModal(scope, sourceId) {
  const source = findSourceById(scope, sourceId);
  if (!source) return;
  filterEditScope = scope;
  filterSourceTarget = {id: sourceId, epoch: workspaceEpoch};
  let label = source.name || source.value || "";
  if (scope === "channel") label = "# " + (source.name || sourceId);
  if (scope === "xsource") label = "@" + sourceId;
  if (scope === "bsource") label = String(source.value || "");
  prepareFilterModal(source, label);
}

const MANUAL_RELAY_SCOPE_META = {
  relay:   { key: "qq_c2c_relay_filter",       label: "Discord 手动转发", tab: "Discord" },
  xrelay:  { key: "x_c2c_relay_filter",        label: "X 手动转发",       tab: "X" },
  birelay: { key: "bilibili_c2c_relay_filter", label: "Bilibili 手动转发", tab: "Bilibili" },
};

// 统一「内容筛选」二级窗口：三个链路 Tab（Discord / X / Bilibili），
// 各自用独立 scope 编辑与保存；每次保存只写当前 Tab 对应的配置键。
function renderFilterScopeTabs() {
  const tabs = document.getElementById("filterScopeTabs");
  if (!tabs) return;
  const show = !!MANUAL_RELAY_SCOPE_META[filterEditScope];
  tabs.style.display = show ? "flex" : "none";
  if (!show) return;
  renderMultiLinkScopeSwitch(tabs, "scope", filterEditScope);
}

function openUnifiedRelayFilterModal() {
  switchRelayFilterTab("relay");
}

function switchRelayFilterTab(scope) {
  const meta = MANUAL_RELAY_SCOPE_META[scope];
  if (!meta) return;
  filterEditScope = scope;
  filterSourceTarget = null;
  prepareFilterModal(settings[meta.key] || {}, meta.label);
  renderFilterScopeTabs();
}

function closeFilterModal() {
  document.getElementById("filterModal").style.display = "none";
  filterSourceTarget = null;
  filterEditScope = "channel";
}

document.getElementById("filterUsernameAdd").addEventListener("click", function () { addFilterItem("usernames"); });
document.getElementById("filterKeywordAdd").addEventListener("click", function () { addFilterItem("keywords"); });
document.getElementById("filterTranslationEnabled").addEventListener("change", syncFilterTranslationInputs);
document.getElementById("filterUsernameInput").addEventListener("keydown", function (e) {
  if (e.key === "Enter") { e.preventDefault(); addFilterItem("usernames"); }
});
document.getElementById("filterKeywordInput").addEventListener("keydown", function (e) {
  if (e.key === "Enter") { e.preventDefault(); addFilterItem("keywords"); }
});

document.getElementById("filterSave").addEventListener("click", async function () {
  if (filterEditScope === "relay" || filterEditScope === "xrelay" || filterEditScope === "birelay") {
    const key = filterEditScope === "xrelay"
      ? "x_c2c_relay_filter"
      : filterEditScope === "birelay"
        ? "bilibili_c2c_relay_filter"
        : "qq_c2c_relay_filter";
    // 只保存该链路支持的类型。全选（= 不过滤）时同样落盘为完整支持集，
    // 而不是删除字段：X/Bilibili 的支持集与 Discord 六类型不同，删除字段
    // 会被读成"全部六类启用"，与面板显示不一致。
    const supported = filterSupportedTypes();
    settings[key] = {
      filter_message_types: filterMessageTypes.filter(function (t) {
        return supported.indexOf(t) >= 0;
      }),
    };
  } else {
    if (!filterSourceTarget || filterSourceTarget.epoch !== workspaceEpoch) return;
    const c = findSourceById(filterEditScope, filterSourceTarget.id);
    if (!c) {
      showAlert("保存失败", "该来源已变化，请重新打开内容筛选。");
      return;
    }
    c.translation_enabled = document.getElementById("filterTranslationEnabled").checked;
    c.translation_append_original = document.getElementById("filterTranslationAppendOriginal").checked;
    c.translation_show_datetime = document.getElementById("filterTranslationShowDatetime").checked;
    if (filterEditScope === "xsource") {
      c.filter_keywords = filterKeywords.slice();
      saveSourceMessageTypes(c, "xsource");
      if (filterXPostTypes.length > 0 && filterXPostTypes.length < FILTER_X_POST_TYPES.length) {
        c.filter_post_types = filterXPostTypes.slice();
      } else {
        delete c.filter_post_types;
      }
    } else if (filterEditScope === "bsource") {
      c.filter_keywords = filterKeywords.slice();
      saveSourceMessageTypes(c, "bsource");
      c.filter_bilibili_post_types = filterBilibiliPostTypes.slice();
      delete c.filter_tags;
      delete c.filter_title_keywords;
      delete c.filter_exclude_keywords;
      delete c.filter_authors;
    } else if (filterEditScope === "youtubeSource") {
      c.filter_keywords = filterKeywords.slice();
      saveSourceMessageTypes(c, "youtubeSource");
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
  }
  const imageCardChanges = readFilterImageCardOptions();
  closeFilterModal();
  await saveAndReloadWithImageCard(imageCardChanges);
});

async function saveAndReloadWithImageCard(changes) {
  const original = settings;
  const previous = Object.fromEntries(Object.keys(changes).map(key => [key, original[key]]));
  Object.assign(original, changes);
  let saved = false;
  try {
    saved = await saveAndReload();
    return saved;
  } finally {
    if (!saved && settings === original) {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete original[key];
        else original[key] = value;
      }
    }
  }
}

async function saveAndReload() {
  if (settings?._profile_bot && settings._profile_bot !== currentQQBotId()) {
    showAlert("保存失败", "账号正在切换，请等待配置加载完成"); return false;
  }
  const epoch = workspaceEpoch;
  const result = await jpost("/api/settings/save", settings);
  if (epoch !== workspaceEpoch) return false;
  if (!result.ok) {
    if (result.conflict) {
      const loaded = result.settings || await jget("/api/settings");
      if (epoch !== workspaceEpoch) return false;
      settings = loaded;
      renderSettings();
      showAlert("配置已刷新", "配置已被其他操作修改，当前页面已刷新，请重新确认本次修改。");
    } else {
      showAlert("保存失败", result.msg || "配置保存失败");
    }
    return false;
  }
  const loaded = await jget("/api/settings");
  if (epoch !== workspaceEpoch) return false;
  settings = loaded;
  renderSettings();
  await refreshChannelNames();
  await refreshStatus();
  return true;
}

// ---------- QQ Bot 配置弹窗 ----------
let qqBotDraft = [];

function closeQQConfigModal() {
  document.getElementById("qqConfigModal").style.display = "none";
}
function renderQQBotRows() {
  const body = document.getElementById("qqConfigBots");
  body.innerHTML = qqBotDraft.map(function (bot, index) {
    return '<div class="qq-bot-form-row">' +
      '<label style="font-size:13px;color:var(--muted);">账号名称<input class="qq-bot-name" data-i="' + index + '" maxlength="80" value="' + escapeHtml(bot.name || "") + '" autocomplete="off" style="display:block;width:100%;box-sizing:border-box;margin-top:4px;"></label>' +
      '<label style="font-size:13px;color:var(--muted);">AppID<input class="qq-bot-appid" data-i="' + index + '" value="' + escapeHtml(bot.appid || "") + '" autocomplete="off" spellcheck="false" style="display:block;width:100%;box-sizing:border-box;margin-top:4px;"></label>' +
      '<label style="font-size:13px;color:var(--muted);">AppSecret<input class="qq-bot-secret" data-i="' + index + '" type="password" autocomplete="new-password" style="display:block;width:100%;box-sizing:border-box;margin-top:4px;"></label>' +
      '<button type="button" class="danger qq-bot-remove" data-i="' + index + '" ' + (qqBotDraft.length === 1 && !personalQQConnections.some(item => item.enabled) ? 'disabled title="至少保留一个 Bot"' : 'title="删除此 Bot"') + '>删除</button></div>';
  }).join("");
  body.querySelectorAll(".qq-bot-remove").forEach(function (button) {
    button.addEventListener("click", function () {
      showConfirmDlg("删除 Bot 配置", "从当前编辑列表删除这个 Bot？保存后生效。", function () {
        qqBotDraft.splice(Number(button.dataset.i), 1);
        renderQQBotRows();
      }, "删除");
    });
  });
}
document.getElementById("qqConfigAddBot").addEventListener("click", function () {
  qqBotDraft.push({appid: "", name: ""});
  renderQQBotRows();
});
async function openQQConfigModal() {
  qqBotDraft = [];
  document.getElementById("qqConfigModal").style.display = "flex";
  let r;
  try {
    r = await jget("/api/bot/qq-config");
  } catch (e) { r = null; }
  if (r && r.ok) {
    qqBotAccounts = (r.bots || []).map(function (bot) { return {appid: bot.appid || "", name: bot.name || ""}; });
    qqBotDraft = qqBotAccounts.map(function (bot) { return {appid: bot.appid, name: bot.name}; });
    if (!qqBotDraft.length && !personalQQConnections.some(item => item.enabled)) qqBotDraft = [{appid: ""}];
    renderQQBotRows();
  } else {
    showAlert("读取配置失败", (r && r.msg) || "无法读取当前 QQ Bot 配置");
  }
}
async function saveQQConfig() {
  const bots = qqBotDraft.map(function (bot, index) {
    const name = document.querySelector('.qq-bot-name[data-i="' + index + '"]').value.trim();
    const appid = document.querySelector('.qq-bot-appid[data-i="' + index + '"]').value.trim();
    const secret = document.querySelector('.qq-bot-secret[data-i="' + index + '"]').value;
    return {appid: appid, name: name, secret: secret};
  });
  if (bots.some(function (bot) { return !bot.appid; })) { showAlert("QQ Bot 配置", "AppID 不能为空"); return; }
  const saveBtn = document.getElementById("qqConfigSave");
  saveBtn.disabled = true;
  let r;
  try {
    r = await jpost("/api/bot/qq-config", {bots: bots});
  } catch (e) { r = { ok: false, msg: "保存失败，请重试" }; }
  if (!r || !r.ok) {
    saveBtn.disabled = false;
    showAlert("保存失败", (r && r.msg) || "保存 QQ Bot 配置失败");
    return;
  }
  if (Array.isArray(r.bots)) {
    qqBotAccounts = r.bots.map(function (bot) { return {appid: bot.appid || "", name: bot.name || ""}; });
  } else {
    qqBotAccounts = bots.map(function (bot) { return {appid: bot.appid, name: bot.name}; });
  }
  if (r.restart_required !== false) {
    // 凭据变化后重启使新配置生效；仅改显示名称不重启。
    let rr;
    try {
      rr = await postBotLifecycle("/api/bot/restart");
    } catch (e) { rr = { ok: false }; }
    if (!rr || !rr.ok) {
      saveBtn.disabled = false;
      showAlert("配置已保存", "配置已保存，但机器人重启失败，请稍后手动重启");
      return;
    }
  }
  closeQQConfigModal();
  qqBotIds = (r.bot_ids || []).map(String).concat(personalQQConnections.map(item => "personal:" + item.uin));
  renderSettings();
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
  const profileIds = profiles.map(function (profile) { return String(profile.id || ""); });
  let priority = Array.isArray(settings && settings.ai_profile_priority)
    ? settings.ai_profile_priority.map(String).filter(function (id, index, ids) {
      return profileIds.includes(id) && ids.indexOf(id) === index;
    })
    : [];
  if (!priority.length) {
    if (profileIds.includes(active)) priority.push(active);
    profileIds.forEach(function (id) { if (!priority.includes(id)) priority.push(id); });
  } else {
    profileIds.forEach(function (id) { if (!priority.includes(id)) priority.push(id); });
  }
  const apiKeys = {};
  profiles.forEach(function (profile) {
    // 旧版误把密钥填进 api_key_env 时，迁移到临时密码框并恢复合法变量名；
    // 密钥只在当前页面内存中保留，保存时写入 .env.prod。
    if (profile.api_key_env && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(profile.api_key_env))) {
      apiKeys[String(profile.id)] = String(profile.api_key_env);
      profile.api_key_env = "WRELAY_AI_API_KEY";
    }
  });
  return {profiles: profiles, active: active, priority: priority, apiKeys: apiKeys};
}

function activeAiDraftProfile() {
  if (!aiProfileDraft) return null;
  return aiProfileDraft.profiles.find(function (profile) {
    return String(profile.id) === String(aiProfileDraft.active);
  }) || null;
}

function renderAiProfileEditor() {
  if (!aiProfileDraft) return;
  const profile = activeAiDraftProfile();
  const summary = document.getElementById("aiProfileEditorSummary");
  if (!summary || !profile) return;
  summary.textContent = "配置详情：" + (profile.name || profile.id || "当前配置") + (profile.model ? " · " + profile.model : "");
  document.getElementById("aiProfileName").value = profile.name || "";
  document.getElementById("aiBaseUrl").value = profile.base_url || "";
  document.getElementById("aiModel").value = profile.model || "";
  document.getElementById("aiApiKeyEnv").value = profile.api_key_env || "WRELAY_AI_API_KEY";
  document.getElementById("aiApiKey").value = (aiProfileDraft.apiKeys || {})[String(profile.id)] || "";
  document.getElementById("aiProfileDelete").disabled = aiProfileDraft.profiles.length <= 1;
  document.getElementById("aiApiTestStatus").textContent = "";
  renderAiModelPriorityList();
}

function captureAiApiKeyDraft() {
  const profile = activeAiDraftProfile();
  const input = document.getElementById("aiApiKey");
  if (!profile || !input || !aiProfileDraft) return;
  aiProfileDraft.apiKeys = aiProfileDraft.apiKeys || {};
  const id = String(profile.id);
  const previous = aiProfileDraft.apiKeys[id] || "";
  if (previous !== input.value && profile.last_test) {
    profile.last_test = null;
    profile.last_tested_model = "";
  }
  aiProfileDraft.apiKeys[id] = input.value;
}

function captureAiProfileEditorFields(profile) {
  if (!profile) return;
  const changed = profile.base_url !== document.getElementById("aiBaseUrl").value.trim() ||
    profile.model !== document.getElementById("aiModel").value.trim() ||
    profile.api_key_env !== document.getElementById("aiApiKeyEnv").value.trim();
  profile.name = document.getElementById("aiProfileName").value.trim();
  profile.base_url = document.getElementById("aiBaseUrl").value.trim();
  profile.model = document.getElementById("aiModel").value.trim();
  profile.api_key_env = document.getElementById("aiApiKeyEnv").value.trim();
  captureAiApiKeyDraft();
  if (changed) {
    profile.last_test = null;
    profile.last_tested_model = "";
  }
  renderAiModelPriorityList();
}

function aiProfileTestPayload(profile) {
  return {
    base_url: profile.base_url || "",
    model: profile.model || "",
    api_key_env: profile.api_key_env || "",
    api_key: (aiProfileDraft.apiKeys || {})[String(profile.id)] || "",
    timeout_seconds: profile.timeout_seconds,
  };
}

function recordAiTestResult(profile, result, fallbackMessage) {
  const model = String((result && result.model) || profile.model || "").trim();
  const ok = !!(result && result.ok);
  const message = ok
    ? String(result.msg || ("AI API 检测成功（" + model + "）"))
    : localizeUserError((result && result.msg) || fallbackMessage, "检测失败");
  profile.last_test = {
    ok: ok,
    model: model,
    message: message,
    at: Date.now(),
    diagnostic: result && result.diagnostic ? Object.assign({}, result.diagnostic) : null,
  };
  if (ok) profile.last_tested_model = model;
  renderAiModelPriorityList();
  return message;
}

function fallbackAiTestDiagnostic(profile, test) {
  const message = String((test && test.message) || "");
  if (message.includes("无法连接")) {
    return {
      category: "network",
      detail: "无法连接 AI API",
    };
  }
  if (message.includes("未返回检测结果")) {
    return {
      category: "empty_content",
      detail: "AI API 返回内容为空",
    };
  }
  return {
    category: test && test.ok ? "success" : "unknown",
    detail: test && test.ok ? "检测成功" : "检测失败",
  };
}

function aiTestDiagnosticLabel(category) {
  return {
    success: "请求成功",
    config: "配置错误",
    network: "网络连接",
    http: "HTTP 错误",
    invalid_json: "响应 JSON",
    response_format: "响应格式",
    empty_content: "响应内容",
    unknown: "未分类错误",
  }[String(category || "unknown")] || "检测诊断";
}

function renderAiTestDiagnostic(profile, test) {
  const diagnostic = test && test.diagnostic && typeof test.diagnostic === "object"
    ? test.diagnostic
    : fallbackAiTestDiagnostic(profile, test);
  const detail = String(diagnostic.detail || "").trim();
  const parts = ["类型：" + aiTestDiagnosticLabel(diagnostic.category)];
  if (detail) parts.push("原因：" + detail);
  return parts.map(function (part) { return escapeHtml(part); }).join(" · ");
}

function renderAiModelPriorityList() {
  const summary = document.querySelector("#aiModelPriorityMenu > summary");
  const list = document.getElementById("aiModelPriorityList");
  if (!summary || !list || !aiProfileDraft) return;
  const profilesById = new Map(aiProfileDraft.profiles.map(function (profile) {
    return [String(profile.id), profile];
  }));
  const profileIds = aiProfileDraft.profiles.map(function (profile) { return String(profile.id); });
  const priority = Array.isArray(aiProfileDraft.priority) ? aiProfileDraft.priority : [];
  const orderedIds = priority.filter(function (id, index) {
    return profilesById.has(String(id)) && priority.indexOf(id) === index;
  }).map(String);
  profileIds.forEach(function (id) { if (!orderedIds.includes(id)) orderedIds.push(id); });
  aiProfileDraft.priority = orderedIds;

  let successCount = 0;
  let failureCount = 0;
  aiProfileDraft.profiles.forEach(function (profile) {
    const test = profile.last_test;
    if (test && test.ok === true) successCount += 1;
    else if (test && test.ok === false) failureCount += 1;
    else if (!test && profile.last_tested_model && profile.last_tested_model === profile.model) successCount += 1;
  });
  summary.textContent = "模型优先级（可用 " + successCount + " · 失败 " + failureCount + " · 共 " + profileIds.length + "）";
  if (!orderedIds.length) {
    list.innerHTML = '<div class="ai-model-priority-empty">尚未添加 AI 模型配置。</div>';
    return;
  }

  list.innerHTML = orderedIds.map(function (id, index) {
    const profile = profilesById.get(id);
    if (!profile) return "";
    const legacyTest = profile.last_tested_model && profile.last_tested_model === profile.model
      ? {ok: true, model: profile.last_tested_model, message: "历史检测成功"}
      : null;
    const test = profile.last_test || legacyTest;
    const state = test && test.ok === true ? "ok" : (test && test.ok === false ? "failed" : "unknown");
    const isWorking = latestTranslationRuntime.provider === "ai" &&
      String(latestTranslationRuntime.model || "") === String(profile.model || "") &&
      (!latestTranslationRuntime.profile || String(latestTranslationRuntime.profile) === String(profile.name || ""));
    const displayState = isWorking ? "working" : state;
    const statusText = isWorking ? "工作中" : (state === "ok" ? "可用" : (state === "failed" ? "不可用" : "未检测"));
    const statusDetail = isWorking ? "最近一次真实翻译成功使用此模型" : (test ? renderAiTestDiagnostic(profile, test) : "检测后显示此模型状态");
    const orderLabel = index === 0 ? "首选" : "备用 " + index;
    const selected = String(id) === String(aiProfileDraft.active);
    return '<div class="ai-model-priority-row' + (selected ? ' selected' : '') + '" data-profile-id="' + escapeHtml(id) + '" role="listitem" tabindex="0" aria-current="' + (selected ? 'true' : 'false') + '" title="点击选择并编辑此配置">' +
      '<button class="ai-model-priority-handle" type="button" draggable="true" title="拖动调整模型优先级" aria-label="拖动调整模型优先级">' + SVG_GRIP + '</button>' +
      '<span class="ai-model-priority-rank">' + orderLabel + '</span>' +
      '<div class="ai-model-priority-copy"><strong>' + escapeHtml(String(profile.model || "未填写模型")) + '</strong>' +
        '<small>' + escapeHtml(String(profile.name || profile.id)) + '</small></div>' +
      '<span class="ai-model-priority-status ' + displayState + '" title="' + escapeHtml(statusDetail) + '">' +
        '<span class="ai-model-priority-status-line"><i aria-hidden="true"></i><span>' + statusText + '</span></span>' +
        '<small>' + statusDetail + '</small></span></div>';
  }).join("");

  if (list.dataset.dragHandlersAttached === "1") return;
  list.dataset.dragHandlersAttached = "1";
  function selectProfileRow(row) {
    if (!row || !aiProfileDraft) return;
    const profileId = String(row.dataset.profileId || "");
    if (!aiProfileDraft.profiles.some(function (profile) { return String(profile.id) === profileId; })) return;
    captureAiProfileEditorFields(activeAiDraftProfile());
    aiProfileDraft.active = profileId;
    const editorDetails = document.getElementById("aiProfileEditorDetails");
    if (editorDetails) editorDetails.open = true;
    renderAiProfileEditor();
  }
  list.addEventListener("click", function (event) {
    if (event.target.closest(".ai-model-priority-handle")) return;
    if (Date.now() <= Number(list.dataset.suppressRowClickUntil || 0)) return;
    selectProfileRow(event.target.closest(".ai-model-priority-row"));
  });
  list.addEventListener("keydown", function (event) {
    if (event.target.closest(".ai-model-priority-handle")) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest(".ai-model-priority-row");
    if (!row) return;
    event.preventDefault();
    selectProfileRow(row);
  });
  list.addEventListener("dragstart", function (event) {
    const handle = event.target.closest(".ai-model-priority-handle");
    const row = handle && handle.closest(".ai-model-priority-row");
    if (!row) { event.preventDefault(); return; }
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.profileId || "");
    row.classList.add("dragging");
  });
  list.addEventListener("dragover", function (event) {
    const row = event.target.closest(".ai-model-priority-row");
    if (!row) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    list.querySelectorAll(".ai-model-priority-row").forEach(function (item) {
      item.classList.remove("drop-before", "drop-after");
    });
    const rect = row.getBoundingClientRect();
    row.classList.add(event.clientY < rect.top + rect.height / 2 ? "drop-before" : "drop-after");
  });
  list.addEventListener("drop", function (event) {
    const row = event.target.closest(".ai-model-priority-row");
    const fromId = event.dataTransfer.getData("text/plain");
    if (!row || !fromId || !aiProfileDraft) return;
    event.preventDefault();
    const targetId = row.dataset.profileId || "";
    const fromIndex = aiProfileDraft.priority.indexOf(fromId);
    if (fromIndex < 0 || fromId === targetId) return;
    const rect = row.getBoundingClientRect();
    const insertBefore = event.clientY < rect.top + rect.height / 2;
    const reordered = aiProfileDraft.priority.slice();
    reordered.splice(fromIndex, 1);
    let targetIndex = reordered.indexOf(targetId);
    if (!insertBefore) targetIndex += 1;
    reordered.splice(targetIndex, 0, fromId);
    aiProfileDraft.priority = reordered;
    renderAiModelPriorityList();
  });
  list.addEventListener("dragend", function () {
    list.querySelectorAll(".ai-model-priority-row").forEach(function (item) {
      item.classList.remove("dragging", "drop-before", "drop-after");
    });
    list.dataset.suppressRowClickUntil = String(Date.now() + 500);
  });
}

async function persistAiTestRecord(profile) {
  if (!profile || !profile.last_test) return false;
  const draftKey = aiProfileDraft && aiProfileDraft.apiKeys
    ? String(aiProfileDraft.apiKeys[String(profile.id)] || "").trim()
    : "";
  if (draftKey) return false;
  const savedProfiles = Array.isArray(settings && settings.ai_profiles) ? settings.ai_profiles : [];
  const savedProfile = savedProfiles.find(function (item) {
    return item && String(item.id || "") === String(profile.id || "");
  });
  const legacyAi = settings && settings.ai && typeof settings.ai === "object" ? settings.ai : {};
  const matchesSavedConfig = savedProfile
    ? String(savedProfile.model || "") === String(profile.model || "") &&
      String(savedProfile.base_url || "") === String(profile.base_url || "") &&
      String(savedProfile.api_key_env || "") === String(profile.api_key_env || "")
    : String(profile.id || "") === "default" && !savedProfiles.length &&
      String(legacyAi.model || "") === String(profile.model || "") &&
      String(legacyAi.base_url || "") === String(profile.base_url || "") &&
      String(legacyAi.api_key_env || "WRELAY_AI_API_KEY") === String(profile.api_key_env || "");
  if (!matchesSavedConfig) return false;
  try {
    const result = await jpost("/api/translation/ai-test-record", {
      profile_id: String(profile.id || ""),
      record: profile.last_test,
    });
    if (!result || !result.ok) return false;

    // 检测记录单独保存后，同步当前页面的 settings。否则关闭弹窗再打开时，
    // cloneAiProfileDraft() 会从保存前的旧内存快照复制，误显示为“尚未检测”。
    settings = settings || {};
    if (!Array.isArray(settings.ai_profiles)) settings.ai_profiles = [];
    let updatedProfile = settings.ai_profiles.find(function (item) {
      return item && String(item.id || "") === String(profile.id || "");
    });
    if (!updatedProfile && String(profile.id || "") === "default" && !settings.ai_profiles.length) {
      updatedProfile = Object.assign({}, profile);
      settings.ai_profiles.push(updatedProfile);
      settings.ai_active_profile = settings.ai_active_profile || "default";
    }
    if (updatedProfile) {
      updatedProfile.last_test = Object.assign({}, profile.last_test);
      if (profile.last_test.ok) updatedProfile.last_tested_model = profile.last_test.model || "";
    }
    return true;
  } catch (err) {
    return false;
  }
}

async function testAiProfile(profile) {
  try {
    const result = await jpost("/api/translation/ai-test", aiProfileTestPayload(profile));
    const message = recordAiTestResult(profile, result, "检测失败");
    const persisted = await persistAiTestRecord(profile);
    return {result: result, message: message, persisted: persisted};
  } catch (err) {
    const result = {ok: false, msg: "检测请求失败"};
    const message = recordAiTestResult(profile, result, "检测请求失败");
    const persisted = await persistAiTestRecord(profile);
    return {result: result, message: message, persisted: persisted};
  }
}

async function testAiApi() {
  const button = document.getElementById("aiApiTest");
  const allButton = document.getElementById("aiApiTestAll");
  const status = document.getElementById("aiApiTestStatus");
  const profile = activeAiDraftProfile();
  if (!button || !status || !profile) return;
  captureAiProfileEditorFields(profile);
  button.disabled = true;
  if (allButton) allButton.disabled = true;
  status.textContent = "检测中…";
  const result = await testAiProfile(profile);
  status.textContent = result.message;
  button.disabled = false;
  if (allButton) allButton.disabled = false;
}

async function testAllAiApis() {
  const button = document.getElementById("aiApiTestAll");
  const currentButton = document.getElementById("aiApiTest");
  const status = document.getElementById("aiApiTestStatus");
  if (!button || !status || !aiProfileDraft) return;
  captureAiProfileEditorFields(activeAiDraftProfile());
  button.disabled = true;
  if (currentButton) currentButton.disabled = true;
  const profilesById = new Map(aiProfileDraft.profiles.map(function (profile) {
    return [String(profile.id), profile];
  }));
  const profiles = aiProfileDraft.priority.map(function (id) { return profilesById.get(String(id)); })
    .filter(Boolean);
  let successCount = 0;
  for (let i = 0; i < profiles.length; i += 1) {
    status.textContent = "正在检测全部 API（" + (i + 1) + "/" + profiles.length + "）…";
    const result = await testAiProfile(profiles[i]);
    if (result.result && result.result.ok) successCount += 1;
  }
  aiProfileDraft.last_batch_test = {
    success: successCount,
    total: profiles.length,
    at: Date.now(),
  };
  renderAiModelPriorityList();
  status.textContent = "";
  button.disabled = false;
  if (currentButton) currentButton.disabled = false;
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
    status.textContent = "TMT SecretId / SecretKey 已配置。";
  } else {
    status.textContent = "TMT 密钥尚未完整配置，请填写 SecretId 和 SecretKey。";
  }
}
async function saveTranslationConfig() {
  const profile = activeAiDraftProfile();
  if (!profile) { showAlert("保存失败", "请先新增或选择一个 AI 配置"); return; }
  captureAiProfileEditorFields(profile);
  captureAiApiKeyDraft();
  if (!profile.name) { showAlert("保存失败", "配置名称不能为空"); return; }
  if (!profile.api_key_env) { showAlert("保存失败", "API 密钥环境变量名不能为空"); return; }
  const aiEnvUpdates = new Map();
  for (const item of aiProfileDraft.profiles) {
    const envName = String(item.api_key_env || "").trim();
    const draftKey = String((aiProfileDraft.apiKeys || {})[String(item.id)] || "").trim();
    if (!draftKey) continue;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(envName)) {
      showAlert("保存失败", "配置「" + String(item.name || item.id) + "」的 API 密钥环境变量名格式不正确");
      return;
    }
    if (aiEnvUpdates.has(envName) && aiEnvUpdates.get(envName) !== draftKey) {
      showAlert("保存失败", "多个配置使用了同一密钥环境变量，但填写了不同密钥；请为它们设置不同的环境变量名");
      return;
    }
    aiEnvUpdates.set(envName, draftKey);
  }
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
  for (const [envName, keyValue] of aiEnvUpdates.entries()) {
    let aiEnvResult;
    try {
      aiEnvResult = await jpost("/api/translation/ai-env", {
        env_name: envName,
        api_key: keyValue,
      });
    } catch (e) { aiEnvResult = {ok: false, msg: "保存失败，请重试"}; }
    if (!aiEnvResult || !aiEnvResult.ok) {
      saveBtn.disabled = false;
      cancelBtn.disabled = false;
      showAlert("保存失败", (aiEnvResult && aiEnvResult.msg) || "保存 AI API 密钥失败");
      return;
    }
  }
  settings.ai_profiles = aiProfileDraft.profiles;
  settings.ai_profile_priority = aiProfileDraft.priority.slice();
  const primary = aiProfileDraft.profiles.find(function (item) {
    return String(item.id) === String(aiProfileDraft.priority[0] || "");
  }) || profile;
  settings.ai_active_profile = String(primary.id || "");
  syncLegacyAiConfig(primary);
  const saved = await saveAndReload();
  if (!saved) {
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
    return;
  }
  // .env.prod 中的密钥需要新 Bot 进程重新加载；失败时不回滚已保存配置。
  let restartResult;
  try { restartResult = await postBotLifecycle("/api/bot/restart"); } catch (e) { restartResult = {ok: false}; }
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
  saveBtn.disabled = true;
  let r;
  try {
    r = await jpost("/api/bot/discord-config", { token: token });
  } catch (e) { r = { ok: false, msg: "保存失败，请重试" }; }
  if (!r || !r.ok) {
    saveBtn.disabled = false;
    showAlert("保存失败", (r && r.msg) || "保存 Discord Bot 配置失败");
    return;
  }
  if (latestStatus && latestStatus.health) {
    latestStatus = {...latestStatus, health: {...latestStatus.health, discord: false}};
    renderPageCredentialShortcut();
  }
  // 保存成功后再重启，使新配置生效；重启失败不回滚配置
  let rr;
  try {
    rr = await postBotLifecycle("/api/bot/restart");
  } catch (e) { rr = { ok: false }; }
  if (!rr || !rr.ok) {
    saveBtn.disabled = false;
    await refreshStatus();
    showAlert("配置已保存", "配置已保存，但机器人重启失败，请稍后手动重启");
    return;
  }
  closeDiscordConfigModal();
  await refreshStatus();
}

const NETWORK_PROXY_FIELDS = {
  discord: "networkProxyDiscord",
  x: "networkProxyX",
  rsshub: "networkProxyRsshub",
  bilibili: "networkProxyBilibili",
  youtube: "networkProxyYoutube",
  steam: "networkProxySteam",
  translation: "networkProxyTranslation",
};
const networkProxyTestRequests = new WeakMap();
let networkProxyModalSession = 0;
function closeNetworkProxyModal() {
  networkProxyModalSession += 1;
  document.getElementById("networkProxyModal").style.display = "none";
}
function setNetworkProxyTestStatus(service, message, state) {
  const fieldId = NETWORK_PROXY_FIELDS[service];
  const status = fieldId && document.getElementById(fieldId + "Status");
  if (!status) return;
  status.textContent = message || "";
  status.classList.toggle("success", state === "success");
  status.classList.toggle("failure", state === "failure");
}
async function openNetworkProxyModal() {
  const session = ++networkProxyModalSession;
  const modal = document.getElementById("networkProxyModal");
  const saveButton = document.getElementById("networkProxySave");
  const detectButton = document.getElementById("networkProxyDetectSystem");
  const detectStatus = document.getElementById("networkProxySystemStatus");
  modal.style.display = "flex";
  saveButton.disabled = true;
  detectButton.disabled = true;
  if (detectStatus) {
    detectStatus.textContent = "";
    detectStatus.classList.remove("success", "failure");
  }
  const testButtons = modal.querySelectorAll("[data-proxy-test]");
  for (const button of testButtons) {
    networkProxyTestRequests.delete(button);
    button.disabled = true;
    setNetworkProxyTestStatus(button.dataset.proxyTest, "", "");
  }
  let result;
  try { result = await jget("/api/network-proxy"); } catch (_error) { result = null; }
  if (session !== networkProxyModalSession) return;
  if (!result || !result.ok || !result.proxies) {
    showAlert("读取代理设置失败", (result && result.msg) || "无法读取当前代理设置");
    return;
  }
  for (const [service, id] of Object.entries(NETWORK_PROXY_FIELDS)) {
    const input = document.getElementById(id);
    if (input) input.value = result.proxies[service] || "";
  }
  for (const button of testButtons) button.disabled = false;
  detectButton.disabled = false;
  saveButton.disabled = false;
}
async function testNetworkProxy(service, button) {
  const input = document.getElementById(NETWORK_PROXY_FIELDS[service]);
  const proxy = input ? input.value.trim() : "";
  if (!proxy) {
    setNetworkProxyTestStatus(service, "请先填写代理地址", "failure");
    input?.focus();
    return false;
  }
  const request = {session: networkProxyModalSession};
  networkProxyTestRequests.set(button, request);
  button.disabled = true;
  setNetworkProxyTestStatus(service, "正在检测…", "");
  let result;
  try {
    result = await jpost("/api/network-proxy/test", {service: service, proxy: proxy});
  } catch (_error) { result = null; }
  if (request.session !== networkProxyModalSession || networkProxyTestRequests.get(button) !== request) return false;
  networkProxyTestRequests.delete(button);
  button.disabled = false;
  if (input.value.trim() !== proxy) {
    setNetworkProxyTestStatus(service, "", "");
    return false;
  }
  const success = !!(result && result.ok);
  setNetworkProxyTestStatus(
    service,
    (result && result.msg) || "连接检测失败，请检查代理地址和网络",
    success ? "success" : "failure"
  );
  return success;
}
for (const [service, fieldId] of Object.entries(NETWORK_PROXY_FIELDS)) {
  document.getElementById(fieldId)?.addEventListener("input", function () {
    setNetworkProxyTestStatus(service, "", "");
  });
}
async function detectSystemNetworkProxy(button) {
  const session = networkProxyModalSession;
  const modal = document.getElementById("networkProxyModal");
  const status = document.getElementById("networkProxySystemStatus");
  const saveButton = document.getElementById("networkProxySave");
  if (saveButton.disabled) return;
  button.disabled = true;
  saveButton.disabled = true;
  status.textContent = "正在读取 Windows 系统代理…";
  status.classList.remove("success", "failure");
  let result;
  try { result = await jget("/api/network-proxy/detect-system"); }
  catch (_error) { result = null; }
  if (session !== networkProxyModalSession) return;
  saveButton.disabled = false;
  if (!result || !result.ok || !result.proxy) {
    status.textContent = (result && result.msg) || "读取系统代理失败，请检查 Clash Verge 状态。";
    status.classList.add("failure");
    button.disabled = false;
    return;
  }
  const fields = Object.values(NETWORK_PROXY_FIELDS).map(id => document.getElementById(id));
  for (const field of fields) field.value = result.proxy;
  status.textContent = "已填入全部 " + fields.length + " 项代理，正在检测连接…";
  const checks = await Promise.all(Array.from(modal.querySelectorAll("[data-proxy-test]"), function (testButton) {
    return testNetworkProxy(testButton.dataset.proxyTest, testButton);
  }));
  if (session !== networkProxyModalSession) return;
  const passed = checks.filter(Boolean).length;
  if (fields.some(field => field.value.trim() !== result.proxy)) {
    status.textContent = "部分代理地址已修改，请检测修改后的地址。";
  } else if (passed === fields.length) {
    status.textContent = "全部 " + fields.length + " 项连接检测通过。点击「保存并重启机器人」应用设置。";
    status.classList.add("success");
  } else {
    status.textContent = "已填入全部 " + fields.length + " 项代理，" + passed + " 项检测通过。请查看各行结果。";
    status.classList.add("failure");
  }
  button.disabled = false;
}
async function saveNetworkProxy() {
  const modal = document.getElementById("networkProxyModal");
  const saveButton = document.getElementById("networkProxySave");
  const proxies = {};
  for (const [service, id] of Object.entries(NETWORK_PROXY_FIELDS)) {
    const input = document.getElementById(id);
    proxies[service] = input ? input.value.trim() : "";
  }
  saveButton.disabled = true;
  let result;
  try { result = await jpost("/api/network-proxy/save", {proxies: proxies}); }
  catch (_error) { result = null; }
  if (!result || !result.ok) {
    saveButton.disabled = false;
    showAlert("保存代理设置失败", (result && result.msg) || "保存失败，请稍后重试");
    return;
  }
  if (!result.changed && modal.dataset.restartPending !== "1") {
    saveButton.disabled = false;
    closeNetworkProxyModal();
    return;
  }
  let restarted;
  try { restarted = await postBotLifecycle("/api/bot/restart"); } catch (_error) { restarted = {ok: false}; }
  saveButton.disabled = false;
  if (!restarted || !restarted.ok) {
    modal.dataset.restartPending = "1";
    showAlert("代理设置已保存", "机器人重启失败；代理设置将在机器人下次启动时生效，请稍后手动重启。");
    return;
  }
  delete modal.dataset.restartPending;
  closeNetworkProxyModal();
  await refreshStatus();
}

document.addEventListener("contextmenu", async function (e) {
  const value = e.target.closest(".fgsection :is(.middle-value, .id-value)[data-copy-value]");
  if (!value || value.closest(".scheduled-message-schedule")) return;
  e.preventDefault();
  e.stopPropagation();
  await copyForwardingValue(value);
});

document.addEventListener("click", async function (e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.id === "btnNetworkProxy") {
    await openNetworkProxyModal();
    return;
  }
  if (btn.id === "networkProxyDetectSystem") {
    await detectSystemNetworkProxy(btn);
    return;
  }
  if (btn.id === "networkProxySave") {
    await saveNetworkProxy();
    return;
  }
  if (btn.dataset.proxyTest) {
    await testNetworkProxy(btn.dataset.proxyTest, btn);
    return;
  }
  if (btn.id === "btnStatusSheenToggle") {
    const enabled = btn.dataset.on !== "1";
    try { localStorage.setItem(STATUS_SHEEN_KEY, enabled ? "1" : "0"); } catch (err) {}
    applyStatusSheenPreference(enabled);
    renderStatusSheenToggle(enabled);
    return;
  }
  if (btn.dataset.scrollTarget) {
    const target = document.getElementById(btn.dataset.scrollTarget);
    if (target) target.scrollIntoView({block: "start", behavior: "auto"});
    return;
  }
  if (btn.dataset.actionTarget) {
    const target = document.getElementById(btn.dataset.actionTarget);
    if (target) target.click();
    return;
  }
  if (btn.id === "confirmOk") {
    const fn = confirmAction;
    hideConfirm();
    if (fn) await fn();
    return;
  }
  if (btn.classList.contains("modal-x") || btn.dataset.close) {
    const closeMap = {
      runtimeLogModal: function () { document.getElementById("runtimeLogModal").style.display = "none"; },
      confirmModal: hideConfirm,
      bilibiliSourceModal: closeBilibiliSourceModal,
      youtubeSourceModal: closeYoutubeSourceModal,
      filterModal: closeFilterModal,
      relayChannelsModal: closeRelayChannelsModal,
      translationChainsModal: closeTranslationChainsModal,
      qqWhitelistModal: closeQQWhitelistModal,
      translationConfigModal: closeTranslationConfigModal,
      qqConfigModal: closeQQConfigModal,
      personalQQModal: function () { window.WRelayPersonalQQ.close(); },
      discordConfigModal: closeDiscordConfigModal,
      youtubeApiKeyModal: closeYoutubeApiKeyModal,
      scheduledMessageContentModal: closeScheduledMessageEditor,
      messageTraceModal: closeMessageTraceModal,
      diagnosticModal: closeDiagnosticModal,
      backupRestoreModal: closeBackupRestore,
      networkProxyModal: closeNetworkProxyModal,
      chainDetailModal: closeChainDetailModal,
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
  if (btn.id === "btnBilibiliProtection") {
    openBilibiliProtectionModal();
    return;
  }
  if (btn.id === "btnYoutubeProtection") {
    openYoutubeProtectionModal();
    return;
  }
  if (btn.id === "btnXProtection") {
    openXProtectionModal();
    return;
  }
  if (btn.id === "aiProfileAdd") {
    if (!aiProfileDraft) aiProfileDraft = cloneAiProfileDraft();
    captureAiProfileEditorFields(activeAiDraftProfile());
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
    aiProfileDraft.priority.push(id);
    aiProfileDraft.apiKeys = aiProfileDraft.apiKeys || {};
    aiProfileDraft.apiKeys[id] = "";
    aiProfileDraft.last_batch_test = null;
    aiProfileDraft.active = id;
    renderAiProfileEditor();
    const editorDetails = document.getElementById("aiProfileEditorDetails");
    if (editorDetails) editorDetails.open = true;
    return;
  }
  if (btn.id === "aiApiTest") {
    await testAiApi();
    return;
  }
  if (btn.id === "aiApiTestAll") {
    await testAllAiApis();
    return;
  }
  if (btn.id === "aiProfileDelete") {
    if (!aiProfileDraft || aiProfileDraft.profiles.length <= 1) return;
    const index = aiProfileDraft.profiles.findIndex(function (profile) {
      return String(profile.id) === String(aiProfileDraft.active);
    });
    showConfirmDlg("删除翻译配置", "删除当前翻译配置及其密钥草稿？保存后生效。", function () {
      if (index >= 0) aiProfileDraft.profiles.splice(index, 1);
      aiProfileDraft.priority = aiProfileDraft.priority.filter(function (id) {
        return String(id) !== String(aiProfileDraft.active);
      });
      if (aiProfileDraft.apiKeys) delete aiProfileDraft.apiKeys[String(aiProfileDraft.active)];
      aiProfileDraft.last_batch_test = null;
      aiProfileDraft.active = String((aiProfileDraft.profiles[Math.max(0, index - 1)] || aiProfileDraft.profiles[0]).id);
      renderAiProfileEditor();
    }, "删除");
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
  if (["btnMaintenanceOpen", "btnMaintenanceBanner"].includes(btn.id)) { openBackupRestore(); return; }
  if (["btnAutoBackupToggle", "btnAutoBackupNow", "btnAutoBackupRestore"].includes(btn.id)) { await runMaintenanceAction(btn); return; }
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
  if (btn.id === "btnFailureNotificationToggle" || btn.id === "failureNotificationShortcut") {
    if (!settings) return;
    settings.failure_notification = Object.assign(
      {cooldown_seconds: 900},
      settings.failure_notification || {},
      {enabled: btn.dataset.on !== "1"},
    );
    await saveAndReload();
    return;
  }
  if (btn.id === "btnUnifiedRelaySources") {
    openUnifiedRelaySourcesModal();
    return;
  }
  if (btn.id === "btnUnifiedRelayFilter") {
    openUnifiedRelayFilterModal();
    return;
  }
  if (btn.id === "btnTranslationChains") {
    openTranslationChainsModal();
    return;
  }
  if (btn.dataset.rscope) {
    switchRelayChannelsTab(btn.dataset.rscope);
    return;
  }
  if (btn.dataset.scope && MANUAL_RELAY_SCOPE_META[btn.dataset.scope]) {
    switchRelayFilterTab(btn.dataset.scope);
    return;
  }
  if (btn.id === "relayChannelsSave") {
    await saveRelayChannels();
    return;
  }
  if (btn.dataset.fold) {
    const el = document.getElementById(btn.dataset.fold);
    const folded = el.style.display === "none";
    if (!setForwardingPaneFold(btn.dataset.fold, !folded)) {
      el.style.display = folded ? "" : "none";
      btn.classList.toggle("folded", !folded);
      btn.setAttribute("aria-expanded", String(folded));
    }
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
    const ctx = FG_CTX[btn.dataset.ctx];
    if (ctx) startFgRename(ctx, btn.dataset.id);
    return;
  }
  if (k === "fgDel") {
    const ctx = FG_CTX[btn.dataset.ctx];
    if (!ctx) return;
    const groupId = btn.dataset.id;
    const fg = getFgList(ctx).find(item => item.id === groupId);
    // Discord/X 至少保留 1 个转发组；QQ→QQ 允许删除仅含一个群的最后一组。
    if (!fg) return;
    const epoch = workspaceEpoch;
    const removeGroup = async function () {
      if (epoch !== workspaceEpoch) return;
      // 删除时基于最新 settings 解析，避免操作期间对象被替换
      const curList = getFgList(ctx);
      const targetIndex = curList.findIndex(function (x) { return x.id === groupId; });
      if (targetIndex < 0) return;
      curList.splice(targetIndex, 1);
      if (ctx.activeId === groupId) {
        const next = curList[Math.max(0, targetIndex - 1)] || null;
        ctx.activeId = next ? next.id : null;
        try { localStorage.setItem(ctx.storageKey, ctx.activeId || ""); } catch (e) {}
      }
      await saveAndReload();
    };
    if (forwardingGroupIsEmpty(ctx, fg)) {
      await removeGroup();
    } else {
      showConfirmDlg("删除转发组", "确定删除转发组「" + fg.name + "」？成员关系会移除，但" + (ctx.key === "x" || ctx.key === "youtube" ? "来源和群" : ctx.key === "qq" ? "群" : "频道和群") + "本身不会删除。", removeGroup, "删除");
    }
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
  if (btn.id === "btnAddBilibiliFg") {
    await addForwardingGroup(FG_BILIBILI);
    return;
  }
  if (btn.id === "btnAddYoutubeFg") {
    await addForwardingGroup(FG_YOUTUBE);
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
  if (btn.id === "translationConfigCancel") {
    closeTranslationConfigModal();
    return;
  }
  if (btn.id === "translationConfigSave") {
    saveTranslationConfig();
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
  if (btn.id === "discordConfigShortcut") {
    openDiscordConfigModal();
    return;
  }
  if (btn.id === "btnYoutubeApiKeyConfig" || btn.id === "youtubeApiKeyShortcut") {
    openYoutubeApiKeyModal();
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
  if (btn.id === "btnAddBilibiliSource") {
    // 「注册」：登记 Bilibili 的 UP 主、标签或关键词来源，不自动加入转发组
    openBilibiliSourceRegister();
    return;
  }
  if (btn.id === "btnAddBilibiliFgSource") {
    openFgAddPicker(FG_BILIBILI, "biliSource");
    return;
  }
  if (btn.id === "btnAddBilibiliFgGroup") {
    openFgAddPicker(FG_BILIBILI, "group");
    return;
  }
  if (btn.id === "btnAddYoutubeSource") {
    // 「注册频道」：登记 YouTube 频道（UC 频道 ID 或 @handle），不自动加入转发组
    openYoutubeSourceRegister();
    return;
  }
  if (btn.id === "btnAddYoutubeFgSource") {
    openFgAddPicker(FG_YOUTUBE, "youtubeSource");
    return;
  }
  if (btn.id === "btnAddYoutubeFgGroup") {
    openFgAddPicker(FG_YOUTUBE, "group");
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
  if (btn.id === "btnSyncBilibiliGroups") {
    await openRegistrationReview("groups");
    return;
  }
  if (btn.id === "btnSyncYoutubeGroups") {
    await openRegistrationReview("groups");
    return;
  }
  if (btn.id === "btnSyncQQGroups") {
    await openRegistrationReview("groups");
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
  if (btn.id === "btnScanChannels") {
    scanReadableChannels(btn);
    return;
  }
  const rowId = btn.closest("tr[data-id]")?.dataset.id;
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
  if (k === "biliFgSourceRemove") {
    removeFgMember(FG_BILIBILI, btn.dataset.id, "sources");
    return;
  }
  if (k === "biliSourceFilterEdit") {
    openSourceFilterModal("bsource", rowId);
    return;
  }
  if (k === "biliSourceEdit") {
    openSourceContentEditor("bsource", rowId);
    return;
  }
  if (k === "youtubeFgSourceRemove") {
    removeFgMember(FG_YOUTUBE, btn.dataset.id, "sources");
    return;
  }
  if (k === "youtubeSourceFilterEdit") {
    openSourceFilterModal("youtubeSource", rowId);
    return;
  }
  if (k === "youtubeSourceEdit") {
    openSourceContentEditor("youtubeSource", rowId);
    return;
  }
  if (k === "youtubeFgGroupRemove") {
    removeFgMember(FG_YOUTUBE, btn.dataset.id, "groups");
    return;
  }
  if (k === "biliFgGroupRemove") {
    removeFgMember(FG_BILIBILI, btn.dataset.id, "groups");
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
  } else if (k === "groupEdit") {
    const group = (settings.qq_group_openids || []).find(function (item) { return String(item.openid) === btn.dataset.id; });
    if (group) editGroupDetails(group);
    return;
  } else if (k === "userEdit") {
    const u = (settings.qq_user_openids || []).find(item => String(item.openid) === rowId);
    if (u) editUserDetails(u);
    return;
  } else if (k === "userDel" || k === "xUserDel") {
    const u = (settings.qq_user_openids || []).find(item => String(item.openid) === rowId);
    if (!u) return;
    const epoch = workspaceEpoch;
    const label = (u.name || "").trim() || (u.remark || "").trim() || identityName("users", u.openid) || u.openid;
    showConfirmDlg("删除用户", "确定要删除用户「" + label + "」吗？", async function () {
      if (epoch !== workspaceEpoch) return;
      const index = (settings.qq_user_openids || []).findIndex(item => String(item.openid) === rowId);
      if (index < 0) return;
      settings.qq_user_openids.splice(index, 1);
      await saveAndReload();
    }, "删除");
  } else if (k === "chanFilter") {
    openSourceFilterModal("channel", rowId);
  } else if (k === "xSourceFilter") {
    openSourceFilterModal("xsource", rowId);
  } else if (k === "chanEdit") {
    openSourceContentEditor("channel", rowId);
  } else if (k === "xSourceEdit") {
    openSourceContentEditor("xsource", rowId);
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
    openExternal("https://q.qq.com/qqbot/dashboard/");

  } else if (btn.id === "btnDiscordDev") {
    openExternal("https://discord.com/developers/home");
  } else if (btn.id === "btnOpenRuntimeLog") {
    document.getElementById("runtimeLogModal").style.display = "flex";
    if (logAutoScroll && logFollow) {
      const log = document.getElementById("log");
      log.scrollTop = log.scrollHeight;
    }
  } else if (btn.id === "btnLogAutoScroll") {
    logAutoScroll = !logAutoScroll;
    if (logAutoScroll) {
      logFollow = true;
      const log = document.getElementById("log");
      log.scrollTop = log.scrollHeight;
    }
    renderLogAutoScrollToggle();
  } else if (btn.id === "btnCopyLog") {
    const originalText = btn.textContent;
    try {
      await copyTextToClipboard(document.getElementById("log").textContent);
      btn.textContent = "已复制";
    } catch (err) {
      btn.textContent = "复制失败";
    }
    setTimeout(function () { btn.textContent = originalText; }, 1100);
  } else if (btn.id === "btnClearLog") {
    showConfirmDlg("清空运行日志", "确定清空运行日志？清空后无法恢复。", async function () {
      const r = await jpost("/api/bot/log/clear");
      if (!r.ok) showAlert("清空失败", r.msg || "清空失败");
      await refreshStatus();
    }, "清空");
  } else if (btn.id === "btnStart") {
    await runBotAction("/api/bot/start", "启动失败", "启动机器人失败");
    await sleep(800);
    await Promise.all([refreshStatus(), refreshWatchedChainStatuses()]);
  } else if (btn.id === "btnStop") {
    await runBotAction("/api/bot/stop", "停止失败", "停止机器人失败");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnRestart") {
    await runBotAction("/api/bot/restart", "重启失败", "重启机器人失败");
    await sleep(1500);
    await Promise.all([refreshStatus(), refreshWatchedChainStatuses()]);
  } else if (btn.id === "btnSettingsAutostart") {
    const stateButton = document.getElementById("btnSettingsAutostart");
    const enabled = stateButton.dataset.on !== "1";
    btn.disabled = true;
    stateButton.disabled = true;
    let r;
    try { r = await jpost("/api/autostart/set", {enabled: enabled}); }
    catch (err) { r = {ok: false}; }
    if (!r || !r.ok) showAlert("设置失败", "设置开机自启失败");
    renderAutostart(stateButton, r && r.ok ? !!r.enabled : stateButton.dataset.on === "1");
    stateButton.disabled = false;
    btn.disabled = false;
  } else if (btn.id === "btnBilibiliAutoResolveToggle") {
    settings.bilibili_auto_resolve = settings.bilibili_auto_resolve || {};
    settings.bilibili_auto_resolve.enabled = btn.dataset.on !== "1";
    await saveAndReload();
  } else if (btn.id === "btnTranslationDiscord") {
    const enabled = btn.dataset.on !== "1";
    (settings.discord_channels || []).forEach(function (channel) {
      channel.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnTranslationX") {
    const enabled = btn.dataset.on !== "1";
    (settings.x_rss_sources || []).forEach(function (source) {
      source.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnTranslationBilibili") {
    const enabled = btn.dataset.on !== "1";
    (settings.bilibili_sources || []).forEach(function (source) {
      source.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnTranslationYoutube") {
    const enabled = btn.dataset.on !== "1";
    (settings.youtube_sources || []).forEach(function (source) {
      source.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnTranslationSteam") {
    const enabled = btn.dataset.on !== "1";
    (settings.steam_sources || []).forEach(function (source) {
      source.translation_enabled = enabled;
    });
    await saveAndReload();
  } else if (btn.id === "btnTranslationManual") {
    settings.translation = settings.translation || {};
    settings.translation.enabled = btn.dataset.on !== "1";
    await saveAndReload();
  } else if (btn.id === "btnCheckUpdates") {
    await runUpdateAction();
  } else if (btn.id === "btnAutoUpdateCheck") {
    const enabled = btn.dataset.on !== "1";
    applyAutoUpdateCheckPreference(enabled);
    try { localStorage.setItem(AUTO_UPDATE_CHECK_KEY, enabled ? "1" : "0"); } catch (e) {}
    if (enabled) await checkForUpdates();
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
  }
});

const keyboardModalClosers = {
  personalQQModal: function () { window.WRelayPersonalQQ.close(); },
  confirmModal: hideConfirm,
  bilibiliSourceModal: closeBilibiliSourceModal,
  youtubeSourceModal: closeYoutubeSourceModal,
  filterModal: closeFilterModal,
  relayChannelsModal: closeRelayChannelsModal,
  translationChainsModal: closeTranslationChainsModal,
  qqWhitelistModal: closeQQWhitelistModal,
  translationConfigModal: closeTranslationConfigModal,
  qqConfigModal: closeQQConfigModal,
  discordConfigModal: closeDiscordConfigModal,
  youtubeApiKeyModal: closeYoutubeApiKeyModal,
  scheduledMessageContentModal: closeScheduledMessageEditor,
  messageTraceModal: closeMessageTraceModal,
  diagnosticModal: closeDiagnosticModal,
  backupRestoreModal: closeBackupRestore,
  networkProxyModal: closeNetworkProxyModal,
  runtimeLogModal: function () { document.getElementById("runtimeLogModal").style.display = "none"; },
};
const keyboardModalSaveButtons = {
  personalQQModal: "personalQQSave",
  bilibiliSourceModal: "bilibiliSourceSave",
  youtubeSourceModal: "youtubeSourceSave",
  filterModal: "filterSave",
  relayChannelsModal: "relayChannelsSave",
  translationConfigModal: "translationConfigSave",
  qqConfigModal: "qqConfigSave",
  discordConfigModal: "discordConfigSave",
  networkProxyModal: "networkProxySave",
  youtubeApiKeyModal: "youtubeApiKeySave",
  scheduledMessageContentModal: "scheduledMessageContentConfirm",
};

document.addEventListener("keydown", async function (event) {
  if (event.defaultPrevented || event.isComposing) return;
  const focused = document.activeElement;
  const visibleModal = Object.keys(keyboardModalClosers).map(id => document.getElementById(id))
    .find(modal => modal && getComputedStyle(modal).display !== "none");
  if (event.key === "Tab" && visibleModal) {
    const controls = [...visibleModal.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')]
      .filter(el => !el.disabled && el.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (first && (!visibleModal.contains(focused) || (event.shiftKey ? focused === first : focused === last))) {
      event.preventDefault(); (event.shiftKey ? last : first).focus();
    }
    return;
  }
  // Checkboxes enable routing; they are never deletion selections. Only a
  // focused delete action with an existing confirmation flow accepts Delete.
  if (event.key === "Delete" && !event.ctrlKey && !event.altKey && !event.metaKey &&
      focused?.matches('button[data-kind="fgDel"], button[data-kind="userDel"], button[data-kind="xUserDel"]') && !focused.disabled &&
      !Object.keys(keyboardModalClosers).some(id => {
        const modal = document.getElementById(id);
        return modal && getComputedStyle(modal).display !== "none";
      })) {
    event.preventDefault(); focused.click(); return;
  }
  if (focused?.matches(".fgsection :is(.middle-value, .id-value)[data-copy-value]") &&
      (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c" && !window.getSelection()?.toString()) ||
       (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key === "Enter" || event.key === " ")))) {
    event.preventDefault();
    copyForwardingValue(focused);
    return;
  }
  if ((event.key === "Enter" || event.key === " ") && focused?.matches(".chan-name-edit")) {
    event.preventDefault(); focused.click(); return;
  }
  if (event.key === "Escape") {
    if (window.dismissWRelayTooltip?.()) { event.preventDefault(); return; }
    const modalId = Object.keys(keyboardModalClosers).find(function (id) {
      const modal = document.getElementById(id);
      return modal && getComputedStyle(modal).display !== "none";
    });
    if (modalId) {
      event.preventDefault();
      keyboardModalClosers[modalId]();
    }
    return;
  }
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "s") return;
  const modalId = Object.keys(keyboardModalClosers).find(function (id) {
    const modal = document.getElementById(id);
    return modal && getComputedStyle(modal).display !== "none";
  });
  if (modalId) {
    const saveButtonId = modalId === "confirmModal" && document.getElementById("confirmOk").dataset.saveForm === "true"
      ? "confirmOk" : keyboardModalSaveButtons[modalId];
    const saveButton = saveButtonId && document.getElementById(saveButtonId);
    if (saveButton && !saveButton.disabled) { event.preventDefault(); saveButton.click(); }
    return;
  }
  const active = document.activeElement;
  if (active && ["xRsshubBase", "xPollInterval", "bilibiliRsshubBase", "bilibiliPollInterval", "youtubePollInterval"].includes(active.id)) {
    event.preventDefault();
    active.dispatchEvent(new Event("change", {bubbles: true}));
    return;
  }

});

document.addEventListener("change", async function (e) {
  const el = e.target;
  if (el.id === "themeMode") {
    applyTheme(el.value === THEME_LIGHT ? THEME_LIGHT : THEME_DARK);
    return;
  }
  if (el.id === "translationProvider" || el.id === "translationFallback") {
    if (el.id === "translationProvider") syncTranslationFallback("translationProvider", "translationFallback");
    saveAIControls();
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
  if (el.id === "bilibiliSourceAllEnabled") {
    const fg = getActiveFg(FG_BILIBILI);
    const sourceSet = memberIdSet(fg && fg.sources);
    (settings.bilibili_sources || []).forEach(function (source) {
      const sourceId = String(source.id || bilibiliSourceId(source.kind || "up", source.value || ""));
      if (sourceSet[sourceId]) source.enabled = el.checked;
    });
    await saveAndReload();
    return;
  }
  if (el.id === "youtubeSourceAllEnabled") {
    const fg = getActiveFg(FG_YOUTUBE);
    const sourceSet = memberIdSet(fg && fg.sources);
    (settings.youtube_sources || []).forEach(function (source) {
      const sourceId = String(source.id || youtubeSourceId(source.value));
      if (sourceSet[sourceId]) source.enabled = el.checked;
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
  if (el.id === "bilibiliRsshubBase") {
    settings.bilibili_rsshub_base = el.value.trim().replace(/\/+$/, "");
    await saveAndReload();
    return;
  }
  if (el.id === "bilibiliPollInterval") {
    let v = parseInt(el.value, 10);
    if (!(v > 0)) v = 300;
    if (v < 30) v = 30;
    settings.bilibili_poll_interval = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (el.id === "youtubePollInterval") {
    let v = parseInt(el.value, 10);
    if (!(v > 0)) v = 300;
    if (v < 30) v = 30;
    settings.youtube_poll_interval = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (el.id === "bilibiliMinSendInterval" || el.id === "bilibiliHourlyLimit" || el.id === "bilibiliBacklogLimit" || el.id === "bilibiliOverflowPolicy") {
    const protection = Object.assign({
      min_send_interval: 60,
      hourly_limit: 10,
      backlog_limit: 10,
      overflow_policy: "latest",
    }, settings.bilibili_protection || {});
    if (el.id === "bilibiliMinSendInterval") {
      let v = parseInt(el.value, 10);
      if (!(v >= 0)) v = 60;
      if (v > 86400) v = 86400;
      protection.min_send_interval = v;
      el.value = v;
    } else if (el.id === "bilibiliHourlyLimit") {
      let v = parseInt(el.value, 10);
      if (!(v > 0)) v = 10;
      if (v > 10000) v = 10000;
      protection.hourly_limit = v;
      el.value = v;
    } else if (el.id === "bilibiliBacklogLimit") {
      let v = parseInt(el.value, 10);
      if (!(v > 0)) v = 10;
      if (v > 1000) v = 1000;
      protection.backlog_limit = v;
      el.value = v;
    } else {
      protection.overflow_policy = el.value === "oldest" ? "oldest" : "latest";
    }
    settings.bilibili_protection = protection;
    await saveAndReload();
    return;
  }
  if (el.id === "xUserAllEnabled") {
    settings.qq_user_openids.forEach(function (u) { u.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "scheduledGroupTargetsAllEnabled") {
    const group = activeScheduledMessageGroup();
    if (!group) return;
    const qqGroups = (settings.qq_group_openids || []).filter(groupInActiveQQBotWorkspace);
    scheduledMessageGroupTargetSelection(group, qqGroups).ids.forEach(function (id) { setScheduledTargetEnabled(group, id, el.checked); });
    await saveScheduledMessageGroups();
    return;
  }
  if (el.id === "groupAllEnabled" || el.id === "xGroupAllEnabled" || el.id === "bilibiliGroupAllEnabled" || el.id === "youtubeGroupAllEnabled" || el.id === "qqGroupAllEnabled") {
    const ctx = el.id === "xGroupAllEnabled" ? FG_X : el.id === "bilibiliGroupAllEnabled" ? FG_BILIBILI : el.id === "youtubeGroupAllEnabled" ? FG_YOUTUBE : el.id === "qqGroupAllEnabled" ? FG_QQ : FG_DISCORD;
    const fg = getActiveFg(ctx);
    (fg && fg.groups || []).forEach(function (id) { setFgTargetEnabled(fg, id, el.checked); });
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
  if (el.dataset.kind === "forwardGroupTarget") {
    const fg = getActiveFg(FG_CTX[el.dataset.fg] || FG_DISCORD);
    if (!fg) return;
    setFgTargetEnabled(fg, el.dataset.openid, el.checked);
    await saveAndReload();
    return;
  }
  if (el.dataset.kind === "scheduledTargetGroup") {
    setScheduledTargetEnabled(activeScheduledMessageGroup(), el.dataset.openid, el.checked);
    await saveScheduledMessageGroups();
    return;
  }
  if (el.dataset.kind === "xSource") {
    const s = (settings.x_rss_sources || [])[Number(el.dataset.i)];
    if (s) {
      s.enabled = el.checked;
      await saveAndReload();
    }
    return;
  }
  if (el.dataset.kind === "biliSource") {
    const source = (settings.bilibili_sources || [])[Number(el.dataset.i)];
    if (source) {
      source.enabled = el.checked;
      await saveAndReload();
    }
    return;
  }
  if (el.dataset.kind === "youtubeSource") {
    const source = (settings.youtube_sources || [])[Number(el.dataset.i)];
    if (source) {
      source.enabled = el.checked;
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

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

let latestStatus = null;
let latestYoutubeStatus = null;

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
    value.className = "diagnostic-result " + (row[1] === "无" ? "neutral" : row[2] ? "ok" : "warn");
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
  const owner = activeQQBotId;
  const epoch = workspaceEpoch;
  try {
    const st = await jget("/api/status");
    if (owner !== activeQQBotId || epoch !== workspaceEpoch || (settings && settings._profile_bot && settings._profile_bot !== owner)) return;
    const processChanged = latestStatus && (latestStatus.running !== st.running || latestStatus.pid !== st.pid);
    latestStatus = st;
    renderStatus(st);
    if (processChanged) void refreshWatchedChainStatuses();
    if (st.scheduled_message !== undefined && settings && !scheduledMessageEditorFocused() && !scheduledMessageTargetPickerOpen && !scheduledMessageGroupRenameActive) {
      settings.scheduled_message = preserveScheduledMessageAttachments(
        st.scheduled_message,
        settings.scheduled_message,
      );
      renderScheduledMessage(settings.scheduled_message);
    }
  } catch (err) {
    const badge = document.getElementById("badge");
    badge.textContent = "不可用";
    badge.className = "badge warn";
    badge.title = err && err.message ? "管理功能不可用：" + err.message : "管理功能不可用";
    setStatusCardAmbient(badge, false);
  }
}

// Keep the complete value in the DOM while showing both ends in narrow cells.
function setupForwardingValueClipping() {
  const tableIds = [
    "chanList", "groupList", "userList", "xSourceList", "xGroupList", "xUserList",
    "bilibiliSourceList", "bilibiliGroupList", "youtubeSourceList",
    "youtubeGroupList", "qqFgGroupList", "steamSourceList", "steamGroupList",
    "scheduledGroupMessageTable", "scheduledGroupTargetTable",
  ];
  const bodies = tableIds.map(id => document.querySelector("#" + id + " tbody")).filter(Boolean);
  const observer = new MutationObserver(formatValues);

  function formatValues() {
    observer.disconnect();
    for (const body of bodies) {
      for (const row of body.querySelectorAll("tr:not(.fgempty-row)")) {
        const cells = Array.from(row.cells).slice(2, -1);
        for (const cell of cells) {
          const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
          const nodes = [];
          while (walker.nextNode()) nodes.push(walker.currentNode);
          for (const node of nodes) {
            const value = node.nodeValue;
            const parent = node.parentElement;
            if (!parent || !value.trim() ||
                parent.closest(".middle-value, .id-cell, button, select, textarea, svg, .chan-name-edit, .scheduled-group-message-copy small")) continue;
            const characters = Array.from(value);
            const tailLength = characters.length > 12 ? 4 : characters.length > 4 ? 2 : 1;
            const wrapper = document.createElement("span");
            const prefix = document.createElement("span");
            const suffix = document.createElement("span");
            wrapper.className = "middle-value";
            const copyOverride = parent.closest("[data-copy-value-override]");
            const copyValue = copyOverride ? copyOverride.dataset.copyValueOverride : value;
            wrapper.dataset.copyValue = copyValue;
            wrapper.setAttribute("role", "button");
            wrapper.setAttribute("tabindex", "0");
            wrapper.setAttribute("aria-label", copyValue + (cell.matches(".scheduled-message-schedule")
              ? "，聚焦后按 Enter 或空格复制" : "，右键复制；聚焦后按 Enter 或空格复制"));
            prefix.className = "middle-value-prefix";
            suffix.className = "middle-value-suffix";
            prefix.textContent = characters.slice(0, -tailLength).join("");
            suffix.textContent = characters.slice(-tailLength).join("");
            wrapper.append(prefix, suffix);
            node.replaceWith(wrapper);
          }
        }
      }
    }
    for (const body of bodies) observer.observe(body, {childList: true, subtree: true});
  }

  formatValues();
}

setupForwardingValueClipping();

let pollingStarted = false;

async function init() {
  // 防重复启动：无论 init() 被调用多少次（重复加载/脚本二次执行），
  // 轮询定时器只创建一套，避免 /api/status → /api/health 出现多重轮询。
  if (pollingStarted) return;
  pollingStarted = true;
  document.body.classList.add("panel-initializing");
  beginPanelLoading("正在加载配置…");
  try {
    // 桌面端默认总览，并记忆页面选择。
    setActivePage(activePage);
    const qqConfig = await jget("/api/bot/qq-config");
    qqBotIds = qqConfig && qqConfig.ok && Array.isArray(qqConfig.bot_ids) ? qqConfig.bot_ids.map(String) : [];
    qqBotAccounts = qqConfig && qqConfig.ok && Array.isArray(qqConfig.bots)
      ? qqConfig.bots.map(function (bot) { return {appid: bot.appid || "", name: bot.name || ""}; }) : [];
    const personalConfig = await jget("/api/personal-qq/config");
    if (personalConfig.ok) {
      personalQQConnections = personalConfig.connections || [];
      qqBotIds.push(...personalQQConnections.map(item => "personal:" + item.uin));
    }
    const api = window.pywebview && window.pywebview.api;
    if (IS_DESKTOP_HOST && api && api.get_desktop_preferences) {
      try {
        const preferences = await api.get_desktop_preferences();
        if (preferences && qqBotIds.includes(preferences.last_qq_bot)) {
          activeQQBotId = preferences.last_qq_bot;
        }
      } catch (error) { /* fall back to the browser selection */ }
    }
    currentQQBotId();
    renderQQBotWorkspace();
    settings = await jget("/api/settings");
    if (settings.ok === false) { const message = settings.msg; settings = null; throw new Error(message); }
    renderSettings();
    renderOverviewCounts();
    if (settings._profile_warnings?.length) showAlert("配置迁移提示", settings._profile_warnings.join("\n"));
  } catch (err) { showAlert("配置读取失败", err.message); }
  finally {
    endPanelLoading();
    document.body.classList.remove("panel-initializing");
  }
  void refreshWatchedChainStatuses();
  setInterval(refreshYoutubeStatus, 10000);
  setInterval(refreshSteamStatus, 10000);
  await loadAppInfo();
  // 启动后自动检查一次（默认开启，可在设置页关闭）；只提示，不自动安装
  if (isAutoUpdateCheckEnabled()) await checkForUpdates();
  // 若上次的更新安装仍在进行（例如页面刚刷新），继续显示进度
  await resumeUpdateProgressIfRunning();
  await refreshChannelNames();
  await refreshStatus();
  setInterval(refreshStatus, 3000);
  await refreshIdentities();
  setInterval(refreshIdentities, 30000);
  // X RSS 状态卡片：独立轻量轮询（只更新卡片文本，10 秒足够）
  await refreshXStatus();
  setInterval(refreshXStatus, 10000);
  // Bilibili RSS 状态卡片：与 X RSS 相同的轻量轮询
  await refreshBilibiliStatus();
  setInterval(refreshBilibiliStatus, 10000);
  await refreshMaintenance();
  setInterval(refreshMaintenance, 10000);
  await refreshMessageTrace();
  setInterval(refreshMessageTrace, 10000);
}

init();
