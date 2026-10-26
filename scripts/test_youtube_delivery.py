"""YouTube 视频渲染与限速队列投递测试。"""

import asyncio
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import youtube
from plugins.retry import SendResult


SOURCE_ID = "channel:UC_x5XG1OV2P6uZZ5FSM9Ttw"
CHANNEL_KEY = "youtube:" + SOURCE_ID
CURSOR = "1750000000000"
POST = {
    "id": "video1",
    "cursor_id": CURSOR,
    "title": "测试视频",
    "link": "https://www.youtube.com/watch?v=video1",
    "thumbnail": "https://i.ytimg.com/vi/video1/hqdefault.jpg",
    "author": "测试频道",
}
SOURCE = {"id": SOURCE_ID, "name": "测试频道", "value": "UC_x5XG1OV2P6uZZ5FSM9Ttw"}


async def render(post, source, enabled_types, fetch_result):
    with mock.patch.object(
        youtube, "fetch_bytes_many", new=mock.AsyncMock(return_value=fetch_result)
    ) as fetch:
        rendered = await youtube._render_post(
            post, source, enabled_types=enabled_types
        )
    return rendered, fetch


async def test_render():
    rendered, fetch = await render(POST, SOURCE, None, {POST["thumbnail"]: b"image"})
    text_parts, media_items = rendered
    assert len(text_parts) == 2
    assert "自动转发来自 YouTube 中[测试频道]的视频：" in text_parts[0].data["text"]
    assert text_parts[1].data["text"] == "\n测试视频\n" + POST["link"]
    assert len(media_items) == 1
    assert media_items[0]["kind"] == "图片"
    assert media_items[0]["url"] == POST["thumbnail"]
    assert media_items[0]["segment"].type == "file_image"
    fetch.assert_awaited_once_with([POST["thumbnail"]])

    rendered, fetch = await render(POST, SOURCE, {"文字"}, {})
    text_parts, media_items = rendered
    assert text_parts[1].data["text"] == "\n测试视频"
    assert media_items == []
    fetch.assert_not_awaited()

    rendered, fetch = await render(POST, SOURCE, {"链接"}, {})
    assert rendered[0][1].data["text"] == "\n" + POST["link"]
    assert rendered[1] == []
    fetch.assert_not_awaited()

    rendered, fetch = await render(
        POST, SOURCE, {"文字", "链接"}, {}
    )
    assert [part.data["text"] for part in rendered[0]] == [
        "自动转发来自 YouTube 中[测试频道]的视频：",
        "\n测试视频\n" + POST["link"],
    ]
    assert rendered[1] == []
    fetch.assert_not_awaited()

    rendered, fetch = await render(POST, SOURCE, {"图片"}, {})
    text_parts, media_items = rendered
    assert [part.data["text"] for part in text_parts] == [
        "自动转发来自 YouTube 中[测试频道]的视频：",
    ]
    assert len(media_items) == 1
    assert media_items[0]["segment"].type == "text"
    assert "[图片] " + POST["thumbnail"] in media_items[0]["segment"].data["text"]

    rendered, _ = await render(
        {"thumbnail": POST["thumbnail"]}, {}, set(), {}
    )
    assert rendered is None
    print("PASS - YouTube 视频渲染按消息类型过滤")


def dispatch_patches(state, protection=None, translate=None):
    if translate is None:
        translate = mock.AsyncMock(return_value=["HEADER", "TRANSLATED"])
    return [
        mock.patch.object(
            youtube,
            "_load_protection_config",
            new=mock.AsyncMock(return_value=protection or {
                "min_send_interval": 0,
                "hourly_limit": 100,
                "backlog_limit": 10,
                "overflow_policy": "latest",
            }),
        ),
        mock.patch.object(
            youtube,
            "_load_protection_state_async",
            new=mock.AsyncMock(return_value=(state, "")),
        ),
        mock.patch.object(
            youtube, "_save_protection_state_async", new=mock.AsyncMock()
        ),
        mock.patch.object(
            youtube,
            "_load_polling_snapshot",
            new=mock.AsyncMock(return_value=(
                [SOURCE],
                {SOURCE_ID: ["G1"]},
            )),
        ),
        mock.patch.object(
            youtube,
            "_load_source_delivery_config",
            new=mock.AsyncMock(return_value={
                "filter": {
                    "filter_keywords": [],
                    "filter_message_types": ["文字", "图片", "链接"],
                },
                "translation": {
                    "enabled": True,
                    "append_original": True,
                    "show_datetime": False,
                },
            }),
        ),
        mock.patch.object(
            youtube, "_render_post",
            new=mock.AsyncMock(return_value=(["HEADER", "TEXT"], [])),
        ),
        mock.patch.object(
            youtube,
            "translate_text_parts",
            new=translate,
        ),
    ]


def queue_state():
    return {
        "version": 1,
        "sent_at": [],
        "queues": {
            SOURCE_ID: {
                "items": [{
                    "id": POST["id"],
                    "cursor_id": CURSOR,
                    "post": POST,
                }],
                "deferred_skips": [],
            },
        },
    }


async def test_dispatch_success():
    state = queue_state()
    translate = mock.AsyncMock(return_value=["HEADER", "TRANSLATED"])
    patches = dispatch_patches(state, translate=translate)
    send = mock.AsyncMock(return_value=SendResult(
        {"G1": True}, sent_groups={"G1"}
    ))
    record = mock.AsyncMock()
    load_last = mock.AsyncMock(side_effect=[
        {},
        {CHANNEL_KEY: {"G1": CURSOR}},
    ])
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
            patches[5], patches[6], \
            mock.patch.object(youtube, "send_and_record", new=send), \
            mock.patch.object(youtube, "record", new=record), \
            mock.patch.object(youtube, "load_last_messages_async", new=load_last):
        counts = await youtube._dispatch_one_locked()

    assert counts == (1, 0)
    send.assert_awaited_once()
    args, kwargs = send.await_args
    assert args == (
        CHANNEL_KEY,
        CURSOR,
        ["HEADER", "TRANSLATED"],
        [],
        ["G1"],
    )
    assert kwargs == {"idempotency_key": "youtube:video1"}
    translate.assert_awaited_once_with(
        ["HEADER", "TEXT"],
        ("youtube", SOURCE_ID, "video1"),
        skip_leading_parts=1,
        config={
            "enabled": True,
            "append_original": True,
            "show_datetime": False,
        },
    )
    record.assert_awaited_once_with(True)
    assert state["queues"][SOURCE_ID]["items"] == []
    assert len(state["sent_at"]) == 1
    print("PASS - YouTube 投递使用幂等键、翻译与发送限额")


async def test_dispatch_failure_keeps_queue():
    state = queue_state()
    patches = dispatch_patches(state)
    send = mock.AsyncMock(return_value=SendResult({"G1": False}))
    record = mock.AsyncMock()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
            patches[5], patches[6], \
            mock.patch.object(youtube, "send_and_record", new=send), \
            mock.patch.object(youtube, "record", new=record), \
            mock.patch.object(
                youtube, "load_last_messages_async", new=mock.AsyncMock(return_value={})
            ):
        counts = await youtube._dispatch_one_locked()

    assert counts == (0, 1)
    record.assert_not_awaited()
    assert [item["id"] for item in state["queues"][SOURCE_ID]["items"]] == [
        "video1"
    ]
    assert state["sent_at"] == []
    print("PASS - YouTube 发送失败保留限速队列")


async def test_dispatch_rate_limited():
    state = queue_state()
    state["sent_at"] = [time.time()]
    translate = mock.AsyncMock()
    patches = dispatch_patches(
        state,
        protection={
            "min_send_interval": 3600,
            "hourly_limit": 100,
            "backlog_limit": 10,
            "overflow_policy": "latest",
        },
        translate=translate,
    )
    send = mock.AsyncMock()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
            patches[5], patches[6], \
            mock.patch.object(youtube, "send_and_record", new=send):
        counts = await youtube._dispatch_one_locked()

    assert counts == (0, 0)
    send.assert_not_awaited()
    translate.assert_not_awaited()
    assert len(state["queues"][SOURCE_ID]["items"]) == 1
    print("PASS - YouTube 最短发送间隔阻止本轮投递")


async def main():
    assert youtube._source_channel_key(SOURCE_ID) == CHANNEL_KEY
    await test_render()
    await test_dispatch_success()
    await test_dispatch_failure_keeps_queue()
    await test_dispatch_rate_limited()


if __name__ == "__main__":
    asyncio.run(main())
