"""验证群聊/私聊引用直发与最近内容回退。"""
import asyncio
from types import SimpleNamespace
from unittest import mock

import nonebot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

nonebot.init()

from plugins import command, qq_fwd
from plugins.media import make_text


class FakeBot:
    self_id = "bot-id"
    id = "bot-id"
    self_info = SimpleNamespace(username="WRelay")

    def __init__(self):
        self.replies = []

    async def send(self, _event, message):
        self.replies.append(message)


class FakeQQMessage:
    def __getitem__(self, key):
        if key == "emoji":
            return []
        raise KeyError(key)


class FakeC2CEvent:
    attachments = []
    msg_elements = None

    def __init__(self, text, event_id="evt-1", reply=None):
        self.id = event_id
        self.text = text
        self.reply = reply
        self.author = SimpleNamespace(user_openid="user-1")

    def get_plaintext(self):
        return self.text

    def get_message(self):
        return FakeQQMessage()


class FakeGroupEvent:
    group_openid = "source-group"
    author = SimpleNamespace(member_openid="member-1")
    attachments = []
    msg_elements = None
    id = "group-event-1"

    def __init__(self, text="/转发 1", reply=None):
        self.text = text
        self.reply = reply

    def get_plaintext(self):
        return self.text


def _quote(content="带图消息", attachments=None, bot=False, author=True):
    return SimpleNamespace(
        content=content,
        attachments=[
            SimpleNamespace(url=url, content_type=content_type)
            for url, content_type in (attachments or [])
        ],
        author=(
            SimpleNamespace(bot=bot, id="member-2", username="quoted-user")
            if author
            else None
        ),
    )


def _extracted(reply):
    return qq_fwd.extract_quoted_message(
        SimpleNamespace(reply=reply, msg_elements=None)
    )


def _reply_text(message):
    if isinstance(message, str):
        return message
    for segment in message:
        markdown = (getattr(segment, "data", {}) or {}).get("markdown")
        content = getattr(markdown, "content", None)
        if content:
            return content
    return ""


def _groups():
    return [
        {"openid": "group-1", "enabled": True, "name": "目标群1"},
        {"openid": "group-2", "enabled": True, "name": "目标群2"},
        {"openid": "group-3", "enabled": True, "name": "目标群3"},
    ]


def _check_quote_extraction_and_validation():
    quote = _quote(author=False)
    event = SimpleNamespace(reply=None, msg_elements=[quote])
    extracted = qq_fwd.extract_quoted_message(event)
    assert extracted["content"] == "带图消息"
    assert extracted["author_known"] is False

    assert qq_fwd.quoted_message_error(None) is None
    assert qq_fwd.quoted_message_error(_extracted(_quote(bot=True))) == qq_fwd.BOT_QUOTE_TEXT
    assert qq_fwd.quoted_message_error(_extracted(_quote(content="/转发 1"))) == qq_fwd.BOT_QUOTE_TEXT
    assert qq_fwd.quoted_message_error(_extracted(_quote(content=""))) == qq_fwd.EMPTY_QUOTE_TEXT
    assert (
        qq_fwd.quoted_message_error(
            _extracted(_quote(content="转发群列表\n1. 目标群", author=False))
        )
        == qq_fwd.BOT_QUOTE_TEXT
    )
    assert qq_fwd.quoted_message_error(_extracted(_quote())) is None


async def _check_private_quote_multi_target():
    bot = FakeBot()
    event = FakeC2CEvent(
        "/转发 1 3",
        "evt-forward",
        reply=_quote("私聊带图", [("https://example.test/a.png", "image/png")]),
    )
    captured = {}

    async def build(message):
        captured.update(message)
        return [make_text("私聊带图")], [
            {
                "kind": "图片",
                "url": "https://example.test/a.png",
                "segment": QQMessageSegment.file_image(b"image", "a.png"),
            }
        ]

    with (
        mock.patch.object(command, "get_active_user_openids", return_value={"user-1"}),
        mock.patch.object(command, "get_qq_group_entries", return_value=_groups()),
        mock.patch.object(command, "is_user_authorized", return_value=True),
        mock.patch.object(command, "translate_text_parts", new=mock.AsyncMock(return_value=[make_text("私聊带图")])),
        mock.patch.object(command, "send_relay_message", new=mock.AsyncMock(return_value={"group-1": True, "group-3": True})) as relay_send,
        mock.patch.object(qq_fwd, "build_forward_content", new=build),
        mock.patch.object(command, "_stop_propagation"),
    ):
        await command._handle_relay_cmd(bot, event, "/转发 1 3", "user-1")

    assert captured == {
        "content": "私聊带图",
        "attachments": [
            {"url": "https://example.test/a.png", "content_type": "image/png"}
        ],
    }
    assert relay_send.await_args.kwargs["groups"] == ["group-1", "group-3"]
    assert relay_send.await_args.args[1][0]["kind"] == "图片"
    assert _reply_text(bot.replies[-1]).endswith("转发成功"), bot.replies


async def _check_private_recent_content_fallback():
    command._c2c_last_content.clear()
    bot = FakeBot()

    # 没有引用且没有最近内容：提示没有内容，不发送。
    with (
        mock.patch.object(command, "send_titled_reply", new=mock.AsyncMock()) as reply,
        mock.patch.object(command, "send_relay_message", new=mock.AsyncMock()) as relay_send,
    ):
        await command._forward_quoted_or_recent(
            bot, FakeC2CEvent("/转发 1"), ["group-1"], "user-1"
        )
    reply.assert_awaited_once_with(
        bot, mock.ANY, "转发结果", qq_fwd.NO_CONTENT_TEXT
    )
    relay_send.assert_not_awaited()

    # 普通消息被记住后，/转发 全部 直接使用该内容。
    command._c2c_last_content.clear()
    observed = FakeC2CEvent("最近内容", "evt-recent")
    with (
        mock.patch.object(command, "QQBot", FakeBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "is_user_authorized", return_value=True),
        mock.patch.object(command, "get_active_user_openids", return_value={"user-1"}),
        mock.patch.object(command, "get_qq_group_entries", return_value=_groups()),
        mock.patch.object(command, "translate_text_parts", new=mock.AsyncMock(return_value=[make_text("最近内容")])),
        mock.patch.object(command, "send_relay_message", new=mock.AsyncMock(return_value={"group-1": True, "group-2": True, "group-3": True})) as relay_send,
        mock.patch.object(qq_fwd, "build_forward_content", new=mock.AsyncMock(return_value=([make_text("最近内容")], []))),
        mock.patch.object(command, "_stop_propagation"),
    ):
        await command.handle(bot, observed)
        await command._handle_relay_cmd(
            bot, FakeC2CEvent("/转发 全部", "evt-all"), "/转发 全部", "user-1"
        )

    assert relay_send.await_args.kwargs["groups"] == ["group-1", "group-2", "group-3"]
    assert _reply_text(bot.replies[-1]).endswith("转发成功"), bot.replies


async def _check_group_multi_target_and_partial_failure():
    bot = FakeBot()
    event = FakeGroupEvent(
        "/转发 1 2",
        reply=_quote("群聊内容", [("https://example.test/b.png", "image/png")]),
    )
    entries = [
        {"openid": "source-group", "enabled": True, "name": "来源群"},
        {"openid": "group-1", "enabled": True, "name": "目标群1"},
        {"openid": "group-2", "enabled": True, "name": "目标群2"},
    ]
    with (
        mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            return_value=(entries, {"group-1", "group-2"}),
        ),
        mock.patch.object(
            qq_fwd,
            "build_forward_content",
            new=mock.AsyncMock(return_value=([make_text("群聊内容")], [])),
        ),
        mock.patch.object(
            qq_fwd,
            "translate_text_parts",
            new=mock.AsyncMock(return_value=[make_text("群聊内容")]),
        ),
        mock.patch.object(
            qq_fwd,
            "send_forward_to_group",
            new=mock.AsyncMock(return_value={"group-1": True, "group-2": False}),
        ),
    ):
        await qq_fwd._cmd_relay_forward(bot, event, [1, 2])

    body = _reply_text(bot.replies[-1])
    assert "已转发到「目标群1」" in body, bot.replies
    assert "未转发到「目标群2」" in body, bot.replies


async def _check_bot_quote_rejected():
    bot = FakeBot()
    event = FakeC2CEvent("/转发 1", "evt-bot", reply=_quote(bot=True))
    with (
        mock.patch.object(command, "send_relay_message", new=mock.AsyncMock()) as relay_send,
        mock.patch.object(command, "send_titled_reply", new=mock.AsyncMock()) as reply,
    ):
        await command._forward_quoted_or_recent(
            bot, event, ["group-1"], "user-1"
        )
    reply.assert_awaited_once_with(
        bot, event, "转发结果", qq_fwd.BOT_QUOTE_TEXT
    )
    relay_send.assert_not_awaited()


async def main():
    _check_quote_extraction_and_validation()
    await _check_private_quote_multi_target()
    await _check_private_recent_content_fallback()
    await _check_group_multi_target_and_partial_failure()
    await _check_bot_quote_rejected()
    print("PASS - 群聊/私聊引用直发、附件、多目标与最近内容回退")


if __name__ == "__main__":
    asyncio.run(main())
