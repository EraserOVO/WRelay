/* Personal QQ setup: outbound accounts and explicitly selected receiving groups. */
(function () {
  const modal = document.getElementById("personalQQModal");
  const rows = document.getElementById("personalQQRows");
  let revision = "", draft = [], statuses = [];
  const esc = escapeHtml;

  function close() { modal.style.display = "none"; rows.innerHTML = ""; draft = []; }
  function collect() {
    draft = [...rows.querySelectorAll(".personal-qq-row")].map(row => ({
      name: row.querySelector("[name=name]").value,
      uin: row.querySelector("[name=uin]").value.trim(),
      implementation: row.querySelector("[name=implementation]").value,
      token: row.querySelector("[name=token]").value,
      enabled: row.querySelector("[name=enabled]").getAttribute("aria-checked") === "true",
    }));
  }
  function render() {
    rows.innerHTML = draft.map((item, index) => {
      const state = statuses.find(status => status.uin === item.uin);
      const status = state ? state.message + (state.version ? " · " + state.version : "") : "未连接";
      return '<section class="personal-qq-row" data-index="' + index + '">' +
        '<label class="form-field">名称<input name="name" maxlength="80" value="' + esc(item.name || "") + '"></label>' +
        '<label class="form-field">QQ 号<input name="uin" inputmode="numeric" value="' + esc(item.uin || "") + '"></label>' +
        '<label class="form-field">协议端<select name="implementation"><option value="napcat"' + (item.implementation === "napcat" ? " selected" : "") + '>NapCat</option><option value="snowluma"' + (item.implementation === "snowluma" ? " selected" : "") + '>SnowLuma</option></select></label>' +
        '<label class="form-field">访问令牌<input name="token" type="password" autocomplete="new-password" placeholder="留空保留已保存令牌" value="' + esc(item.token || "") + '"></label>' +
        '<div class="personal-qq-state">' + esc(status) + '</div>' +
        '<div class="personal-qq-actions"><button name="enabled" type="button" class="pill-switch" role="switch" aria-checked="' + (item.enabled !== false) + '" data-on="' + (item.enabled !== false ? "1" : "0") + '">' + pillSwitchMarkup("启用连接", item.enabled !== false) + '</button>' +
        '<button type="button" class="sec" data-personal-groups="' + index + '">接收群</button>' +
        '<button type="button" class="danger" data-personal-remove="' + index + '">删除</button></div></section>';
    }).join("");
  }
  async function refreshAccounts() {
    const response = await jget("/api/personal-qq/config");
    if (!response.ok) throw new Error(response.msg || "个人 QQ 配置读取失败");
    personalQQConnections = response.connections || [];
    qqBotIds = qqBotIds.filter(id => !id.startsWith("personal:"));
    qqBotIds.push(...personalQQConnections.map(item => "personal:" + item.uin));
    return response;
  }
  async function open() {
    try {
      const response = await refreshAccounts();
      revision = response.revision;
      draft = response.connections.map(item => ({...item, token: ""}));
      statuses = response.status || [];
      document.getElementById("personalQQAddress").value = response.reverse_url;
      render();
      renderUncertain(response.uncertain || []);
      modal.style.display = "flex";
    } catch (error) { showAlert("个人 QQ", error.message); }
  }
  function renderUncertain(records) {
    const list = document.getElementById("personalQQUncertain");
    list.innerHTML = records.map(record => '<div class="personal-qq-uncertain"><span>' +
      esc(record.group + " · 来源 " + record.channel_id + " · 消息 " + record.message_id) +
      '</span><button type="button" class="sec" data-personal-key="' + esc(record.key) + '" data-personal-resolve="delivered">确认已送达</button>' +
      '<button type="button" class="sec" data-personal-key="' + esc(record.key) + '" data-personal-resolve="retry">允许重试</button></div>').join("");
    document.getElementById("personalQQUncertainSection").hidden = !records.length;
  }
  async function save() {
    collect();
    const button = document.getElementById("personalQQSave");
    button.disabled = true;
    try {
      const result = await jpost("/api/personal-qq/config", {connections: draft, revision});
      if (!result.ok) throw new Error(result.msg || "保存失败");
      await refreshAccounts();
      renderSettings();
      await refreshStatus();
      close();
    } catch (error) { showAlert("个人 QQ", error.message); }
    finally { button.disabled = false; }
  }
  async function selectGroups(index) {
    collect();
    const account = draft[index];
    const saved = personalQQConnections.find(item => item.uin === account.uin && item.enabled);
    if (!saved) { showAlert("接收群", "请先保存并启用连接，再由协议端连接 WRelay。"); return; }
    try {
      const result = await jget("/api/personal-qq/groups?uin=" + encodeURIComponent(account.uin));
      if (!result.ok) throw new Error(result.msg || "读取群列表失败");
      showConfirmSelect("添加个人 QQ 接收群", result.groups || [],
        group => group.group_name + " · " + group.group_id,
        async picked => {
          if (!settings) return;
          const conflicting = picked.filter(group => settings.qq_group_openids.some(item =>
            item.openid === "personal:" + group.group_id && item.bot_id !== "personal:" + account.uin));
          if (conflicting.length) {
            showAlert("接收群已绑定其他账号", "请先从已注册群列表删除原绑定，再添加这些群：" + conflicting.map(group => group.group_id).join("、"));
            return;
          }
          picked.forEach(group => {
            const openid = "personal:" + group.group_id;
            const existing = settings.qq_group_openids.find(item => item.openid === openid);
            if (!existing) settings.qq_group_openids.push({openid, name: group.group_name,
              qq_id: group.group_id, bot_id: "personal:" + account.uin, enabled: true, remark: ""});
          });
          await saveAndReload();
        }, {defaultChecked: false, okText: "添加接收群"});
    } catch (error) { showAlert("接收群", error.message); }
  }
  document.getElementById("btnPersonalQQ").addEventListener("click", open);
  document.getElementById("personalQQSave").addEventListener("click", save);
  document.getElementById("personalQQAdd").addEventListener("click", () => {
    collect(); draft.push({uin: "", name: "", implementation: "napcat", enabled: true, token: ""}); render();
  });
  modal.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.name === "enabled") {
      const on = button.getAttribute("aria-checked") !== "true";
      button.setAttribute("aria-checked", String(on)); button.dataset.on = on ? "1" : "0";
    } else if (button.dataset.personalRemove !== undefined) {
      collect();
      const index = Number(button.dataset.personalRemove);
      showConfirmDlg("删除个人 QQ 连接", "从当前编辑列表删除这个连接？保存后生效。", () => {
        draft.splice(index, 1); render();
      }, "删除");
    } else if (button.dataset.personalGroups !== undefined) {
      selectGroups(Number(button.dataset.personalGroups));
    } else if (button.dataset.personalResolve) {
      const action = button.dataset.personalResolve;
      showConfirmDlg("处理待确认投递", action === "retry" ? "允许再次发送这部分内容？若群内已收到，重试可能造成重复。" : "确认群内已经收到这部分内容？确认后将跳过重发。", async () => {
        const result = await jpost("/api/personal-qq/resolve/" + button.dataset.personalKey + "/" + action, {});
        if (!result.ok) showAlert("处理失败", result.msg || "请刷新后重试");
        else renderUncertain((await jget("/api/personal-qq/config")).uncertain || []);
      }, "确定");
    }
  });
  window.WRelayPersonalQQ = {close, refreshAccounts};
})();
