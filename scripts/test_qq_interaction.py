"""QQ 按钮回调（INTERACTION_CREATE）处理测试。

覆盖：
1. 交互事件能解析出按钮 data（命令文本）与所在消息 id
2. 私聊/群聊交互都能还原成等价的 C2C / 群聊消息事件
3. 还原后的事件 to_me、session_id 正确，能进入现有命令处理器
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from nonebot.compat import type_validate_python
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.adapters.qq.event import InteractionCreateEvent

from plugins.qq_interaction import _build_event
from plugins.qq_interaction import _command_text
from plugins.qq_interaction import _message_id


def _payload(**overrides):
    payload = {
        "event_id": "EVT1",
        "id": "INT1",
        "type": 11,
        "version": 1,
        "timestamp": "2026-10-18T01:20:00+08:00",
        "scene": "c2c",
        "chat_type": 2,
        "user_openid": "USER_OPENID",
        "application_id": "1905519536",
        "data": {
            "resolved": {
                "button_id": "b1",
                "button_data": "/运行状态",
                "message_id": "MSG1",
                "user_id": "USER_OPENID",
            }
        },
    }
    payload.update(overrides)
    return payload


def main():
    # ---------- 私聊 ----------
    event = type_validate_python(InteractionCreateEvent, _payload())
    assert _command_text(event) == "/运行状态"
    assert _message_id(event) == "MSG1"

    synthetic = _build_event(event, "/运行状态")
    assert isinstance(synthetic, C2CMessageCreateEvent)
    # 回复用按钮所在消息做被动回复，用户账号不会多出一条消息
    assert synthetic.id == "MSG1"
    assert synthetic.get_plaintext() == "/运行状态"
    assert synthetic.get_user_id() == "USER_OPENID"
    assert synthetic.get_session_id() == "friend_USER_OPENID"
    assert synthetic.is_tome()

    # ---------- 群聊 ----------
    group_event = type_validate_python(
        InteractionCreateEvent,
        _payload(chat_type=1, group_openid="GRP", group_member_openid="MEM", user_openid=None),
    )
    assert _command_text(group_event) == "/运行状态"
    group_synthetic = _build_event(group_event, "/转发 1")
    assert isinstance(group_synthetic, GroupMessageCreateEvent)
    assert group_synthetic.group_openid == "GRP"
    assert group_synthetic.get_plaintext() == "/转发 1"
    assert group_synthetic.get_user_id() == "MEM"
    assert group_synthetic.get_session_id() == "group_GRP_MEM"

    # ---------- 按钮无 data ----------
    empty = type_validate_python(
        InteractionCreateEvent,
        _payload(data={"resolved": {"button_id": "b1", "message_id": "MSG1"}}),
    )
    assert _command_text(empty) == ""

    print("PASS - QQ 按钮回调（命令还原、私聊/群聊事件构造）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
