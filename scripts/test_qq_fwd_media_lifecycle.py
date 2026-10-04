"""验证关机时 QQ 群手动转发附件先取消回收，再关闭 HTTP 客户端。"""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import qq_fwd


async def main():
    previous_client = qq_fwd._qq_media_client
    previous_stopping = qq_fwd._qq_media_stopping
    previous_semaphore = qq_fwd._media_sema
    previous_tasks = set(qq_fwd._qq_media_tasks)
    qq_fwd._qq_media_tasks.clear()
    qq_fwd._qq_media_stopping = False
    qq_fwd._media_sema = asyncio.Semaphore(1)
    stream_started = asyncio.Event()
    stream_cancelled = asyncio.Event()

    class BlockingStream:
        async def __aenter__(self):
            stream_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stream_cancelled.set()
                raise

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        closed = False

        def stream(self, *_args, **_kwargs):
            return BlockingStream()

        async def aclose(self):
            assert stream_cancelled.is_set(), "HTTP 客户端在附件下载收尾前关闭"
            self.closed = True

    client = FakeClient()
    qq_fwd._qq_media_client = client
    caller = None
    try:
        with mock.patch.object(qq_fwd, "_get_qq_media_client", return_value=client):
            caller = asyncio.create_task(
                qq_fwd._fetch_no_proxy("https://qq-media.example/file")
            )
            await asyncio.wait_for(stream_started.wait(), timeout=1)
            assert caller in qq_fwd._qq_media_tasks

            await qq_fwd._close_qq_media_client()

            result = await asyncio.gather(caller, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result
            assert stream_cancelled.is_set()
            assert client.closed
            assert qq_fwd._qq_media_client is None
            assert qq_fwd._qq_media_tasks == set()

            with mock.patch.object(qq_fwd, "_get_qq_media_client") as create_client:
                assert await qq_fwd._fetch_no_proxy("https://qq-media.example/late") is None
                create_client.assert_not_called()
        print("PASS - 关闭时 QQ 群附件下载先取消回收，再关闭共享客户端")
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        qq_fwd._qq_media_tasks.clear()
        qq_fwd._qq_media_tasks.update(previous_tasks)
        qq_fwd._qq_media_client = previous_client
        qq_fwd._qq_media_stopping = previous_stopping
        qq_fwd._media_sema = previous_semaphore


if __name__ == "__main__":
    asyncio.run(main())
