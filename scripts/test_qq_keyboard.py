"""QQ 消息按钮（keyboard）构造与回退测试。

覆盖：
1. markdown 转义：命令输出里的群名/昵称不会被渲染成强调或链接
2. 指令按钮结构：action.type=2、data 为命令文本、所有人可点
3. 行数/每行数量受平台 5x5 限制，群按钮超容量时截断
4. markdown + keyboard 消息组装
5. 按钮消息发送失败时回退为纯文本，命令输出不会丢
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nonebot import logger
from nonebot.adapters.qq import Message

from plugins.qq_keyboard import (
    MAX_ROWS,
    RELAY_BUTTONS_PER_ROW,
    button_mode,
    command_button,
    command_rows,
    escape_markdown,
    markdown_message,
    relay_button_capacity,
    relay_group_page_rows,
    relay_group_rows,
    send_with_buttons,
    truncate_label,
)


class FakeBot:
    def __init__(self, fail_buttons=False):
        self.sent = []
        self.fail_buttons = fail_buttons

    async def send(self, event, message):
        if self.fail_buttons and not isinstance(message, str):
            raise RuntimeError("platform rejected keyboard")
        self.sent.append(message)


def main():
    logging.disable(logging.CRITICAL)
    # 回退路径会用 logger.exception 记录堆栈，测试里不需要输出
    logger.remove()

    # ---------- 转义 ----------
    assert escape_markdown("a*b_c~d[e]#f>g\\h") == "a\\*b\\_c\\~d\\[e\\]\\#f\\>g\\\\h"
    # 中文、emoji、换行、数字保持原样
    assert escape_markdown("群 1\n✨测试") == "群 1\n✨测试"
    assert escape_markdown(None) == ""

    # ---------- 截断 ----------
    assert truncate_label("短名字") == "短名字"
    long_label = truncate_label("这是一个非常长的群名称用于测试")
    assert len(long_label) == 13 and long_label.endswith("…"), long_label

    # ---------- 指令按钮结构 ----------
    button = command_button("b1", "运行状态", "/运行状态")
    assert button.id == "b1"
    assert button.render_data.label == "运行状态"
    assert button.render_data.visited_label == "运行状态"
    assert button.render_data.style == 1
    # 2 = 指令按钮 + enter：点击后客户端自动发送 data（不依赖交互回调）
    assert button.action.type == 2
    assert button.action.enter is True
    # 2 = 所有人可操作
    assert button.action.permission.type == 2
    assert button.action.data == "/运行状态"
    assert button.action.unsupport_tips

    # ---------- 回调按钮结构 ----------
    # callback 模式下 type=1，由 plugins/qq_interaction.py 回应并执行同一条命令
    callback = command_button("b2", "运行状态", "/运行状态", mode="callback")
    assert callback.action.type == 1
    assert callback.action.enter is None
    assert callback.action.permission.type == 2
    assert callback.action.data == "/运行状态"
    # 默认模式（未配置时）仍是指令按钮 + 自动发送
    assert command_button("b3", "x", "/运行状态", mode=None).action.type in (1, 2)
    assert button_mode() in ("enter", "callback")

    # ---------- 行数限制 ----------
    rows = command_rows([command_button(str(i), "b%d" % i, "cmd") for i in range(12)], per_row=3)
    assert len(rows) == 4
    assert [len(r.buttons) for r in rows] == [3, 3, 3, 3]
    over = command_rows([command_button(str(i), "b%d" % i, "cmd") for i in range(40)], per_row=5)
    assert len(over) == MAX_ROWS
    assert all(len(r.buttons) <= 5 for r in over)

    # ---------- 群按钮 ----------
    names = ["WuppoRelay测试", "乌姆之家 wuppo", "波波城 wuppo"]
    rows = relay_group_rows(names, prefix="/转发", include_all=True)
    flat = [b for r in rows for b in r.buttons]
    assert [b.action.data for b in flat] == ["/转发 1", "/转发 2", "/转发 3", "/转发 全部"]
    assert flat[0].render_data.label == "1. WuppoRelay测试"
    assert len(rows) <= MAX_ROWS
    assert all(len(r.buttons) <= RELAY_BUTTONS_PER_ROW for r in rows)

    # 群聊不支持「/转发 全部」：不追加全选按钮
    chat_rows = relay_group_rows(names, prefix="/转发", include_all=False)
    assert all(b.action.data != "/转发 全部" for r in chat_rows for b in r.buttons)

    # 超出按钮容量：只渲染前 capacity 个，总按钮数仍不超过 5 行
    capacity = relay_button_capacity(include_all=True)
    many = relay_group_rows(["群%d" % i for i in range(1, 41)], prefix="/转发", include_all=True)
    many_flat = [b for r in many for b in r.buttons]
    assert len(many) <= MAX_ROWS
    assert len(many_flat) == capacity + 1  # capacity 个群 + 全部群
    assert many_flat[-2].action.data == "/转发 %d" % capacity
    assert many_flat[-1].action.data == "/转发 全部"
    # 群聊容量比私聊多 1（没有全选按钮占位）
    assert relay_button_capacity(include_all=False) == capacity + 1

    # 空列表不产生按钮
    assert relay_group_rows([], include_all=True) == []
    # 只有一个群时不追加全选按钮
    single = relay_group_rows(["唯一群"], include_all=True)
    assert [b.action.data for r in single for b in r.buttons] == ["/转发 1"]

    second_page = relay_group_rows(
        ["群10", "群11"], prefix="/转发", start_index=10, max_items=9
    )
    assert [b.action.data for r in second_page for b in r.buttons] == [
        "/转发 10", "/转发 11",
    ]
    navigation = relay_group_page_rows(2, 4, "/转发 列表", back_command="/菜单")
    assert [b.action.data for r in navigation for b in r.buttons] == [
        "/转发 列表 1", "/转发 列表 3", "/菜单",
    ]

    # ---------- 消息组装 ----------
    message = markdown_message("# 标题\n正文", rows)
    assert isinstance(message, Message)
    assert [type(seg).__name__ for seg in message] == ["Markdown", "Keyboard"]
    keyboard = message[1].data["keyboard"]
    assert len(keyboard.content.rows) == len(rows)
    assert markdown_message("# 无按钮", []) is None

    # ---------- 发送与回退 ----------
    async def run():
        ok_bot = FakeBot()
        assert await send_with_buttons(ok_bot, None, "# 标题", rows, fallback="纯文本") is True
        assert len(ok_bot.sent) == 1 and isinstance(ok_bot.sent[0], Message)

        fail_bot = FakeBot(fail_buttons=True)
        assert await send_with_buttons(fail_bot, None, "# 标题", rows, fallback="纯文本") is False
        # 失败后必须补发纯文本，命令输出不能丢
        assert fail_bot.sent == ["纯文本"]

        empty_bot = FakeBot()
        assert await send_with_buttons(empty_bot, None, "# 无按钮", [], fallback="纯文本") is False
        assert empty_bot.sent == ["纯文本"]

    asyncio.run(run())

    print("PASS - QQ 按钮消息（转义、自动发送按钮、行数上限、群按钮、发送回退）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
