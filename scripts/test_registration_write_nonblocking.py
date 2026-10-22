"""验证 QQ 注册申请的文件写入不会阻塞消息事件循环。"""
import asyncio
import json
import re
import tempfile
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

    @staticmethod
    def get_plaintext():
        return "/注册"


class FakeGroupEvent:
    class Author:
        member_openid = "operator-openid"

    author = Author()
    group_openid = "group-openid"

    @staticmethod
    def get_plaintext():
        return "/注册群"


def sent_message_text(bot):
    message = bot.sent[0]
    markdown = getattr(getattr(message, "data", None), "markdown", None)
    return getattr(markdown, "content", None) or str(message)


async def verify_nonblocking(handler, event, message, patch_target, expected_args):
    write_started = threading.Event()
    release_write = threading.Event()
    bot = FakeBot()

    def blocking_write(*args):
        write_started.set()
        if not release_write.wait(1):
            raise AssertionError("测试未及时释放模拟注册写入")
        return "ABCD-EFGH"

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
        assert bot.sent and "已提交" in sent_message_text(bot), bot.sent
        assert "ABCD-EFGH" in sent_message_text(bot), bot.sent
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
            mock.patch.object(registration, "get_qq_group_entries", return_value=[]),
            mock.patch.object(
                registration, "load_registrations", return_value={"groups": {}}
            ),
            mock.patch.object(
                registration, "register_group", return_value="WXYZ-2345"
            ) as save,
        ):
            task = asyncio.create_task(registration._handle_group_register(
                bot,
                FakeGroupEvent(),
                "/注册群",
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
                "",
                "",
                "operator-openid",
                "bot-1",
            )
        assert len(bot.sent) == 1
        assert "群注册申请已提交" in sent_message_text(bot)
        assert "WXYZ-2345" in sent_message_text(bot)
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
                "/注册",
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
            save.assert_called_once_with("user-openid", "", "")
        assert bot.sent == [], "已取消的请求不应发送迟到的成功回复"
    finally:
        release_write.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)


async def verify_menu_commands_submit_without_profile():
    user_bot = FakeBot()
    with (
        mock.patch.object(registration, "QQBot", FakeBot),
        mock.patch.object(registration, "C2CMessageCreateEvent", FakeUserEvent),
        mock.patch.object(
            registration, "register_user", return_value="USER-2345"
        ) as save_user,
    ):
        await registration.handle_register(user_bot, FakeUserEvent())
    save_user.assert_called_once_with("user-openid", "", "")
    assert "注册申请已提交" in sent_message_text(user_bot)
    assert "USER-2345" in sent_message_text(user_bot)
    assert "用法：" not in sent_message_text(user_bot)

    group_bot = FakeBot()
    with (
        mock.patch.object(registration, "QQBot", FakeBot),
        mock.patch.object(registration, "GroupMessageCreateEvent", FakeGroupEvent),
        mock.patch.object(
            registration,
            "get_active_user_openids",
            return_value=["operator-openid"],
        ),
        mock.patch.object(registration, "get_qq_group_entries", return_value=[]),
        mock.patch.object(
            registration, "load_registrations", return_value={"groups": {}}
        ),
        mock.patch.object(
            registration, "register_group", return_value="GROUP-2345"
        ) as save_group,
    ):
        await registration.handle_register(group_bot, FakeGroupEvent())
    save_group.assert_called_once_with(
        "group-openid", "", "", "operator-openid", "bot-1"
    )
    assert "群注册申请已提交" in sent_message_text(group_bot)
    assert "GROUP-2345" in sent_message_text(group_bot)
    assert "用法：" not in sent_message_text(group_bot)


async def verify_duplicate_group_registration_is_not_submitted():
    with (
        mock.patch.object(
            registration, "get_active_user_openids", return_value=["operator-openid"]
        ),
        mock.patch.object(
            registration,
            "get_qq_group_entries",
            return_value=[
                {"openid": "group-openid", "name": "Existing", "bot_id": "bot-1"}
            ],
        ),
        mock.patch.object(registration, "register_group") as save,
    ):
        bot = FakeBot()
        await registration._handle_group_register(bot, FakeGroupEvent(), "/注册群")
    save.assert_not_called()
    assert "已经登记" in sent_message_text(bot)


async def verify_group_registered_to_another_bot_can_submit():
    with (
        mock.patch.object(
            registration, "get_active_user_openids", return_value=["operator-openid"]
        ),
        mock.patch.object(
            registration,
            "get_qq_group_entries",
            return_value=[{"openid": "group-openid", "bot_id": "other-bot"}],
        ),
        mock.patch.object(
            registration, "load_registrations", return_value={"groups": {}}
        ),
        mock.patch.object(registration, "register_group", return_value="NEW-2345") as save,
    ):
        bot = FakeBot()
        await registration._handle_group_register(bot, FakeGroupEvent(), "/注册群")
    save.assert_called_once_with(
        "group-openid", "", "", "operator-openid", "bot-1"
    )
    assert "已提交" in sent_message_text(bot)
    assert "NEW-2345" in sent_message_text(bot)


async def verify_pending_group_registration_reuses_code():
    with (
        mock.patch.object(
            registration, "get_active_user_openids", return_value=["operator-openid"]
        ),
        mock.patch.object(registration, "get_qq_group_entries", return_value=[]),
        mock.patch.object(
            registration,
            "load_registrations",
            return_value={"groups": {"group-openid": {"application_code": "699H-Z9VN"}}},
        ),
        mock.patch.object(registration, "register_group") as save,
    ):
        bot = FakeBot()
        await registration._handle_group_register(bot, FakeGroupEvent(), "/注册群")
    save.assert_not_called()
    assert "已有待审核申请" in sent_message_text(bot)
    assert "699H-Z9VN" in sent_message_text(bot)


async def main():
    await verify_nonblocking(
        registration._handle_user_register,
        FakeUserEvent(),
        "/注册",
        "register_user",
        ("user-openid", "", ""),
    )
    with mock.patch.object(
        registration,
        "get_active_user_openids",
        return_value=["operator-openid"],
    ), mock.patch.object(
        registration, "get_qq_group_entries", return_value=[]
    ), mock.patch.object(
        registration, "load_registrations", return_value={"groups": {}}
    ):
        await verify_nonblocking(
            registration._handle_group_register,
            FakeGroupEvent(),
            "/注册群",
            "register_group",
            ("group-openid", "", "", "operator-openid", "bot-1"),
        )
    await verify_group_authorization_nonblocking()
    await verify_cancelled_registration_waits_for_write()
    await verify_menu_commands_submit_without_profile()
    await verify_duplicate_group_registration_is_not_submitted()
    await verify_group_registered_to_another_bot_can_submit()
    await verify_pending_group_registration_reuses_code()
    verify_application_codes_are_unique_and_stable()
    print("PASS - 用户/群注册写入不阻塞事件循环，取消时等待申请落盘")


def verify_application_codes_are_unique_and_stable():
    with tempfile.TemporaryDirectory(prefix="wrelay_registration_code_test_") as tmp:
        path = tmp + "/registrations.json"
        with mock.patch.object(registration, "REGISTRATIONS_FILE", path):
            button_user_code = registration.register_user("button-user")
            user_code = registration.register_user("user-1", "123", "Alice")
            repeated_user_code = registration.register_user("user-1", "123", "Alice")
            repeated_empty_user_code = registration.register_user("user-1")
            group_code = registration.register_group(
                "group-1", "456", "Group", "operator-1", "bot-1"
            )
            repeated_empty_group_code = registration.register_group(
                "group-1", operator_openid="operator-1", bot_id="bot-1"
            )

            assert re.fullmatch(r"[23456789A-HJKM-NP-Z]{4}-[23456789A-HJKM-NP-Z]{4}", user_code)
            assert button_user_code != user_code
            assert repeated_user_code == user_code
            assert repeated_empty_user_code == user_code
            assert group_code != user_code
            assert repeated_empty_group_code == group_code
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["users"]["user-1"]["application_code"] == user_code
            assert saved["users"]["user-1"]["qq_id"] == "123"
            assert saved["users"]["user-1"]["nickname"] == "Alice"
            assert saved["users"]["button-user"]["qq_id"] == ""
            assert saved["users"]["button-user"]["nickname"] == ""
            assert saved["users"]["button-user"]["application_code"] == button_user_code
            assert saved["groups"]["group-1"]["application_code"] == group_code
            assert saved["groups"]["group-1"]["qq_id"] == "456"
            assert saved["groups"]["group-1"]["group_name"] == "Group"
    print("PASS - 注册识别码跨用户/群唯一，同一待审申请保持不变并写入存储")


if __name__ == "__main__":
    asyncio.run(main())
