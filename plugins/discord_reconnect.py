"""Discord 网关连接重试。

NoneBot 的 Discord 适配器在首次网关请求失败后直接返回，不会自行重试
（nonebot/adapters/discord/adapter.py:91-101）。重启电脑后代理软件往往比
机器人晚就绪，启动瞬间的隧道抖动就会让 Discord 一直离线，这里按退避持续
重试，并在每次重试前复查代理端口。
"""

import asyncio
import os

from nonebot import get_adapter, get_bots, get_driver, logger
from nonebot.adapters.discord import Adapter as DiscordAdapter
from nonebot.adapters.discord import Bot as DiscordBot

from plugins.proxy_guard import align_discord_proxy

driver = get_driver()

# 代理隧道在冷启动后可能需要几分钟才可用，覆盖约 24 分钟；连上即停止。
RETRY_DELAYS = (15, 30, 60, 120, 300, 300, 300, 300)


def _refresh_proxy(adapter):
    """重试前复查 DISCORD_PROXY 的本机端口，必要时跟随系统代理并同步给所有客户端。"""
    switched = align_discord_proxy()
    if not switched:
        return
    proxy = os.environ.get("DISCORD_PROXY") or None
    adapter.discord_config.discord_proxy = proxy
    # 补发与媒体客户端读取的是驱动配置，只改适配器会让网关恢复而 REST 仍走旧端口
    driver.config.discord_proxy = proxy
    logger.warning(
        "Discord 专用代理端口 {} 不可用，重试前改用系统代理 {}",
        switched[0],
        switched[1],
    )


def _discord_connected():
    return any(isinstance(bot, DiscordBot) for bot in get_bots().values())


def _attempt_in_flight(adapter):
    return any(not task.done() for task in adapter.tasks)


async def _retry_gateway():
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        await asyncio.sleep(delay)
        if _discord_connected():
            return
        try:
            adapter = get_adapter(DiscordAdapter)
        except ValueError:
            return
        bot_infos = list(adapter.discord_config.discord_bots)
        if not bot_infos:
            return
        _refresh_proxy(adapter)
        if _attempt_in_flight(adapter):
            continue
        logger.warning("Discord 尚未建立网关连接，进行第 {} 次重试", attempt)
        for bot_info in bot_infos:
            adapter.tasks.add(asyncio.create_task(adapter.run_bot(bot_info)))


@driver.on_startup
async def _schedule_gateway_retry():
    asyncio.create_task(_retry_gateway())
