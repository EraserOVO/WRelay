"""Verify malformed Discord snowflakes cannot break or advance backfill."""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import fetch


class JsonResponse:
    text = ""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


async def main():
    payloads = [
        [{"id": "not-a-snowflake"}],
        [7],
        [{"id": "9" * 64}],
        [{"id": "101", "author": {"username": "alice"}, "content": "valid"}],
    ]
    responses = [JsonResponse(payload) for payload in payloads]

    with (
        mock.patch.object(fetch, "get_discord_token", return_value="test-token"),
        mock.patch.object(
            fetch,
            "_discord_request",
            new_callable=mock.AsyncMock,
            side_effect=responses,
        ) as request,
        mock.patch.object(fetch.logger, "warning"),
    ):
        for _ in payloads[:3]:
            messages = await fetch.fetch_channel_messages_after(
                "111", "100", limit=10
            )
            assert messages == [], messages

        messages = await fetch.fetch_channel_messages_after("111", "100", limit=10)
        assert len(messages) == 1, messages
        assert messages[0]["message_id"] == "101", messages
        assert messages[0]["content"] == "valid", messages
        assert request.await_count == 4

    limited_responses = [
        JsonResponse({"retry_after": 0}, status_code=429),
        JsonResponse([{"id": "102", "content": "after retry"}]),
    ]
    with (
        mock.patch.object(fetch, "get_discord_token", return_value="test-token"),
        mock.patch.object(
            fetch,
            "_discord_request",
            new_callable=mock.AsyncMock,
            side_effect=limited_responses,
        ) as request,
        mock.patch.object(fetch, "_rate_limit_wait", return_value=0),
        mock.patch.object(fetch.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep,
    ):
        messages = await fetch.fetch_channel_messages_after("111", "100", limit=10)
        assert len(messages) == 1, messages
        assert messages[0]["message_id"] == "102", messages
        assert request.await_count == 2
        sleep.assert_awaited_once_with(0)

    print("PASS - 补发分页拒绝坏消息/ID，保留有效雪花 ID并重试429")


if __name__ == "__main__":
    asyncio.run(main())
