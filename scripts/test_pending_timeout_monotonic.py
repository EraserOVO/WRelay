"""Verify QQ manual relay pending expiry uses a monotonic clock."""

from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import command, qq_fwd


def _wall_clock_must_not_be_used():
    raise AssertionError("pending timeout must not read wall-clock time")


def main():
    qq_fwd._pending.clear()
    command._c2c_pending.clear()
    fake_clock = SimpleNamespace(monotonic=lambda: 100.0, time=_wall_clock_must_not_be_used)

    with (
        mock.patch.object(qq_fwd, "time", fake_clock),
        mock.patch.object(command, "time", fake_clock),
        mock.patch.object(qq_fwd, "get_qq_fwd_select_timeout", return_value=10),
        mock.patch.object(command, "get_qq_fwd_select_timeout", return_value=10),
    ):
        qq_fwd.set_pending("group-1", "member-1", {"openid": "target"})
        command.set_c2c_pending("user-1", ["target"])
        group_entry = qq_fwd.get_pending("group-1", "member-1")
        c2c_entry = command.get_c2c_pending("user-1")

        assert group_entry["created_ts"] == 100.0
        assert c2c_entry["created_ts"] == 100.0
        assert not qq_fwd.is_pending_expired(group_entry, 10)
        assert command.expire_c2c_pendings() == []
        assert qq_fwd.is_pending_expired(group_entry, 10, now_ts=111.0)
        assert command.expire_c2c_pendings(now_ts=111.0) == ["user-1"]
        assert not command.get_c2c_pending("user-1")

    qq_fwd._pending.clear()
    command._c2c_pending.clear()
    print("PASS - QQ 私聊与群 relay pending 使用单调时钟超时")


if __name__ == "__main__":
    main()
