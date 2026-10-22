"""Verify failed or filtered manual link content replies once without delivery."""

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import command


class FakeBot:
    def __init__(self):
        self.replies = []

    async def send(self, _event, message):
        self.replies.append(message)


async def check_content_error(source, fetch_failed):
    user = "user-1"
    message = {"content": "link", "attachments": []}
    bot = FakeBot()
    match = SimpleNamespace(group=lambda _index: "source")
    fetch_error = "sample-error" if fetch_failed else None
    post = None if fetch_failed else {"id": "123"}

    with ExitStack() as patches:
        sync_values = {
            "is_discord_url": source == "discord",
            "is_x_status_url": match if source == "x" else None,
            "parse_manual_post_url": source == "bilibili",
            "get_all_channels": {"channel-1": "频道"},
            "get_qq_c2c_relay_filter": {},
            "get_x_c2c_relay_filter": {},
            "get_bilibili_c2c_relay_filter": {},
            "get_channel_translation_config": {},
            "get_x_source_translation_config": {},
            "get_enabled_message_types": {"文字"},
            "localize_user_error": "诊断信息",
        }
        for name, value in sync_values.items():
            patches.enter_context(mock.patch.object(command, name, return_value=value))
        patches.enter_context(mock.patch.object(command, "is_user_authorized", return_value=True))
        async_values = {
            "fetch_message": (
                None if fetch_failed else {"channel_id": "channel-1", "message_id": "123"},
                fetch_error,
            ),
            "fetch_x_post": (post, "source", fetch_error),
            "fetch_bilibili_manual_post": (post, "source", fetch_error),
            "build_parts": ([], [], False),
            "render_x_post": None,
            "render_bilibili_post": None,
        }
        for name, value in async_values.items():
            patches.enter_context(mock.patch.object(command, name, new=mock.AsyncMock(return_value=value)))
        translator = patches.enter_context(mock.patch.object(command, "translate_text_parts", new=mock.AsyncMock()))
        sender = patches.enter_context(mock.patch.object(command, "send_relay_message", new=mock.AsyncMock()))
        stop = patches.enter_context(mock.patch.object(command, "_stop_propagation"))
        await command._consume_next(
            bot, SimpleNamespace(id="event-1"), message, ["group-1"], user,
        )

    prefixes = {"discord": "获取Discord消息失败", "x": "获取X消息失败", "bilibili": "获取Bilibili投稿失败"}
    expected = prefixes[source] + "（诊断信息）" if fetch_failed else "消息被筛选，未转发"
    # 转发结果现以 Markdown 标题消息发送（「# 转发结果\n…」），只校验正文与条数。
    assert len(bot.replies) == 1, (source, bot.replies)
    text = "".join(
        ((getattr(segment, "data", {}) or {}).get("markdown").content
         if (getattr(segment, "data", {}) or {}).get("markdown") else str(segment))
        for segment in bot.replies[0]
    )
    assert text.endswith(expected), (source, bot.replies)
    assert translator.await_count == sender.await_count == 0
    stop.assert_called_once()
    print(f"PASS - {source} {'抓取失败' if fetch_failed else '筛选为空'}：回复一次、不投递")


async def main():
    for source in ("discord", "x", "bilibili"):
        await check_content_error(source, fetch_failed=True)
        await check_content_error(source, fetch_failed=False)


if __name__ == "__main__":
    asyncio.run(main())
