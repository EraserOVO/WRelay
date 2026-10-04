"""Verify channel metadata lookups are bounded and coalesce duplicate IDs."""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import fetch


async def verify_shutdown_reaps_metadata_tasks():
    previous_stopping = fetch._metadata_stopping
    previous_discord_stopping = fetch._discord_stopping
    previous_client = fetch._discord_client
    previous_tasks = dict(fetch._channel_metadata_tasks)
    fetch._metadata_stopping = False
    fetch._discord_stopping = False
    fetch._channel_metadata_tasks.clear()
    started = asyncio.Event()
    stopped = asyncio.Event()
    client = None

    class FakeDiscordClient:
        closed = False

        async def aclose(self):
            self.closed = True

    async def blocked_fetch(_channel_id):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    try:
        with mock.patch.object(fetch, "fetch_channel_metadata", side_effect=blocked_fetch):
            caller = asyncio.create_task(
                fetch._resolve_channel_metadata("shutdown-channel")
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            metadata_task = fetch._channel_metadata_tasks["shutdown-channel"]
            client = FakeDiscordClient()
            fetch._discord_client = client

            await fetch._close_discord_client()

            result = await asyncio.gather(caller, return_exceptions=True)
            assert metadata_task.done()
            assert stopped.is_set()
            assert isinstance(result[0], asyncio.CancelledError), result
            assert fetch._channel_metadata_tasks == {}
            assert client.closed
            assert fetch._discord_client is None

            with mock.patch.object(fetch, "fetch_channel_metadata") as late_fetch:
                assert await fetch._resolve_channel_metadata("late-channel") is None
                late_fetch.assert_not_called()
    finally:
        for task in list(fetch._channel_metadata_tasks.values()):
            task.cancel()
        fetch._channel_metadata_tasks.clear()
        fetch._channel_metadata_tasks.update(previous_tasks)
        fetch._metadata_stopping = previous_stopping
        fetch._discord_stopping = previous_discord_stopping
        fetch._discord_client = previous_client


async def verify_shutdown_reaps_rest_requests():
    previous_client = fetch._discord_client
    previous_stopping = fetch._discord_stopping
    previous_metadata_stopping = fetch._metadata_stopping
    previous_tasks = set(fetch._discord_request_tasks)
    fetch._discord_client = None
    fetch._discord_request_tasks.clear()
    fetch._discord_stopping = False
    fetch._metadata_stopping = False
    request_started = asyncio.Event()
    request_cancelled = asyncio.Event()

    class FakeDiscordClient:
        closed = False

        async def get(self, *_args, **_kwargs):
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise

        async def aclose(self):
            assert request_cancelled.is_set(), "Discord 客户端在 REST 请求收尾前关闭"
            self.closed = True

    client = FakeDiscordClient()
    fetch._discord_client = client
    caller = None
    try:
        caller = asyncio.create_task(
            fetch._discord_request("get", "https://discord.example/api")
        )
        await asyncio.wait_for(request_started.wait(), timeout=1)
        assert caller in fetch._discord_request_tasks

        await fetch._close_discord_client()

        result = await asyncio.gather(caller, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError), result
        assert request_cancelled.is_set()
        assert client.closed
        assert fetch._discord_client is None
        assert fetch._discord_request_tasks == set()

        try:
            await fetch._discord_request("get", "https://discord.example/late")
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("关闭后仍允许发起 Discord REST 请求")
        print("PASS - Discord REST 请求在关闭时回收，关闭后拒绝新请求")
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        fetch._discord_request_tasks.clear()
        fetch._discord_request_tasks.update(previous_tasks)
        fetch._discord_client = previous_client
        fetch._discord_stopping = previous_stopping
        fetch._metadata_stopping = previous_metadata_stopping


async def main():
    original_cache = dict(fetch._channel_metadata_cache)
    original_failures = dict(fetch._channel_metadata_failures)
    original_fetch_channel_metadata = fetch.fetch_channel_metadata
    fetch._channel_metadata_cache.clear()
    fetch._channel_metadata_failures.clear()
    active = 0
    peak = 0
    calls = []

    async def fake_fetch(channel_id):
        nonlocal active, peak
        calls.append(channel_id)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return {"name": channel_id}

    try:
        with mock.patch.object(fetch, "fetch_channel_metadata", side_effect=fake_fetch):
            results = await asyncio.gather(*(
                fetch._resolve_channel_metadata("channel-" + str(index))
                for index in range(25)
            ))
            assert len(results) == 25
            assert peak <= fetch.CHANNEL_METADATA_CONCURRENCY, peak
            assert peak > 1, peak

            fetch._channel_metadata_cache.clear()
            calls.clear()
            duplicates = await asyncio.gather(*(
                fetch._resolve_channel_metadata("same-channel")
                for _ in range(20)
            ))
            assert len(duplicates) == 20
            assert calls == ["same-channel"], calls

            fetch._channel_metadata_cache.clear()
            fetch._channel_metadata_failures.clear()
            calls.clear()
            responses = [None, {"name": "recovered-channel"}]

            async def flaky_fetch(channel_id):
                calls.append(channel_id)
                return responses.pop(0)

            with mock.patch.object(
                fetch,
                "fetch_channel_metadata",
                side_effect=flaky_fetch,
            ), mock.patch.object(fetch, "CHANNEL_METADATA_FAILURE_TTL", 0.01):
                assert await fetch._resolve_channel_metadata("retry-channel") is None
                assert await fetch._resolve_channel_metadata("retry-channel") is None
                assert calls == ["retry-channel"]
                await asyncio.sleep(0.02)
                assert await fetch._resolve_channel_metadata("retry-channel") == {
                    "name": "recovered-channel",
                }
                assert calls == ["retry-channel", "retry-channel"]

            class InvalidJsonResponse:
                status_code = 200
                text = "<html>upstream error</html>"

                def json(self):
                    raise ValueError("invalid JSON response")

            invalid_json_response = InvalidJsonResponse()
            with (
                mock.patch.object(fetch, "get_discord_token", return_value="test-token"),
                mock.patch.object(
                    fetch,
                    "fetch_channel_metadata",
                    side_effect=original_fetch_channel_metadata,
                ),
                mock.patch.object(
                    fetch,
                    "_discord_get_with_retry",
                    new_callable=mock.AsyncMock,
                    return_value=invalid_json_response,
                ) as metadata_request,
                mock.patch.object(fetch.logger, "warning") as metadata_warning,
            ):
                channel_id = "invalid-json-channel"
                parts, _media_items, has_content = await fetch.build_parts(
                    {
                        "channel_id": channel_id,
                        "username": "alice",
                        "content": "正文仍应转发",
                    },
                    enabled_types={fetch.MESSAGE_TYPE_TEXT},
                )
                assert has_content
                assert channel_id in parts[0].data["text"]
                metadata_request.assert_awaited_once()
                metadata_warning.assert_called_once()

            fetch._channel_metadata_cache.clear()

            async def cache_fetch(channel_id):
                return {"name": channel_id}

            with mock.patch.object(
                fetch,
                "fetch_channel_metadata",
                side_effect=cache_fetch,
            ), mock.patch.object(fetch, "CHANNEL_METADATA_CACHE_LIMIT", 2):
                await fetch._resolve_channel_metadata("old-1")
                await fetch._resolve_channel_metadata("old-2")
                await fetch._resolve_channel_metadata("old-1")
                await fetch._resolve_channel_metadata("new-3")
                assert list(fetch._channel_metadata_cache) == ["old-1", "new-3"]
    finally:
        fetch._channel_metadata_cache.clear()
        fetch._channel_metadata_cache.update(original_cache)
        fetch._channel_metadata_failures.clear()
        fetch._channel_metadata_failures.update(original_failures)

    await verify_shutdown_reaps_metadata_tasks()
    await verify_shutdown_reaps_rest_requests()
    print("PASS - 频道元数据并发有界、重复合并且失败可恢复")


if __name__ == "__main__":
    asyncio.run(main())
