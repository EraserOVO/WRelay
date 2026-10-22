"""验证定时消息的「每条消息独立目标」：
- 旧消息（无 target_mode）沿用顶层 target_groups；
- 指定群 / 转发组 / 全部群三种模式解析正确；
- 转发组被暂停或成员为空时该条无目标；
- 只保留全局启用的群；
- 开关校验与目标摘要不依赖顶层 target_groups 非空。
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import config, manage, settings_model

GROUPS = [
    {"openid": "g1", "enabled": True},
    {"openid": "g2", "enabled": True},
    {"openid": "g3", "enabled": False},
]
FORWARD_GROUPS = [
    {"id": "qq_default", "name": "转发组1", "enabled": True, "groups": ["g1", "g2"]},
    {"id": "paused", "name": "暂停组", "enabled": False, "groups": ["g2"]},
    {"id": "empty", "name": "空组", "enabled": True, "groups": []},
]
ACTIVE = ["g1", "g2"]


def resolve(message):
    return config.resolve_scheduled_targets(message, GROUPS, FORWARD_GROUPS, ACTIVE)


def main():
    # 1) 旧消息：按指定群并沿用默认目标
    legacy = config._scheduled_message_targets({}, ["g1", "g2"])
    assert legacy["target_mode"] == "groups", legacy
    assert legacy["target_groups"] == ["g1", "g2"], legacy
    assert resolve(legacy) == ["g1", "g2"]

    # 2) 消息级指定群，未启用的群被过滤
    own = config._scheduled_message_targets(
        {"target_mode": "groups", "target_groups": ["g2", "g3"]}, ["g1"]
    )
    assert own["target_groups"] == ["g2", "g3"], own
    assert resolve(own) == ["g2"], own

    # 3) 转发组模式：动态取成员并去重，暂停组不生效
    fg = config._scheduled_message_targets(
        {"target_mode": "forward_group", "forward_groups": ["qq_default", "paused"]}, ["g1"]
    )
    assert fg["target_groups"] == [], fg
    assert resolve(fg) == ["g1", "g2"], resolve(fg)
    paused = config._scheduled_message_targets(
        {"target_mode": "forward_group", "forward_groups": ["paused"]}, []
    )
    assert resolve(paused) == [], resolve(paused)
    empty = config._scheduled_message_targets(
        {"target_mode": "forward_group", "forward_groups": ["empty"]}, []
    )
    assert resolve(empty) == [], resolve(empty)

    # 4) 全部群模式
    everything = config._scheduled_message_targets({"target_mode": "all"}, [])
    assert resolve(everything) == ["g1", "g2"], resolve(everything)

    # 5) 校验：非法模式与空的目标列表
    base = {
        "enabled": True,
        "content": "",
        "target_groups": [],
        "messages": [{
            "id": "m1",
            "content": "hi",
            "schedule_type": "interval",
            "interval_days": 1,
            "interval_hours": 0,
            "interval_minutes": 0,
            "anchor_at": "2026-10-20T09:00",
        }],
    }

    def scheduled_error(payload):
        return settings_model._validate_scheduled_messages({"scheduled_message": payload})

    assert scheduled_error(dict(base)) is None
    bad_mode = dict(base)
    bad_mode["messages"] = [dict(base["messages"][0], target_mode="nope")]
    assert "target_mode" in (scheduled_error(bad_mode) or "")
    bad_fg = dict(base)
    bad_fg["messages"] = [dict(base["messages"][0], target_mode="forward_group", forward_groups=[])]
    assert "forward_groups" in (scheduled_error(bad_fg) or "")
    # 指定群模式允许空列表（表示沿用顶层默认目标），保存不应失败；
    # 这正是「添加转发组」等无关保存曾被整份配置校验拦住的原因。
    empty_groups = dict(base)
    empty_groups["messages"] = [dict(base["messages"][0], target_mode="groups", target_groups=[])]
    assert scheduled_error(empty_groups) is None, scheduled_error(empty_groups)
    bad_type = dict(base)
    bad_type["messages"] = [dict(base["messages"][0], target_mode="groups", target_groups="g1")]
    assert "必须是列表" in (scheduled_error(bad_type) or "")
    ok_fg = dict(base)
    ok_fg["messages"] = [dict(base["messages"][0], target_mode="forward_group", forward_groups=["qq_default"])]
    assert scheduled_error(ok_fg) is None

    # 6) 开关校验：顶层默认为空但消息自带目标 → 允许开启
    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(return_value={"scheduled_message": {
            "enabled": False,
            "target_groups": [],
            "messages": [{"content": "hi", "target_mode": "forward_group", "forward_groups": ["qq_default"]}],
        }}),
    ), mock.patch.object(manage, "_panel_post", new=mock.AsyncMock(return_value={"ok": True})):
        import asyncio
        assert asyncio.run(manage._cmd_timer(["开启"])) == "已开启定时消息"
    with mock.patch.object(
        manage,
        "_panel_get",
        new=mock.AsyncMock(return_value={"scheduled_message": {
            "enabled": False,
            "target_groups": [],
            "messages": [{"content": "hi", "target_mode": "groups", "target_groups": []}],
        }}),
    ):
        import asyncio
        text = asyncio.run(manage._cmd_timer(["开启"]))
    assert "设置目标群或转发组" in text, text

    # 7) 目标摘要
    assert "统一默认目标 2 个群" in manage._scheduled_target_summary(
        [{"target_mode": "groups", "target_groups": []}], ["g1", "g2"]
    )
    summary = manage._scheduled_target_summary(
        [
            {"target_mode": "groups", "target_groups": ["g1"]},
            {"target_mode": "forward_group", "forward_groups": ["qq_default"]},
        ],
        [],
    )
    assert "按消息配置" in summary and "指定群 1" in summary and "转发组 1" in summary, summary

    print("PASS - 定时消息按消息配置目标（旧配置回落 / 三种模式 / 暂停组 / 开关校验 / 摘要）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
