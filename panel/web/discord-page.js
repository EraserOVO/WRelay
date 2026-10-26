/* Shared forwarding-page presentation, based on the Discord template.
   Move existing controls, preserving IDs,
   handlers and state; restore their exact slots when leaving this page. */
(function () {
  const byId = id => document.getElementById(id);
  byId("btnBackfillRefresh").title = "刷新待补发数量";
  byId("btnBackfillRefresh").setAttribute("aria-label", "刷新待补发数量");
  byId("btnBackfillClear").title = "清空待补发";
  byId("btnBackfillClear").setAttribute("aria-label", "清空待补发");
  const pages = {
    discord: {workspace: "fgBody"},
    x: {workspace: "xFgBody"},
    bilibili: {workspace: "bilibiliFgBody"},
    youtube: {workspace: "youtubeFgBody"},
    qq: {workspace: "qqFgBody"},
  };
  let active = "";
  let enabled = false;
  const moves = [];
  const groups = [];
  function group(parent, className, nodes) {
    const wrapper = document.createElement("div");
    wrapper.className = className;
    parent.append(wrapper);
    groups.push(wrapper);
    nodes.forEach(node => move(node, wrapper));
    return wrapper;
  }
  function move(node, parent) {
    const slot = document.createComment("Desktop workspace control slot");
    node.before(slot);
    moves.push({node, slot});
    parent.append(node);
  }
  const toggles = ["btnBackfillToggle", "btnScheduledMessageToggle"].map(byId);
  function decorateToggle(button) {
    if (!enabled) return;
    button.setAttribute("role", "switch");
    button.setAttribute("aria-checked", String(button.dataset.on === "1"));
    button.removeAttribute("aria-pressed");
    if (!button.querySelector(".settings-switch-track")) {
      const track = document.createElement("span");
      track.className = "settings-switch-track";
      track.setAttribute("aria-hidden", "true");
      button.append(track);
    }
  }
  toggles.forEach(button => new MutationObserver(() => decorateToggle(button)).observe(button,
    {attributes:true, attributeFilter:["data-on"], childList:true}));
  const scheduledEdit = byId("btnScheduledMessageEdit");
  const originalScheduledEdit = {
    html: scheduledEdit.innerHTML, className: scheduledEdit.className,
    title: scheduledEdit.title, label: scheduledEdit.getAttribute("aria-label"),
  };
  function scheduledPresentation() {
    if (active !== "qq") return;
    const empty = byId("scheduledMessageSummary").classList.contains("is-empty");
    const label = empty ? "+ 新增" : "编辑定时消息";
    scheduledEdit.textContent = label;
    scheduledEdit.title = label;
    scheduledEdit.setAttribute("aria-label", label);
    scheduledEdit.className = empty ? "" : "sec";
  }
  new MutationObserver(scheduledPresentation).observe(byId("scheduledMessageSummary"),
    {attributes:true, attributeFilter:["class"], childList:true});
  function sync() {
    const page = document.body.dataset.activePage;
    const next = document.body.classList.contains("desktop-host") && pages[page] ? page : "";
    if (next === active) return;
    // Restore before entering a different page so shared controls never accumulate.
    if (enabled) {
      moves.reverse().forEach(({node, slot}) => {slot.replaceWith(node);});
      moves.length = 0;
      groups.forEach(node => node.remove());
      groups.length = 0;
      document.querySelectorAll(".discord-external-actions").forEach(node => node.remove());
      if (active === "qq") {
        scheduledEdit.innerHTML = originalScheduledEdit.html;
        scheduledEdit.className = originalScheduledEdit.className;
        scheduledEdit.title = originalScheduledEdit.title;
        scheduledEdit.setAttribute("aria-label", originalScheduledEdit.label);
      }
    }
    active = next;
    enabled = !!active;
    if (enabled) {
      const status = document.querySelector(".robot-status-summary");
      group(status, "desktop-connection-group", [byId("badge"), status.querySelector(".qq-bot-workspace-field")]);
      group(status, "desktop-statistics-group", [...status.querySelectorAll(".robot-stat")]);
      const config = pages[active];
      if (active === "discord") {
        // 离线补发与其它功能页的小卡片共用两列网格。
        const lowerCards = group(byId("pageDiscord"), "relay-translation-row func-cards", []);
        move(byId("backfillCard"), lowerCards);
      }
      if (active === "qq") {
        // 转发组下方的所有小卡片共用同一个两列网格：同宽同高，从左至右、从上至下排列。
        const lowerCards = group(byId("pageQQ"), "relay-translation-row func-cards", []);
        move(byId("scheduledMessageCard"), lowerCards);
        move(byId("unifiedRelayCard"), lowerCards);
        move(byId("autoTranslationCard"), lowerCards);
      }
      // Align the primary and external actions in the two table footers.
      byId(config.workspace).querySelectorAll(".addrow").forEach(bar => {
        const external = document.createElement("span");
        external.className = "discord-external-actions";
        bar.append(external);
        [...bar.querySelectorAll(".linkbtn, .iconbtn")].forEach(node => move(node, external));
      });
      toggles.forEach(decorateToggle);
    } else {
      toggles.forEach(button => {
        button.removeAttribute("role"); button.removeAttribute("aria-checked");
        button.querySelector(".settings-switch-track")?.remove();
      });
    }
    scheduledPresentation();
  }
  new MutationObserver(sync).observe(document.body, {attributes:true, attributeFilter:["data-active-page", "class"]});
  sync();
})();

/* One tooltip for static and dynamically rendered controls. Native titles are
   restored before click handlers run, preserving copy feedback and renderers. */
(function () {
  const tooltip = document.createElement("div");
  tooltip.id = "uiTooltip";
  tooltip.className = "ui-tooltip";
  tooltip.setAttribute("role", "tooltip");
  tooltip.hidden = true;
  document.body.append(tooltip);
  let owner = null;
  let description = null;
  let suppressedTitles = [];
  const textTitles = new WeakMap();
  function labelText(control) {
    const label = control.cloneNode(true);
    label.querySelectorAll('svg, [aria-hidden="true"]').forEach(node => node.remove());
    return label.textContent.trim();
  }
  function rememberTextTitle(control) {
    if (!control.matches("button, .linkbtn, .id-cell")) return;
    const title = control.getAttribute("title");
    if (title === null) return;
    if (control.disabled && title !== labelText(control)) return;
    if (control.matches(".id-cell") || (!control.matches(".hintbtn") && labelText(control))) {
      // Keep the full label for clipped text, without a second native tooltip.
      textTitles.set(control, title);
      control.removeAttribute("title");
    }
  }
  function hide() {
    const visible = !tooltip.hidden;
    tooltip.hidden = true;
    if (owner) {
      if (description === null) owner.removeAttribute("aria-describedby");
      else owner.setAttribute("aria-describedby", description);
    }
    owner = null;
    suppressedTitles.forEach(([node, title]) => {
      if (!node.hasAttribute("title")) node.title = title;
    });
    suppressedTitles = [];
    return visible;
  }
  window.dismissWRelayTooltip = hide;
  document.querySelectorAll(".hintbtn").forEach(button => {
    button.setAttribute("aria-label", (button.dataset.title || "帮助") + "说明");
  });
  const actionLabels = {
    btnQQConfig: "配置 QQ Bot 账号与密钥",
    btnDiscordConfig: "配置 Discord Bot 连接与密钥",
    btnTranslationConfig: "配置翻译服务与 API 密钥",
    btnBilibiliProtection: "设置 Bilibili 投递保护参数",
    btnCopyLog: "复制当前运行日志", btnClearLog: "清空运行日志",
  };
  Object.entries(actionLabels).forEach(([id, label]) => {
    const button = document.getElementById(id);
    if (button) { button.title = label; button.setAttribute("aria-label", label); }
  });
  document.querySelectorAll(".linkbtn").forEach(button => {
    if (!button.title) button.title = "打开外部页面：" + button.textContent.trim();
    if (!button.getAttribute("aria-label")) button.setAttribute("aria-label", button.title);
  });
  document.querySelectorAll("button[title], .linkbtn[title], .id-cell[title]").forEach(rememberTextTitle);
  new MutationObserver(records => {
    records.forEach(record => {
      if (record.type === "attributes") rememberTextTitle(record.target);
      else record.addedNodes.forEach(node => {
        if (node.nodeType !== 1) return;
        rememberTextTitle(node);
        node.querySelectorAll("button[title], .linkbtn[title], .id-cell[title]").forEach(rememberTextTitle);
      });
    });
  }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["title"]});
  function show(target) {
    const control = target.closest("button, .rowicon, .iconbtn, .linkbtn, .chan-name-edit, .id-value, [data-fold]");
    if (control === owner) return;
    if (!control || control.disabled) { if (!tooltip.contains(target)) hide(); return; }
    hide();
    if (control.matches(".settings-switch")) return;
    const label = labelText(control);
    const iconOnly = !label || control.matches(".iconbtn, .rowicon, .panel-action-icon, .foldbtn, .chan-name-edit, .modal-x") ||
      (control.matches(".nav-item") && parseFloat(getComputedStyle(control).fontSize) === 0);
    const clipped = [control, ...control.querySelectorAll(".fgtab-name, .table-cell-clip")]
      .some(node => node.scrollWidth > node.clientWidth + 1);
    const fullId = control.matches(".id-value")
      ? textTitles.get(control.closest(".id-cell")) || control.closest(".id-cell").title : "";
    if (!control.matches(".hintbtn") && !iconOnly && !clipped && !(fullId && fullId !== label)) return;
    let text = control.dataset.hint
      ? document.getElementById(control.dataset.hint)?.textContent.trim()
      : control.title || textTitles.get(control) || control.getAttribute("aria-label") || label;
    if (control.matches(".id-value")) text = fullId;
    if (!text) return;
    owner = control;
    [control, control.closest(".id-cell")].filter(Boolean).forEach(node => {
      if (node.hasAttribute("title")) {
        suppressedTitles.push([node, node.title]); node.removeAttribute("title");
      }
    });
    description = control.getAttribute("aria-describedby");
    control.setAttribute("aria-describedby", [description, tooltip.id].filter(Boolean).join(" "));
    tooltip.textContent = text;
    tooltip.hidden = false;
    const rect = control.getBoundingClientRect();
    const box = tooltip.getBoundingClientRect();
    tooltip.style.left = Math.max(8, Math.min(rect.left, innerWidth - box.width - 8)) + "px";
    tooltip.style.top = Math.max(8, rect.bottom + box.height + 8 <= innerHeight
      ? rect.bottom + 6 : rect.top - box.height - 6) + "px";
  }
  document.addEventListener("pointerover", event => show(event.target));
  document.addEventListener("pointerout", event => {
    if (owner && !owner.contains(event.relatedTarget) && !tooltip.contains(event.relatedTarget)) hide();
  });
  document.addEventListener("focusin", event => show(event.target));
  document.addEventListener("focusout", hide);
  document.addEventListener("click", hide, true);
  document.addEventListener("scroll", event => { if (!tooltip.contains(event.target)) hide(); }, true);
  window.addEventListener("resize", hide);
  window.addEventListener("blur", hide);
  new MutationObserver(() => { if (owner && (!owner.isConnected || !owner.getClientRects().length)) hide(); })
    .observe(document.body, {childList: true, subtree: true});
})();

// Restore focus after a modal closes; confirmations start on Cancel so a
// repeated Enter cannot accidentally execute a destructive action.
Object.keys(keyboardModalClosers).forEach(id => {
  const modal = document.getElementById(id);
  if (!modal) return;
  let wasOpen = false, returnTo = null;
  new MutationObserver(() => {
    const open = getComputedStyle(modal).display !== "none";
    if (open === wasOpen) return;
    wasOpen = open;
    if (open) {
      returnTo = document.activeElement;
      const cancel = id === "confirmModal" && document.getElementById("confirmCancel");
      const control = cancel && !cancel.hidden ? cancel : [...modal.querySelectorAll("input, select, textarea, button")]
        .find(el => !el.disabled && el.getClientRects().length && !el.classList.contains("modal-x"));
      control?.focus();
    } else if (returnTo?.isConnected && returnTo.getClientRects().length) {
      returnTo.focus();
    }
  }).observe(modal, {attributes: true, attributeFilter: ["style", "class"]});
});
