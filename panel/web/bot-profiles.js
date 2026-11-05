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
  let owner = "", documentValue = null, preview = null, generation = 0, busy = false;
  const names = {discord_channels: "Discord 来源", x_rss_sources: "X 来源", bilibili_sources: "Bilibili 来源", youtube_sources: "YouTube 来源", forwarding_groups: "Discord 转发组", x_forwarding_groups: "X 转发组", bilibili_forwarding_groups: "Bilibili 转发组", youtube_forwarding_groups: "YouTube 转发组", qq_forwarding_groups: "QQ 转发组"};
  function invalidate() {
    generation++;
    preview = null;
    $("profileImportSave").disabled = true;
    $("profileImportReview").textContent = "请预览并检查导入结果。";
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
    const mapping = {};
    $("profileImportMapping").querySelectorAll("select").forEach(select => { mapping[select.dataset.source] = select.value; });
    return {document: documentValue, mode: $("profileImportMode").value, mapping};
  }
  $("profileExport").addEventListener("click", async () => {
    closeMenu();
    const selected = currentQQBotId();
    if (!selected) return showAlert("导出失败", "请先配置并选择 QQ 账号");
    try {
      const value = await jget("/api/bot-profile/export?bot_id=" + encodeURIComponent(selected));
      if (value.format !== "wrelay-bot-export") throw new Error(value.msg || "无法导出配置");
      const content = JSON.stringify(value, null, 2);
      const filename = "WRelay-" + selected.replace(":", "_") + "-" + new Date().toISOString().slice(0, 10) + ".json";
      const api = window.pywebview && window.pywebview.api;
      if (api) {
        if (!api.save_bot_profile) throw new Error("桌面程序需要重新启动后才能导出配置");
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
  });
  $("profileImport").addEventListener("click", () => {
    closeMenu();
    owner = currentQQBotId();
    if (!owner) return showAlert("上传失败", "请先配置并选择目标 QQ 账号");
    documentValue = null;
    $("profileImportFile").value = "";
    $("profileImportMode").value = "merge";
    $("profileImportMapping").replaceChildren();
    $("profileImportOwner").textContent = "导入到：" + owner;
    invalidate();
    modal.style.display = "flex";
    $("qqBotWorkspace").disabled = true;
  });
  $("profileImportFile").addEventListener("change", async event => {
    invalidate(); documentValue = null;
    $("profileImportMapping").replaceChildren();
    const version = generation;
    try {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > 5 * 1024 * 1024) throw new Error("配置文件不能超过 5 MB");
      const value = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
      if (version !== generation) return;
      if (value.format !== "wrelay-bot-export" || value.version !== 1 || !value.config) throw new Error("不支持的 Bot 配置格式或版本");
      const targets = (settings.qq_group_openids || []).filter(group => group.bot_id === owner);
      const metadata = new Map((value.targets || []).map(group => [group.openid, group]));
      const used = new Set();
      Object.keys(names).filter(key => key.endsWith("forwarding_groups") || key === "forwarding_groups").forEach(key => {
        (value.config[key] || []).forEach(group => (group.groups || []).forEach(id => used.add(id)));
      });
      used.forEach(id => {
        const source = metadata.get(id);
        const label = document.createElement("label");
        label.className = "form-field";
        label.textContent = (source?.name || id) + " → 接收群";
        const select = document.createElement("select");
        select.dataset.source = id;
        select.add(new Option("暂不对应（相关转发组将暂停）", ""));
        targets.forEach(group => select.add(new Option(group.name || group.openid, group.openid)));
        if (value.origin_bot === owner && targets.some(group => group.openid === id)) select.value = id;
        select.addEventListener("change", invalidate);
        label.append(select); $("profileImportMapping").append(label);
      });
      documentValue = value;
    } catch (error) { $("profileImportReview").textContent = error.message; }
  });
  $("profileImportMode").addEventListener("change", invalidate);
  $("profileImportPreview").addEventListener("click", async () => {
    invalidate(); const version = generation;
    try {
      const response = await jpost(endpoint("import"), body());
      if (version !== generation) return;
      if (!response.ok) throw new Error(response.msg || "预览失败");
      preview = response;
      const replacing = $("profileImportMode").value === "replace";
      $("profileImportReview").textContent = (replacing ? "将替换当前账号的来源、转发组及链路设置。\n" : "将合并到当前账号，已有同标识配置保留。\n") +
        Object.entries(response.counts).map(([key, count]) => names[key] + "：" + (settings[key] || []).length + " → " + count).join("\n") +
        "\n\n" + response.warnings.join("\n");
      $("profileImportSave").disabled = false;
    } catch (error) { if (version === generation) $("profileImportReview").textContent = error.message; }
  });
  $("profileImportSave").addEventListener("click", async () => {
    if (!preview || busy) return;
    try {
      const request = {...body(), confirm: true, revision: preview.revision, preview_hash: preview.preview_hash};
      busy = true;
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
      modal.querySelectorAll("input, select, button").forEach(node => { node.disabled = false; });
      $("profileImportSave").disabled = !preview;
    }
  });
  modal.addEventListener("click", event => { if (event.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && modal.style.display !== "none") { event.preventDefault(); close(); }
  });
})();
