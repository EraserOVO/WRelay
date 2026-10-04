"""验证 QQ 引用消息缺少元素字段时可被兼容补全。"""

import nonebot

nonebot.init()

from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq.adapter import Dispatch

from plugins.qq_event_compat import (
    install_qq_event_compat,
    normalize_qq_dispatch_payload,
)


def main():
    install_qq_event_compat()
    payload = Dispatch(
        type="GROUP_MESSAGE_CREATE",
        id="event-1",
        sequence=1,
        data={
            "message_type": 103,
            "msg_elements": [
                {"content": "引用消息"},
                {"content": "完整元素", "message_type": 1, "msg_idx": "idx-1"},
            ],
            "reply": {"content": "引用回复"},
        },
    )
    normalized = normalize_qq_dispatch_payload(payload)
    assert normalized is not payload
    assert normalized.data["msg_elements"][0]["message_type"] == 103
    assert normalized.data["msg_elements"][0]["msg_idx"] == ""
    assert normalized.data["msg_elements"][1] == payload.data["msg_elements"][1]
    assert normalized.data["reply"]["message_type"] == 103
    assert normalized.data["reply"]["msg_idx"] == ""

    complete = Dispatch(
        type="GROUP_MESSAGE_CREATE",
        id="event-2",
        sequence=2,
        data={
            "msg_elements": [{"content": "完整", "message_type": 1, "msg_idx": "idx-2"}],
        },
    )
    assert normalize_qq_dispatch_payload(complete) is complete

    event_payload = Dispatch(
        type="GROUP_MESSAGE_CREATE",
        id="event-3",
        sequence=3,
        data={
            "id": "message-3",
            "content": "引用消息测试",
            "timestamp": "2026-09-20T23:00:00+08:00",
            "author": {
                "id": "user-1",
                "bot": False,
                "member_openid": "user-1",
                "member_role": "member",
            },
            "group_id": "group-1",
            "group_openid": "group-1",
            "msg_elements": [{"content": "引用消息"}],
        },
    )
    event = QQAdapter.payload_to_event(event_payload)
    assert event.msg_elements[0].message_type == 0
    assert event.msg_elements[0].msg_idx == ""
    print("PASS - QQ 引用消息事件兼容")


if __name__ == "__main__":
    main()
