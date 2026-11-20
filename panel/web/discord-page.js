/* Shared forwarding-page presentation, based on the Discord template.
   Move existing controls, preserving IDs,
   handlers and state; restore their exact slots when leaving this page. */
(function () {
  const byId = id => document.getElementById(id);
  const pages = {
    discord: {workspace: "fgBody"},
    x: {workspace: "xFgBody"},
    bilibili: {workspace: "bilibiliFgBody"},
    youtube: {workspace: "youtubeFgBody"},
    steam: {workspace: "steamWorkspace"},
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
  const toggles = [];
  function decorateToggle(button) {
    if (!enabled || !pages[active]) return;
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
    const label = "＋ 新增";
    scheduledEdit.textContent = label;
    scheduledEdit.title = label;
    scheduledEdit.setAttribute("aria-label", label);
    scheduledEdit.className = "";
  }
  new MutationObserver(scheduledPresentation).observe(byId("scheduledMessageSummary"),
    {attributes:true, attributeFilter:["class"], childList:true});
  function sync() {
    const page = document.body.dataset.activePage;
    const next = document.body.classList.contains("desktop-host") && (pages[page] || page === "overview") ? page : "";
    if (next === active) return;
    // Restore before entering a different page so shared controls never accumulate.
    if (enabled) {
      moves.reverse().forEach(({node, slot}) => {slot.replaceWith(node);});
      moves.length = 0;
      groups.forEach(node => node.remove());
      groups.length = 0;
      document.querySelectorAll(".discord-external-actions").forEach(node => node.remove());
      if (pages[active]) {
        toggles.forEach(button => {
          button.removeAttribute("role"); button.removeAttribute("aria-checked");
          button.querySelector(".settings-switch-track")?.remove();
        });
      }
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
      // Align the primary and external actions in the two table footers.
      // 转发组底部已不再挂平台外链，没有可归类的控件时不生成空容器。
      if (config) {
        // Keep the source and group tools page-specific; overview only shares the status row.
        byId(config.workspace).querySelectorAll(".addrow").forEach(bar => {
          const nodes = [...bar.querySelectorAll(".linkbtn, .iconbtn")];
          if (!nodes.length) return;
          const external = document.createElement("span");
          external.className = "discord-external-actions";
          bar.append(external);
          nodes.forEach(node => move(node, external));
        });
        toggles.forEach(decorateToggle);
      }
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
  tooltip.setAttribute("aria-live", "polite");
  tooltip.hidden = true;
  document.body.append(tooltip);
  let owner = null;
  let description = null;
  let feedbackTimer = null;
  let feedbackPinned = false;
  let suppressedTitles = [];
  const textTitles = new WeakMap();
  const suppressedTooltipSelector = [
    ".robot-stat", "#pidinfo", "#statsToday", "#statsTotal", "#statsFailed",
    ".overview-route-copy b", ".col-muted", ".foldbtn",
    ".fgtab-add", ".fg-toggle", ".fgctrl:not(.fg-delete)",
    "#btnStatusSheenToggle", "#closeBehavior",
    "#btnFailureNotificationToggle", "#btnSettingsAutostart",
  ].join(", ");
  const suppressedTooltipText = new Set([
    "拖拽排序", "删除转发组", "移出当前转发组", "编辑群资料", "内容筛选",
  ]);
  function hasSuppressedTooltip(control) {
    if (!(control instanceof Element)) return false;
    if (control.matches(suppressedTooltipSelector)) return true;
    return [
      textTitles.get(control), control.dataset.wrelayTitle, control.dataset.tooltip,
      control.getAttribute("title"), control.getAttribute("aria-label"),
    ].some(text => text && suppressedTooltipText.has(text.trim()));
  }
  function labelText(control) {
    const label = control.cloneNode(true);
    label.querySelectorAll('svg, [aria-hidden="true"]').forEach(node => node.remove());
    return label.textContent.trim();
  }
  function rememberTextTitle(control) {
    if (!(control instanceof Element) || !control.hasAttribute("title")) return;
    const title = control.getAttribute("title");
    if (title === null) return;
    if (hasSuppressedTooltip(control)) {
      textTitles.delete(control);
      control.removeAttribute("data-wrelay-title");
      control.removeAttribute("title");
      return;
    }
    if (!title.trim()) {
      textTitles.delete(control);
      control.removeAttribute("data-wrelay-title");
      control.removeAttribute("title");
      return;
    }
    // Keep the title text for the shared panel tooltip, never the browser tooltip.
    textTitles.set(control, title);
    control.setAttribute("data-wrelay-title", title);
    control.removeAttribute("title");
  }
  function hide(force = false) {
    if (feedbackPinned && !force) return false;
    clearTimeout(feedbackTimer);
    feedbackTimer = null;
    feedbackPinned = false;
    const visible = !tooltip.hidden;
    tooltip.hidden = true;
    tooltip.classList.remove("ui-tooltip-copy-feedback", "is-fading");
    tooltip.replaceChildren();
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
  window.dismissWRelayTooltip = () => hide(true);
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
  document.querySelectorAll("[title]").forEach(rememberTextTitle);
  new MutationObserver(records => {
    records.forEach(record => {
      if (record.type === "attributes") rememberTextTitle(record.target);
      else record.addedNodes.forEach(node => {
        if (node.nodeType !== 1) return;
        rememberTextTitle(node);
        node.querySelectorAll("[title]").forEach(rememberTextTitle);
      });
    });
  }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ["title"]});
  function show(target) {
    if (feedbackPinned) return;
    const control = target.closest("[data-wrelay-title], button, .rowicon, .iconbtn, .linkbtn, .chan-name-edit, .middle-value, .id-value, [data-fold]");
    if (control === owner) return;
    if (!control) { if (!tooltip.contains(target)) hide(); return; }
    if (hasSuppressedTooltip(control)) { hide(); return; }
    hide();
    const label = labelText(control);
    const iconOnly = !label || control.matches(".iconbtn, .rowicon, .panel-action-icon, .foldbtn, .chan-name-edit, .modal-x") ||
      (control.matches(".nav-item") && parseFloat(getComputedStyle(control).fontSize) === 0);
    const clipped = [control, ...control.querySelectorAll(".fgtab-name, .table-cell-clip")]
      .some(node => node.scrollWidth > node.clientWidth + 1);
    const copyValue = control.dataset.copyValue;
    const hasTitle = textTitles.has(control) || control.hasAttribute("data-wrelay-title");
    const titleOnlyTarget = hasTitle && !control.matches("button, .rowicon, .iconbtn, .linkbtn, .chan-name-edit, .middle-value, .id-value, [data-fold]");
    const disabledTitle = hasTitle && control.disabled;
    if (!control.matches(".hintbtn") && !iconOnly && !clipped && copyValue === undefined && !titleOnlyTarget && !disabledTitle) return;
    let text = control.dataset.tooltip || (control.dataset.hint
      ? document.getElementById(control.dataset.hint)?.textContent.trim()
      : textTitles.get(control) || control.dataset.wrelayTitle || control.getAttribute("aria-label") || label);
    if (copyValue !== undefined) text = copyValue;
    if (!text) return;
    owner = control;
    [control, control.closest(".id-cell"), control.closest("a[title]")].filter(Boolean).forEach(node => {
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
  window.showWRelayCopyFeedback = function (control, message) {
    hide(true);
    show(control);
    if (owner !== control) return;
    if (message === "已复制" && typeof globalThis.mcIcon === "function") {
      const icon = document.createElement("span");
      icon.className = "ui-tooltip-copy-icon";
      icon.innerHTML = globalThis.mcIcon("check", 16);
      const label = document.createElement("span");
      label.textContent = message;
      tooltip.replaceChildren(icon, label);
      tooltip.classList.add("ui-tooltip-copy-feedback");
    } else {
      tooltip.textContent = message;
    }
    feedbackPinned = true;
    feedbackTimer = setTimeout(function () {
      if (owner !== control) return;
      tooltip.classList.add("is-fading");
      feedbackTimer = setTimeout(function () { if (owner === control) hide(true); }, 240);
    }, 2000);
  };
  document.addEventListener("pointerover", event => show(event.target));
  document.addEventListener("pointerout", event => {
    if (owner && !owner.contains(event.relatedTarget) && !tooltip.contains(event.relatedTarget)) hide();
  });
  document.addEventListener("focusin", event => show(event.target));
  document.addEventListener("focusout", () => hide());
  document.addEventListener("click", () => hide(), true);
  document.addEventListener("scroll", event => { if (!tooltip.contains(event.target)) hide(); }, true);
  window.addEventListener("resize", () => hide());
  window.addEventListener("blur", () => hide());
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
