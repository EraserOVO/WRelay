"""验证 X RSS 多源有限并发和单源异常隔离。

不访问真实 RSSHub/QQ，只打桩 _poll_source；用法：
.venv/Scripts/python.exe scripts/test_x_poll_concurrency.py
"""
import asyncio
import sys
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss as x_rss_mod


async def main():
    active = 0
    peak = 0
    polled = []

    async def fake_poll(source):
        nonlocal active, peak
        polled.append(source["key"])
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if source["key"] == "bad":
            raise RuntimeError("模拟单源失败")
        return {"result": "ok", "new": 1, "sent": 1, "failed": 0}

    sources = [
        {"key": str(i), "name": str(i), "enabled": True}
        for i in range(6)
    ] + [
        {"key": "bad", "name": "bad", "enabled": True},
        {"key": "ignored", "name": "ignored", "enabled": True},
    ]

    with mock.patch.object(x_rss_mod, "get_x_rss_sources", return_value=sources), \
            mock.patch.object(
                x_rss_mod,
                "get_x_groups_for_source",
                side_effect=lambda key: [] if key == "ignored" else ["group"],
            ), \
            mock.patch.object(x_rss_mod, "_poll_source", side_effect=fake_poll):
        await x_rss_mod._poll_all()

    assert "ignored" not in polled
    assert peak <= x_rss_mod.X_POLL_CONCURRENCY, peak
    assert peak > 1, peak
    assert x_rss_mod._runtime_status["last_result"] == "fetch_failed"
    assert x_rss_mod._runtime_status["last_new_count"] == 6
    assert x_rss_mod._runtime_status["last_sent_count"] == 6
    print("PASS - X RSS 来源有限并发，峰值=%d，单源异常未阻断其他来源" % peak)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
