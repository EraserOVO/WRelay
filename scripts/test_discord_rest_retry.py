"""验证 Discord 轻量 REST 查询的瞬时错误重试；不访问真实 Discord。"""

import asyncio

import nonebot

nonebot.init()

from plugins import fetch


class FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def get(self, _api, headers=None):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


async def main():
    original_client = fetch._discord_client
    original_sleep = fetch.asyncio.sleep

    async def no_sleep(_seconds):
        return None

    fetch.asyncio.sleep = no_sleep
    try:
        client = FakeClient([
            RuntimeError("temporary connection failure"),
            FakeResponse(200),
        ])
        fetch._discord_client = client
        response = await fetch._discord_get_with_retry("https://example.test", {})
        assert response.status_code == 200
        assert client.calls == 2

        client = FakeClient([
            FakeResponse(429, {"retry-after": "1"}),
            FakeResponse(200),
        ])
        fetch._discord_client = client
        response = await fetch._discord_get_with_retry("https://example.test", {})
        assert response.status_code == 200
        assert client.calls == 2
    finally:
        fetch._discord_client = original_client
        fetch.asyncio.sleep = original_sleep

    print("PASS - Discord REST 瞬时异常与 429 重试")


if __name__ == "__main__":
    asyncio.run(main())
