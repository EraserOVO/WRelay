"""QQ 消息按钮（keyboard）构造与发送。

QQ 官方要求按钮只能挂在 markdown 消息上（发送时 msg_type=2），这里统一
封装按钮结构与发送回退，避免各命令模块各自拼 JSON。

两种按钮模式，由配置项 QQ_BUTTON_MODE 切换（默认 enter）：

- enter（action.type=2 且 action.enter=true）：点击后客户端把 data 当作一条
  用户消息发出，由现有命令处理器接管并回复。不依赖平台交互回调链路，但聊天
  里会多出一条用户消息，且需要客户端版本 8983+。
- callback（action.type=1）：点击后平台下发 INTERACTION_CREATE，机器人回应后
  直接回复内容，用户账号不产生消息。依赖平台把回调点击推给开发者服务器，实测
  WebSocket 接入下客户端直接提示「请求第三方失败」，事件不会到达机器人；仅在
  接入方式切到 Webhook（需公网 HTTPS）时可用，见 plugins/qq_interaction.py。

按钮不改变任何命令语义与权限路径，执行逻辑与手输命令完全一致。

发送失败（例如客户端或平台拒绝 keyboard）时回退为纯文本，保证命令输出
本身永远可用。
"""

import os

from nonebot import get_driver
from nonebot import logger
from nonebot.adapters.qq import Message
from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.models import Action
from nonebot.adapters.qq.models import Button
from nonebot.adapters.qq.models import InlineKeyboard
from nonebot.adapters.qq.models import InlineKeyboardRow
from nonebot.adapters.qq.models import MessageKeyboard
from nonebot.adapters.qq.models import Permission
from nonebot.adapters.qq.models import RenderData


# 平台限制：最多 5 行，每行最多 5 个按钮
MAX_ROWS = 5
MAX_BUTTONS_PER_ROW = 5
MAX_BUTTONS = MAX_ROWS * MAX_BUTTONS_PER_ROW

# 0 灰色线框，1 蓝色线框
STYLE_BLUE = 1

# relay 群按钮每行个数：群名较长，3 个一行更易点选
RELAY_BUTTONS_PER_ROW = 3
RELAY_PAGE_SIZE = 9

UNSUPPORTED_TIP = "请升级 QQ 客户端后使用按钮"

# 按钮模式：enter = 指令按钮 + 自动发送；callback = 回调按钮（机器人直接回复）
BUTTON_MODE_ENTER = "enter"
BUTTON_MODE_CALLBACK = "callback"
BUTTON_MODES = (BUTTON_MODE_ENTER, BUTTON_MODE_CALLBACK)


def button_mode():
    """读取按钮模式配置，非法值回退为 enter。"""
    value = ""
    try:
        value = str(getattr(get_driver().config, "qq_button_mode", "") or "")
    except Exception:
        value = ""
    if not value:
        value = os.getenv("QQ_BUTTON_MODE", "")
    mode = str(value or "").strip().lower()
    return mode if mode in BUTTON_MODES else BUTTON_MODE_ENTER

# markdown 转义字符：命令输出里的群名、昵称可能带这些符号，
# 不转义会被渲染成强调/链接/标题，导致显示错乱。
_MARKDOWN_SPECIALS = frozenset("\\*_~`[]#>")


def escape_markdown(text):
    """把普通文本转义为 markdown 字面量（保留换行）。"""
    out = []
    for ch in str(text or ""):
        if ch in _MARKDOWN_SPECIALS:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def truncate_label(text, limit=12):
    """按钮文字过长会被截断，保留序号等前缀信息。"""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def command_button(
    button_id, label, command, visited=None, style=STYLE_BLUE, mode=None,
    selected=False,
):
    """构造一个「点击即触发」的按钮。

    - enter 模式：action.type=2 的指令按钮配合 enter=True，点击后客户端自动把
      data 作为用户消息发出，等价于用户手输该命令并发送。
    - callback 模式：action.type=1 的回调按钮，点击后平台下发 INTERACTION_CREATE，
      由 plugins/qq_interaction.py 回应并执行同一条命令。
    - selected：切换型按钮的「已选」态，label/visited_label 加【】标记，使同一张
      选择卡上可以直观看出哪些目标已被勾选（平台按钮不支持真正的按下状态）。
    """
    if selected:
        label = "【{}】".format(label)
        visited = "【{}】".format(visited or str(label).strip("【】"))
    mode = mode or button_mode()
    if mode == BUTTON_MODE_CALLBACK:
        action = Action(
            type=1,
            permission=Permission(type=2),
            data=command,
            unsupport_tips=UNSUPPORTED_TIP,
        )
    else:
        action = Action(
            type=2,
            permission=Permission(type=2),
            data=command,
            enter=True,
            unsupport_tips=UNSUPPORTED_TIP,
        )
    return Button(
        id=str(button_id),
        render_data=RenderData(
            label=label,
            visited_label=visited or label,
            style=style,
        ),
        action=action,
    )


def command_rows(buttons, per_row=3):
    """按每行 per_row 个切分按钮，受平台 5x5 上限约束。"""
    rows = []
    for start in range(0, min(len(buttons), MAX_BUTTONS), per_row):
        chunk = buttons[start:start + per_row]
        if chunk:
            rows.append(InlineKeyboardRow(buttons=chunk))
    return rows[:MAX_ROWS]


def relay_button_capacity(include_all=False):
    """一屏按钮最多能显示多少个群。

    command_rows 每行放 3 个，最多 5 行；「全部群」按钮也占一个名额。
    超出的群不渲染按钮，由正文提示手动输入序号。
    """
    capacity = MAX_ROWS * RELAY_BUTTONS_PER_ROW
    return capacity - 1 if include_all else capacity


def relay_group_rows(
    names, prefix="/转发", include_all=False, start_index=1, max_items=None
):
    """把群名列表渲染成「/转发 {序号}」按钮。

    names 顺序即「/转发 列表」的序号顺序；超出行数上限的部分不渲染，
    调用方应在 markdown 正文里提示剩余群需手动输入序号。
    include_all 为真时追加一个「全部群」按钮（仅私聊 /转发 全部支持）。
    """
    labels = [truncate_label(str(name or "").strip() or "未命名群") for name in names]
    capacity = relay_button_capacity(include_all)
    if max_items is not None:
        capacity = min(capacity, max(0, int(max_items)))
    buttons = [
        command_button(
            "g%d" % (start_index + offset),
            "%d. %s" % (start_index + offset, label),
            "%s %d" % (prefix, start_index + offset),
        )
        for offset, label in enumerate(labels[:capacity])
    ]
    if include_all and max_items is None and len(labels) > 1:
        buttons.append(command_button("gall", "全部群", "%s 全部" % prefix))
    return command_rows(buttons, per_row=RELAY_BUTTONS_PER_ROW)


def relay_group_page_rows(
    page, total_pages, list_command, include_all=False, back_command="/菜单"
):
    """构造转发目标页导航；目标按钮已单独占用最多三行。"""
    page = max(1, int(page))
    total_pages = max(1, int(total_pages))
    buttons = []
    if page > 1:
        buttons.append(command_button(
            "relay-prev", "上一页", "%s %d" % (list_command, page - 1)
        ))
    if include_all:
        buttons.append(command_button("relay-all", "全部群", "/转发 全部"))
    if page < total_pages:
        buttons.append(command_button(
            "relay-next", "下一页", "%s %d" % (list_command, page + 1)
        ))
    if back_command:
        buttons.append(command_button("relay-back", "返回菜单", back_command))
    return command_rows(buttons, per_row=3)


def relay_target_toggle_rows(entries, limit=None, prefix="/转发 选"):
    """私聊转发选择卡：每个群一个可反复点击的「已选/未选」切换按钮。

    entries: [(序号, 标签, 是否已选)]；点击按钮发送「/转发 选 {序号}」，
    由 command.py 翻转该群的选中状态并重发选择卡，用户不需要先选群再发内容。
    """
    capacity = MAX_BUTTONS if limit is None else min(MAX_BUTTONS, max(0, int(limit)))
    buttons = [
        command_button(
            "pick%d" % index,
            truncate_label(str(label or "").strip() or "未命名群"),
            "%s %d" % (prefix, index),
            selected=bool(selected),
        )
        for index, label, selected in list(entries)[:capacity]
    ]
    return command_rows(buttons, per_row=RELAY_BUTTONS_PER_ROW)


def relay_picker_rows(
    entries, selected_count, limit=None, prefix="/转发 选", back_command="/菜单",
    include_all=True,
):
    """选择卡的目标按钮 + 操作按钮（全部群 / 清空 / 开始转发 / 返回菜单）。

    include_all=False 用于群聊：目标范围限定在同转发组内，不提供「全部群」。
    """
    capacity = MAX_BUTTONS if limit is None else min(MAX_BUTTONS, max(0, int(limit)))
    # 目标按钮最多占 4 行，最后一行留给操作按钮
    target_rows = relay_target_toggle_rows(entries, limit=min(capacity, RELAY_BUTTONS_PER_ROW * 4), prefix=prefix)
    actions = []
    if include_all:
        actions.append(command_button("pick-all", "全部群", "/转发 全部"))
    actions.append(command_button("pick-clear", "清空", "/转发 清空"))
    if selected_count:
        actions.append(command_button("pick-go", "开始转发(%d)" % selected_count, "/转发 开始"))
    else:
        actions.append(command_button("pick-go", "开始转发", "/转发 开始"))
    if back_command:
        actions.append(command_button("pick-back", "返回菜单", back_command))
    rows = list(target_rows) + command_rows(actions, per_row=min(len(actions), MAX_BUTTONS_PER_ROW))
    return rows[:MAX_ROWS]


def markdown_message(content, rows):
    """组装 markdown + keyboard 消息；没有按钮时返回 None。"""
    rows = list(rows or [])
    if not rows:
        return None
    return Message([
        MessageSegment.markdown(str(content)),
        MessageSegment.keyboard(MessageKeyboard(content=InlineKeyboard(rows=rows))),
    ])


async def send_with_buttons(bot, event, content, rows, fallback=None):
    """发送带按钮的消息，失败时回退为纯文本。

    content 应是已转义的 markdown 文本；fallback 是未转义的纯文本。
    返回是否成功发送了按钮消息。
    """
    plain = fallback if fallback is not None else content
    message = markdown_message(content, rows)
    if message is None:
        await bot.send(event, plain)
        return False
    try:
        await bot.send(event, message)
        return True
    except Exception:
        logger.exception("发送 QQ 按钮消息失败，回退为纯文本")
        await bot.send(event, plain)
        return False


def titled_markdown(title, content):
    """把回复正文整理为一级标题 + 正文，兼容旧的【标题】首行。"""
    text = str(content or "").strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        return text
    if lines and lines[0].startswith("【") and lines[0].endswith("】"):
        title = lines[0][1:-1].strip() or title
        lines = lines[1:]
    elif lines and lines[0].strip() == str(title).strip():
        lines = lines[1:]
    body = escape_markdown("\n".join(lines).strip())
    heading = escape_markdown(title)
    return "# " + heading + ("\n" + body if body else "")


async def send_titled_reply(bot, event, title, content, rows=None):
    """用大标题发送 QQ 回复；按钮失败时仍回退为带标题的 Markdown。"""
    markdown = titled_markdown(title, content)
    rows = list(rows or [])
    message = markdown_message(markdown, rows)
    try:
        if message is None:
            await bot.send(event, Message([MessageSegment.markdown(markdown)]))
        else:
            await bot.send(event, message)
        return bool(rows)
    except Exception:
        logger.exception("发送带标题的 QQ 回复失败，回退为纯文本")
        if rows:
            try:
                await bot.send(event, Message([MessageSegment.markdown(markdown)]))
                return False
            except Exception:
                logger.exception("发送带标题的 QQ Markdown 回退消息失败")
        plain = str(content or "").strip()
        if plain.startswith("【") and "】" in plain.splitlines()[0]:
            plain = "\n".join(plain.splitlines()[1:]).strip()
        await bot.send(event, str(title) + ("\n" + plain if plain else ""))
        return False
