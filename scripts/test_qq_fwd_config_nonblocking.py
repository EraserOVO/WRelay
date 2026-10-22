"""验证 QQ 群手动转发的配置读取不阻塞事件循环。"""
import asyncio
import time
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import config, qq_fwd
from plugins.media import make_text


class FakeBot:
    self_id = "bot-id"
    id = "bot-id"
    self_info = SimpleNamespace(username="WRelay")

    def __init__(self):
        self.replies = []

    async def send(self, _event, message):
        self.replies.append(message)


class FakeEvent:
    group_openid = "source-group"
    author = type("Author", (), {"member_openid": "member-1"})()
    attachments = []
    msg_elements = None
    id = "event-1"

    def __init__(self, text, reply=None):
        self.text = text
        self.reply = reply

    def get_plaintext(self):
        return self.text


def _quote(content="hello"):
    return SimpleNamespace(
        content=content,
        attachments=[],
        author=SimpleNamespace(bot=False, id="member-2", username="quoted-user"),
    )


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


def _slow(value, ticks, label):
    def getter(*_args, **_kwargs):
        before = ticks[0]
        time.sleep(0.06)
        assert ticks[0] > before, "{} 配置读取阻塞了事件循环".format(label)
        return value
    return getter


async def _with_heartbeat(operation):
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


async def main():
    settings = {
        "qq_group_openids": [
            {"openid": "source-group", "enabled": True, "name": "来源群"},
            {"openid": "target-group", "enabled": True, "name": "目标群"},
        ],
        "qq_forwarding_groups": [{
            "id": "qq-group-1",
            "name": "群转发组",
            "enabled": True,
            "groups": ["source-group", "target-group"],
        }],
    }
    settings_loads = []
    with mock.patch.object(
        config,
        "_load_settings",
        side_effect=lambda: settings_loads.append(1) or settings,
    ):
        entries, related = config.get_qq_fwd_routing_snapshot("source-group")
    assert settings_loads == [1], settings_loads
    assert [item["openid"] for item in entries] == ["source-group", "target-group"]
    assert related == {"target-group"}, related

    async def check_list_lookup(ticks):
        bot = FakeBot()
        event = FakeEvent("/转发 列表")
        with mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            side_effect=_slow((entries, related), ticks, "群转发候选快照"),
        ):
            await qq_fwd._cmd_relay_list(bot, event)
        assert len(bot.replies) == 1, bot.replies
        body = _reply_text(bot.replies[0])
        assert "1. 目标群" in body, bot.replies
        assert "引用要转发的消息后发送 /转发 序号" in body, bot.replies
        assert "未引用时使用最近一条消息。" in body, bot.replies

    await _with_heartbeat(check_list_lookup)

    async def check_forward_validation(ticks):
        bot = FakeBot()
        event = FakeEvent("/转发 1", reply=_quote())
        forward_send = mock.AsyncMock(return_value={"target-group": True})
        with (
            mock.patch.object(qq_fwd, "QQBot", FakeBot),
            mock.patch.object(qq_fwd, "GroupMessageCreateEvent", FakeEvent),
            mock.patch.object(
                qq_fwd,
                "is_group_registered",
                return_value=True,
            ),
            mock.patch.object(
                qq_fwd,
                "get_qq_fwd_routing_snapshot",
                side_effect=_slow((entries, related), ticks, "发送前目标校验"),
            ),
            mock.patch.object(
                qq_fwd,
                "translate_text_parts",
                new=mock.AsyncMock(return_value=[make_text("hello")]),
            ),
            mock.patch.object(
                qq_fwd,
                "send_forward_to_group",
                new=forward_send,
            ),
        ):
            await qq_fwd.handle_fwd(bot, event)
        assert forward_send.await_args.args[0] == ["target-group"]
        assert forward_send.await_args.args[3] == "来源群"
        assert "已转发到「目标群」" in _reply_text(bot.replies[-1]), bot.replies

    await _with_heartbeat(check_forward_validation)

    async def check_commands_are_not_forwarded_and_recent_content_fallback():
        bot = FakeBot()
        forward_send = mock.AsyncMock(return_value={"target-group": True})
        with (
            mock.patch.object(qq_fwd, "QQBot", FakeBot),
            mock.patch.object(qq_fwd, "GroupMessageCreateEvent", FakeEvent),
            mock.patch.object(qq_fwd, "is_group_registered", return_value=True),
            mock.patch.object(
                qq_fwd,
                "get_qq_fwd_routing_snapshot",
                return_value=(entries, related),
            ),
            mock.patch.object(
                qq_fwd,
                "translate_text_parts",
                new=mock.AsyncMock(return_value=[make_text("hello")]),
            ),
            mock.patch.object(
                qq_fwd,
                "send_forward_to_group",
                new=forward_send,
            ),
        ):
            await qq_fwd.handle_fwd(bot, FakeEvent("/菜单"))
            assert bot.replies == [], bot.replies
            await qq_fwd.handle_fwd(bot, FakeEvent("hello"))
            assert bot.replies == [], bot.replies
            await qq_fwd.handle_fwd(bot, FakeEvent("/转发 1"))
        assert forward_send.await_count == 1, forward_send.await_args_list
        assert forward_send.await_args.args[0] == ["target-group"]
        assert "已转发到「目标群」" in _reply_text(bot.replies[-1]), bot.replies

    await check_commands_are_not_forwarded_and_recent_content_fallback()

    first_route_started = asyncio.Event()
    loop = asyncio.get_running_loop()
    route_calls = 0

    def slow_first_route(_group):
        nonlocal route_calls
        route_calls += 1
        if route_calls == 1:
            loop.call_soon_threadsafe(first_route_started.set)
            time.sleep(0.12)
        return entries, related

    bot = FakeBot()
    forward_send = mock.AsyncMock(return_value={"target-group": True})
    with (
        mock.patch.object(qq_fwd, "QQBot", FakeBot),
        mock.patch.object(qq_fwd, "GroupMessageCreateEvent", FakeEvent),
        mock.patch.object(qq_fwd, "is_group_registered", return_value=True),
        mock.patch.object(
            qq_fwd,
            "get_qq_fwd_routing_snapshot",
            side_effect=slow_first_route,
        ),
        mock.patch.object(
            qq_fwd,
            "translate_text_parts",
            new=mock.AsyncMock(return_value=[make_text("hello")]),
        ),
        mock.patch.object(
            qq_fwd,
            "send_forward_to_group",
            new=forward_send,
        ),
    ):
        # 名单（慢读快照）与引用转发并发：锁保证两者串行，转发仍正常完成。
        listing = asyncio.create_task(
            qq_fwd.handle_fwd(bot, FakeEvent("/转发 列表"))
        )
        await asyncio.wait_for(first_route_started.wait(), timeout=1)
        forwarding = asyncio.create_task(
            qq_fwd.handle_fwd(bot, FakeEvent("/转发 1", reply=_quote()))
        )
        await asyncio.gather(listing, forwarding)
    assert forward_send.await_count == 1
    assert "已转发到「目标群」" in _reply_text(bot.replies[-1]), bot.replies
    print("PASS - QQ 群转发配置慢读不阻塞事件循环与最近内容回退")


if __name__ == "__main__":
    asyncio.run(main())
