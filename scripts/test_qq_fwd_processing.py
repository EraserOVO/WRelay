"""验证 QQ 群手动转发内容构造/翻译异常与引用附件处理。"""

import asyncio
from types import SimpleNamespace
from unittest import mock

import nonebot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

nonebot.init()

from plugins import qq_fwd, sender
from plugins.media import make_text


class FakeEvent:
    group_openid = "source-group"
    author = SimpleNamespace(member_openid="member-1")
    attachments = []
    msg_elements = None
    id = "message-1"

    def __init__(self, reply=None):
        self.reply = reply

    @staticmethod
    def get_plaintext():
        return "/转发 1"


class FakeBot:
    self_id = "bot-id"
    id = "bot-id"
    self_info = SimpleNamespace(username="WRelay")

    def __init__(self):
        self.replies = []

    async def send(self, event, message):
        self.replies.append(message)


def _reply_text(message):
    """标题回复通过 Markdown segment 发送，提取其正文用于断言。"""
    if isinstance(message, str):
        return message
    for segment in message:
        markdown = (getattr(segment, "data", {}) or {}).get("markdown")
        content = getattr(markdown, "content", None)
        if content:
            return content
    return ""


def _quote(content="待转发消息", attachments=None):
    return SimpleNamespace(
        content=content,
        attachments=[
            SimpleNamespace(url=url, content_type=content_type)
            for url, content_type in (attachments or [])
        ],
        author=SimpleNamespace(
            bot=False,
            id="member-2",
            username="quoted-user",
        ),
    )


async def _raise_processing_error(*_args, **_kwargs):
    raise RuntimeError("simulated processing failure")


async def _check_source_text_precedes_images():
    sent = []

    async def capture(_bot, groups, message, **_kwargs):
        sent.append((groups, message))
        return {group: True for group in groups}

    media_items = [
        {
            "kind": "图片",
            "url": "https://example.test/photo.png",
            "segment": QQMessageSegment.file_image(b"image", "photo.png"),
        },
        {
            "kind": "图片",
            "url": "https://example.test/photo-2.png",
            "segment": QQMessageSegment.file_image(b"image2", "photo-2.png"),
        },
    ]
    with mock.patch.object(sender, "_send_to_groups", side_effect=capture):
        result = await sender._send_source_tagged_content(
            object(),
            ["target-group"],
            [make_text("转发正文")],
            media_items,
            "来源测试群",
        )

    assert result == {"target-group": True}
    assert len(sent) == 3
    first_groups, first_message = sent[0]
    assert first_groups == ["target-group"]
    assert "".join(
        part.data.get("text", "")
        for part in first_message
        if part.type == "text"
    ) == "转发于「来源测试群」：\n\n转发正文"
    assert all(part.type != "file_image" for part in first_message)
    assert all(message[-1].type == "file_image" for _, message in sent[1:])

    # 即使表情图片最初夹在正文段之间，也先发送完整文字，再单独发送图片。
    sent.clear()
    inline_emoji = QQMessageSegment.file_image(b"emoji", "emoji.png")
    with mock.patch.object(sender, "_send_to_groups", side_effect=capture):
        result = await sender._send_source_tagged_content(
            object(),
            ["target-group"],
            [make_text("图片前"), inline_emoji, make_text("图片后")],
            [],
            "来源测试群",
        )
    assert result == {"target-group": True}
    assert len(sent) == 2
    first_message = sent[0][1]
    assert "".join(
        part.data.get("text", "") for part in first_message if part.type == "text"
    ) == "转发于「来源测试群」：\n\n图片前图片后"
    assert all(part.type != "file_image" for part in first_message)
    image_message = sent[1][1]
    assert getattr(image_message, "type", "") == "file_image"


async def _check_quote_attachment_is_passed_through():
    captured = {}

    async def build(message):
        captured.update(message)
        return [make_text(message["content"])], []

    bot = FakeBot()
    event = FakeEvent(
        reply=_quote(
            "带图消息",
            [("https://example.test/photo.png", "image/png")],
        )
    )
    with mock.patch.object(
        qq_fwd,
        "get_qq_fwd_routing_snapshot",
        return_value=(
            [
                {"openid": "source-group", "enabled": True, "name": "来源群"},
                {"openid": "target-group", "enabled": True, "name": "目标群"},
            ],
            {"target-group"},
        ),
    ), mock.patch.object(
        qq_fwd,
        "build_forward_content",
        new=build,
    ), mock.patch.object(
        qq_fwd,
        "translate_text_parts",
        new=mock.AsyncMock(return_value=[make_text("带图消息")]),
    ), mock.patch.object(
        qq_fwd,
        "send_forward_to_group",
        new=mock.AsyncMock(return_value={"target-group": True}),
    ) as forward:
        await qq_fwd._cmd_relay_forward(bot, event, [1])

    assert captured == {
        "content": "带图消息",
        "attachments": [
            {"url": "https://example.test/photo.png", "content_type": "image/png"}
        ],
    }
    assert forward.await_args.args[3] == "来源群"
    assert "已转发到「目标群」" in _reply_text(bot.replies[-1])


async def _check_failure_reply(processor):
    bot = FakeBot()
    event = FakeEvent(reply=_quote())
    with (
        mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            return_value=(
                [
                    {"openid": "source-group", "enabled": True, "name": "来源群"},
                    {"openid": "target-group", "enabled": True, "name": "目标群"},
                ],
                {"target-group"},
            ),
        ),
        mock.patch.object(qq_fwd, "build_forward_content", side_effect=processor),
        mock.patch.object(qq_fwd.logger, "exception"),
    ):
        await qq_fwd._cmd_relay_forward(bot, event, [1])
    assert "转发失败：内容处理失败。" in _reply_text(bot.replies[-1]), bot.replies


async def main():
    await _check_source_text_precedes_images()
    await _check_quote_attachment_is_passed_through()
    await _check_failure_reply(_raise_processing_error)

    async def fail_translation(*_args, **_kwargs):
        raise RuntimeError("simulated translation failure")

    bot = FakeBot()
    event = FakeEvent(reply=_quote())
    with (
        mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            return_value=(
                [
                    {"openid": "source-group", "enabled": True, "name": "来源群"},
                    {"openid": "target-group", "enabled": True, "name": "目标群"},
                ],
                {"target-group"},
            ),
        ),
        mock.patch.object(
            qq_fwd,
            "build_forward_content",
            new=mock.AsyncMock(return_value=([make_text("待转发消息")], [])),
        ),
        mock.patch.object(
            qq_fwd,
            "translate_text_parts",
            side_effect=fail_translation,
        ),
        mock.patch.object(qq_fwd.logger, "exception"),
    ):
        await qq_fwd._cmd_relay_forward(bot, event, [1])
    assert "转发失败：内容处理失败。" in _reply_text(bot.replies[-1]), bot.replies

    print("PASS - QQ 群转发引用附件、内容处理与翻译异常回复")


if __name__ == "__main__":
    asyncio.run(main())
