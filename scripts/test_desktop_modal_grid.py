"""校验「卡片二级窗口」的三条长期规则是否仍然成立。

规则（见 docs/DESIGN.md「二级窗口统一规则」）：
  1) 正文网格式对齐：卡片二级窗口的正文由 modalShell 加 .modal-grid，工具栏用 .modal-split、
     跨行用 .modal-span-all；
  2) 底栏左右分区：.modal-footer 第一个按钮贴左下、最后一个贴右下（主要动作在右下）；
  3) 开关一律胶囊式：窗口内与卡片上的开/关按钮统一为 .pill-switch（文字 + .settings-switch-track 轨道），
     渲染函数只写标签与轨道，不再画 ✓/× 图标。
用法：.venv/Scripts/python.exe scripts/test_desktop_modal_grid.py
"""
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "panel" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
UI_JS = (WEB / "ui-components.js").read_text(encoding="utf-8")
UI_CSS = (WEB / "ui-components.css").read_text(encoding="utf-8")
APP_JS = (WEB / "app.js").read_text(encoding="utf-8")
DISCORD_JS = (WEB / "discord-page.js").read_text(encoding="utf-8")
STYLE_CSS = (WEB / "style.css").read_text(encoding="utf-8")

CARD_MODALS = [
    "scheduledMessageContentModal", "relayChannelsModal", "translationChainsModal",
    "backupRestoreModal", "runtimeLogModal", "translationSourceModal",
]
PILL_BUTTONS = [
    "btnTranslationDiscord", "btnTranslationX", "btnTranslationBilibili", "btnTranslationManual",
    "btnAutoBackupToggle", "btnLogAutoScroll", "btnBackfillToggle",
    "btnScheduledMessageToggle", "btnBilibiliAutoResolveToggle",
    "translationSourceEnabled", "translationSourceAppendOriginal", "translationSourceShowDatetime",
]

failures = []


def check(name, ok):
    print(("OK   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


specs = re.search(r"const specs = \{(.*?)\n  \};", UI_JS, re.S)
check("ui-components.js 含 specs 表", bool(specs))
spec_block = specs.group(1) if specs else ""
for modal in CARD_MODALS:
    line = re.search(r"^\s*" + modal + r":\s*\{([^}]*)\}", spec_block, re.M)
    check(modal + " 声明 grid: true", bool(line) and "grid: true" in line.group(1))
check("modalShell 按 spec 给正文加 .modal-grid",
      'spec.grid ? "modal-body modal-grid" : "modal-body"' in UI_JS)
check("共享规则含 .modal-grid 单列网格",
      ".modal-body.modal-grid" in UI_CSS and "grid-template-columns: minmax(0, 1fr)" in UI_CSS)
check("共享规则含 .modal-split 等宽两列", ".modal-split { display: grid" in UI_CSS)
check("共享规则含 .modal-span-all 跨行", ".modal-span-all { grid-column: 1 / -1; }" in UI_CSS)
check("底栏首按钮贴左下", re.search(r"\.modal-footer > button:first-child:not\(:only-child\) \{ margin-right: auto;", UI_CSS) is not None)
check("底栏末按钮（主要动作）贴右下", re.search(r"\.modal-footer > button:last-child \{ margin-left: auto;", UI_CSS) is not None)
check("胶囊开关共享样式存在", ".pill-switch > .settings-switch-track" in UI_CSS)
check("胶囊外围无描边与常态底色",
      re.search(r"border: 0; border-radius: 999px; background: transparent;", UI_CSS) is not None)
check("胶囊开启态不再描边外围",
      'button.pill-switch[data-on="1"] { border-color' not in UI_CSS)
check("胶囊悬停只显示浅色圆角底",
      "button.pill-switch:not(:disabled):hover { background: var(--sec-bg); }" in UI_CSS)
check("胶囊键盘焦点框缩到轨道",
      "body .pill-switch:focus-visible { outline: none; }" in UI_CSS
      and ".pill-switch:focus-visible .settings-switch-track" in UI_CSS)
check("胶囊轨道沿用「启用补发」32×18",
      re.search(r"\.pill-switch > \.settings-switch-track \{ flex: 0 0 32px; width: 32px; height: 18px", UI_CSS) is not None)
card_pill = re.search(
    r":is\(#btnBackfillToggle, #btnScheduledMessageToggle, #btnBilibiliAutoResolveToggle\) \{\s*"
    r"flex: 0 0 auto; width: auto; min-width: 0; max-width: 100%;\s*justify-content: flex-start;",
    STYLE_CSS,
)
check("卡片胶囊为内容宽度且文字贴近轨道", card_pill is not None)
pill_placement = re.search(
    r":is\(\.scheduled-message-actions, \.relay-link-actions, \.auto-translation-actions, "
    r"#backfillCard #backfillContent > \.row\) > button\.pill-switch:not\(:first-child\) \{\s*"
    r"margin-left: auto;",
    STYLE_CSS,
)
check("卡片胶囊：左侧有按钮时贴右下", pill_placement is not None)
pill_before_right_button = re.search(
    r":is\(\.scheduled-message-actions, \.relay-link-actions, \.auto-translation-actions, "
    r"#backfillCard #backfillContent > \.row\) > button\.pill-switch:not\(:first-child\) ~ \* \{\s*"
    r"margin-left: 0;",
    STYLE_CSS,
)
check("卡片胶囊：右下角有按钮时紧贴其左侧", pill_before_right_button is not None)
scheduled_actions = re.search(
    r'<div class="row scheduled-message-actions">(.*?)</div>', HTML, re.S
)
check("定时消息动作栏：编辑按钮在前、胶囊在后",
      bool(scheduled_actions)
      and scheduled_actions.group(1).find("btnScheduledMessageEdit")
      < scheduled_actions.group(1).find("btnScheduledMessageToggle"))
check("Discord 离线补发进入小卡片网格",
      'group(byId("pageDiscord"), "relay-translation-row func-cards", [])' in DISCORD_JS
      and 'move(byId("backfillCard"), lowerCards)' in DISCORD_JS)
check("离线补发不再使用旧横向分组",
      "desktop-backfill-parameters" not in STYLE_CSS
      and "desktop-backfill-parameters" not in DISCORD_JS
      and "desktop-backfill-actions" not in STYLE_CSS
      and "desktop-backfill-actions" not in DISCORD_JS)
discord_fc_grid = re.search(
    r'body\.desktop-host:is\(\[data-active-page="discord"\], \[data-active-page="bilibili"\], '
    r'\[data-active-page="qq"\]\)\s*:is\(#pageDiscord, #pageBilibili, #pageQQ\) > \.func-cards \{\s*'
    r'display: grid;\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);',
    STYLE_CSS,
)
check("小卡片两列网格覆盖 Discord", discord_fc_grid is not None)
backfill_actions = re.search(
    r'<div class="row backfill-actions">(.*?)</div>', HTML, re.S
)
check("离线补发动作栏：胶囊在前、立即补发在后",
      bool(backfill_actions)
      and backfill_actions.group(1).find("btnBackfillToggle")
      < backfill_actions.group(1).find("btnBackfillRun"))
settings_toggle = re.search(
    r"#pageSettings :is\(\.settings-switch, #btnSettingsAutostart\) \{\s*"
    r"flex: 0 0 32px; width: 32px; min-width: 32px; height: 18px; min-height: 18px;\s*"
    r"padding: 0; margin: 0; border: 0; background: transparent;",
    STYLE_CSS,
)
check("设置页开关外围无描边与底色", settings_toggle is not None)
check("设置页开关只保留 32×18 轨道",
      "#pageSettings :is(.settings-switch, #btnSettingsAutostart) .settings-switch-track { width: 32px; height: 18px; }" in STYLE_CSS)
check("设置页开关焦点框缩到轨道",
      "body #pageSettings :is(.settings-switch, #btnSettingsAutostart):focus-visible { outline: none; }" in UI_CSS
      and "#pageSettings :is(.settings-switch, #btnSettingsAutostart):focus-visible .settings-switch-track" in UI_CSS)
check("设置页开关悬停不再绘制外围底",
      "#pageSettings .settings-switch:not(:disabled):is(:hover, :focus-visible)" not in UI_CSS)
check("app.js 提供 pillSwitchMarkup", "function pillSwitchMarkup(" in APP_JS)
for fn in ["renderTranslationToggleFor", "renderTranslationSettingButton", "renderBilibiliAutoResolveToggle",
           "renderScheduledMessageToggle", "renderBackfillToggle"]:
    body = re.search(r"function " + fn + r"\([^)]*\) \{(.*?)\n\}", APP_JS, re.S)
    check(fn + " 改用胶囊标记", bool(body) and "pillSwitchMarkup(" in body.group(1)
          and "<svg" not in body.group(1))
for button in PILL_BUTTONS:
    tag = re.search(r"<button[^>]*id=\"" + button + r"\"[^>]*>", HTML)
    check(button + " 带 pill-switch 与 role=switch",
          bool(tag) and "pill-switch" in tag.group(0) and 'role="switch"' in tag.group(0))
check("定时消息条目改为两列网格起排",
      ".scheduled-message-entry-body" in STYLE_CSS and ".scheduled-message-target-row" in STYLE_CSS)
check("定时消息条目 DOM 顺序：正文 → 附件 → 发送目标 → 时间 → 说明",
      APP_JS.find("scheduled-message-image-row") < APP_JS.find("scheduled-message-target-row")
      < APP_JS.find("scheduled-message-timing-row") < APP_JS.find("scheduled-message-image-hint"))
check("已注册频道 tab 收进窗口头部工具区",
      'relayChannelsModal: {size: "medium", footer: "relayChannelsSave", grid: true, tools: ["relayScopeTabs"]' in spec_block)

print("PASS - 卡片二级窗口统一规则（网格对齐 / 底栏左右分区 / 胶囊开关）" if not failures
      else "FAIL - " + "; ".join(failures))
sys.exit(1 if failures else 0)
