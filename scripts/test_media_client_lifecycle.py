"""验证关机时媒体下载先收尾再关闭共享 HTTP 客户端。"""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import media


async def main():
    previous_client = media._media_client
    previous_stopping = media._media_stopping
    previous_tasks = set(media._media_download_tasks)
    media._media_download_tasks.clear()
    media._media_stopping = False
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
            assert stream_cancelled.is_set(), "HTTP 客户端在下载任务收尾前关闭"
            self.closed = True

    client = FakeClient()
    media._media_client = client
    caller = None
    try:
        with (
            mock.patch.object(media, "_get_proxy", return_value=None),
            mock.patch.object(media, "_get_timeout", return_value=1),
            mock.patch.object(media, "_get_max_download_bytes", return_value=1024),
            mock.patch.object(media, "_get_semaphore", return_value=asyncio.Semaphore(1)),
            mock.patch.object(media, "_get_media_client", return_value=client),
        ):
            caller = asyncio.create_task(
                media.fetch_bytes("https://media.example/file")
            )
            await asyncio.wait_for(stream_started.wait(), timeout=1)
            assert caller in media._media_download_tasks

            await media._close_media_client()

            result = await asyncio.gather(caller, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result
            assert stream_cancelled.is_set()
            assert client.closed
            assert media._media_client is None
            assert media._media_download_tasks == set()

            with mock.patch.object(media, "_get_media_client") as create_client:
                assert await media.fetch_bytes("https://media.example/late") is None
                create_client.assert_not_called()
        print("PASS - 关闭时媒体下载先取消回收，再关闭共享客户端")
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        media._media_download_tasks.clear()
        media._media_download_tasks.update(previous_tasks)
        media._media_client = previous_client
        media._media_stopping = previous_stopping


if __name__ == "__main__":
    asyncio.run(main())
