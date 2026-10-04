"""Verify translation HTTP requests are reaped before shutdown closes clients."""
import asyncio

import nonebot

nonebot.init()

from plugins import ai_client, tencent_tmt


async def verify(module, request_name, close_name, client_name, tasks_name, stopping_name, error_type, label):
    previous_client = getattr(module, client_name)
    previous_tasks = set(getattr(module, tasks_name))
    previous_stopping = getattr(module, stopping_name)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class FakeClient:
        closed = False
        calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def aclose(self):
            assert cancelled.is_set(), "翻译客户端在活动请求收尾前关闭"
            self.closed = True

    client = FakeClient()
    setattr(module, client_name, client)
    getattr(module, tasks_name).clear()
    setattr(module, stopping_name, False)
    caller = None
    try:
        caller = asyncio.create_task(
            getattr(module, request_name)("post", "https://api.example")
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        assert caller in getattr(module, tasks_name)

        await getattr(module, close_name)()

        result = await asyncio.gather(caller, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError), result
        assert cancelled.is_set()
        assert client.closed
        assert getattr(module, client_name) is None
        assert getattr(module, tasks_name) == set()

        try:
            await getattr(module, request_name)("post", "https://api.example/late")
        except error_type:
            pass
        else:
            raise AssertionError("关闭后仍允许发起翻译请求")
        assert client.calls == 1
        print("PASS - {} 请求在关闭时回收，关闭后拒绝新请求".format(label))
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        getattr(module, tasks_name).clear()
        getattr(module, tasks_name).update(previous_tasks)
        setattr(module, client_name, previous_client)
        setattr(module, stopping_name, previous_stopping)


async def main():
    await verify(
        ai_client,
        "_request_ai",
        "_close_ai_client",
        "_ai_client",
        "_ai_request_tasks",
        "_ai_stopping",
        ai_client.AIClientError,
        "AI",
    )
    await verify(
        tencent_tmt,
        "_request_tmt",
        "_close_tmt_client",
        "_tmt_client",
        "_tmt_request_tasks",
        "_tmt_stopping",
        tencent_tmt.TencentTMTError,
        "TMT",
    )


if __name__ == "__main__":
    asyncio.run(main())
