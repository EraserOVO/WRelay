"""验证 QQ 群内 @ 机器人的未知命令提示及命令/pending 排除。"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import manage


class FakeSegment:
    def __init__(self, text, segment_type="text"):
        self.type = segment_type
        self.data = {"text": text}


class FakeGroupMessage:
    def __init__(self, plaintext="", segments=None, author_bot=False):
        self.plaintext = plaintext
        self.segments = list(segments or [])
        self.author = type(
            "Author",
            (),
            {"bot": author_bot, "member_openid": "member-openid"},
        )()
        self.group_openid = "group-openid"

    def get_plaintext(self):
        return self.plaintext

    def get_message(self):
        return self.segments

    def is_tome(self):
        return False


class FakeGroupAtMessage(FakeGroupMessage):
    def is_tome(self):
        return True


class FakeQQBot:
    def __init__(self):
        self.messages = []

    async def send(self, event, message):
        self.messages.append((event, message))


async def main():
    bot = FakeQQBot()
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "GroupMessageCreateEvent", FakeGroupMessage),
        mock.patch.object(
            manage,
            "GroupAtMessageCreateEvent",
            FakeGroupAtMessage,
        ),
        mock.patch.object(manage.qq_fwd, "has_pending", return_value=False) as has_pending,
    ):
        unknown_at = FakeGroupAtMessage(
            "你会做什么？",
            [FakeSegment("你会做什么？")],
        )
        await manage.handle_group_at_hint(bot, unknown_at)
        assert bot.messages == [
            (unknown_at, "未知命令，输入 list 查看可用命令")
        ]

        bot.messages.clear()
        await manage.handle_group_at_hint(
            bot,
            FakeGroupMessage("普通群聊", [FakeSegment("普通群聊")]),
        )
        assert not bot.messages, "普通群消息不应触发未知命令提示"

        recognized = (
            FakeGroupAtMessage("list", [FakeSegment("list")]),
            FakeGroupAtMessage("relay list", [FakeSegment("relay list")]),
            FakeGroupAtMessage("relay 1", [FakeSegment("relay 1")]),
            FakeGroupAtMessage(
                "register-group 123 测试群",
                [FakeSegment("register-group 123 测试群")],
            ),
            FakeGroupAtMessage(
                "register 123 测试用户",
                [FakeSegment("register 123 测试用户")],
            ),
        )
        for event in recognized:
            await manage.handle_group_at_hint(bot, event)
        assert not bot.messages, "已有命令应由各自处理器响应"

        legacy_blank = FakeGroupMessage(
            "",
            [FakeSegment("")],
        )
        await manage.handle_group_at_hint(bot, legacy_blank)
        assert bot.messages == [
            (legacy_blank, "未知命令，输入 list 查看可用命令")
        ], "保留旧适配器的空文本 @ 兼容路径"

        bot.messages.clear()
        has_pending.return_value = True
        pending_at = FakeGroupAtMessage(
            "未知内容",
            [FakeSegment("未知内容")],
        )
        await manage.handle_group_at_hint(bot, pending_at)
        assert not bot.messages, "relay pending 应继续消费下一条消息"

        has_pending.return_value = False
        bot_message = FakeGroupAtMessage(
            "未知内容",
            [FakeSegment("未知内容")],
            author_bot=True,
        )
        await manage.handle_group_at_hint(bot, bot_message)
        assert not bot.messages, "机器人自己的消息不应触发提示"

    print("PASS - QQ 群内 @ 未知命令提示与排除条件")


if __name__ == "__main__":
    asyncio.run(main())
