"""验证 QQ 注册申请的文件写入不会阻塞消息事件循环。"""
import asyncio
import threading
from unittest import mock

import nonebot

nonebot.init()

from plugins import registration


class FakeBot:
    self_id = "bot-1"

    def __init__(self):
        self.sent = []

    async def send(self, event, message):
        self.sent.append(message)


class FakeUserEvent:
    class Author:
        user_openid = "user-openid"

    author = Author()


class FakeGroupEvent:
    class Author:
        member_openid = "operator-openid"

    author = Author()
    group_openid = "group-openid"


async def verify_nonblocking(handler, event, message, patch_target, expected_args):
    write_started = threading.Event()
    release_write = threading.Event()
    bot = FakeBot()

    def blocking_write(*args):
        write_started.set()
        if not release_write.wait(1):
            raise AssertionError("测试未及时释放模拟注册写入")

    task = None
    try:
        with mock.patch.object(
            registration,
            patch_target,
            side_effect=blocking_write,
        ) as save:
            task = asyncio.create_task(handler(bot, event, message))
            started = await asyncio.wait_for(
                asyncio.to_thread(write_started.wait, 0.5),
                timeout=0.75,
            )
            assert started

            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_write.set()
            await task
            save.assert_called_once_with(*expected_args)
        assert bot.sent and "已提交" in bot.sent[0], bot.sent
    finally:
        release_write.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def verify_group_authorization_nonblocking():
    read_started = threading.Event()
    release_read = threading.Event()
    bot = FakeBot()

    def blocking_read():
        read_started.set()
        if not release_read.wait(1):
            raise AssertionError("测试未及时释放模拟白名单读取")
        return ["operator-openid"]

    task = None
    try:
        with (
            mock.patch.object(
                registration,
                "get_active_user_openids",
                side_effect=blocking_read,
            ),
            mock.patch.object(registration, "register_group") as save,
        ):
            task = asyncio.create_task(registration._handle_group_register(
                bot,
                FakeGroupEvent(),
                "register-group 654321 Test Group",
            ))
            started = await asyncio.wait_for(
                asyncio.to_thread(read_started.wait, 0.5),
                timeout=0.75,
            )
            assert started
            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_read.set()
            await task
            save.assert_called_once_with(
                "group-openid",
                "654321",
                "Test Group",
                "operator-openid",
                "bot-1",
            )
        assert bot.sent == ["群注册申请已提交，等待管理员审核（审核通过后仍需管理员启用）"]
    finally:
        release_read.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def verify_cancelled_registration_waits_for_write():
    write_started = threading.Event()
    release_write = threading.Event()
    bot = FakeBot()

    def blocking_write(*_args):
        write_started.set()
        if not release_write.wait(1):
            raise AssertionError("测试未及时释放被取消的注册写入")

    task = None
    try:
        with mock.patch.object(
            registration,
            "register_user",
            side_effect=blocking_write,
        ) as save:
            task = asyncio.create_task(registration._handle_user_register(
                bot,
                FakeUserEvent(),
                "register 123456 Alice",
            ))
            started = await asyncio.wait_for(
                asyncio.to_thread(write_started.wait, 0.5),
                timeout=0.75,
            )
            assert started
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "取消不应先于申请文件写入完成"
            loop_tick = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_tick.set)
            await asyncio.wait_for(loop_tick.wait(), timeout=0.2)
            release_write.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("注册任务取消应在写入完成后传播")
            save.assert_called_once_with("user-openid", "123456", "Alice")
        assert bot.sent == [], "已取消的请求不应发送迟到的成功回复"
    finally:
        release_write.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def main():
    await verify_nonblocking(
        registration._handle_user_register,
        FakeUserEvent(),
        "register 123456 Alice",
        "register_user",
        ("user-openid", "123456", "Alice"),
    )
    with mock.patch.object(
        registration,
        "get_active_user_openids",
        return_value=["operator-openid"],
    ):
        await verify_nonblocking(
            registration._handle_group_register,
            FakeGroupEvent(),
            "register-group 654321 Test Group",
            "register_group",
            ("group-openid", "654321", "Test Group", "operator-openid", "bot-1"),
        )
    await verify_group_authorization_nonblocking()
    await verify_cancelled_registration_waits_for_write()
    print("PASS - 用户/群注册写入不阻塞事件循环，取消时等待申请落盘")


if __name__ == "__main__":
    asyncio.run(main())
