"""Automatic X/Bilibili RSS source message-type filter regression tests."""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nonebot
nonebot.init()

from plugins import bilibili, config, x_rss
from plugins.filter import BILIBILI_MESSAGE_TYPES, X_MESSAGE_TYPES, check_bilibili_video_filter, check_x_post_filter
from plugins.settings_model import SettingsModel, validate_settings


def verify_source_config_and_legacy_migration():
    data = {
        "x_rss_sources": [
            {"source": "alice"},
            {"source": "bob", "filter_message_types": ["文字", "图片", "文件", "语音"], "filter_post_types": ["引用"]},
            {"source": "carol", "filter_message_types": []},
            {"source": "dave", "filter_message_types": ["语音", "表情"]},
        ],
        "bilibili_sources": [
            {"id": "up:1", "kind": "up", "value": "1"},
            {"id": "up:2", "kind": "up", "value": "2", "filter_message_types": ["文字", "图片", "链接", "文件", "语音"], "filter_bilibili_post_types": ["视频"]},
            {"id": "up:3", "kind": "up", "value": "3", "filter_message_types": []},
            {"id": "up:4", "kind": "up", "value": "4", "filter_message_types": ["语音", "表情"]},
        ],
    }
    assert config._x_source_filter(data, "alice")["filter_message_types"] == list(X_MESSAGE_TYPES)
    bob = config._x_source_filter(data, "bob")
    assert bob["filter_message_types"] == ["文字", "图片", "视频"]
    assert bob["filter_post_types"] == ["引用"]
    assert config._x_source_filter(data, "carol")["filter_message_types"] == []
    assert config._x_source_filter(data, "dave")["filter_message_types"] == list(X_MESSAGE_TYPES)

    assert config._bilibili_source_filter(data, "up:1")["filter_message_types"] == list(BILIBILI_MESSAGE_TYPES)
    bili = config._bilibili_source_filter(data, "up:2")
    assert bili["filter_message_types"] == ["文字", "图片", "链接", "视频"]
    assert bili["filter_bilibili_post_types"] == ["视频"]
    assert config._bilibili_source_filter(data, "up:3")["filter_message_types"] == []
    assert config._bilibili_source_filter(data, "up:4")["filter_message_types"] == list(BILIBILI_MESSAGE_TYPES)

    legacy = SettingsModel.default()
    legacy["x_rss_sources"] = [{"source": "alice", "filter_message_types": ["文字", "文件", "表情"]}]
    legacy["bilibili_sources"] = [{"id": "up:1", "kind": "up", "value": "1", "filter_message_types": ["文字", "文件", "语音"]}]
    assert validate_settings(legacy) is None, validate_settings(legacy)


def _x_post(*, is_retweet=False, media=None, quote=None):
    return {
        "id": "123", "is_retweet": is_retweet,
        "rt_author": "转发作者" if is_retweet else "",
        "text": "正文内容", "quote": quote, "media": list(media or []),
    }


async def verify_x_component_filter_and_intersection():
    # 推文类型先整条放行/拒绝，消息类型随后控制组件；同时配置是 AND。
    filter_config = {"filter_post_types": ["引用"], "filter_message_types": ["文字"]}
    assert not check_x_post_filter(filter_config, _x_post(is_retweet=False))
    quoted = _x_post(quote={"text": "引用正文", "media": []})
    assert check_x_post_filter(filter_config, quoted)

    # Queued RSS posts recheck current source settings before sending; ensure the
    # type-filtered renderer is wired into automatic dispatch, without doing API or QQ I/O.
    queue_state = {
        "queues": {"alice": {"items": [{
            "id": "123", "cursor_id": "123", "post": _x_post(media=[{"kind": "视频", "url": "https://video.invalid/v.mp4"}]),
        }], "deferred_skips": []}},
        "sent_at": [],
    }
    fake_delivery = {"filter": {"filter_keywords": [], "filter_post_types": None, "filter_message_types": ["文字"]},
                     "translation": {"show_datetime": False}}
    sources = {"alice": {"name": "alice", "key": "alice"}}
    targets = {"alice": ["G1"]}
    with mock.patch.object(x_rss, "_load_source_delivery_config", new=mock.AsyncMock(return_value=fake_delivery)), \
         mock.patch.object(x_rss, "_load_polling_snapshot", new=mock.AsyncMock(return_value=(list(sources.values()), targets))), \
         mock.patch.object(x_rss, "load_last_messages_async", new=mock.AsyncMock(return_value={"x_rss:alice": {"G1": "1"}})), \
         mock.patch.object(x_rss, "_load_x_protection_config", new=mock.AsyncMock(return_value={"min_send_interval":0,"hourly_limit":10,"backlog_limit":10,"overflow_policy":"latest"})), \
         mock.patch.object(x_rss, "_load_protection_state_async", new=mock.AsyncMock(return_value=(queue_state, None))), \
         mock.patch.object(x_rss, "_save_protection_state_async", new=mock.AsyncMock(return_value=True)), \
         mock.patch.object(x_rss, "_remove_protection_item", new=mock.AsyncMock()), \
         mock.patch.object(x_rss, "_skip_protection_items", new=mock.AsyncMock()), \
         mock.patch.object(x_rss, "_record_post_delivery_result", new=mock.AsyncMock()), \
         mock.patch.object(x_rss, "_record_dispatch_counters"), \
         mock.patch.object(x_rss, "get_bots", return_value={"qq": object()}), \
         mock.patch.object(x_rss, "QQBot", object), \
         mock.patch.object(x_rss, "translate_text_parts", new=mock.AsyncMock(side_effect=lambda parts,*a,**k:parts)), \
         mock.patch.object(x_rss, "fetch_bytes_many", new=mock.AsyncMock(return_value={})), \
         mock.patch.object(x_rss, "send_and_record", new=mock.AsyncMock(return_value={"G1": True})):
        # Queue dispatcher type-gates the item; the direct renderer assertion above proves
        # the filtered media component itself is removed.
        with mock.patch.object(x_rss, "time") as fake_time:
            fake_time.time.return_value = 1000
            fake_time.monotonic.return_value = 1000
            fake_time.sleep = asyncio.sleep
            fake_time.time_ns.return_value = 1000
            await x_rss._dispatch_one_locked()

    media = [
        {"kind": "图片", "url": "https://image.invalid/p.jpg"},
        {"kind": "视频", "url": "https://video.invalid/v.mp4"},
    ]
    with mock.patch.object(x_rss, "fetch_bytes_many", new=mock.AsyncMock(return_value={
        "https://image.invalid/p.jpg": b"image", "https://video.invalid/v.mp4": b"video",
    })):
        image_and_text = await x_rss._render_post(_x_post(media=media), "alice", enabled_types={"文字", "图片"})
        text_only = await x_rss._render_post(_x_post(media=media), "alice", enabled_types={"文字"})
        default = await x_rss._render_post(_x_post(media=media), "alice", enabled_types=None)
    assert image_and_text is not None and [m["kind"] for m in image_and_text[1]] == ["图片"]
    assert text_only is not None and text_only[1] == []
    assert default is not None and [m["kind"] for m in default[1]] == ["图片", "视频"]


async def verify_bilibili_component_filter_and_intersection():
    post = {
        "id": "BV1test", "post_type": "视频", "title": "标题",
        "link": "https://www.bilibili.com/video/BV1test",
        "description": "简介不该自动发", "author": "UP主",
        "media": [{"kind": "图片", "url": "https://image.invalid/c.jpg"}],
    }
    source = {"name": "UP主", "value": "1"}
    # 内容类型与投稿类型独立叠加：任一维度不通过，整条不会进入渲染。
    both = {"filter_bilibili_post_types": ["视频"], "filter_message_types": ["文字", "链接"]}
    assert check_bilibili_video_filter(both, post)
    assert not check_bilibili_video_filter({**both, "filter_bilibili_post_types": ["专栏"]}, post)

    with mock.patch.object(bilibili, "fetch_bytes_many", new=mock.AsyncMock(return_value={
        "https://image.invalid/c.jpg": b"cover",
    })), mock.patch.object(
        bilibili, "_fetch_video_url", new=mock.AsyncMock(return_value=("https://video.invalid/v.mp4", b"video"))
    ) as fetch_video:
        text_link = await bilibili._render_post(post, source, enabled_types={"文字", "链接"}, include_body=False)
        image_only = await bilibili._render_post(post, source, enabled_types={"图片"}, include_body=False)
        default = await bilibili._render_post(post, source, enabled_types=None, include_body=False)
    assert text_link is not None and text_link[1] == []
    assert image_only is not None and [m["kind"] for m in image_only[1]] == ["图片"]
    assert default is not None and [m["kind"] for m in default[1]] == ["图片", "视频"]
    assert all("简介不该自动发" not in str(getattr(part, "data", {})) for part in default[0])
    assert fetch_video.await_count == 1


async def main():
    verify_source_config_and_legacy_migration()
    await verify_x_component_filter_and_intersection()
    await verify_bilibili_component_filter_and_intersection()
    print("PASS - X/Bilibili 自动 RSS 消息类型筛选、交集、旧配置迁移与默认输出")


if __name__ == "__main__":
    asyncio.run(main())
