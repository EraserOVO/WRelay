"""Verify RSS polling skips duplicate active state identities."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili, x_rss


async def main():
    x_calls = []
    x_sources = [
        {"key": "Alice", "url": "https://one.example.test/feed", "name": "first", "enabled": True},
        {"key": "alice", "url": "https://two.example.test/feed", "name": "second", "enabled": True},
    ]

    async def poll_x(source):
        x_calls.append(source)
        return {"result": "ok", "error": "", "new": 0, "sent": 0, "failed": 0}

    with (
        mock.patch.object(
            x_rss,
            "get_x_rss_polling_snapshot",
            return_value=(x_sources, {source["key"]: ["group"] for source in x_sources}),
        ),
        mock.patch.object(x_rss, "_poll_source", side_effect=poll_x),
        mock.patch.object(x_rss.logger, "warning") as x_warning,
    ):
        await x_rss._poll_all()
    assert x_calls == [x_sources[0]]
    x_warning.assert_called_once()

    bili_calls = []
    bili_sources = [
        {"id": "same-id", "name": "first", "enabled": True, "feeds": []},
        {"id": "same-id", "name": "second", "enabled": True, "feeds": []},
    ]

    async def poll_bilibili(source):
        bili_calls.append(source)
        return {"result": "ok", "error": "", "new": 0, "sent": 0, "failed": 0}

    with (
        mock.patch.object(
            bilibili,
            "get_bilibili_polling_snapshot",
            return_value=(bili_sources, {source["id"]: ["group"] for source in bili_sources}),
        ),
        mock.patch.object(bilibili, "_poll_source", side_effect=poll_bilibili),
        mock.patch.object(bilibili.logger, "warning") as bili_warning,
    ):
        await bilibili._poll_all()
    assert bili_calls == [bili_sources[0]]
    bili_warning.assert_called_once()

    print("PASS - 重复 X key/Bilibili ID 每轮只轮询一次")


if __name__ == "__main__":
    asyncio.run(main())
