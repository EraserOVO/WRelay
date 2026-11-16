/* Import is reviewed against one captured account; nothing is saved by preview. */
(function () {
  const $ = id => document.getElementById(id);
  const modal = $("profileImportModal");
  const menuButton = $("profileMenuButton"), menu = $("profileMenu");
  function closeMenu() { menu.hidden = true; menuButton.setAttribute("aria-expanded", "false"); }
  menuButton.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    menuButton.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("click", event => { if (!event.target.closest(".profile-config-menu")) closeMenu(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !menu.hidden) { closeMenu(); menuButton.focus(); } });
  let owner = "", documentValue = null, preview = null, importMapping = {}, generation = 0, busy = false;
  const fileInput = $("profileImportFile"), fileName = $("profileImportFileName");
  async function chooseFile() {
    const api = window.pywebview && window.pywebview.api;
    if (api && api.open_bot_profile) {
      try {
        const result = await api.open_bot_profile();
        if (!result || !result.ok) throw new Error(result?.msg || "无法打开配置文件");
        if (result.cancelled) return;
        const content = String(result.content || "");
        await processSelectedFile({
          name: result.filename || "Bot 配置.json",
          size: new Blob([content]).size,
          text: async () => content,
        });
      } catch (error) { showAlert("上传失败", error.message || "无法打开配置文件"); }
      return;
    }
    // Use the browser picker when the native bridge is not available yet.
    fileInput.value = "";
    fileInput.click();
  }
  $("profileImportChoose").addEventListener("click", chooseFile);
  function invalidate() {
    generation++;
    preview = null;
    $("profileImportSave").disabled = true;
    $("profileImportReview").textContent = "正在核对导入内容，请检查账号、文件和预览结果。";
  }
  function close() {
    if (busy) return;
    invalidate();
    modal.style.display = "none";
    $("qqBotWorkspace").disabled = false;
    renderQQBotWorkspace();
  }
  function endpoint(action) {
    return "/api/bot-profile/" + action + "?bot_id=" + encodeURIComponent(owner);
  }
  function body() {
    if (!documentValue) throw new Error("请先选择 Bot 配置文件");
    if (currentQQBotId() !== owner) throw new Error("当前账号已变化，请重新打开上传窗口");
    return {document: documentValue, mode: "replace", mapping: importMapping};
  }
  function summarizeLine(label, items, getName) {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) return label + "：无（0个）";
    const first = String(getName(list[0]) || "未命名").trim();
    return label + "：" + (list.length === 1 ? first + "（1个）" : first + "等" + list.length + "个");
  }
  function buildGroupMapping(value) {
    const groups = (settings.qq_group_openids || []).filter(group => String(group.bot_id || "") === owner);
    const targetById = new Map(groups.map(group => [String(group.openid), group]));
    const targetsByQQ = new Map();
    groups.forEach(group => {
      const qqId = String(group.qq_id || "").trim();
      if (!qqId) return;
      if (!targetsByQQ.has(qqId)) targetsByQQ.set(qqId, []);
      targetsByQQ.get(qqId).push(group);
    });
    const metadata = new Map((Array.isArray(value.targets) ? value.targets : []).map(group => [String(group.openid), group]));
    const used = new Set();
    ["forwarding_groups", "x_forwarding_groups", "bilibili_forwarding_groups", "youtube_forwarding_groups", "steam_forwarding_groups", "qq_forwarding_groups"]
      .forEach(key => (value.config[key] || []).forEach(group => (group.groups || []).forEach(id => used.add(String(id)))));
    importMapping = {};
    used.forEach(id => {
      if (value.origin_bot === owner && targetById.has(id)) {
        importMapping[id] = id;
        return;
      }
      const qqId = String(metadata.get(id)?.qq_id || "").trim();
      const matches = qqId ? targetsByQQ.get(qqId) || [] : [];
      importMapping[id] = matches.length === 1 ? String(matches[0].openid) : "";
    });
    return Object.values(importMapping).filter(target => !target).length;
  }
  function renderSummary(value, unmappedCount) {
    const config = value.config || {};
    const currentUsers = Array.isArray(settings.qq_user_openids) ? settings.qq_user_openids : [];
    const lines = [
      summarizeLine("Discord来源", config.discord_channels, item => item.name || item.id),
      summarizeLine("X来源", config.x_rss_sources, item => item.name || item.source),
      summarizeLine("Bilibili来源", config.bilibili_sources, item => item.name || item.value || item.uid),
      summarizeLine("YouTube来源", config.youtube_sources, item => item.name || item.value),
      summarizeLine("Steam来源", config.steam_sources, item => item.name || item.value),
      summarizeLine("QQ接收群", value.targets, item => item.name || item.qq_id || item.openid) +
        (unmappedCount ? "（未匹配" + unmappedCount + "个，相关转发组将暂停）" : ""),
      summarizeLine("QQ授权用户（当前保留）", currentUsers, item => item.name || item.qq_id || item.openid),
    ];
    $("profileImportReview").textContent = lines.join("\n");
  }
  async function exportProfile() {
    closeMenu();
    const selected = currentQQBotId();
    if (!selected) return showAlert("导出失败", "请先配置并选择 QQ 账号");
    try {
      const value = await jget("/api/bot-profile/export?bot_id=" + encodeURIComponent(selected));
      if (value.format !== "wrelay-bot-export") throw new Error(value.msg || "无法导出配置");
      const content = JSON.stringify(value, null, 2);
      const filename = "WRelay-" + selected.replace(":", "_") + "-" + new Date().toISOString().slice(0, 10) + ".json";
      const api = window.pywebview && window.pywebview.api;
      if (api && api.save_bot_profile) {
        const result = await api.save_bot_profile(filename, content);
        if (!result.ok) throw new Error(result.msg || "保存失败");
        if (!result.cancelled) showAlert("导出成功", "配置已保存到：\n" + result.path);
        return;
      }
      const url = URL.createObjectURL(new Blob([content], {type: "application/json"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (error) { showAlert("导出失败", error.message); }
  }
  function beginImport() {
    closeMenu();
    owner = currentQQBotId();
    if (!owner) return showAlert("上传失败", "请先配置并选择目标 QQ 账号");
    documentValue = null;
    fileInput.value = "";
    fileName.textContent = "未选择文件";
    fileName.title = "";
    importMapping = {};
    invalidate();
    modal.style.display = "none";
    chooseFile();
  }
  $("profileExport").addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    void exportProfile();
  });
  $("profileImport").addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    beginImport();
  });
  fileInput.addEventListener("change", event => processSelectedFile(event.target.files[0] || null));
  async function processSelectedFile(file) {
    if (!file) return;
    if (!owner || currentQQBotId() !== owner) {
      owner = "";
      return showAlert("上传失败", "当前账号已变化，请重新选择目标账号并上传。");
    }
    fileName.textContent = file?.name || "未选择文件";
    fileName.title = file?.name || "";
    invalidate(); documentValue = null; importMapping = {};
    $("profileImportReview").textContent = "正在读取配置文件…";
    modal.style.display = "flex";
    $("qqBotWorkspace").disabled = true;
    const version = generation;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("配置文件不能超过 5 MB");
      const value = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
      if (version !== generation) return;
      if (value.format !== "wrelay-bot-export" || value.version !== 1 || !value.config) throw new Error("不支持的 Bot 配置格式或版本");
      documentValue = value;
      const unmappedCount = buildGroupMapping(value);
      renderSummary(value, unmappedCount);
      await previewImport(version);
    } catch (error) { if (version === generation) $("profileImportReview").textContent = error.message; }
  }
  async function previewImport(version) {
    if (!documentValue) return;
    version = version === undefined ? generation : version;
    try {
      const response = await jpost(endpoint("import"), body());
      if (version !== generation) return;
      if (!response.ok) throw new Error(response.msg || "预览失败");
      preview = response;
      $("profileImportSave").disabled = false;
    } catch (error) { if (version === generation) $("profileImportReview").textContent += "\n\n校验失败：" + error.message; }
  }
  $("profileImportSave").addEventListener("click", async () => {
    if (!preview || busy) return;
    try {
      const request = {...body(), confirm: true, revision: preview.revision, preview_hash: preview.preview_hash};
      busy = true;
      fileInput.disabled = true;
      modal.querySelectorAll("input, select, button").forEach(node => { node.disabled = true; });
      const response = await jpost(endpoint("import"), request);
      if (!response.ok) throw new Error(response.msg || "导入失败");
      settings = await jget("/api/settings");
      if (settings.ok === false) throw new Error(settings.msg || "导入已保存，请刷新页面");
      renderSettings(); busy = false; close();
    } catch (error) {
      invalidate(); $("profileImportReview").textContent = error.message;
    } finally {
      busy = false;
      fileInput.disabled = false;
      modal.querySelectorAll("input, select, button").forEach(node => { node.disabled = false; });
      $("profileImportSave").disabled = !preview;
    }
  });
  modal.addEventListener("click", event => { if (event.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && modal.style.display !== "none") { event.preventDefault(); close(); }
  });
})();
