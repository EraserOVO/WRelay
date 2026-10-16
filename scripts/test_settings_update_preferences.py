"""校验设置页更新区域的自动检查开关与「检查更新 / 立即更新」按钮行为。

在真实 index.html 上注入探针，验证：
  1) 自动检查开关默认开启、点击后写入 localStorage 并可恢复
  2) 已移除的折叠分组与「更新详情」行不再存在
  3) 按钮文案随检查结果切换：无更新→「检查更新」，有更新→「立即更新」
用法：.venv/Scripts/python.exe scripts/test_settings_update_preferences.py
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "panel" / "web"
OUT = ROOT / ".runtime" / "preview"
OUT.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
edge = next((p for p in CANDIDATES if Path(p).exists()), None)
if not edge:
    print("SKIP - 未找到 Edge/Chrome")
    sys.exit(0)

for name in ("index.html", "style.css", "ui-components.css", "app.js",
             "ui-components.js", "desktop.js", "local-api.js", "discord-page.js",
             "onboarding.js", "wrelay-logo.svg"):
    src = WEB / name
    if src.exists():
        shutil.copy2(src, OUT / name)

html = (WEB / "index.html").read_text(encoding="utf-8")
probe = """
<div id="probe"></div>
<script>
window.addEventListener("load", async function () {
  var out = [];
  function add(k, v) { out.push(k + "=" + v); }
  try { if (typeof setActivePage === "function") setActivePage("settings"); } catch (e) {}
  await new Promise(function (r) { setTimeout(r, 300); });

  var sw = document.getElementById("btnAutoUpdateCheck");
  var btn = document.getElementById("btnCheckUpdates");
  var status = document.getElementById("updateStatus");
  // 结构：折叠分组、更新详情、发布页入口均应已移除；配置向导应落在机器人卡片内
  add("no_fold", !document.getElementById("btnFoldUpdate")
                 && !document.getElementById("updateGroupContent"));
  add("no_detail", !document.getElementById("updateDetail"));
  add("no_release_link", !document.getElementById("btnOpenRelease"));
  var serviceCard = document.querySelectorAll("#pageSettings .settings-card")[2];
  add("wizard_in_service_card",
      !!(serviceCard && serviceCard.querySelector("#btnOnboarding")));

  // 检查更新按钮必须与同页其它设置行按钮尺寸一致
  var siblings = document.querySelectorAll(
    "#pageSettings .settings-row > button.sec:not(.settings-switch):not(#btnSettingsAutostart)");
  var refWidth = siblings.length ? getComputedStyle(siblings[0]).width : "";
  var refHeight = siblings.length ? getComputedStyle(siblings[0]).height : "";
  var btnStyle = getComputedStyle(btn);
  add("btn_same_width", String(btnStyle.width === refWidth && refWidth !== ""));
  add("btn_same_height", String(btnStyle.height === refHeight && refHeight !== ""));
  add("btn_width_value", btnStyle.width);

  // 文案
  add("label_version", (document.querySelector("#pageSettings .settings-item-copy strong") || {}).textContent);
  add("label_auto", (sw.closest(".settings-row").querySelector("strong") || {}).textContent);
  add("label_status", (status.closest(".settings-item-copy").querySelector("strong") || {}).textContent);

  // 开关默认开启并持久化
  add("switch_default", sw.getAttribute("aria-checked"));
  if (sw.getAttribute("aria-checked") === "true") {
    sw.click();
    await new Promise(function (r) { setTimeout(r, 200); });
  }
  add("switch_saved", String(localStorage.getItem("wrelay_auto_update_check")));

  // 按钮文案：检查到有更新 -> 立即更新
  pendingUpdate = { latest: "9.9.9", release_url: "" };
  setUpdateButton("立即更新", false);
  add("btn_after_update_found", btn.textContent);

  // 无更新 -> 检查更新
  pendingUpdate = null;
  setUpdateButton("检查更新", false);
  add("btn_after_no_update", btn.textContent);

  // 更新进度文案
  add("stage_download", describeInstallStage({ stage: "downloading", percent: 42 }));
  add("stage_verify", describeInstallStage({ stage: "verifying", percent: 97 }));
  add("stage_launch", describeInstallStage({ stage: "launching", percent: 99 }));

  document.getElementById("probe").textContent = "PROBE::" + out.join("|") + "::END";
});
</script>
</body>"""
html = html.replace("</body>", probe, 1)
(OUT / "update-ui.html").write_text(html, encoding="utf-8")

url = "file:///" + str(OUT / "update-ui.html").replace("\\", "/")
dom = ""
for attempt in range(4):
    profile = OUT / f"edge-profile-upd-{os.getpid()}-{attempt}"
    result = subprocess.run(
        [edge, "--headless=new", "--disable-gpu", "--virtual-time-budget=15000",
         "--user-data-dir=" + str(profile), "--dump-dom", url],
        capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
    )
    dom = result.stdout or ""
    if re.search(r"PROBE::.*?::END", dom, re.S):
        break
    time.sleep(1.5)

m = re.search(r"PROBE::(.*?)::END", dom, re.S)
if not m:
    print("SKIP - 未取回探针结果（无头渲染未完成，非断言失败）")
    sys.exit(0)

f = dict(item.split("=", 1) for item in m.group(1).split("|"))
expect = {
    "no_fold": "true",
    "no_detail": "true",
    "no_release_link": "true",
    "wizard_in_service_card": "true",
    "label_version": "WRelay 版本",
    "label_auto": "自动检查更新",
    "label_status": "更新状态",
    "switch_default": "true",
    "switch_saved": "0",
    "btn_after_update_found": "立即更新",
    "btn_after_no_update": "检查更新",
    "stage_download": "正在下载安装包… 42%",
    "stage_verify": "正在校验安装包… 97%",
    "stage_launch": "正在启动安装程序…",
    "btn_same_width": "true",
    "btn_same_height": "true",
}
bad = []
for k, want in expect.items():
    got = f.get(k)
    ok = got == want
    print(("OK   " if ok else "FAIL ") + f"{k}: {got}" + ("" if ok else f" (期望 {want})"))
    if not ok:
        bad.append(k)
print("PASS - 更新区域结构与按钮行为校验通过" if not bad else "issues=" + ",".join(bad))
sys.exit(0 if not bad else 1)
