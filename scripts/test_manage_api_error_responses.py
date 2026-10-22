"""验证 QQ 管理列表查询会显示管理 API 错误，而非伪装成空列表。"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import manage


async def main():
    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(return_value={"ok": False, "msg": "桌面管理程序尚未启动"}),
    ):
        reply = await manage._cmd_list("qq_group_openids")
    assert reply == "桌面管理程序尚未启动"
    assert "总计0个" not in reply

    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(return_value={
            "qq_group_openids": [
                {"openid": "group-1", "enabled": True, "name": "测试群"},
            ],
        }),
    ):
        reply = await manage._cmd_list("qq_group_openids")
    assert "测试群 group-1" in reply
    assert "总计1个" in reply

    status_api = mock.AsyncMock(
        return_value={"ok": False, "msg": "状态接口暂不可用"},
    )
    with mock.patch.object(manage, "_panel_get", new=status_api):
        reply = await manage._cmd_status()
    assert reply == "状态接口暂不可用"
    status_api.assert_awaited_once_with("/api/status")

    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(side_effect=[
            {
                "running": True,
                "pid": 123,
                "stats": {},
                "health": None,
                "autostart": True,
            },
            {"ok": False, "msg": "设置接口暂不可用"},
        ]),
    ):
        reply = await manage._cmd_status()
    assert reply == "设置接口暂不可用"

    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(side_effect=[
            {
                "running": True,
                "pid": 123,
                "stats": {"today_forwarded": 4, "total_forwarded": 8, "total_failed": 1},
                "health": None,
                "autostart": True,
            },
            {"backfill_enabled": False},
        ]),
    ):
        reply = await manage._cmd_status()
    assert "运行中 PID:123" in reply
    assert "今日转发4条 累计8条 失败1条" in reply
    assert "离线补发：关闭" in reply

    settings_error = {"ok": False, "msg": "设置暂不可用"}
    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(return_value=settings_error),
    ):
        assert await manage._cmd_backfill([]) == "设置暂不可用"
        assert await manage._cmd_timer([]) == "设置暂不可用"
        assert await manage._cmd_timer(["开启"]) == "设置暂不可用"

    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(return_value={
            "backfill_enabled": False,
            "backfill_limit": 5,
            "scheduled_message": {
                "enabled": True,
                "content": "每日提醒",
                "send_time": "09:30",
                "target_groups": ["G1"],
            },
        }),
    ):
        backfill_reply = await manage._cmd_backfill([])
        timer_reply = await manage._cmd_timer([])
    assert "当前关闭" in backfill_reply
    assert "单次补发上限：5条/频道" in backfill_reply
    assert "状态：开启" in timer_reply
    assert "内容：每日提醒" in timer_reply

    print("PASS - QQ 管理 API 错误反馈与正常响应")


if __name__ == "__main__":
    asyncio.run(main())
