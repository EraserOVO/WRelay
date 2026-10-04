"""Verify RSS parsed-post caches drop keys removed from configuration."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili as bilibili_mod
from plugins import x_rss as x_rss_mod


async def main():
    x_rss_mod._posts_cache.clear()
    x_rss_mod._posts_cache.update({"removed": ["old"], "disabled-but-configured": ["keep"]})
    with mock.patch.object(
        x_rss_mod,
        "get_x_rss_polling_snapshot",
        return_value=([{"key": "disabled-but-configured", "enabled": False}], {}),
    ):
        await x_rss_mod._poll_all()
    assert "removed" not in x_rss_mod._posts_cache
    assert x_rss_mod._posts_cache["disabled-but-configured"] == ["keep"]

    # An empty current configuration must also clear old entries.
    with mock.patch.object(
        x_rss_mod, "get_x_rss_polling_snapshot", return_value=([], {})
    ):
        await x_rss_mod._poll_all()
    assert x_rss_mod._posts_cache == {}

    bilibili_mod._posts_cache.clear()
    bilibili_mod._posts_cache.update({
        "removed-source": ["old"],
        "configured-source": ["keep"],
        "configured-source::article": ["old article"],
    })
    with mock.patch.object(
        bilibili_mod,
        "get_bilibili_polling_snapshot",
        return_value=([{
            "id": "configured-source",
            "enabled": False,
            "feeds": [{"state_key": "configured-source"}],
        }], {}),
    ):
        await bilibili_mod._poll_all()
    assert "removed-source" not in bilibili_mod._posts_cache
    assert "configured-source::article" not in bilibili_mod._posts_cache
    assert bilibili_mod._posts_cache["configured-source"] == ["keep"]

    with mock.patch.object(
        bilibili_mod, "get_bilibili_polling_snapshot", return_value=([], {})
    ):
        await bilibili_mod._poll_all()
    assert bilibili_mod._posts_cache == {}
    print("PASS - X/Bilibili RSS 缓存随来源配置清理")


if __name__ == "__main__":
    asyncio.run(main())
