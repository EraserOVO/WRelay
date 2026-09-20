import asyncio
from collections import deque

from nonebot import get_driver
from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Event

from nonebot.adapters.discord import Bot as DiscordBot
from nonebot.adapters.discord.event import GuildMessageCreateEvent

from plugins.config import (
    get_active_channels,
    get_groups_for_channel,
    get_channel_filter,
)
from plugins.filter import check_message_filter
from plugins.history import (
    load_last_messages,
    update_last_messages,
)
from plugins.fetch import normalize_event
from plugins.forward_service import prepare_discord_message
from plugins.sender import log_partial_failure
from plugins.stats import record
from plugins.retry import (
    send_and_record,
    schedule_retry,
)
from plugins.relay_inflight import (
    claim_processing,
    release_processing,
)
from plugins.dedup import (
    normalize_channel_map,
    select_target_groups,
    apply_success,
    apply_skipped,
)


relay = on_message(
    priority=20,
    block=False
)


# 实时 Discord 消息背压：同频道串行处理，跨频道有限并发。
# 队列满时入口等待，不丢弃事件；失败仍由现有 retry/backfill 兜底。
MAX_RELAY_PENDING = 128
MAX_RELAY_CONCURRENCY = 4
_relay_capacity = asyncio.Semaphore(MAX_RELAY_PENDING)
_relay_processing = asyncio.Semaphore(MAX_RELAY_CONCURRENCY)
_relay_queues = {}
_relay_workers = set()
_relay_tasks = set()


async def _drain_relay_channel(channel_id):
    """按消息进入队列的顺序处理一个频道的实时消息。"""
    try:
        while True:
            queue = _relay_queues.get(channel_id)
            if not queue:
                _relay_queues.pop(channel_id, None)
                _relay_workers.discard(channel_id)
                return

            bot, event, processing_key = queue.popleft()
            try:
                async with _relay_processing:
                    await _process_event(bot, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "实时 Discord 消息处理异常: 频道{} 消息{}",
                    channel_id,
                    getattr(event, "id", "?"),
                )
            finally:
                release_processing(*processing_key)
                _relay_capacity.release()
    finally:
        # 取消时释放尚未取出的队列项占用，避免关闭/测试场景泄漏容量。
        queue = _relay_queues.pop(channel_id, None)
        if queue:
            for _, _, processing_key in queue:
                release_processing(*processing_key)
                _relay_capacity.release()
        _relay_workers.discard(channel_id)


async def _enqueue_relay_event(channel_id, bot, event, processing_key=None):
    """把实时事件放入有界频道队列。"""
    if processing_key is None:
        processing_key = (str(channel_id), str(getattr(event, "id", "")))
    await _relay_capacity.acquire()
    queue = _relay_queues.setdefault(channel_id, deque())
    queue.append((bot, event, processing_key))
    if channel_id in _relay_workers:
        return

    _relay_workers.add(channel_id)
    task = asyncio.create_task(_drain_relay_channel(channel_id))
    _relay_tasks.add(task)
    task.add_done_callback(_relay_tasks.discard)


@get_driver().on_shutdown
async def _shutdown_relay_queue():
    """Bot 关闭时停止实时队列任务，避免遗留后台任务。"""
    tasks = list(_relay_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _relay_tasks.clear()
    _relay_queues.clear()
    _relay_workers.clear()


async def _process_event(
    bot: DiscordBot,
    event: Event
):

    if not isinstance(
        bot,
        DiscordBot
    ):
        return


    if not isinstance(
        event,
        GuildMessageCreateEvent
    ):
        return


    channel_id = str(
        event.channel_id
    )


    channels = get_active_channels()


    if channel_id not in channels:
        return


    # 该频道没有任何目标群（全局未启用群 / 频道未勾选任何转发组）
    # 时视为暂停转发，避免白下载媒体
    active_groups = get_groups_for_channel(channel_id)

    if not active_groups:
        return


    message_id = str(
        event.id
    )


    last_messages = load_last_messages()

    channel_map = normalize_channel_map(
        last_messages,
        channel_id,
    )

    # 按群分别判断是否跳过（A4：某个群发送失败不会拖累其他群的去重，
    # 重连补发时已成功的群跳过、失败的群重试）
    target_groups = select_target_groups(
        channel_map,
        active_groups,
        message_id,
    )


    if not target_groups:

        logger.debug(
            "跳过旧消息: {}",
            message_id
        )

        return

    # 频道消息筛选：被过滤的消息仍更新去重记录，
    # 避免补发时反复重处理同一条消息
    author_username = getattr(event.author, "username", "") if getattr(event, "author", None) else ""
    filter_config = get_channel_filter(channel_id)

    if not check_message_filter(
        filter_config,
        author_username,
        getattr(event, "content", "") or "",
        getattr(event, "embeds", None),
    ):
        logger.debug(
            "消息被筛选跳过: {} {} (author={})",
            channel_id,
            message_id,
            author_username,
        )

        await update_last_messages(
            lambda last: apply_success(
                normalize_channel_map(last, channel_id),
                {g: True for g in target_groups},
                message_id,
            )
        )

        return

    message = normalize_event(
        event,
        channels[channel_id]
    )


    # 消息类型筛选：按消息内部各内容组件过滤（由 build_parts 执行），
    # 过滤后无内容时不发送（has_content=False）
    rendered = await prepare_discord_message(
        message,
        channel_id,
        message_id,
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
        return

    text_parts, media_items = rendered

    # 组状态可能在消息处理期间被面板修改；发送前重新计算目标，避免暂停后仍投递一条。
    active_groups = get_groups_for_channel(channel_id)
    if not active_groups:
        return
    target_groups = select_target_groups(
        normalize_channel_map(load_last_messages(), channel_id),
        active_groups,
        message_id,
    )
    if not target_groups:
        return

    ok_map = await send_and_record(
        channel_id,
        message_id,
        text_parts,
        media_items,
        target_groups,
    )


    if not ok_map:
        # QQ Bot 未连接：本次消息未发送、不记录去重ID
        return

    # 多群部分失败时明确列出失败群（全成功/全失败由后续日志负责）
    log_partial_failure(
        ok_map,
        channel_id,
        message_id,
    )


    if any(ok_map.values()):
        await record(True)
    else:
        await record(False)


    failed_groups = [
        group
        for group, ok in ok_map.items()
        if not ok
    ]

    if failed_groups:
        # 只登记失败群延迟重试（已成功的群不受影响）；
        # 若失败群其实正在被补发/重试其他路径送达，purge 会清掉欠账
        schedule_retry(
            channel_id,
            message_id,
            message,
            failed_groups,
            source_label="自动转发来自",
        )


    if any(ok_map.values()):

        logger.info(
            "记录Discord消息: {} {}",
            channel_id,
            message_id
        )

    else:

        logger.warning(
            "Discord消息发送失败，不记录去重ID（延迟重试/重连补发会处理）: {} {}",
            channel_id,
            message_id
        )


@relay.handle()
async def handle(
    bot: DiscordBot,
    event: Event
):
    """实时事件入口：快速入队，把耗时处理移到有界频道队列。"""
    if not isinstance(bot, DiscordBot):
        return
    if not isinstance(event, GuildMessageCreateEvent):
        return
    processing_key = (str(event.channel_id), str(event.id))
    if not claim_processing(*processing_key):
        logger.debug("实时 Discord 消息已在处理，跳过重复事件: {} {}", *processing_key)
        return
    try:
        await _enqueue_relay_event(processing_key[0], bot, event, processing_key)
    except BaseException:
        release_processing(*processing_key)
        raise


# =====================================================
# 离线消息丢失提示（B5）已移除：
# 启动历史补发由 plugins/backfill.py 承担（连接后逐条补发缺口消息），
# 不再只告警不补发。
# =====================================================
