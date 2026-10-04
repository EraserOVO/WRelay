"""Verify QQ OpenID and identity persistence runs outside the event loop."""

import asyncio
import threading
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import command


class FakeBot:
    self_id = "bot-1"


class FakeC2CEvent:
    author = SimpleNamespace(user_openid="user-1", username="User")
    timestamp = 1_700_000_000


class FakeGroupEvent:
    group_openid = "group-1"
    timestamp = 1_700_000_001


class FakeGroupAddEvent:
    group_openid = "group-2"
    timestamp = 1_700_000_002


async def main():
    expected_now = 1_700_000_100.0
    with mock.patch.object(command.time, "time", return_value=expected_now):
        invalid_timestamps = (
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
            10 ** 1000,
            "NaN",
            "Infinity",
            expected_now + 301,
            "not-a-timestamp",
        )
        for timestamp in invalid_timestamps:
            assert command._event_ts(SimpleNamespace(timestamp=timestamp)) == expected_now
        assert command._event_ts(SimpleNamespace(timestamp=1_700_000_000)) == 1_700_000_000
        assert command._event_ts(SimpleNamespace(timestamp=1_700_000_000_000)) == 1_700_000_000

    main_thread = threading.get_ident()
    calls = []

    def record_call(name):
        calls.append((name, threading.get_ident()))

    with (
        mock.patch.object(command, "QQBot", FakeBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "GroupMessageCreateEvent", FakeGroupEvent),
        mock.patch.object(command, "GroupAddRobotEvent", FakeGroupAddEvent),
        mock.patch.object(command, "save_user_openid", side_effect=lambda *_: record_call("user-openid")),
        mock.patch.object(command, "save_group_openid", side_effect=lambda *_: record_call("group-openid")),
        mock.patch.object(
            command.identities,
            "record_user_identity",
            side_effect=lambda *_args, **_kwargs: record_call("user-identity"),
        ),
        mock.patch.object(
            command.identities,
            "record_group_identity",
            side_effect=lambda *_args, **_kwargs: record_call("group-identity"),
        ),
    ):
        await command.log_group_message_openid(FakeBot(), FakeC2CEvent())
        await command.log_group_message_openid(FakeBot(), FakeGroupEvent())
        await command.log_group_add_openid(FakeBot(), FakeGroupAddEvent())

    assert [name for name, _thread in calls].count("user-openid") == 1
    assert [name for name, _thread in calls].count("user-identity") == 1
    assert [name for name, _thread in calls].count("group-openid") == 2
    assert [name for name, _thread in calls].count("group-identity") == 2
    assert all(thread_id != main_thread for _name, thread_id in calls)
    print("PASS - QQ OpenID 与身份记录在线程池中落盘")

    discovered = []
    with (
        mock.patch.object(command, "QQBot", FakeBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "GroupMessageCreateEvent", FakeGroupEvent),
        mock.patch.object(command, "save_user_openid", side_effect=lambda value: discovered.append(("user", value))),
        mock.patch.object(command, "save_group_openid", side_effect=lambda value, _bot_id: discovered.append(("group", value))),
        mock.patch.object(
            command.identities,
            "record_user_identity",
            side_effect=OSError("identity file unavailable"),
        ),
        mock.patch.object(
            command.identities,
            "record_group_identity",
            side_effect=OSError("identity file unavailable"),
        ),
    ):
        await command.log_group_message_openid(FakeBot(), FakeC2CEvent())
        await command.log_group_message_openid(FakeBot(), FakeGroupEvent())
    assert discovered == [("user", "user-1"), ("group", "group-1")]
    print("PASS - 身份资料写入失败不会影响 QQ OpenID 自动发现")


if __name__ == "__main__":
    asyncio.run(main())
