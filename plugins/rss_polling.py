"""Shared worker scheduling helpers for RSS integrations."""

import asyncio


async def read_rss_poll_interval(read_value, default_interval, *, source_name, logger):
    """Read a source's polling interval off-loop and fall back on read errors."""
    try:
        return await asyncio.to_thread(read_value)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "读取 {} 轮询间隔失败，暂用默认值 {} 秒",
            source_name,
            default_interval,
        )
        return default_interval


async def run_rss_poll_loop(
    *,
    source_name,
    has_connected_bot,
    poll_all,
    read_interval,
    logger,
    connection_check_interval=5,
):
    """Poll while connected and monitor connection changes between poll intervals."""
    loop = asyncio.get_running_loop()
    next_poll_at = loop.time()
    previous_connected = None

    while True:
        try:
            connected = bool(has_connected_bot())
            now = loop.time()

            if connected and (not previous_connected or now >= next_poll_at):
                try:
                    await poll_all()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("{} 轮询异常", source_name)

                interval = await read_interval()
                next_poll_at = loop.time() + interval
            elif not connected and previous_connected is not False:
                logger.debug(
                    "QQ Bot 未连接，暂停 {} 上游拉取",
                    source_name,
                )

            if connected and previous_connected is False:
                logger.info("QQ Bot 已连接，恢复 {} 上游轮询", source_name)

            previous_connected = connected
            delay = connection_check_interval
            if connected:
                delay = min(delay, max(0, next_poll_at - loop.time()))
            await asyncio.sleep(max(0.1, delay))
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("{} 轮询异常", source_name)
            await asyncio.sleep(connection_check_interval)
