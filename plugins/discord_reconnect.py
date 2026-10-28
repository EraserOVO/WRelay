"""QQ / Discord 网关连接监督。

NoneBot 的两个适配器在首次取 access token / 网关地址失败后直接返回，不会
自行重试（nonebot/adapters/discord/adapter.py:91-101、
nonebot/adapters/qq/adapter.py:148-164）。网络抖动或代理隧道短暂不可用时，
对应平台会一直离线，只能等面板看门狗按最长一小时的退避重启。这里在启动后
常驻检查，发现适配器没有存活连接就重新发起，直到连上为止。
"""

import asyncio
import os

from nonebot import get_adapter, get_bots, get_driver, logger
from nonebot.adapters.discord import Adapter as DiscordAdapter
from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.qq import Bot as QQBot

from plugins.proxy_guard import align_discord_proxy

driver = get_driver()

# 启动 15 秒后首次检查；未连上时按 15/30/60 秒退避持续重连，连上后每分钟巡检。
RETRY_DELAYS = (15, 30, 60)
HEALTHY_INTERVAL = 60


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


def _gateway_connected(bot_type):
    return any(isinstance(bot, bot_type) for bot in get_bots().values())


def _attempt_in_flight(adapter):
    return any(not task.done() for task in adapter.tasks)


def _start_bots(adapter, bot_infos, runner):
    for bot_info in bot_infos:
        task = asyncio.create_task(runner(bot_info))
        task.add_done_callback(adapter.tasks.discard)
        adapter.tasks.add(task)


async def _ensure_discord():
    try:
        adapter = get_adapter(DiscordAdapter)
    except ValueError:
        return True
    if _gateway_connected(DiscordBot):
        return True
    bot_infos = list(adapter.discord_config.discord_bots)
    if not bot_infos or _attempt_in_flight(adapter):
        return not bot_infos
    _refresh_proxy(adapter)
    logger.warning("Discord 尚未建立网关连接，重新发起连接")
    _start_bots(adapter, bot_infos, adapter.run_bot)
    return False


async def _ensure_qq():
    try:
        adapter = get_adapter(QQAdapter)
    except ValueError:
        return True
    if _gateway_connected(QQBot):
        return True
    bot_infos = [info for info in adapter.qq_config.qq_bots if info.use_websocket]
    if not bot_infos or _attempt_in_flight(adapter):
        return not bot_infos
    logger.warning("QQ 尚未建立网关连接，重新发起连接")
    _start_bots(adapter, bot_infos, adapter.run_bot_websocket)
    return False


async def _supervise_gateways():
    delay = RETRY_DELAYS[0]
    failed = 0
    while True:
        await asyncio.sleep(delay)
        healthy = True
        for ensure in (_ensure_qq, _ensure_discord):
            if not await ensure():
                healthy = False
        if healthy:
            failed = 0
            delay = HEALTHY_INTERVAL
        else:
            failed += 1
            delay = RETRY_DELAYS[min(failed, len(RETRY_DELAYS) - 1)]


@driver.on_startup
async def _schedule_gateway_supervisor():
    asyncio.create_task(_supervise_gateways())
