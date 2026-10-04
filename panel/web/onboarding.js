/* 按用途分流的首次配置向导。密钥仅存在输入框和一次保存请求中。 */
(function () {
  "use strict";
  const labels = {discord: "Discord 频道消息", x: "X 账号动态", bilibili: "Bilibili 投稿", qq: "QQ 授权私聊转发"};
  const stages = ["用途", "账号", "授权用户", "接收群", "内容来源", "确认启用", "验证结果"];
  const descriptions = {discord: "把可读取的频道消息发送到 QQ 群", x: "通过 RSSHub 订阅账号动态", bilibili: "通过 RSSHub 订阅 UP 主、标签或关键词", qq: "由授权用户私聊机器人，手动选择群并发送文字"};
  let saved = {}, credentials = {}, snapshot = {}, registrations = {}, health = {};
  let draft = fresh(), busy = false, trigger = null, scanned = [];
  const modal = document.createElement("div");
  modal.id = "onboardingModal";
  modal.className = "modal-overlay";
  modal.style.display = "none";
  modal.innerHTML = '<section class="modal-shell modal-large" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle">' +
    '<header class="modal-header"><div class="modal-heading"><h3 id="onboardingTitle">配置向导</h3><p>跟随步骤，完成你的第一条转发</p></div>' +
    '<div class="modal-header-actions"><button type="button" class="modal-x" id="onboardingClose" aria-label="稍后配置"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div></header>' +
    '<div class="modal-body"><ol class="onboarding-steps" aria-label="配置步骤"></ol><fieldset id="onboardingForm"></fieldset><p id="onboardingNotice" role="status" aria-live="polite"></p></div>' +
    '<footer class="modal-footer"><button type="button" class="sec" id="onboardingLater">稍后配置</button><button type="button" class="sec" id="onboardingBack">上一步</button><button type="button" id="onboardingNext">下一步</button></footer></section>';
  document.body.append(modal);
  const el = id => document.getElementById(id);
  const esc = escapeHtml;
  const form = el("onboardingForm");
  function fresh() { return {purpose: "discord", step: "0", bot_id: "", source_id: "", source_kind: "up", source_value: "", rsshub: "", name: "", groups: [], users: []}; }
  function notice(text, error) { el("onboardingNotice").textContent = text || ""; el("onboardingNotice").dataset.error = String(!!error); }
  function checked(result) { if (!result || result.ok === false) throw new Error(result?.msg || "操作失败，请稍后重试"); return result; }
  async function post(url, body) { return checked(await jpost(url, body)); }
  function button(action, text) { return '<button type="button" class="sec" data-setup="' + action + '">' + text + '</button>'; }
  function option(value, text, selected) { return '<option value="' + esc(value) + '"' + (value === selected ? ' selected' : '') + '>' + esc(text) + '</option>'; }
  function field(key, label, placeholder, type) { return '<label>' + label + '<input data-draft="' + key + '" value="' + esc(draft[key] || "") + '" placeholder="' + esc(placeholder || "") + '" type="' + (type || "text") + '"></label>'; }
  function name(item) { return item.name || item.remark || item.qq_id || item.openid; }
  function shortId(value) { value = String(value || ""); return value.length > 16 ? value.slice(0, 6) + "…" + value.slice(-6) : value; }
  function capture() {
    form.querySelectorAll("[data-draft]").forEach(input => { draft[input.dataset.draft] = input.value.trim(); });
    if (Number(draft.step) === 2) draft.users = [...form.querySelectorAll('[data-user]:checked')].map(input => input.dataset.user);
    if (Number(draft.step) === 3) draft.groups = [...form.querySelectorAll('[data-group]:checked')].map(input => input.dataset.group);
    if (Number(draft.step) === 0) draft.purpose = form.querySelector('[name="onboardingPurpose"]:checked')?.value || draft.purpose;
  }
  async function persist(newFlow) {
    const result = await post("/api/onboarding/progress", {flow_id: saved.flow_id || "", draft, new: !!newFlow});
    saved = result.state;
  }
  async function refresh() {
    const [s, r, h, o] = await Promise.all([jget("/api/settings"), jget("/api/registrations"), jget("/api/status"), jget("/api/onboarding")]);
    snapshot = checked(s); registrations = checked(r); health = checked(h); credentials = checked(o).credentials;
  }
  function controls() {
    form.disabled = busy;
    el("onboardingClose").disabled = busy;
    el("onboardingLater").disabled = busy;
    el("onboardingBack").disabled = busy || Number(draft.step) === 0 || Number(draft.step) === 6;
    el("onboardingNext").disabled = busy;
    el("onboardingNext").textContent = busy ? "请稍候…" : Number(draft.step) === 5 ? "确认并启用" : Number(draft.step) === 6 ? "完成" : "下一步";
    el("onboardingLater").textContent = Number(draft.step) === 6 ? "稍后验证" : "稍后配置";
  }
  async function run(action) {
    if (busy) return;
    capture(); const previousStep = draft.step; busy = true; controls(); notice("");
    try { await action(); } catch (err) {
      if (draft.step !== previousStep) { draft.step = previousStep; render(); }
      notice(err.message || "无法连接管理程序，请稍后重试", true);
    }
    finally { busy = false; controls(); }
  }
  function connections() {
    const h = health.health || {};
    const qq = Array.isArray(h.qq_bots) && h.qq_bots.includes(draft.bot_id);
    return {qq, discord: h.discord === true};
  }
  function connectionText() {
    const c = connections();
    return "当前 QQ 机器人：" + (c.qq ? "已连接" : "未连接") + (draft.purpose === "discord" ? "；Discord：" + (c.discord ? "已连接" : "未连接") : "");
  }
  function relevantGroups() { return (snapshot.qq_group_openids || []).filter(item => String(item.bot_id || "") === draft.bot_id || (!item.bot_id && credentials.bots?.length === 1)); }
  function render() {
    const step = Number(draft.step);
    modal.querySelector(".onboarding-steps").innerHTML = stages.map((title, index) =>
      '<li' + (index === step ? ' aria-current="step"' : '') + '>' + (index + 1) + ' ' + title + '</li>').join("");
    el("onboardingTitle").textContent = step === 0 ? "欢迎使用 WRelay" : "配置向导 · " + labels[draft.purpose];
    notice("");
    if (step === 0) {
      form.innerHTML = '<h4>你想把什么内容发送到 QQ？</h4><p class="help-text">先完成一种用途，之后可以随时从设置中再次打开向导。</p><div class="onboarding-options">' +
        Object.keys(labels).map(key => '<label class="onboarding-option"><input type="radio" name="onboardingPurpose" value="' + key + '"' + (draft.purpose === key ? ' checked' : '') + '><span><strong>' + labels[key] + '</strong><small>' + descriptions[key] + '</small></span></label>').join("") + '</div>';
    } else if (step === 1) {
      const bots = credentials.bots || [];
      form.innerHTML = '<h4>连接需要的机器人账号</h4><p class="help-text">在 QQ 开放平台创建官方机器人，取得 AppID 和 AppSecret，并按平台要求开通群聊／私聊能力。已有账号可直接选用。保存账号不会立即重启。</p>' +
        '<div class="onboarding-actions"><a class="external-link-button" href="https://q.qq.com/" target="_blank" rel="noopener noreferrer">QQ 开放平台 ↗</a></div>' +
        '<div class="onboarding-fields"><label class="onboarding-wide">已配置 QQ 账号<select id="onboardingBot">' + option("", "填写新的账号", bots.some(bot => bot.appid === draft.bot_id) ? draft.bot_id : "") + bots.map(bot => option(bot.appid, bot.appid, draft.bot_id)).join("") + '</select></label>' +
        field("bot_id", "QQ AppID", "从 QQ 开放平台复制") + '<label>QQ AppSecret<input id="onboardingSecret" type="password" autocomplete="new-password" placeholder="已有账号留空保留"></label>' +
        (draft.purpose === "discord" ? '<label class="onboarding-wide">Discord Token<input id="onboardingToken" type="password" autocomplete="new-password" placeholder="' + (credentials.discord_configured ? '已配置，留空保留' : '从 Discord 开发者平台复制') + '"></label>' : '') + '</div>' +
        (draft.purpose === "discord" ? '<p class="help-text">在 Discord 开发者平台创建 Bot，开启 Message Content Intent，并邀请进入来源服务器，授予查看频道和读取历史消息的权限。本机须能访问 Discord；可在 .env.prod 设置 Discord 专用 DISCORD_PROXY，或使用可用的系统虚拟网卡（TUN）路由。</p><a class="external-link-button" href="https://discord.com/developers/applications" target="_blank" rel="noopener noreferrer">Discord 开发者平台 ↗</a>' : '') +
        '<div class="onboarding-actions">' + button("credentials", "保存账号") + button("connect", "启动／重启并检查") + button("refreshConnection", "刷新连接状态") + '</div><p class="help-text">启动或重启会作用于整个机器人进程，现有转发也会短暂重连。连接可能需要数秒；未连接时请核对凭据、平台权限和网络，再刷新。</p><p id="onboardingConnection">' + connectionText() + '</p>';
    } else if (step === 2) {
      form.innerHTML = '<h4>确认谁可以操作机器人</h4><p class="help-text">新增接收群需要一位已授权用户提交申请。让该用户先私聊机器人发送下方命令，将示例 QQ 号与昵称替换为本人信息，然后刷新列表。</p><code>register 123456789 我的昵称</code><p class="help-text">审核只登记身份；点击授权后，该用户可以使用私聊转发及现有管理命令（包括重启）。请只授权可信用户。已有接收群的自动转发可跳过本步。</p>' +
        button("refresh", "刷新用户列表") + userList() + pendingList("users");
    } else if (step === 3) {
      form.innerHTML = '<h4>选择接收消息的 QQ 群</h4><p class="help-text">把当前机器人加入目标群，由上一步已授权用户在群内 @机器人，发送以下命令（替换群号和群名）。刷新后审核，再勾选接收群。</p><code>register-group 987654321 我的接收群</code><p class="help-text">群需要具备平台允许的消息接收权限。这里只列出当前机器人登记的群；审核不会自动启用，最后确认时才启用所选群。</p>' +
        button("refresh", "刷新群列表") + '<div class="onboarding-list">' + relevantGroups().map(item => '<label><input type="checkbox" data-group="' + esc(item.openid) + '"' + (draft.groups.includes(item.openid) ? ' checked' : '') + '><span>' + esc(name(item)) + '<small>' + esc(item.qq_id || shortId(item.openid)) + (item.enabled ? ' · 已启用' : ' · 待启用') + '</small></span></label>').join("") + '</div>' +
        (!relevantGroups().length ? '<p class="help-text">还没有已审核的接收群。请完成群注册后刷新。</p>' : '') + pendingList("groups");
    } else if (step === 4) {
      renderSource();
    } else if (step === 5) {
      const targets = relevantGroups().filter(item => draft.groups.includes(item.openid)).map(name);
      form.innerHTML = '<h4>确认这次配置</h4><div class="onboarding-summary"><strong>' + esc(labels[draft.purpose]) + '</strong><br>QQ 机器人：' + esc(draft.bot_id) + '<br>接收群：' + esc(targets.join("、")) +
        (draft.purpose === "qq" ? '<br>由已授权用户主动选择接收群并发送消息' : '<br>来源：' + esc(sourceLabel())) + '</div><p class="help-text">确认后会启用所选来源和接收群，并保存转发关系。已有筛选条件继续生效；启用已登记来源或群也可能使其他包含它的转发组恢复投递。</p>' +
        (draft.purpose === "x" || draft.purpose === "bilibili" ? '<p class="help-text">RSSHub 地址由这条链路的所有来源共用。本次保存地址：' + esc(draft.rsshub) + '。首次启用从当前内容建立基线，等待之后的新内容，不批量发送历史窗口。</p>' : '') +
        (draft.purpose === "qq" ? '<p class="help-text">私聊用户可选择所有已启用的接收群，本次勾选不构成独立的用户权限范围。不会创建群聊自动转发。</p>' : '');
    } else {
      form.innerHTML = '<h4>配置已保存，接下来验证投递</h4><p class="help-text">' + connectionText() + '。连接成功不代表消息已经送达。</p><div class="onboarding-summary">' +
        (draft.purpose === "discord" ? '请在所选 Discord 频道发送一条符合当前筛选条件的新消息，再到所选 QQ 群确认收到。' : draft.purpose === "qq" ? '由授权用户私聊机器人发送 <code>relay list</code>，按返回列表发送 <code>relay 序号</code>，然后发送一条测试文字，并到所选群确认收到。' : '等待所选来源发布新内容。首次轮询只建立基线；之后的新内容才会转发，可在对应功能页查看轮询状态。') +
        '</div><div class="onboarding-actions">' + button("verify", "刷新投递结果") + button("received", "我已在目标群收到消息") + button("another", "再配置一种用途") + '</div><p class="help-text">点击“完成”可进入对应页面，稍后继续查看消息追踪。测试内容由你主动发送，向导不会自动向群发消息。</p>';
      if (saved.receipt_confirmed) notice("你已确认在目标群收到消息。");
    }
    controls();
    modal.querySelector(".modal-body").scrollTop = 0;
  }
  function userList() {
    return '<div class="onboarding-list">' + (snapshot.qq_user_openids || []).map(item => item.enabled ?
      '<label><input type="checkbox" data-user="' + esc(item.openid) + '"' + (draft.users.includes(item.openid) ? ' checked' : '') + '><span>' + esc(name(item)) + '<small>已授权 · ' + esc(item.qq_id || shortId(item.openid)) + '</small></span></label>' :
      '<div class="onboarding-entry"><span>' + esc(name(item)) + '<small>已审核，尚未授权 · ' + esc(item.qq_id || shortId(item.openid)) + '</small></span><button type="button" class="sec" data-authorize="' + esc(item.openid) + '">授权此用户</button></div>').join("") + '</div>';
  }
  function pendingList(kind) {
    const items = (registrations[kind] || []).filter(item => kind === "users" || item.bot_id === draft.bot_id);
    return '<div class="onboarding-list">' + items.map(item => '<div class="onboarding-entry"><span>' + esc(item.nickname || item.group_name || shortId(item.openid)) + '<small>待审核 · ' + esc(item.qq_id || shortId(item.openid)) + '</small></span><button type="button" class="sec" data-review="' + esc(item.openid) + '" data-review-kind="' + kind + '">审核通过</button></div>').join("") + '</div>';
  }
  function sourceItems() {
    const items = snapshot[{discord: "discord_channels", x: "x_rss_sources", bilibili: "bilibili_sources"}[draft.purpose]] || [];
    return draft.purpose === "bilibili" ? items.map(item => ({...item, id: item.id || (item.kind || "up") + ":" + (item.value || item.uid || "")})) : items;
  }
  function sourceLabel() {
    const item = sourceItems().find(item => String(draft.purpose === "x" ? item.source : item.id) === draft.source_id);
    if (draft.purpose === "bilibili") return item ? item.value || item.id : draft.source_value;
    return item ? item.name || item.source || item.value || item.id : draft.name || draft.source_value;
  }
  function renderSource() {
    if (draft.purpose === "qq") {
      form.innerHTML = '<h4>通过私聊手动转发</h4><p class="help-text">授权用户先发送 <code>relay list</code> 查看群，再发送 <code>relay 序号</code> 选择目标，下一条文字会转发到该群。纯文字转发不需要 Discord 账号；抓取 Discord 消息链接仍需另行配置 Discord。</p>';
      return;
    }
    const existing = sourceItems();
    form.innerHTML = '<h4>选择要转发的内容</h4><div class="onboarding-fields"><label class="onboarding-wide">已登记的来源<select data-draft="source_id" id="onboardingSource">' + option("", "添加新的来源", draft.source_id) + existing.map(item => { const id = String(draft.purpose === "x" ? item.source : item.id); const label = draft.purpose === "bilibili" ? item.value || id : item.name || item.value || id; return option(id, label, draft.source_id); }).join("") + '</select></label>' +
      (draft.purpose === "discord" ? '<label class="onboarding-wide">可读取的频道<select id="onboardingChannel">' + option("", "先点击扫描频道", draft.source_value) + scanned.map(item => option(String(item.id), (item.guild || "") + " / " + item.name, draft.source_value)).join("") + '</select></label>' :
        field("rsshub", "RSSHub 服务地址", "http://127.0.0.1:1200") +
        (draft.purpose === "bilibili" ? '<label>来源类型<select data-draft="source_kind">' + [["up", "UP 主投稿"], ["tag", "标签"], ["keyword", "关键词"]].map(item => option(item[0], item[1], draft.source_kind)).join("") + '</select></label>' : '') +
        field("source_value", draft.purpose === "x" ? "X 用户名" : "UID、标签或关键词", draft.purpose === "x" ? "例如 example，无需 @" : "UP 主 UID 可从个人空间网址中获取")) +
      (draft.purpose === "bilibili" ? "" : field("name", "显示名称（可选）", "方便自己辨认")) + '</div>' +
      (draft.purpose === "discord" ? button("scan", "扫描可读取频道") + '<p class="help-text">没有找到频道时，确认机器人已加入服务器，具有查看频道、读取历史消息权限，并已开启 Message Content Intent。</p>' :
        '<p class="help-text">需要一个已运行且可从本机访问的 RSSHub 服务。只填写地址不会安装服务；没有服务时可稍后继续。Bilibili 标签／关键词可能需要 RSSHub 配置 Cookie 或浏览器支持。</p><div class="onboarding-actions"><a class="external-link-button" href="https://docs.rsshub.app/" target="_blank" rel="noopener noreferrer">RSSHub 使用说明 ↗</a>' + button("probe", "检测来源") + '</div>');
  }
  async function open() {
    trigger = document.activeElement;
    const result = checked(await jget("/api/onboarding")); saved = result.state; credentials = result.credentials;
    draft = {...fresh(), ...(saved.draft || {})};
    if (saved.status === "applied") draft.step = "6";
    if (!/^[0-6]$/.test(draft.step)) draft.step = "0";
    await refresh();
    modal.style.display = "flex"; render(); el("onboardingLater").focus();
  }
  async function close() {
    if (busy) return;
    await run(async () => { await persist(); modal.style.display = "none"; form.innerHTML = ""; trigger?.focus(); await reloadMain(); });
  }
  async function reloadMain() {
    settings = await jget("/api/settings");
    qqBotIds = (credentials.bots || []).map(bot => bot.appid);
    renderSettings(); await refreshStatus();
  }
  async function next() {
    await run(async () => {
      const step = Number(draft.step);
      if (step === 6) { await persist(); modal.style.display = "none"; form.innerHTML = ""; setActivePage(draft.purpose === "qq" ? "overview" : draft.purpose); trigger?.focus(); await reloadMain(); return; }
      if (step === 0) {
        if (saved.draft && saved.draft.purpose !== draft.purpose) draft = {...fresh(), purpose: draft.purpose};
        if (!draft.bot_id && credentials.bots?.length) draft.bot_id = credentials.bots[0].appid;
        if (!draft.rsshub) draft.rsshub = snapshot[draft.purpose + "_rsshub_base"] || "http://127.0.0.1:1200";
      }
      if (step === 1) {
        if (el("onboardingSecret")?.value || el("onboardingToken")?.value) throw new Error("请先点击“保存账号”，再启动／重启并检查连接");
        await refresh();
        if (!credentials.bots.some(bot => bot.appid === draft.bot_id)) throw new Error("请先保存当前 QQ 账号");
        if (draft.purpose === "discord" && !credentials.discord_configured) throw new Error("请先保存 Discord Token");
        if (credentials.restart_needed) throw new Error("账号已修改，请先点击“启动／重启并检查”，使新配置生效");
        const c = connections();
        if (!c.qq || (draft.purpose === "discord" && !c.discord)) throw new Error(connectionText() + "。请检查凭据、平台权限和网络，启动／重启后刷新连接状态。");
      }
      if (step === 2 && draft.purpose === "qq" && !draft.users.length) throw new Error("请至少选择一位已明确授权的用户");
      if (step === 3 && !draft.groups.length) throw new Error("请至少勾选一个接收群");
      if (step === 4) {
        if (draft.purpose === "discord" && !draft.source_id && !draft.source_value) throw new Error("请扫描并选择一个频道");
        if (["x", "bilibili"].includes(draft.purpose)) { notice("正在检测来源，请稍候…"); checked(await jpost("/api/onboarding/probe", draft)); }
      }
      if (step === 5) {
        const result = await post("/api/onboarding/apply", {draft, flow_id: saved.flow_id, revision: snapshot._revision}); saved = result.state; draft = {...saved.draft};
      }
      draft.step = String(step + 1);
      await persist(); await refresh(); render(); el("onboardingNext").focus();
    });
  }
  const actions = {
    refresh: async () => { await persist(); await refresh(); render(); },
    refreshConnection: async () => { await refresh(); el("onboardingConnection").textContent = connectionText(); },
    credentials: async () => {
      const result = await post("/api/onboarding/credentials", {draft, secret: el("onboardingSecret").value, token: el("onboardingToken")?.value || ""});
      credentials = result.credentials; await persist(); render(); notice("账号已保存。请点击“启动／重启并检查”使配置生效。");
    },
    connect: async () => {
      if (el("onboardingSecret")?.value || el("onboardingToken")?.value) throw new Error("请先保存输入的账号");
      if (!credentials.bots.some(bot => bot.appid === draft.bot_id)) throw new Error("请先保存当前 QQ 账号");
      await persist(); await refresh();
      await post("/api/onboarding/start");
      await refresh(); render(); notice("已提交启动请求，请稍等数秒，再点击“刷新连接状态”。");
    },
    scan: async () => { const r = await post("/api/channels/refresh"); scanned = r.audit?.readable || []; render(); notice(scanned.length ? "请选择一个频道。" : "未发现可读取频道，请检查机器人是否在服务器内及频道权限。"); },
    probe: async () => { const r = await post("/api/onboarding/probe", draft); notice(r.msg); },
    verify: async () => {
      await refresh();
      const r = checked(await jget("/api/message-trace?limit=200"));
      const kind = {discord: "discord", x: "x_rss", bilibili: "bilibili"}[draft.purpose];
      let id = draft.source_id || draft.source_value;
      if (draft.purpose === "bilibili" && !draft.source_id) id = draft.source_kind + ":" + draft.source_value;
      if (draft.purpose === "x") id = xSourceKey(id);
      const found = kind && (r.entries || []).find(entry => entry.source_kind === kind && entry.source_id === id && entry.first_at >= saved.applied_at && !(saved.previous_traces || []).includes(entry.trace_key) && draft.groups.every(group => (entry.groups || []).some(target => target.id === group && target.ok)));
      notice(found ? "已查到保存配置后的成功投递记录，所选接收群均发送成功。你也可以到群内确认。" : connectionText() + "。尚未查到本次配置后的完整成功记录；请按上方步骤验证，也可进入消息追踪查看详情。");
    },
    received: async () => { const result = await post("/api/onboarding/receipt", {flow_id: saved.flow_id}); saved = result.state; notice("你已确认在目标群收到消息。此状态来自你的确认。"); },
    another: async () => { draft = fresh(); await persist(true); await refresh(); render(); },
  };
  modal.addEventListener("change", event => {
    if (event.target.id === "onboardingBot") { draft.bot_id = event.target.value; draft.groups = []; render(); }
    else if (event.target.id === "onboardingChannel") { draft.source_value = event.target.value; draft.source_id = ""; const ch = scanned.find(item => String(item.id) === draft.source_value); draft.name = ch?.name || ""; render(); }
    else if (event.target.id === "onboardingSource") { capture(); if (draft.source_id) draft.source_value = ""; render(); }
  });
  modal.addEventListener("click", event => {
    const target = event.target.closest("button");
    if (!target || busy) return;
    if (target.dataset.setup) run(actions[target.dataset.setup]);
    if (target.dataset.review) run(async () => { await post("/api/registrations/review", {kind: target.dataset.reviewKind, openid: target.dataset.review, action: "approve"}); await refresh(); render(); notice("审核通过。用户仍需单独授权，群仍需勾选并在最后确认启用。"); });
    if (target.dataset.authorize) {
      const id = target.dataset.authorize;
      const item = (snapshot.qq_user_openids || []).find(user => user.openid === id);
      showConfirmDlg("授权操作用户", "确认授权“" + name(item) + "”使用私聊转发和现有管理命令（包括重启机器人）？", () => run(async () => { await post("/api/onboarding/authorize", {openid: id, confirm: true}); draft.users.push(id); await refresh(); render(); }), "授权");
    }
  });
  el("onboardingBack").onclick = () => run(async () => { draft.step = String(Math.max(0, Number(draft.step) - 1)); await persist(); await refresh(); render(); });
  el("onboardingNext").onclick = next;
  el("onboardingClose").onclick = close;
  el("onboardingLater").onclick = close;
  keyboardModalClosers.onboardingModal = close;
  el("btnOnboarding").onclick = () => open().catch(err => showAlert("配置向导", err.message));
  jget("/api/onboarding").then(result => { if (result.ok && result.show_welcome) return open(); }).catch(() => {});
})();
