"""验证 QQ 私聊 relay 与 manage.py 的事件消费边界。

授权用户的普通文字会被缓存为最近待转发内容，但不消费事件，因此 `plugins/manage.py`
仍会回复「未知命令」；含链接的内容同样缓存，但会消费事件，不再回复提示。
relay 命令（以及显式标记为已消费的事件）不再按管理命令处理，避免同一次操作
收到重复回复；`stop_propagation` 失效的运行环境（Nuitka 编译产物）由
`plugins/c2c_consumed.py` 的标记兜底。
"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import command, manage
from plugins.c2c_consumed import clear_consumed, is_consumed, mark_consumed


class FakeQQBot:
    def __init__(self):
        self.messages = []

    async def send(self, event, message):
        self.messages.append((event, message))


class FakeQQMessage:
    def __getitem__(self, key):
        if key == "emoji":
            return []
        raise KeyError(key)


class FakeC2CEvent:
    attachments = []

    def __init__(self, text, event_id):
        self.id = event_id
        self.text = text
        self.author = type("Author", (), {"user_openid": "user-openid"})()

    def get_plaintext(self):
        return self.text

    def get_message(self):
        return FakeQQMessage()


async def main():
    bot = FakeQQBot()
    clear_consumed()

    # 授权用户的普通内容：缓存为待转发内容，但不消费，manage.py 仍回复未知命令。
    ordinary = FakeC2CEvent("普通文字", "evt-content")
    with (
        mock.patch.object(command, "QQBot", FakeQQBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "is_user_authorized", return_value=True),
        mock.patch.object(command, "remember_c2c_content") as remember,
    ):
        await command.handle(bot, ordinary)
    remember.assert_called_once()
    assert not is_consumed(ordinary)

    # 授权用户的链接：缓存为待转发内容，并消费事件以抑制「未知命令」提示。
    link = FakeC2CEvent(
        "https://discord.com/channels/1/2/3", "evt-link"
    )
    with (
        mock.patch.object(command, "QQBot", FakeQQBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "is_user_authorized", return_value=True),
        mock.patch.object(command, "remember_c2c_content") as remember_link,
    ):
        await command.handle(bot, link)
    remember_link.assert_called_once()
    assert is_consumed(link)

    # 普通斜杠命令交给 manage.py，不由 relay 消费。
    menu = FakeC2CEvent("/菜单", "evt-menu")
    with (
        mock.patch.object(command, "QQBot", FakeQQBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "is_user_authorized", return_value=True),
    ):
        await command.handle(bot, menu)
    assert not is_consumed(menu)

    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(
            manage, "get_active_user_openids", return_value=["user-openid"]
        ),
        mock.patch.object(
            manage, "send_titled_reply", new=mock.AsyncMock()
        ) as send_titled_reply,
    ):
        # 授权用户的普通内容仍触发「未知命令」提示。
        await manage.handle(bot, ordinary)
        send_titled_reply.assert_awaited_once_with(
            bot,
            ordinary,
            "WRelay菜单",
            manage.PRIVATE_UNKNOWN_COMMAND_REPLY,
            manage._unknown_command_rows(),
        )
        assert not bot.messages

        # 链接内容已由 relay 消费，不再回复未知命令。
        send_titled_reply.reset_mock()
        await manage.handle(bot, link)
        send_titled_reply.assert_not_awaited()

        # 已被 relay 消费的事件不再回复未知命令。
        send_titled_reply.reset_mock()
        consumed = FakeC2CEvent("看起来像未知命令", "evt-consumed")
        mark_consumed(consumed)
        await manage.handle(bot, consumed)
        assert not bot.messages, "已被 relay 消费的事件不应再回复未知命令"
        send_titled_reply.assert_not_awaited()

        # 其他未消费事件行为不变。
        other = FakeC2CEvent("看起来像未知命令", "evt-other")
        await manage.handle(bot, other)
        send_titled_reply.assert_awaited_once_with(
            bot,
            other,
            "WRelay菜单",
            manage.PRIVATE_UNKNOWN_COMMAND_REPLY,
            manage._unknown_command_rows(),
        )
        assert not bot.messages, (
            "只跳过已消费的那一条，其他事件行为不变"
        )

    clear_consumed()
    print("PASS - 普通内容缓存与已消费事件的私聊消息边界")


if __name__ == "__main__":
    asyncio.run(main())
