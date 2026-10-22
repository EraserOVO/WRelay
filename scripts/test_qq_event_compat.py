"""验证 QQ 引用消息缺少元素字段时可被兼容补全。"""

import asyncio

import nonebot

nonebot.init()

from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq.adapter import Dispatch
from nonebot.adapters.qq.bot import _check_reply

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

    # 新版引用载荷：msg_elements 只保留引用索引，内容在 parallel_message 中。
    parallel_payload = Dispatch(
        type="C2C_MESSAGE_CREATE",
        id="event-4",
        sequence=4,
        data={
            "id": "message-4",
            "content": "/转发 1",
            "timestamp": "2026-10-21T00:23:25+08:00",
            "author": {"id": "user-1", "user_openid": "user-1"},
            "message_type": 103,
            "message_scene": {
                "ext": ["ref_msg_idx=REFIDX_ref", "msg_idx=REFIDX_self"],
                "source": "default",
            },
            "msg_elements": [
                {"message_type": 103, "msg_idx": "REFIDX_ref"},
            ],
            "parallel_message": {
                "msg_nodes": [
                    {"content": "https://x.com/Chon_nft", "message_type": 0},
                ],
            },
        },
    )
    parallel_event = QQAdapter.payload_to_event(parallel_payload)
    assert parallel_event.get_plaintext() == "/转发 1"
    assert parallel_event.msg_elements[0].content == "https://x.com/Chon_nft"
    asyncio.run(_check_reply(None, parallel_event))
    assert parallel_event.reply is parallel_event.msg_elements[0]
    print("PASS - QQ 引用消息事件兼容")


if __name__ == "__main__":
    main()
