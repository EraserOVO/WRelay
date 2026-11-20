"""Verify QQ send classification, retry counts and media fallback."""
import asyncio
import json
from unittest import mock

import nonebot
nonebot.init()
from nonebot.drivers import Response
from nonebot.adapters.qq.exception import ActionFailed, NetworkError
from plugins import sender


def api_error(status, message="", code=123):
    return ActionFailed(Response(status, headers={"X-Tps-trace-ID": "trace-test"}, content=json.dumps({"code": code, "message": message}).encode()))


class Bot:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0
    async def send_to_group(self, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)


async def main():
    for exc, category in [(api_error(403), "认证或权限不足"), (api_error(413), "文件过大"), (api_error(400, "内容违规"), "内容违规")]:
        bot = Bot([exc])
        with mock.patch.object(sender.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep:
            assert not await sender._send_with_retry(bot, "g", object())
            assert bot.calls == 1 and sleep.await_count == 0
        info = sender._qq_failure_details(exc)
        assert info[0] == category and info[3] == 123 and info[4] == "trace-test"
    for exc in [api_error(429), api_error(500), NetworkError("network"), api_error(400, "unknown")]:
        bot = Bot([exc, exc])
        with mock.patch.object(sender.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep:
            assert await sender._send_with_retry(bot, "g", object())
            assert bot.calls == 3 and sleep.await_count == 2
    with mock.patch.object(sender, "_send_to_groups", new_callable=mock.AsyncMock, side_effect=[{"g": False}, {"g": True}]) as send:
        result = await sender.send_media_items(Bot([]), ["g"], [{"segment": sender.QQMessageSegment.text("media"), "kind": "image", "url": "https://example.com/a"}])
        assert result == {"g": True} and send.await_count == 2
    with mock.patch.object(sender, "_send_to_groups", new_callable=mock.AsyncMock, return_value={"g": False}) as send:
        result = await sender.send_media_items(Bot([]), ["g"], [{"segment": sender.QQMessageSegment.text("media"), "kind": "video", "url": "https://example.com/a", "no_link_fallback": True}])
        assert result == {"g": True} and send.await_count == 1
    print("PASS - QQ error classification, retry and media fallback")

if __name__ == "__main__":
    asyncio.run(main())
