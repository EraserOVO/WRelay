"""验证 QQ 私聊手动转发的配置读取不会阻塞事件循环。"""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import command
from plugins.c2c_consumed import clear_consumed, is_consumed
from plugins.media import make_text


class FakeBot:
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

    def __init__(self, message, event_id):
        self.author = SimpleNamespace(user_openid="user-1")
        self.id = event_id
        self._message = message

    def get_message(self):
        return FakeQQMessage()

    def get_plaintext(self):
        return self._message


def _reply_text(message):
    """回复可能是纯文本，也可能是 markdown 消息，统一取出正文。"""
    if isinstance(message, str):
        return message
    for segment in message:
        markdown = (getattr(segment, "data", {}) or {}).get("markdown")
        content = getattr(markdown, "content", None)
        if content:
            return content
    return ""


def _assert_single_relay_result(replies):
    """转发结果以 Markdown 标题消息发送，只校验正文与消息条数。"""
    assert len(replies) == 1, replies
    assert _reply_text(replies[0]).endswith("转发成功"), replies


def _slow_config(value, ticks, label):
    def getter(*_args, **_kwargs):
        before = ticks[0]
        time.sleep(0.06)
        assert ticks[0] > before, "{} 配置读取阻塞了事件循环".format(label)
        return value
    return getter


async def _run_with_heartbeat(operation):
    ticks = [0]
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            ticks[0] += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(heartbeat())
    try:
        await operation(ticks)
    finally:
        stop.set()
        await task


def _content(text):
    return {"content": text, "attachments": []}


async def main():
    async def check_authorization_and_menu(ticks):
        bot = FakeBot()
        with (
            mock.patch.object(
                command,
                "get_active_user_openids",
                side_effect=_slow_config({"user-1"}, ticks, "私聊授权白名单"),
            ),
            mock.patch.object(
                command,
                "get_qq_group_entries",
                side_effect=_slow_config(
                    [{"openid": "group-1", "enabled": True, "name": "目标群"}],
                    ticks,
                    "私聊目标群列表",
                ),
            ),
        ):
            await command._handle_relay_cmd(
                bot, SimpleNamespace(), "/转发 列表", "user-1"
            )
        assert len(bot.replies) == 1, bot.replies
        body = _reply_text(bot.replies[0])
        assert "1. 目标群" in body, bot.replies
        assert "/转发 全部 可发往全部群" in body, bot.replies
        assert "引用要转发的消息后发送 /转发 序号" in body, bot.replies

    await _run_with_heartbeat(check_authorization_and_menu)

    async def check_discord_route_config(ticks):
        bot = FakeBot()
        text_parts = [make_text("header"), make_text("body")]

        async def fake_fetch(_message):
            return {
                "channel_id": "channel-1",
                "message_id": "message-1",
            }, None

        async def fake_build(*_args, **_kwargs):
            return text_parts, [], True

        with (
            mock.patch.object(command, "is_user_authorized", return_value=True),
            mock.patch.object(command, "get_all_channels", side_effect=_slow_config({"channel-1": "频道"}, ticks, "频道名称")),
            mock.patch.object(command, "get_qq_c2c_relay_filter", side_effect=_slow_config({}, ticks, "Discord 内容筛选")),
            mock.patch.object(command, "get_channel_translation_config", side_effect=_slow_config({"show_datetime": False, "enabled": False}, ticks, "Discord 翻译配置")),
            mock.patch.object(command, "is_discord_url", return_value=True),
            mock.patch.object(command, "fetch_message", side_effect=fake_fetch),
            mock.patch.object(command, "build_parts", side_effect=fake_build),
            mock.patch.object(command, "get_enabled_message_types", return_value=["文字"]),
            mock.patch.object(command, "translate_text_parts", new=mock.AsyncMock(return_value=text_parts)),
            mock.patch.object(command, "send_relay_message", new=mock.AsyncMock(return_value={"group-1": True})),
        ):
            await command._consume_next(
                bot,
                SimpleNamespace(id="event-1"),
                _content("discord link"),
                ["group-1"],
                "user-1",
            )
        _assert_single_relay_result(bot.replies)

    await _run_with_heartbeat(check_discord_route_config)

    async def check_x_route_config(ticks):
        bot = FakeBot()
        text_parts = [make_text("header"), make_text("body")]

        async def fake_fetch(_message):
            return {"id": "post-1"}, "x-account", None

        async def fake_render(*_args, **_kwargs):
            return text_parts, []

        class Match:
            @staticmethod
            def group(_index):
                return "x-account"

        with (
            mock.patch.object(command, "is_user_authorized", return_value=True),
            mock.patch.object(command, "get_x_c2c_relay_filter", side_effect=_slow_config({}, ticks, "X 内容筛选")),
            mock.patch.object(command, "get_x_source_translation_config", side_effect=_slow_config({"show_datetime": False, "enabled": False}, ticks, "X 翻译配置")),
            mock.patch.object(command, "is_discord_url", return_value=False),
            mock.patch.object(command, "is_x_status_url", return_value=Match()),
            mock.patch.object(command, "fetch_x_post", side_effect=fake_fetch),
            mock.patch.object(command, "render_x_post", side_effect=fake_render),
            mock.patch.object(command, "get_enabled_message_types", return_value=["文字"]),
            mock.patch.object(command, "translate_text_parts", new=mock.AsyncMock(return_value=text_parts)),
            mock.patch.object(command, "send_relay_message", new=mock.AsyncMock(return_value={"group-1": True})),
        ):
            await command._consume_next(
                bot,
                SimpleNamespace(id="event-2"),
                _content("x link"),
                ["group-1"],
                "user-1",
            )
        _assert_single_relay_result(bot.replies)

    await _run_with_heartbeat(check_x_route_config)

    async def check_bilibili_filter(ticks):
        bot = FakeBot()
        text_parts = [make_text("header"), make_text("body")]

        async def fake_fetch(_message):
            return {"id": "post-2"}, "creator", None

        async def fake_render(*_args, **_kwargs):
            return text_parts, []

        with (
            mock.patch.object(command, "is_user_authorized", return_value=True),
            mock.patch.object(command, "get_bilibili_c2c_relay_filter", side_effect=_slow_config({}, ticks, "Bilibili 内容筛选")),
            mock.patch.object(command, "is_discord_url", return_value=False),
            mock.patch.object(command, "is_x_status_url", return_value=None),
            mock.patch.object(command, "parse_manual_post_url", return_value=True),
            mock.patch.object(command, "fetch_bilibili_manual_post", side_effect=fake_fetch),
            mock.patch.object(command, "render_bilibili_post", side_effect=fake_render),
            mock.patch.object(command, "get_enabled_message_types", return_value=["文字"]),
            mock.patch.object(command, "translate_text_parts", new=mock.AsyncMock(return_value=text_parts)),
            mock.patch.object(command, "send_relay_message", new=mock.AsyncMock(return_value={"group-1": True})),
        ):
            await command._consume_next(
                bot,
                SimpleNamespace(id="event-3"),
                _content("bilibili link"),
                ["group-1"],
                "user-1",
            )
        _assert_single_relay_result(bot.replies)

    await _run_with_heartbeat(check_bilibili_filter)

    clear_consumed()
    entered_slow_acl = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_acl_read():
        loop.call_soon_threadsafe(entered_slow_acl.set)
        time.sleep(0.12)
        return {"user-1"}

    bot = FakeBot()
    with (
        mock.patch.object(command, "QQBot", FakeBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "get_active_user_openids", side_effect=slow_acl_read),
        mock.patch.object(command, "is_user_authorized", return_value=True),
        mock.patch.object(
            command,
            "get_qq_group_entries",
            return_value=[{"openid": "group-1", "enabled": True}],
        ),
        mock.patch.object(command, "is_discord_url", return_value=False),
        mock.patch.object(command, "is_x_status_url", return_value=False),
        mock.patch.object(command, "parse_manual_post_url", return_value=False),
        mock.patch.object(
            command,
            "translate_text_parts",
            new=mock.AsyncMock(return_value=[make_text("Hello")]),
        ),
        mock.patch.object(
            command,
            "send_relay_message",
            new=mock.AsyncMock(return_value={"group-1": True}),
        ) as relay_send,
    ):
        first = asyncio.create_task(command.handle(
            bot, FakeC2CEvent("/转发 列表", "relay-command")
        ))
        await asyncio.wait_for(entered_slow_acl.wait(), timeout=1)
        second_event = FakeC2CEvent("Hello", "relay-content")
        second = asyncio.create_task(command.handle(bot, second_event))
        await asyncio.gather(first, second)
        assert not is_consumed(second_event), "普通内容应被记录但不消费"

        await command.handle(bot, FakeC2CEvent("/转发 1", "relay-forward"))

    # 第一条命令只回名单；随后内容消息只记住；最后 /转发 1 使用最近内容转发。
    assert len(bot.replies) == 2, bot.replies
    assert "1. group-1" in _reply_text(bot.replies[0]), bot.replies
    assert _reply_text(bot.replies[-1]).endswith("转发成功"), bot.replies
    assert relay_send.await_args.args[0][0].data.get("text") == "Hello", relay_send.await_args

    clear_consumed()
    print("PASS - QQ 私聊授权、目标和 Discord/X/Bilibili 配置慢读不阻塞事件循环")


if __name__ == "__main__":
    asyncio.run(main())
