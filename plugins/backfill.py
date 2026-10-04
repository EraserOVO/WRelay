import asyncio

from nonebot import get_driver
from nonebot import logger
from nonebot.adapters import Bot

from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.qq import Bot as QQBot

from plugins.config import (
    get_backfill_runtime_snapshot,
)
from plugins.filter import check_message_filter
from plugins.history import (
    load_last_messages,
    load_last_messages_async,
    update_last_messages,
)
from plugins.fetch import fetch_channel_latest, fetch_channel_messages_after
from plugins.forward_service import load_channel_filter, prepare_discord_message
from plugins.sender import log_partial_failure
from plugins.retry import (
    send_and_record,
    schedule_retry_async,
)
from plugins.relay_inflight import is_processing
from plugins.stats import record
from plugins.dedup import (
    normalize_channel_map,
    select_target_groups,
    compute_base_id,
    apply_baseline,
    apply_success,
    apply_skipped,
)


# =====================================================
# 启动历史补发
#
# Discord Bot 每次连接后，读取各启用频道在离线期间新增的
# 尚未转发过的消息，按时间正序逐条转发到缺口群。
# 与实时转发共用 history.py 的去重记录：发送成功即写记录，
# 实时路径先到则补发重读记录跳过，避免同一消息重复发送。
# 每个频道每次最多补发 get_backfill_limit() 条（默认 10），
# 防止离线过久时一次性刷屏。
# =====================================================

_backfill_running = False
_backfill_rerun_requested = False
_backfill_tasks = set()
_backfill_stopping = False

driver = get_driver()


@driver.on_bot_connect
async def _backfill_wrapper(bot: Bot):
    if isinstance(bot, (DiscordBot, QQBot)) and not _backfill_stopping:
        task = asyncio.create_task(_backfill_missed())
        _backfill_tasks.add(task)
        task.add_done_callback(_on_backfill_task_done)


def _on_backfill_task_done(task):
    _backfill_tasks.discard(task)
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.error("启动补发任务异常退出: {}", error)


@driver.on_shutdown
async def _stop_backfill_tasks():
    """Stop and reap backfill work before the bot process shuts down."""
    global _backfill_stopping, _backfill_running, _backfill_rerun_requested
    _backfill_stopping = True
    current_task = asyncio.current_task()
    tasks = [
        task for task in _backfill_tasks
        if task is not current_task and not task.done()
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _backfill_tasks.clear()
    _backfill_running = False
    _backfill_rerun_requested = False


async def _backfill_missed():

    global _backfill_running, _backfill_rerun_requested

    if _backfill_running:
        _backfill_rerun_requested = True
        return

    _backfill_running = True
    _backfill_rerun_requested = False

    try:
        snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)
        while True:
            if not snapshot["enabled"]:
                logger.info(
                    "启动补发: 补发功能已关闭（backfill_enabled=false），跳过"
                )
                break

            try:
                channels = snapshot["channels"]
                groups_by_channel = snapshot["groups_by_channel"]
                limit = snapshot["limit"]

                for channel_id, channel_name in channels.items():
                    # 每个频道独立路由：只补发该频道命中转发组勾选的群
                    active_groups = groups_by_channel.get(channel_id, [])
                    if not active_groups:
                        continue

                    try:
                        await _backfill_channel(
                            channel_id,
                            channel_name,
                            active_groups,
                            limit,
                        )
                    except Exception:
                        logger.exception("启动补发失败: {}", channel_id)
            except Exception:
                logger.exception("启动补发失败")

            if not _backfill_rerun_requested:
                break
            _backfill_rerun_requested = False
            snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)

    finally:

        _backfill_running = False


async def _deliver_backfill_message(
    channel_id,
    message_id,
    msg,
    target_groups,
    rendered,
):
    """Send a rendered message and record retries for groups that failed."""
    text_parts, media_items = rendered
    ok_map = await send_and_record(
        channel_id,
        message_id,
        text_parts,
        media_items,
        target_groups,
    )
    if not ok_map:
        # QQ Bot 未连接：本次消息未发送、不记录去重ID，停止补发。
        logger.warning(
            "启动补发: QQ未连接，补发中断: {} {}",
            channel_id,
            message_id,
        )
        return True

    log_partial_failure(ok_map, channel_id, message_id)
    await record(any(ok_map.values()))

    failed_groups = [group for group, ok in ok_map.items() if not ok]
    if failed_groups:
        # 其他路径送达时会清除这条欠账。
        await schedule_retry_async(
            channel_id,
            message_id,
            msg,
            failed_groups,
            source_label="自动补发来自",
        )

    if any(ok_map.values()):
        logger.info(
            "启动补发: 记录Discord消息 {} {}",
            channel_id,
            message_id,
        )
    else:
        logger.warning(
            "启动补发: 发送失败，不记录去重ID（延迟重试/下次补发会处理）: {} {}",
            channel_id,
            message_id,
        )

    return False


async def _backfill_message(channel_id, channel_name, active_groups, msg):
    """Prepare one historical Discord message; return whether to stop the batch."""
    message_id = msg.get("message_id")
    if not message_id:
        return False

    # 实时事件从入队起就持有在途标记。启动瞬间若该消息已由实时
    # 路径开始渲染，补发不能再走同一条发送链路；实时失败会进入
    # 既有延迟重试/下次补发兜底，成功则会推进游标。
    if is_processing(channel_id, message_id):
        logger.debug(
            "启动补发: 实时路径正在处理，跳过: {} {}",
            channel_id,
            message_id,
        )
        return False

    # 逐条前重读最新记录，实时转发已处理的群自动跳过
    last_messages = await load_last_messages_async(load_last_messages)
    channel_map = normalize_channel_map(last_messages, channel_id)
    target_groups = select_target_groups(
        channel_map,
        active_groups,
        message_id,
    )
    if not target_groups:
        return False

    # 被频道筛选的消息仍更新游标，避免每次补发重复处理。
    msg_author_username = msg.get("author_username") or ""
    filter_config = await load_channel_filter(channel_id)
    if not check_message_filter(
        filter_config,
        msg_author_username,
        msg.get("content") or "",
        msg.get("embeds"),
    ):
        logger.debug(
            "启动补发: 消息被筛选跳过: {} {} (author={})",
            channel_id,
            message_id,
            msg_author_username,
        )
        await update_last_messages(
            lambda last: apply_success(
                normalize_channel_map(last, channel_id),
                {group: True for group in target_groups},
                message_id,
            )
        )
        return False

    msg["channel_name"] = channel_name
    rendered = await prepare_discord_message(
        msg,
        channel_id,
        message_id,
        source_label="自动补发来自",
        filter_config=filter_config,
    )
    if rendered is None:
        await update_last_messages(
            lambda last: apply_skipped(
                normalize_channel_map(last, channel_id),
                target_groups,
                message_id,
            )
        )
        return False

    return await _deliver_backfill_message(
        channel_id,
        message_id,
        msg,
        target_groups,
        rendered,
    )


async def _backfill_channel(channel_id, channel_name, active_groups, limit):

    last_messages = await load_last_messages_async(load_last_messages)

    channel_map = normalize_channel_map(
        last_messages,
        channel_id,
    )

    # 各群有效 last_id 的最小值（群专属优先，缺失回退 "*" 兜底），
    # 避免古老的 "*" 兜底记录把补发起点拖得过旧；
    # 全无有效记录时返回 None（首次启用场景）
    base_id = compute_base_id(
        channel_map,
        active_groups,
    )

    # 无任何群的有效记录（首次启用）：只建基线（记录最新消息 ID），
    # 不补发历史，防止刷屏
    if base_id is None:

        latest = await fetch_channel_latest(
            channel_id
        )

        if latest:

            await update_last_messages(
                lambda last: apply_baseline(
                    normalize_channel_map(last, channel_id),
                    active_groups,
                    latest,
                )
            )

            logger.info(
                "启动补发: 频道[{}]无历史记录，已记录最新消息 {} 作为基线，不补发历史",
                channel_name,
                latest
            )

        return

    messages = await fetch_channel_messages_after(
        channel_id,
        base_id,
        limit,
    )

    if not messages:
        return

    logger.info(
        "启动补发: 频道[{}]本次补发 {} 条缺口消息（旧优先分批，最多 {} 条）",
        channel_name,
        len(messages),
        limit
    )

    # fetch 已返回旧→新，按时间正序逐条转发。
    for msg in messages:
        should_stop = await _backfill_message(
            channel_id,
            channel_name,
            active_groups,
            msg,
        )
        if should_stop:
            return
