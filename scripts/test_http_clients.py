"""验证外部 HTTP 客户端按模块复用并可在关闭时释放。"""
import asyncio

import nonebot

nonebot.init()

from plugins import ai_client, qq_fwd, tencent_tmt, x_rss


async def main():
    clients = [
        (ai_client, "_get_ai_client", "_close_ai_client", "_ai_client"),
        (tencent_tmt, "_get_tmt_client", "_close_tmt_client", "_tmt_client"),
        (x_rss, "_get_feed_client", "_close_feed_client", "_feed_client"),
        (qq_fwd, "_get_qq_media_client", "_close_qq_media_client", "_qq_media_client"),
    ]
    for module, getter_name, closer_name, client_name in clients:
        getter = getattr(module, getter_name)
        closer = getattr(module, closer_name)
        first = getter()
        assert first is getter(), f"{module.__name__} 未复用 HTTP 客户端"
        await closer()
        assert getattr(module, client_name) is None, f"{module.__name__} 关闭后未清理客户端"
    print("HTTP client reuse checks passed")


if __name__ == "__main__":
    asyncio.run(main())
