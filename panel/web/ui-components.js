/* Presentation components: reuse existing nodes, IDs and delegated actions. */
(function () {
  const externalIcon = '<svg class="external-link-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3h7v7M21 3 10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></svg>';
  function externalLinkButton(button) {
    const destination = button.getAttribute("aria-label") ||
      button.closest(".settings-row")?.querySelector("strong")?.textContent || button.textContent.trim();
    const label = "打开" + destination.replace(/^打开\s*/, "");
    button.classList.add("external-link-button");
    button.innerHTML = externalIcon + '<span>打开</span>';
    button.setAttribute("aria-label", label);
    button.title = label;
  }
  document.querySelectorAll('.linkbtn').forEach(externalLinkButton);
  // 发布页入口已从设置页移除；保留空值保护，避免该行缺失时中断后续组件装饰。
  const release = document.getElementById("btnOpenRelease");
  if (release) {
    new MutationObserver(() => {
      if (!release.querySelector(".external-link-icon")) externalLinkButton(release);
    }).observe(release, {childList: true});
  }

  const specs = {
    runtimeLogModal: {size: "large", footer: "btnClearLog", grid: true, backdrop: true},
    discordConfigModal: {size: "medium", footer: "discordConfigSave", grid: true, backdrop: true},
    qqConfigModal: {size: "medium", footer: "qqConfigSave", grid: true, backdrop: true},
    personalQQModal: {size: "medium", footer: "personalQQSave", grid: true, backdrop: true},
    profileImportModal: {size: "medium", footer: "profileImportSave", grid: true, backdrop: true},
    translationConfigModal: {size: "large", footer: "translationConfigSave", backdrop: true},
    backupRestoreModal: {size: "medium", footer: "btnBackupRestoreConfirm", heading: ".backup-title", grid: true, backdrop: true},
    diagnosticModal: {size: "small", heading: ".diagnostic-toolbar", tools: ["btnDiagnosticRefresh"], backdrop: true},
    messageTraceModal: {size: "large", heading: ".message-trace-toolbar", tools: ["messageTraceSummary", "btnMessageTraceRefresh"], backdrop: true},
    confirmModal: {size: "small", footer: "confirmOk", backdrop: true},
    bilibiliSourceModal: {size: "medium", footer: "bilibiliSourceSave", heading: ".bilibili-source-heading", backdrop: true},
    youtubeSourceModal: {size: "medium", footer: "youtubeSourceSave", heading: ".bilibili-source-heading", backdrop: true},
    filterModal: {size: "medium", footer: "filterSave", backdrop: true},
    translationSourceModal: {size: "small", footer: "translationSourceSave", grid: true, backdrop: true},
    relayChannelsModal: {size: "medium", footer: "relayChannelsSave", grid: true, tools: ["relayScopeTabs"], backdrop: true},
    translationChainsModal: {size: "small", grid: true, backdrop: true},
    scheduledMessageContentModal: {size: "large", footer: "scheduledMessageContentConfirm", grid: true, backdrop: true},
  };
  function closeButton(id, text) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sec";
    button.dataset.close = id;
    button.textContent = text;
    return button;
  }
  function modalShell(id, spec) {
    const overlay = document.getElementById(id);
    const shell = overlay.firstElementChild;
    const title = shell.querySelector("h3");
    const close = shell.querySelector(".modal-x");
    const oldHeader = spec.heading && shell.querySelector(spec.heading);
    const subtitle = oldHeader && oldHeader.querySelector("p");
    const oldFooter = spec.footer && document.getElementById(spec.footer).parentElement;
    overlay.classList.add("modal-overlay");
    const display = overlay.style.display;
    overlay.removeAttribute("style");
    overlay.style.display = display || "none";
    overlay.removeAttribute("role");
    overlay.removeAttribute("aria-modal");
    shell.removeAttribute("style");
    shell.classList.add("modal-shell", "modal-" + spec.size);
    shell.setAttribute("role", "dialog");
    shell.setAttribute("aria-modal", "true");
    title.id ||= id + "Title";
    shell.setAttribute("aria-labelledby", title.id);
    const header = document.createElement("header");
    header.className = "modal-header";
    const heading = document.createElement("div");
    heading.className = "modal-heading";
    heading.append(title);
    if (subtitle) heading.append(subtitle);
    const actions = document.createElement("div");
    actions.className = "modal-header-actions";
    (spec.tools || []).forEach(tool => actions.append(document.getElementById(tool)));
    close.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    actions.append(close);
    header.append(heading, actions);
    oldHeader?.remove();
    const footer = document.createElement("footer");
    footer.className = "modal-footer";
    if (oldFooter) {
      footer.append(...oldFooter.childNodes);
      oldFooter.remove();
    } else footer.append(closeButton(id, "关闭"));
    if (id === "filterModal") footer.prepend(closeButton(id, "取消"));
    const body = document.createElement("div");
    body.className = spec.grid ? "modal-body modal-grid" : "modal-body";
    body.append(...shell.childNodes);
    shell.append(header, body, footer);
    body.querySelectorAll('p, .flt-col-hint').forEach(node => node.classList.add("help-text"));
    body.querySelectorAll('.backup-warning').forEach(node => node.classList.add("danger-text"));
    body.querySelectorAll('label').forEach(node => node.classList.add("form-field"));
    let overlayPress = false;
    overlay.addEventListener("pointerdown", event => { overlayPress = event.target === overlay; });
    overlay.addEventListener("click", event => {
      if (spec.backdrop && overlayPress && event.target === overlay) close.click();
      overlayPress = false;
    });
  }
  Object.entries(specs).forEach(([id, spec]) => modalShell(id, spec));

  function hideCurrentSelectOption(select) {
    if (!(select instanceof HTMLSelectElement) || select.multiple || select.size > 1) return;
    const selected = select.selectedOptions[0] || null;
    Array.from(select.options).forEach(option => { option.hidden = option === selected; });
  }
  window.refreshWRelaySelectOptions = hideCurrentSelectOption;
  document.querySelectorAll("select").forEach(hideCurrentSelectOption);
  document.addEventListener("change", event => {
    if (event.target instanceof HTMLSelectElement) hideCurrentSelectOption(event.target);
  }, true);
  document.addEventListener("pointerdown", event => {
    if (event.target instanceof HTMLSelectElement) hideCurrentSelectOption(event.target);
  }, true);
  document.addEventListener("keydown", event => {
    if (event.target instanceof HTMLSelectElement && ["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      hideCurrentSelectOption(event.target);
    }
  }, true);
  new MutationObserver(records => {
    const changedSelects = new Set();
    records.forEach(record => {
      const target = record.target;
      if (target instanceof HTMLSelectElement) changedSelects.add(target);
      else if (target instanceof Element) {
        const parent = target.closest("select");
        if (parent) changedSelects.add(parent);
      }
      record.addedNodes.forEach(node => {
        if (!(node instanceof Element)) return;
        if (node instanceof HTMLSelectElement) changedSelects.add(node);
        node.querySelectorAll("select").forEach(select => changedSelects.add(select));
      });
    });
    changedSelects.forEach(hideCurrentSelectOption);
  }).observe(document.body, {childList: true, subtree: true});

  const autoScroll = document.getElementById("btnLogAutoScroll");
  function decorateLogToggle() {
    autoScroll.setAttribute("role", "switch");
    autoScroll.setAttribute("aria-checked", String(autoScroll.dataset.on === "1"));
    autoScroll.removeAttribute("aria-pressed");
    if (autoScroll.classList.contains("pill-switch")) {
      // 胶囊开关：文字在左、轨道在右；渲染函数只更新 .pill-switch-label，不重建轨道。
      let label = autoScroll.querySelector(".pill-switch-label");
      if (!label) {
        label = document.createElement("span");
        label.className = "pill-switch-label";
        label.textContent = autoScroll.textContent.trim();
        autoScroll.prepend(label);
      }
      if (!autoScroll.querySelector(".settings-switch-track")) {
        autoScroll.append(Object.assign(document.createElement("span"), {className: "settings-switch-track", ariaHidden: "true"}));
      }
      return;
    }
    if (!autoScroll.querySelector(".settings-switch-track")) {
      autoScroll.innerHTML = '<span>自动滚动</span><span class="settings-switch-track" aria-hidden="true"></span>';
    }
  }
  new MutationObserver(decorateLogToggle).observe(autoScroll, {childList: true, attributes: true, attributeFilter: ["data-on"]});
  decorateLogToggle();
  document.querySelectorAll(".foldbtn").forEach(button => {
    button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
    button.setAttribute("aria-controls", button.dataset.fold);
    button.setAttribute("aria-expanded", String(!button.classList.contains("folded")));
  });
})();
