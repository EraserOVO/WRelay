"""Verify each RSS item's filter and translation config share one async snapshot."""
import asyncio
import copy
import time
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili, config, x_rss


def verify_config_snapshots():
    data = {
        "translation": {"provider": "ai", "fallback_provider": "tencent_tmt"},
        "x_rss_sources": [{
            "source": "alice",
            "filter_keywords": ["blocked"],
            "filter_post_types": ["video"],
            "translation_enabled": True,
            "translation_append_original": False,
            "translation_show_datetime": True,
        }],
        "bilibili_sources": [{
            "id": "up:1",
            "kind": "up",
            "value": "1",
            "filter_keywords": ["blocked"],
            "filter_bilibili_post_types": ["视频", "无效"],
            "forward_video": True,
            "translation_enabled": True,
            "translation_append_original": False,
            "translation_show_datetime": True,
        }],
    }
    with mock.patch.object(config, "_load_settings", return_value=copy.deepcopy(data)) as load:
        x_result = config.get_x_source_delivery_config("alice")
    load.assert_called_once_with()
    assert x_result == {
        "filter": {"filter_keywords": ["blocked"], "filter_post_types": ["video"]},
        "translation": {
            "provider": "ai",
            "fallback_provider": "tencent_tmt",
            "enabled": True,
            "append_original": False,
            "show_datetime": True,
        },
    }, x_result

    with mock.patch.object(config, "_load_settings", return_value=copy.deepcopy(data)) as load:
        bili_result = config.get_bilibili_source_delivery_config("up:1")
    load.assert_called_once_with()
    assert bili_result == {
        "filter": {
            "filter_keywords": ["blocked"],
            "filter_bilibili_post_types": ["视频"],
            "forward_video": True,
        },
        "translation": {
            "provider": "ai",
            "fallback_provider": "tencent_tmt",
            "enabled": True,
            "append_original": False,
            "show_datetime": True,
        },
    }, bili_result


async def verify_nonblocking(module, getter_name, loader_name):
    expected = {"filter": {}, "translation": {"show_datetime": True}}

    def slow_getter(_source_id):
        time.sleep(0.25)
        return expected

    with mock.patch.object(module, getter_name, side_effect=slow_getter):
        task = asyncio.create_task(getattr(module, loader_name)("source"))
        started = time.monotonic()
        await asyncio.sleep(0)
        elapsed = time.monotonic() - started
        result = await task
    assert result == expected
    assert elapsed < 0.15, "{} RSS 配置慢读取阻塞事件循环: {:.3f}s".format(
        module.__name__, elapsed
    )


async def main():
    verify_config_snapshots()
    await verify_nonblocking(x_rss, "get_x_source_delivery_config", "_load_source_delivery_config")
    await verify_nonblocking(
        bilibili,
        "get_bilibili_source_delivery_config",
        "_load_source_delivery_config",
    )
    print("PASS - X/Bilibili 每条 RSS 的筛选与翻译配置共用快照并在线程池读取")


if __name__ == "__main__":
    asyncio.run(main())
