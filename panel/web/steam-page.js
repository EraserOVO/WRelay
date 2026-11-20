(function () {
  const $ = id => document.getElementById(id);
  let draft = null, draftRevision = "", draftOwner = "", draftBase = "", draftDirty = false, draftConflict = false, activeGroupId = "";
  let draftSaveTimer = 0, draftSavePromise = null, draftEditVersion = 0, draftSaveError = "", draftSaveInFlight = false;
  const drafts = new Map();
  const clone = value => JSON.parse(JSON.stringify(value));
  const labels = {announcements: "游戏公告", news: "游戏新闻", discount: "游戏折扣",
    new_listing: "新公开商店页面", early_access: "抢先体验开售", release: "正式发售", full_release: "抢先体验转正式版"};
  const defaults = {min_send_interval: 60, hourly_limit: 30, backlog_limit: 500};
  const id = prefix => prefix + "-" + (globalThis.crypto?.randomUUID?.() || (Date.now().toString(36) + Math.random().toString(36).slice(2)));
  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function eventsFor(source) {
    return source.kind === "game" ? ["announcements", "news", "discount"] : ["discount", "new_listing", "early_access", "release", "full_release"];
  }
  function activeGroup() {
    if (!draft?.groups.length) return null;
    const selected = draft.groups.find(group => String(group.id) === String(activeGroupId));
    if (selected) return selected;
    activeGroupId = draft.groups[0].id;
    return draft.groups[0];
  }
  function draftActionOwner() {
    const owner = draftOwner, activeBotId = currentQQBotId();
    return () => draft && draftOwner === owner && (settings._profile_bot || "") === owner && currentQQBotId() === activeBotId;
  }
  async function editRegisteredGroup(target) {
    const isCurrentOwner = draftActionOwner();
    const targetId = String(target.openid);
    if (!await flushDraftSave() || !isCurrentOwner()) return;
    const current = (settings.qq_group_openids || []).find(item =>
      String(item.openid) === targetId && groupInActiveQQBotWorkspace(item));
    if (current) editGroupDetails(current);
  }
  function markDraftDirty(delay = 400) {
    if (!draft) return;
    draftDirty = true; draftEditVersion++; draftSaveError = "";
    if (draftConflict) { draftWarning(); return; }
    clearTimeout(draftSaveTimer);
    if (delay !== null) draftSaveTimer = setTimeout(() => saveDraftNow(), Math.max(0, delay));
    draftWarning();
  }
  function addGroup() {
    renderSteamPage(); if (!draft) return showAlert("请稍候", "账号配置尚未加载。");
    if (draft.groups.length >= 30) return showAlert("添加转发组", "最多 30 个转发组。");
    const names = new Set(draft.groups.map(group => group.name));
    let number = 1; while (names.has("转发组" + number)) number++;
    const group = {id: id("steam-group"), name: "转发组" + number, enabled: true, sources: [], groups: [], disabled_groups: []};
    draft.groups.push(group); activeGroupId = group.id; markDraftDirty(0);
    renderGroups(); renderSources();
  }
  function addRegisteredSources() {
    renderSteamPage();
    const group = activeGroup();
    if (!group) return showAlert("请稍候", "账号配置尚未加载。");
    const memberIds = new Set((group.sources || []).map(String));
    const candidates = draft.sources.filter(source => !memberIds.has(String(source.id)));
    if (!candidates.length) return showAlert("从已登记项新增", "暂无可添加的来源", {emptyState: true});
    showConfirmSelect("从已登记项新增", candidates, source => {
      const type = source.kind === "game" ? "游戏" : "开发商 / 发行商";
      return ((source.name || source.value || "未命名来源") + " · " + type).trim();
    }, selected => {
      group.sources = [...new Set([...(group.sources || []).map(String), ...selected.map(source => String(source.id))])];
      markDraftDirty(0); renderSources(); renderGroups();
    }, {
      defaultChecked: false,
      cardRows: true,
      rowDeleteIcon: globalThis.mcIcon("delete-2", 14),
      onRowDelete: source => showConfirmDlg("删除 Steam 来源", "确定从 Steam 配置和所有转发组中删除「" + (source.name || source.value || "未命名来源") + "」？", () => {
        draft.sources = draft.sources.filter(item => String(item.id) !== String(source.id));
        draft.groups.forEach(item => { item.sources = item.sources.filter(value => String(value) !== String(source.id)); });
        markDraftDirty(0); renderSources(); renderGroups(); addRegisteredSources();
      }, "删除"),
    });
  }
  function openSourceRegistration() {
    renderSteamPage();
    if (!draft || !activeGroup()) return showAlert("请稍候", "Steam 转发组尚未加载。");
    const body = $("confirmBody"); body.replaceChildren();
    const form = node("div", null, "steam-register-form");
    const kindLabel = node("label", null, "steam-field");
    kindLabel.append(node("span", "来源类型", "steam-field-label"));
    const kind = node("select", null, "steam-input");
    [["game", "游戏"], ["creator", "开发商 / 发行商"]].forEach(([value, label]) => {
      const option = node("option", label); option.value = value; kind.append(option);
    });
    kindLabel.append(kind); form.append(kindLabel);
    const nameLabel = node("label", null, "steam-field");
    nameLabel.append(node("span", "名称（可留空）", "steam-field-label"));
    const nameInput = node("input", null, "steam-input"); nameInput.type = "text"; nameInput.maxLength = 120;
    nameLabel.append(nameInput); form.append(nameLabel);
    const valueLabel = node("label", null, "steam-field");
    const valueTitle = node("span", null, "steam-field-label");
    const valueInput = node("input", null, "steam-input"); valueInput.type = "text"; valueInput.autocomplete = "url";
    function updateKind() {
      const isGame = kind.value === "game";
      valueTitle.textContent = isGame ? "AppID 或 Steam 游戏地址" : "开发商或发行商主页地址";
      valueInput.placeholder = isGame ? "620" : "https://store.steampowered.com/developer/example";
    }
    kind.addEventListener("change", updateKind); updateKind();
    valueLabel.append(valueTitle, valueInput); form.append(valueLabel);
    body.append(form);
    $("confirmTitle").textContent = "注册 Steam 来源";
    setConfirmButtons("添加来源", true);
    confirmAction = () => {
      const value = valueInput.value.trim();
      if (!value) { valueInput.focus(); return showAlert("无法添加", "请填写 AppID 或 Steam 来源地址。"); }
      const isGame = kind.value === "game";
      const source = {
        id: id(isGame ? "game" : "creator"), kind: kind.value, value,
        name: nameInput.value.trim(), enabled: true,
        events: isGame ? ["announcements", "news", "discount"] : ["discount", "new_listing", "early_access", "release", "full_release"],
        news_feeds: [], translation_enabled: true, translation_append_original: true,
      };
      draft.sources.push(source);
      markDraftDirty(0); renderSources(); renderGroups();
    };
    $("confirmModal").style.display = "flex";
    valueInput.focus();
  }
  function editSource(source) {
    const isCurrentOwner = draftActionOwner();
    const sourceId = String(source.id);
    const body = $("confirmBody"); body.replaceChildren();
    const form = node("div", null, "steam-register-form");
    function textField(title, value, type = "text") {
      const label = node("label", null, "steam-field");
      label.append(node("span", title, "steam-field-label"));
      const input = node("input", null, "steam-input"); input.type = type; input.value = value || "";
      label.append(input); form.append(label); return input;
    }
    const nameInput = textField("名称（可留空）", source.name || "");
    const valueInput = textField(source.kind === "game" ? "AppID 或 Steam 游戏地址" : "开发商或发行商主页地址", source.value || "");
    body.append(form);
    $("confirmTitle").textContent = "编辑来源内容";
    setConfirmButtons("保存", true);
    confirmAction = () => {
      const value = valueInput.value.trim();
      if (!value) { valueInput.focus(); return showAlert("无法保存", "请填写 AppID 或 Steam 来源地址。"); }
      const current = isCurrentOwner() && draft.sources.find(item => String(item.id) === sourceId);
      if (!current) return showAlert("无法保存", "该 Steam 来源已变化，请刷新后重试。");
      current.name = nameInput.value.trim();
      current.value = value;
      markDraftDirty(0); renderSources(); renderGroups();
    };
    $("confirmModal").style.display = "flex";
    nameInput.focus();
  }
  function steamSourceUrl(source) {
    const value = String(source && source.value || "").trim();
    if (source && source.kind === "game") {
      let appId = /^\d+$/.test(value) ? value.replace(/^0+(?=\d)/, "") : "";
      if (!appId) {
        try {
          const parsed = new URL(value);
          if (["http:", "https:"].includes(parsed.protocol) && ["store.steampowered.com", "steamcommunity.com"].includes(parsed.hostname)) {
            const match = parsed.pathname.match(/^\/(?:app|games)\/(\d+)(?:\/|$)/i);
            appId = match ? match[1].replace(/^0+(?=\d)/, "") : "";
          }
        } catch (_) {}
      }
      return appId && appId !== "0" ? "https://store.steampowered.com/app/" + encodeURIComponent(appId) + "/" : "";
    }
    try {
      const parsed = new URL(value);
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.hostname !== "store.steampowered.com") return "";
      const match = parsed.pathname.match(/^\/(?:developer|publisher|dev|pub)\/([A-Za-z0-9_-]+)\/?$/i);
      return match ? "https://store.steampowered.com" + parsed.pathname.replace(/\/$/, "") : "";
    } catch (_) {
      return "";
    }
  }
  function sourceFilterActive(source) {
    const supportedEvents = eventsFor(source);
    const selectedEvents = Array.isArray(source.events) ? source.events : supportedEvents;
    return selectedEvents.length < supportedEvents.length || !!source.include_dlc || !!source.include_bundles ||
      (Array.isArray(source.news_feeds) && source.news_feeds.length > 0) ||
      source.translation_enabled === true || source.translation_append_original === false;
  }
  function editSourceFilter(source) {
    const isCurrentOwner = draftActionOwner();
    const sourceId = String(source.id);
    const body = $("confirmBody"); body.replaceChildren();
    const supportedEvents = eventsFor(source);
    const selectedEvents = new Set(Array.isArray(source.events) ? source.events : supportedEvents);
    const eventList = node("div", null, "content-filter-checks");
    const optionChanges = {};
    supportedEvents.forEach(event => {
      const label = node("label", null, "content-filter-check");
      const input = node("input"); input.type = "checkbox"; input.checked = selectedEvents.has(event);
      input.addEventListener("change", () => input.checked ? selectedEvents.add(event) : selectedEvents.delete(event));
      label.append(input, document.createTextNode(labels[event])); eventList.append(label);
    });
    const eventTitle = node("div", "投递内容", "steam-field-label");
    const extras = node("div", null, "content-filter-checks");
    function option(title, key) {
      const label = node("label", null, "content-filter-check");
      const input = node("input"); input.type = "checkbox"; input.checked = !!source[key];
      input.addEventListener("change", () => { optionChanges[key] = input.checked; });
      label.append(input, document.createTextNode(title)); extras.append(label);
    }
    option("折扣包含 DLC", "include_dlc"); option("折扣包含捆绑包", "include_bundles");
    const extraTitle = node("div", "折扣附加条件", "steam-field-label");
    body.append(eventTitle, eventList, extraTitle, extras);
    const translationTitle = node("div", "翻译", "steam-field-label");
    const translationOptions = node("div", null, "content-filter-checks");
    function translationOption(title, key, defaultValue) {
      const label = node("label", null, "content-filter-check");
      const input = node("input");
      input.type = "checkbox";
      input.checked = source[key] === undefined ? defaultValue : source[key] === true;
      input.addEventListener("change", () => { optionChanges[key] = input.checked; });
      label.append(input, document.createTextNode(title));
      translationOptions.append(label);
    }
    translationOption("启用翻译", "translation_enabled", false);
    translationOption("保留原文", "translation_append_original", true);
    body.append(translationTitle, translationOptions);
    let feedsInput = null;
    if (source.kind === "game") {
      const field = node("label", null, "steam-field");
      field.append(node("span", "新闻来源筛选（feed 名称，逗号分隔；留空接收全部）", "steam-field-label"));
      feedsInput = node("input", null, "steam-input"); feedsInput.type = "text";
      feedsInput.value = (source.news_feeds || []).join(", "); field.append(feedsInput); body.append(field);
    }
    $("confirmTitle").textContent = "内容筛选";
    setConfirmButtons("保存", true);
    confirmAction = async () => {
      if (draftSavePromise && !await draftSavePromise) return;
      const current = isCurrentOwner() && draft.sources.find(item => String(item.id) === sourceId);
      if (!current) return showAlert("无法保存", "该 Steam 来源已变化，请刷新后重试。");
      current.events = supportedEvents.filter(event => selectedEvents.has(event));
      if (feedsInput) current.news_feeds = feedsInput.value.split(/[,，]/).map(item => item.trim()).filter(Boolean);
      Object.assign(current, optionChanges);
      markDraftDirty(null); renderSources(); renderGroups();
      await saveDraftNow();
    };
    $("confirmModal").style.display = "flex";
  }
  function addRegisteredGroups() {
    renderSteamPage();
    const group = activeGroup();
    if (!group) return showAlert("请稍候", "账号配置尚未加载。");
    const memberIds = new Set((group.groups || []).map(String));
    const candidates = (settings.qq_group_openids || []).filter(target =>
      groupInActiveQQBotWorkspace(target) && !memberIds.has(String(target.openid))
    );
    if (!candidates.length) return showAlert("从已登记项新增", "暂无可添加的群", {emptyState: true});
    showConfirmSelect("从已登记项新增", candidates, target => {
      const name = target.name || target.remark || target.qq_id || target.openid;
      return (name + (target.qq_id ? " · " + target.qq_id : "") + " · " + target.openid).trim();
    }, selected => {
      group.groups = [...new Set([...(group.groups || []).map(String), ...selected.map(target => String(target.openid))])];
      const selectedIds = new Set(selected.map(target => String(target.openid)));
      group.disabled_groups = (group.disabled_groups || []).map(String).filter(value => !selectedIds.has(value));
      markDraftDirty(0); renderGroups();
    }, {defaultChecked: false, cardRows: true, detailFn: target => String(target.openid || ""), onRowEdit: editRegisteredGroup});
  }
  function renderGroupTabs() {
    const tabs = $("steamFgTabs"), priorScroll = tabs.querySelector(".fgtab-list")?.scrollLeft || 0;
    tabs.replaceChildren();
    const list = node("div", null, "fgtab-list");
    const actions = node("div", null, "fgtab-actions");
    tabs.append(list);
    let selected = activeGroup();
    if (!selected && draft.groups.length) { selected = draft.groups[0]; activeGroupId = selected.id; }
    draft.groups.forEach(group => {
      const isActive = group === selected, enabled = group.enabled !== false;
      const tab = node("button", null, "fgtab" + (isActive ? " active" : "") + (enabled ? "" : " paused"));
      tab.type = "button"; tab.dataset.id = group.id; tab.title = enabled ? group.name : group.name + "（已暂停）";
      const name = node("span", group.name || "未命名转发组", "fgtab-name"); tab.append(name);
      if (!enabled) tab.append(node("span", "暂停", "fgtab-state"));
      tab.addEventListener("click", () => { activeGroupId = group.id; renderGroups(); renderSources(); });
      list.append(tab);
      if (!isActive) return;

      const toggle = node("button", null, "fg-toggle rowicon" + (enabled ? "" : " paused"));
      toggle.type = "button"; toggle.title = enabled ? "暂停该转发组" : "恢复该转发组"; toggle.setAttribute("aria-label", toggle.title);
      toggle.innerHTML = globalThis.mcIcon(enabled ? "pause" : "play", 16);
      toggle.addEventListener("click", () => { group.enabled = !enabled; markDraftDirty(0); renderGroups(); });
      actions.append(toggle);

      const rename = node("button", null, "fgctrl rowicon");
      rename.type = "button"; rename.title = "重命名转发组"; rename.setAttribute("aria-label", rename.title);
      rename.innerHTML = globalThis.mcIcon("edit-2", 16);
      rename.addEventListener("click", () => {
        const isCurrentOwner = draftActionOwner();
        const groupId = String(group.id);
        const input = node("input", null, "fg-rename"); input.type = "text"; input.value = group.name || "";
        input.addEventListener("click", event => event.stopPropagation());
        name.replaceWith(input); input.focus(); input.select();
        let finished = false;
        const finish = commit => {
          if (finished) return; finished = true;
          const next = input.value.trim();
          if (!isCurrentOwner()) return;
          const current = draft.groups.find(item => String(item.id) === groupId);
          if (commit && next && current && current.name !== next) {
            current.name = next;
            markDraftDirty(0);
          }
          renderGroups();
        };
        input.addEventListener("blur", () => finish(true));
        input.addEventListener("keydown", event => {
          if (event.key === "Enter") { event.preventDefault(); finish(true); }
          if (event.key === "Escape") { event.preventDefault(); finish(false); }
        });
      });
      actions.append(rename);

      if (draft.groups.length > 1) {
        const remove = node("button", null, "fgctrl rowicon fg-delete");
        remove.type = "button"; remove.title = "删除转发组"; remove.setAttribute("aria-label", remove.title);
        remove.innerHTML = globalThis.mcIcon("delete-2", 16);
        remove.addEventListener("click", () => {
          if (draft.groups.length <= 1) return;
          const isCurrentOwner = draftActionOwner();
          const groupId = String(group.id);
          const deleteGroup = () => {
            if (!isCurrentOwner()) return;
            const index = draft.groups.findIndex(item => String(item.id) === groupId);
            if (index < 0 || draft.groups.length <= 1) return;
            draft.groups.splice(index, 1);
            if (String(activeGroupId) === groupId) activeGroupId = draft.groups[Math.max(0, index - 1)]?.id || draft.groups[0].id;
            markDraftDirty(0); renderGroups(); renderSources();
          };
          const hasMembers = (group.sources || []).length || (group.groups || []).length;
          if (hasMembers) showConfirmDlg("删除转发组", "确定删除转发组「" + group.name + "」？来源和 QQ 群本身不会删除。", deleteGroup, "删除");
          else deleteGroup();
        });
        actions.append(remove);
      }
    });
    if (draft.groups.length < 30) {
      const add = node("button", null, "fgtab-add"); add.id = "steamAddGroup"; add.type = "button";
      add.title = "添加转发组"; add.setAttribute("aria-label", add.title);
      add.innerHTML = globalThis.mcIcon("add", 16); add.addEventListener("click", addGroup); list.append(add);
    }
    list.scrollLeft = priorScroll;
    if (actions.childElementCount) tabs.append(actions);
  }
  function renderSources() {
    const body = $("steamSourceBody"); body.replaceChildren();
    const group = activeGroup();
    const sourceById = new Map(draft.sources.map(source => [String(source.id), source]));
    const sources = (group?.sources || []).map(sourceId => sourceById.get(String(sourceId))).filter(Boolean);
    if (!sources.length) {
      const row = node("tr", null, "fgempty-row");
      row.innerHTML = '<td colspan="6" class="fgempty-msg">还未新增来源，请先「注册来源」，再加入当前转发组。</td>';
      body.append(row);
    }
    sources.forEach(source => {
      const row = node("tr"); row.dataset.id = source.id;
      const orderCell = node("td", null, "steam-order-cell drag-handle");
      orderCell.innerHTML = globalThis.mcIcon("menu", 14);
      orderCell.title = "拖拽排序";
      orderCell.setAttribute("aria-label", orderCell.title);
      row.append(orderCell);
      const enabledCell = node("td"); enabledCell.style.textAlign = "center";
      const enabled = node("input"); enabled.type = "checkbox"; enabled.checked = source.enabled === true;
      enabled.setAttribute("aria-label", "允许 " + (source.name || source.value) + " 参与轮询与发送");
      enabled.addEventListener("change", () => { source.enabled = enabled.checked; markDraftDirty(0); syncSourceHeader(sources); });
      enabledCell.append(enabled); row.append(enabledCell);
      row.append(node("td", source.kind === "game" ? "游戏" : "开发商 / 发行商"));
      const sourceName = node("td");
      const sourceUrl = steamSourceUrl(source);
      if (sourceUrl) {
        const nameLink = node("a", source.name || "未命名来源", "source-link steam-source-link table-cell-clip");
        nameLink.href = sourceUrl; nameLink.target = "_blank"; nameLink.rel = "noopener noreferrer";
        nameLink.title = "打开 Steam 来源";
        sourceName.append(nameLink);
      } else {
        sourceName.append(node("div", source.name || "未命名来源"));
      }
      row.append(sourceName);
      const sourceLink = node("td", null, "mono");
      const sourceValue = String(source.value || "");
      if (sourceUrl) {
        const link = node("a", sourceValue, "source-link steam-source-link table-cell-clip");
        link.href = sourceUrl; link.target = "_blank"; link.rel = "noopener noreferrer"; link.title = "打开 Steam 来源";
        sourceLink.append(link);
      } else {
        sourceLink.append(node("span", sourceValue, "table-cell-clip"));
        sourceLink.title = sourceValue;
      }
      row.append(sourceLink);
      const actionsCell = node("td"); actionsCell.style.textAlign = "right";
      const actions = node("div", null, "table-actions");
      const filter = node("button", null, "sec rowicon" + (sourceFilterActive(source) ? " filter-active" : ""));
      filter.type = "button"; filter.title = "内容筛选"; filter.setAttribute("aria-label", filter.title);
      filter.innerHTML = globalThis.mcIcon("filter-2", 16); filter.addEventListener("click", () => editSourceFilter(source));
      const edit = node("button", null, "sec rowicon"); edit.type = "button"; edit.title = "编辑名称与来源地址"; edit.setAttribute("aria-label", edit.title);
      edit.innerHTML = globalThis.mcIcon("edit-2", 16); edit.addEventListener("click", () => editSource(source));
      const remove = node("button", null, "danger rowicon"); remove.type = "button"; remove.title = "移出当前转发组"; remove.setAttribute("aria-label", remove.title);
      remove.innerHTML = globalThis.mcIcon("delete-2", 16); remove.addEventListener("click", () => {
        if (!group) return;
        group.sources = group.sources.filter(value => String(value) !== String(source.id));
        markDraftDirty(0); renderSources(); renderGroups();
      });
      actions.append(filter, edit, remove); actionsCell.append(actions); row.append(actionsCell); body.append(row);
    });
    syncSourceHeader(sources);
  }
  function syncSourceHeader(sources) {
    const all = $("steamSourceAllEnabled");
    if (!all) return;
    all.checked = sources.length > 0 && sources.every(source => source.enabled === true);
    all.indeterminate = sources.some(source => source.enabled === true) && sources.some(source => source.enabled !== true);
    all.disabled = !sources.length;
  }
  function renderGroups() {
    renderGroupTabs();
    const body = $("steamGroups"); body.replaceChildren();
    const group = activeGroup();
    const availableTargets = new Map((settings.qq_group_openids || [])
      .filter(groupInActiveQQBotWorkspace)
      .map(target => [String(target.openid), target]));
    const targets = (group?.groups || []).map(id => availableTargets.get(String(id))).filter(Boolean);
    if (!targets.length) {
      const row = node("tr", null, "fgempty-row");
      const cell = node("td", "还未新增群，请加入一个 QQ 接收群。", "fgempty-msg");
      cell.colSpan = 6; row.append(cell); body.append(row);
    }
    targets.forEach(target => {
      const row = node("tr"), orderCell = node("td", null, "steam-order-cell drag-handle");
      row.dataset.id = String(target.openid);
      orderCell.innerHTML = globalThis.mcIcon("menu", 14);
      orderCell.title = "拖拽排序";
      orderCell.setAttribute("aria-label", orderCell.title);
      row.append(orderCell);
      const enabledCell = node("td"); enabledCell.style.textAlign = "center";
      const disabled = new Set((group?.disabled_groups || []).map(String));
      const enabled = node("input"); enabled.type = "checkbox"; enabled.checked = !disabled.has(String(target.openid));
      enabled.setAttribute("aria-label", "允许 " + (target.name || target.qq_id || target.openid) + " 接收转发");
      enabled.addEventListener("change", () => {
        const ids = new Set((group.disabled_groups || []).map(String));
        if (enabled.checked) ids.delete(String(target.openid)); else ids.add(String(target.openid));
        group.disabled_groups = Array.from(ids);
        markDraftDirty(0); renderGroups();
      });
      enabledCell.append(enabled); row.append(enabledCell);
      const nameCell = node("td");
      if (target.name || target.remark) {
        const name = node("div", target.name || target.remark); name.style.cssText = "color:var(--fg);font-size:12px;word-break:break-all;";
        nameCell.append(name);
      }
      row.append(nameCell);
      const account = node("td", target.qq_id || ""); account.style.fontSize = "12px"; row.append(account);
      const openid = node("td"); openid.innerHTML = renderIdCell(target.openid); row.append(openid);
      const actions = node("td");
      const actionWrap = node("div", null, "table-actions");
      const edit = node("button", null, "sec rowicon"); edit.type = "button"; edit.title = "编辑群资料"; edit.setAttribute("aria-label", edit.title);
      edit.innerHTML = globalThis.mcIcon("edit-2", 16); edit.addEventListener("click", () => editRegisteredGroup(target));
      const remove = node("button", null, "danger rowicon"); remove.type = "button"; remove.title = "移出当前转发组"; remove.setAttribute("aria-label", remove.title);
      remove.innerHTML = globalThis.mcIcon("delete-2", 16); remove.addEventListener("click", () => {
        if (!group) return;
        group.groups = group.groups.filter(value => String(value) !== String(target.openid));
        group.disabled_groups = (group.disabled_groups || []).map(String).filter(value => value !== String(target.openid));
        markDraftDirty(0); renderGroups();
      });
      actionWrap.append(edit, remove); actions.append(actionWrap); row.append(actions); body.append(row);
    });
    const allEnabled = $("steamGroupAllEnabled");
    if (allEnabled) {
      const disabled = new Set((group?.disabled_groups || []).map(String));
      allEnabled.checked = targets.length > 0 && targets.every(target => !disabled.has(String(target.openid)));
      allEnabled.indeterminate = targets.some(target => !disabled.has(String(target.openid))) && targets.some(target => disabled.has(String(target.openid)));
      allEnabled.disabled = !targets.length;
    }
  }
  function bindMemberDrag(selector, field, visibleIdsFor, rerender) {
    let drag = null;
    document.addEventListener("mousedown", event => {
      const handle = event.target.closest(selector + " .drag-handle");
      const row = handle?.closest("tr[data-id]"), group = activeGroup();
      if (!row || !group) return;
      event.preventDefault();
      drag = {body: row.parentElement, row, groupId: String(group.id), startX: event.clientX, startY: event.clientY};
      row.classList.add("drag-src");
    });
    document.addEventListener("mousemove", event => {
      if (!drag) return;
      event.preventDefault();
      if (Math.abs(event.clientX - drag.startX) < 3 && Math.abs(event.clientY - drag.startY) < 3) return;
      const body = drag.body;
      body.querySelectorAll("tr.drop-before, tr.drop-after").forEach(row => row.classList.remove("drop-before", "drop-after"));
      const under = document.elementFromPoint(event.clientX, event.clientY);
      const row = under?.closest("tr[data-id]");
      if (!row || row.parentElement !== body || row === drag.row) return;
      const rect = row.getBoundingClientRect();
      row.classList.add(event.clientY > rect.top + rect.height / 2 ? "drop-after" : "drop-before");
    });
    document.addEventListener("mouseup", event => {
      if (!drag) return;
      const state = drag; drag = null;
      state.row.classList.remove("drag-src");
      state.body.querySelectorAll("tr.drop-before, tr.drop-after").forEach(row => row.classList.remove("drop-before", "drop-after"));
      if (Math.abs(event.clientX - state.startX) < 3 && Math.abs(event.clientY - state.startY) < 3) return;
      if (!state.row.isConnected) return;
      const under = document.elementFromPoint(event.clientX, event.clientY);
      const target = under?.closest("tr[data-id]");
      const targetBody = target?.parentElement || under?.closest("tbody");
      if (targetBody !== state.body) return;
      if (target && target !== state.row) {
        const rect = target.getBoundingClientRect();
        state.body.insertBefore(state.row, event.clientY > rect.top + rect.height / 2 ? target.nextSibling : target);
      } else if (!target && state.body.lastElementChild && state.body.lastElementChild !== state.row) {
        state.body.appendChild(state.row);
      } else {
        return;
      }
      const group = draft?.groups.find(item => String(item.id) === state.groupId);
      if (!group) return rerender();
      const ids = Array.from(state.body.querySelectorAll("tr[data-id]"), row => String(row.dataset.id));
      const visibleIds = visibleIdsFor(group).map(String), visibleSet = new Set(visibleIds);
      if (visibleSet.size !== visibleIds.length || ids.length !== visibleIds.length ||
          new Set(ids).size !== visibleIds.length || !ids.every(id => visibleSet.has(id))) {
        rerender();
        return;
      }
      let index = 0;
      const nextMembers = (group[field] || []).map(id => visibleSet.has(String(id)) ? ids[index++] : id);
      if (nextMembers.every((id, i) => String(id) === String(group[field][i]))) return;
      group[field] = nextMembers;
      markDraftDirty(0);
      rerender();
    });
  }
  bindMemberDrag("#steamSourceBody", "sources", group => {
    const sourceIds = new Set(draft.sources.map(source => String(source.id)));
    return (group.sources || []).map(String).filter(id => sourceIds.has(id));
  }, renderSources);
  bindMemberDrag("#steamGroups", "groups", group => {
    const targetIds = new Set((settings.qq_group_openids || []).filter(groupInActiveQQBotWorkspace).map(target => String(target.openid)));
    return (group.groups || []).map(String).filter(id => targetIds.has(id));
  }, renderGroups);
  function redraw() { renderSources(); renderGroups(); }
  function savedValues() {
    return {sources: clone(settings.steam_sources || []), groups: clone(settings.steam_forwarding_groups || [])};
  }
  function prepareDraft(values) {
    values.sources.forEach(source => {
      source.events = Array.isArray(source.events) ? source.events : [];
      source.news_feeds = Array.isArray(source.news_feeds) ? source.news_feeds : [];
      source.enabled = source.enabled === true;
    });
    values.groups.forEach(group => {
      group.enabled = group.enabled !== false;
      group.sources = Array.isArray(group.sources) ? group.sources : [];
      group.groups = Array.isArray(group.groups) ? group.groups : [];
      group.disabled_groups = Array.isArray(group.disabled_groups) ? group.disabled_groups.map(String) : [];
    });
    if (!values.groups.length) values.groups.push({id: "steam_default", name: "转发组1", enabled: true, sources: [], groups: [], disabled_groups: []});
    return values;
  }
  function renderPollingSettings() {
    const region = $("steamRegion"), interval = $("steamPollInterval");
    if (region && document.activeElement !== region) {
      const currentRegion = String(settings.steam_region || "CN").trim().toUpperCase();
      if (currentRegion && !Array.from(region.options).some(option => option.value === currentRegion)) {
        const option = document.createElement("option");
        option.value = currentRegion;
        option.textContent = "其他地区（" + currentRegion + "）";
        region.append(option);
      }
      region.value = currentRegion || "CN";
    }
    if (interval && document.activeElement !== interval) {
      const value = Number(settings.steam_poll_interval);
      interval.value = value >= 300 ? String(Math.min(value, 86400)) : "900";
    }
  }
  async function savePollingSettings() {
    const regionInput = $("steamRegion"), intervalInput = $("steamPollInterval");
    const region = String(regionInput.value || "").trim().toUpperCase();
    if (!/^[A-Z]{2}$/.test(region) || !Array.from(regionInput.options).some(option => option.value === region)) {
      regionInput.value = String(settings.steam_region || "CN");
      return showAlert("无法保存", "请选择有效的 Steam 商店地区。");
    }
    let interval = Number.parseInt(intervalInput.value, 10);
    if (!Number.isInteger(interval) || interval < 300) interval = 300;
    if (interval > 86400) interval = 86400;
    regionInput.value = region; intervalInput.value = String(interval);
    const previousRegion = settings.steam_region || "CN";
    const previousInterval = settings.steam_poll_interval || 900;
    if (region === previousRegion && interval === previousInterval) return;
    if (!await flushDraftSave()) return showAlert("无法保存", "请先处理 Steam 来源或转发组的未完成保存。");
    const originalSettings = settings;
    settings.steam_region = region; settings.steam_poll_interval = interval;
    let saved = false;
    try { saved = await saveAndReload(); }
    catch (error) { showAlert("保存失败", "无法连接管理服务，请稍后重试。"); }
    if (!saved && settings === originalSettings) {
      settings.steam_region = previousRegion; settings.steam_poll_interval = previousInterval;
      renderPollingSettings();
    }
  }
  function draftWarning() {
    const actions = $("steamDraftActions"), warning = $("steamDraftWarning");
    if (!actions || !warning) return;
    const visible = draftConflict || !!draftSaveError;
    actions.hidden = !visible;
    warning.textContent = draftConflict
      ? "Steam 来源或转发组已在其他位置更改；草稿仍保留。覆盖保存会替换远端的 Steam 来源和转发组。"
      : draftSaveError;
    const retry = $("steamRetryDraft");
    retry.textContent = draftConflict ? "覆盖保存" : "重试保存";
    retry.disabled = draftSaveInFlight || !draftDirty;
    $("steamResetDraft").disabled = draftSaveInFlight;
  }
  async function saveDraftNow(forceConflict = false) {
    clearTimeout(draftSaveTimer);
    if (draftSavePromise) return draftSavePromise;
    if (!draft || !draftDirty) return true;
    if (forceConflict) { draftConflict = false; draftBase = JSON.stringify(savedValues()); }
    if (draftConflict) { draftWarning(); return false; }
    if (draftOwner !== (settings._profile_bot || "")) {
      draftSaveError = "账号配置已变化，请重新加载 Steam 页面后再修改。"; draftWarning(); return false;
    }
    const current = JSON.stringify(savedValues());
    if (draftBase !== current) {
      draftConflict = true; draftSaveError = ""; draftWarning(); return false;
    }
    if (draft.sources.some(source => !String(source.value || "").trim())) {
      draftSaveError = "请填写每个 Steam 来源地址，才能自动保存。"; draftWarning(); return false;
    }
    const owner = draftOwner, version = draftEditVersion;
    const prior = savedValues();
    const next = {sources: clone(draft.sources), groups: clone(draft.groups)};
    const expected = JSON.stringify(next), originalSettings = settings;
    settings.steam_sources = next.sources; settings.steam_forwarding_groups = next.groups;
    draftSaveError = ""; draftSaveInFlight = true;
    draftSavePromise = (async () => {
      let saved = false;
      try {
        saved = await saveAndReload();
      }
      catch (error) { draftSaveError = "保存失败，草稿已保留。"; showAlert("保存失败", "无法连接管理服务，请稍后重试。"); }
      if (owner !== draftOwner || owner !== (settings._profile_bot || "")) return false;
      if (saved) {
        draftBase = JSON.stringify(savedValues()); draftRevision = settings._revision || "";
        draftConflict = false; draftSaveError = ""; drafts.delete(owner);
        if (draftEditVersion === version) { draft = prepareDraft(savedValues()); draftDirty = false; }
        else draftDirty = true;
        redraw(); draftWarning();
        return true;
      }
      if (settings === originalSettings) {
        settings.steam_sources = prior.sources; settings.steam_forwarding_groups = prior.groups;
      }
      draftBase = JSON.stringify(savedValues());
      draftConflict = draftBase !== expected;
      if (!draftSaveError) draftSaveError = draftConflict ? "配置已在其他位置变化；请先决定覆盖保存或重新加载。" : "保存失败，草稿已保留。";
      draftDirty = true; draftWarning();
      return false;
    })();
    let result = false;
    try { result = await draftSavePromise; }
    finally {
      draftSavePromise = null; draftSaveInFlight = false; draftWarning();
      if (result && draftDirty && !draftConflict) {
        clearTimeout(draftSaveTimer); draftSaveTimer = setTimeout(() => saveDraftNow(), 0);
      }
    }
    return result;
  }
  async function flushDraftSave() {
    clearTimeout(draftSaveTimer);
    if (draftConflict) return false;
    if (draftSavePromise) {
      const saved = await draftSavePromise;
      if (!saved) return false;
    }
    if (draftDirty) return saveDraftNow();
    return true;
  }
  function openProtectionModal() {
    renderSteamPage();
    if (!draft) return showAlert("请稍候", "账号配置尚未加载。");
    const body = $("confirmBody"); body.replaceChildren();
    const form = node("div", null, "bilibili-protection-modal-form");
    const row = node("div", null, "bilibili-protection-modal-row");
    const protectionSettings = Object.assign({}, defaults, settings.steam_protection || {});
    function numberField(key, label, unit, min, max) {
      const wrapper = node("label", null, "bilibili-protection-modal-field");
      wrapper.append(node("span", label));
      const control = node("span"); const input = node("input");
      input.type = "number"; input.min = String(min); input.max = String(max); input.step = "1";
      input.value = String(protectionSettings[key]); input.dataset.key = key;
      control.append(input, node("span", unit)); wrapper.append(control); row.append(wrapper);
    }
    numberField("min_send_interval", "最短发送间隔", "秒", 1, 86400);
    numberField("hourly_limit", "每小时发送上限", "条", 1, 10000);
    numberField("backlog_limit", "待发送数量上限", "条", 1, 10000);
    form.append(row);
    form.append(node("div", "此窗口单独保存投递保护参数；来源和转发组修改后会自动保存。", "steam-protection-hint"));
    body.append(form);
    $("confirmTitle").textContent = "Steam 投递保护";
    setConfirmButtons("保存", true);
    const owner = draftOwner;
    confirmAction = async function () {
      if (!draft || owner !== draftOwner || owner !== (settings._profile_bot || "")) return showAlert("保存失败", "账号配置已变化，请重新打开设置。");
      if (!await flushDraftSave()) return showAlert("保存失败", "请先处理 Steam 来源或转发组的未完成保存。");
      const protection = {};
      for (const input of form.querySelectorAll("input[data-key]")) {
        const value = Number.parseInt(input.value, 10);
        const min = Number.parseInt(input.min, 10), max = Number.parseInt(input.max, 10);
        if (!Number.isInteger(value) || value < min || value > max) {
          input.focus(); return showAlert("无法保存", input.closest("label").firstChild.textContent + "需在 " + min + "–" + max + " 范围内。");
        }
        protection[input.dataset.key] = value;
      }
      const originalSettings = settings;
      const previous = clone(settings.steam_protection || defaults);
      settings.steam_protection = protection;
      let saved = false;
      try { saved = await saveAndReload(); }
      catch (error) { showAlert("保存失败", "无法连接管理服务，请稍后重试。"); }
      if (saved && draft && owner === draftOwner && owner === (settings._profile_bot || "")) {
        draftBase = JSON.stringify(savedValues());
        draftRevision = settings._revision || "";
        draftConflict = false;
        draftWarning();
      } else if (!saved && settings === originalSettings) {
        settings.steam_protection = previous;
      }
    };
    $("confirmModal").style.display = "flex";
  }
  window.renderSteamPage = function () {
    if (typeof settings === "undefined" || !settings || !$("steamSourceBody")) return;
    renderPollingSettings();
    const owner = settings._profile_bot || "", revision = settings._revision || "";
    if (draftSaveInFlight) { draftRevision = revision; return; }
    const values = savedValues(), current = JSON.stringify(values);
    if (draft && draftOwner !== owner) {
      clearTimeout(draftSaveTimer);
      drafts.set(draftOwner, {draft, base: draftBase, dirty: draftDirty});
      draft = null;
    }
    if (!draft) {
      const saved = drafts.get(owner);
      const sourceValues = saved ? saved.draft : values;
      const needsDefaultGroup = !sourceValues.groups.length;
      draft = prepareDraft(sourceValues);
      draftBase = saved ? saved.base : current;
      draftDirty = saved ? saved.dirty || needsDefaultGroup : needsDefaultGroup;
      draftOwner = owner; draftRevision = revision;
      draftConflict = draftDirty && draftBase !== current;
      if (!draftDirty && draftBase !== current) { draft = prepareDraft(values); draftDirty = !values.groups.length; draftBase = current; }
      redraw(); draftWarning();
      if (draftDirty && !draftConflict) { clearTimeout(draftSaveTimer); draftSaveTimer = setTimeout(() => saveDraftNow(), 0); }
    } else if (draftRevision !== revision) {
      draftRevision = revision;
      draftConflict = draftDirty && draftBase !== current;
      if (!draftDirty && draftBase !== current) { draft = prepareDraft(values); draftDirty = !values.groups.length; draftBase = current; redraw(); }
      draftWarning();
      if (draftDirty && !draftConflict) { clearTimeout(draftSaveTimer); draftSaveTimer = setTimeout(() => saveDraftNow(), 0); }
    }
  };
  $("steamRegisterSource").addEventListener("click", openSourceRegistration);
  $("steamAddRegisteredSource").addEventListener("click", addRegisteredSources);
  $("steamSourceAllEnabled").addEventListener("change", event => {
    const group = activeGroup(); if (!group) return;
    const memberIds = new Set((group.sources || []).map(String));
    draft.sources.forEach(source => { if (memberIds.has(String(source.id))) source.enabled = event.target.checked; });
    markDraftDirty(0); renderSources();
  });
  $("steamAddGroupTarget").addEventListener("click", addRegisteredGroups);
  $("steamSyncGroups").addEventListener("click", async () => {
    if (await flushDraftSave()) openRegistrationReview("groups");
  });
  $("steamGroupAllEnabled").addEventListener("change", async event => {
    const group = activeGroup(); if (!group) return;
    const memberIds = new Set((group.groups || []).map(String));
    const disabled = new Set((group.disabled_groups || []).map(String));
    (settings.qq_group_openids || []).filter(target => memberIds.has(String(target.openid)) && groupInActiveQQBotWorkspace(target)).forEach(target => {
      if (event.target.checked) disabled.delete(String(target.openid)); else disabled.add(String(target.openid));
    });
    group.disabled_groups = Array.from(disabled);
    markDraftDirty(0); renderGroups();
  });
  $("steamRegion").addEventListener("change", savePollingSettings);
  $("steamPollInterval").addEventListener("change", savePollingSettings);
  $("btnSteamProtection").addEventListener("click", openProtectionModal);
  $("steamRetryDraft").addEventListener("click", () => saveDraftNow(draftConflict));
  $("steamResetDraft").addEventListener("click", () => showConfirmDlg("重新加载 Steam 设置", "将放弃尚未保存的 Steam 来源和转发组修改。", () => {
    clearTimeout(draftSaveTimer); drafts.delete(draftOwner); draft = null; draftDirty = false; draftConflict = false; draftSaveError = ""; renderSteamPage();
  }, "重新加载"));
  renderSteamPage();
})();
