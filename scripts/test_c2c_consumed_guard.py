"""验证已被 QQ 私聊 relay 消费的事件不再按管理命令回复。

`plugins/command.py` 消费私聊消息后会调用 `stop_propagation()`；该信号依赖
NoneBot 的异常处理链，在部分运行环境（Nuitka 编译产物）中会丢失。本测试覆盖
`plugins/c2c_consumed.py` 提供的兜底标记：已消费的事件不再让 `plugins/manage.py`
回复「未知命令」，而未消费的事件仍按原行为回复。
"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import manage
from plugins.c2c_consumed import clear_consumed, mark_consumed


class FakeQQBot:
    def __init__(self):
        self.messages = []

    async def send(self, event, message):
        self.messages.append((event, message))


class FakeC2CEvent:
    def __init__(self, text, event_id):
        self.id = event_id
        self.text = text
        self.author = type("Author", (), {"user_openid": "user-openid"})()

    def get_plaintext(self):
        return self.text


async def main():
    bot = FakeQQBot()
    clear_consumed()
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(
            manage, "get_active_user_openids", return_value=["user-openid"]
        ),
    ):
        plain = FakeC2CEvent("看起来像未知命令", "evt-plain")
        await manage.handle(bot, plain)
        assert bot.messages == [(plain, manage.UNKNOWN_COMMAND_REPLY)], bot.messages

        bot.messages.clear()
        consumed = FakeC2CEvent("看起来像未知命令", "evt-consumed")
        mark_consumed(consumed)
        await manage.handle(bot, consumed)
        assert not bot.messages, "已被 relay 消费的事件不应再回复未知命令"

        bot.messages.clear()
        other = FakeC2CEvent("看起来像未知命令", "evt-other")
        await manage.handle(bot, other)
        assert bot.messages == [(other, manage.UNKNOWN_COMMAND_REPLY)], (
            "只跳过已消费的那一条，其他事件行为不变"
        )

    clear_consumed()
    print("PASS - 已消费事件的私聊命令跳过与未知命令回复")


if __name__ == "__main__":
    asyncio.run(main())
