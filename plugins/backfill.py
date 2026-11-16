import asyncio
import time

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
from plugins.fetch import fetch_channel_latest, fetch_channel_messages_after, fetch_channel_scan_page
from plugins.forward_service import load_channel_filter, prepare_discord_message
from plugins import personal_qq
from plugins import discord_gaps
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
BACKFILL_CHECK_INTERVAL = 60.0
BACKFILL_SEND_INTERVAL = 5.0
_backfill_send_lock = asyncio.Lock()
_backfill_last_send = None
_backfill_scheduler_task = None
_backfill_states = {}


def get_backfill_status():
    return dict(_backfill_states.get(discord_gaps.current_owner(), {}))

driver = get_driver()


@driver.on_startup
async def _start_backfill_scheduler():
    global _backfill_stopping, _backfill_scheduler_task
    if _backfill_scheduler_task is not None and not _backfill_scheduler_task.done():
        return
    _backfill_stopping = False
    task = asyncio.create_task(_backfill_scheduler())
    _backfill_scheduler_task = task
    _backfill_tasks.add(task)
    task.add_done_callback(_on_backfill_task_done)


async def _backfill_scheduler():
    """Periodically retry outstanding gaps, with one bounded batch per round."""
    while not _backfill_stopping:
        await asyncio.sleep(BACKFILL_CHECK_INTERVAL)
        if _backfill_stopping:
            break
        try:
            await _backfill_missed()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("定期补发检查失败，下轮继续")


@driver.on_bot_connect
async def _backfill_wrapper(bot: Bot):
    personal_ready = any(sender.bot is bot for sender in personal_qq.connected_bots().values())
    if (isinstance(bot, (DiscordBot, QQBot)) or personal_ready) and not _backfill_stopping:
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
    global _backfill_stopping, _backfill_running, _backfill_rerun_requested, _backfill_scheduler_task
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
    _backfill_scheduler_task = None
    _backfill_running = False
    _backfill_rerun_requested = False


_profile_backfill_lock = asyncio.Lock()
backfill_operation_lock = asyncio.Lock()


async def _backfill_missed():
    from plugins import bot_profiles, config
    owners = await asyncio.to_thread(bot_profiles.ids, config.SETTINGS_FILE)
    if not owners:
        if _backfill_running:
            return await _backfill_missed_profile()
        async with backfill_operation_lock:
            return await _backfill_missed_profile()
    selected = bot_profiles.CURRENT.get()
    async with _profile_backfill_lock:
        for owner in ([selected] if selected else owners):
            if owner == "unassigned":
                continue
            try:
                with bot_profiles.scope(owner):
                    async with backfill_operation_lock:
                        await _backfill_missed_profile()
            except Exception:
                logger.exception("Bot {} 补发配置异常，跳过该账号", owner)


async def _backfill_missed_profile():

    global _backfill_running, _backfill_rerun_requested

    if _backfill_running:
        _backfill_rerun_requested = True
        return

    _backfill_running = True
    _backfill_rerun_requested = False
    state = _backfill_states.setdefault(discord_gaps.current_owner(), {})
    state.update(running=True, error="")

    try:
        snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)
        state["enabled"] = snapshot["enabled"]
        while True:
            if not snapshot["enabled"]:
                logger.debug(
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
                        await _scan_gaps_channel(channel_id, active_groups)
                    except Exception:
                        state["error"] = "部分频道检查失败，请查看运行日志"
                        logger.exception("补发历史扫描失败: {}", channel_id)
                    try:
                        await _replay_gaps_channel(channel_id, channel_name, active_groups, limit)
                    except Exception:
                        state["error"] = "部分频道投递失败，请查看运行日志"
                        logger.exception("补发欠账投递失败: {}", channel_id)
            except Exception:
                state["error"] = "补发检查失败，请查看运行日志"
                logger.exception("启动补发失败")

            if not _backfill_rerun_requested:
                break
            _backfill_rerun_requested = False
            snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)

    except Exception:
        state["error"] = "读取补发配置失败，请查看运行日志"
        raise
    finally:

        _backfill_running = False
        state["running"] = False
        state["last_check"] = time.time()


async def _deliver_backfill_message(
    channel_id,
    message_id,
    msg,
    target_groups,
    rendered,
    only_pending=False,
):
    """Send a rendered message and record retries for groups that failed."""
    global _backfill_last_send
    text_parts, media_items = rendered
    async with _backfill_send_lock:
        if _backfill_last_send is not None:
            delay = BACKFILL_SEND_INTERVAL - (time.monotonic() - _backfill_last_send)
            if delay > 0:
                await asyncio.sleep(delay)
        snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)
        if not snapshot["enabled"]:
            return True
        active = set(snapshot["groups_by_channel"].get(channel_id, []))
        target_groups = [group for group in target_groups if group in active]
        if not target_groups:
            return True
        _backfill_last_send = time.monotonic()
        if only_pending:
            ok_map = await send_and_record(channel_id, message_id, text_parts, media_items, target_groups, only_pending=True)
        else:
            ok_map = await send_and_record(channel_id, message_id, text_parts, media_items, target_groups)
    if not ok_map:
        await discord_gaps.defer_async(channel_id, message_id, target_groups)
        _backfill_states.setdefault(discord_gaps.current_owner(), {})["error"] = "QQ 未连接或发送未完成"
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
        busy_groups = set(getattr(ok_map, "busy_groups", ()))
        await discord_gaps.defer_async(channel_id, message_id,
                                       [group for group in failed_groups if group not in busy_groups])
        _backfill_states.setdefault(discord_gaps.current_owner(), {})["error"] = "部分目标群发送失败，将继续重试"
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


async def _scan_gaps_channel(channel_id, active_groups):
    """Read a bounded number of descending pages per target, resuming on disk."""
    from plugins.json_io import run_in_thread_to_completion
    owner = discord_gaps.current_owner()
    last = await load_last_messages_async(load_last_messages)
    channel_map = normalize_channel_map(last, channel_id)
    missing_baseline = [group for group in active_groups if not (channel_map.get(group) or channel_map.get("*"))]
    latest = await fetch_channel_latest(channel_id) if missing_baseline else None
    baselines = {group: str(channel_map.get(group) or channel_map.get("*") or latest)
                 for group in active_groups if channel_map.get(group) or channel_map.get("*") or latest}
    await run_in_thread_to_completion(discord_gaps.initialize_cursors, owner, str(channel_id), baselines)
    for group in active_groups:
        if group not in baselines:
            continue
        for page_index in range(10):
            snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)
            if not snapshot["enabled"]:
                return
            if group not in snapshot["groups_by_channel"].get(channel_id, []):
                break
            base, before = await run_in_thread_to_completion(discord_gaps.start_scan, owner, str(channel_id), group)
            page = await fetch_channel_scan_page(channel_id, before)
            if page is None:
                raise RuntimeError("Discord 历史消息查询失败")
            done = await run_in_thread_to_completion(
                discord_gaps.append_scan_page, owner, str(channel_id), group, base, before, page,
            )
            if done:
                break
            if page_index < 9:
                await asyncio.sleep(0.5)


async def _replay_gaps_channel(channel_id, channel_name, active_groups, limit):
    """Recover recorded targets without using the high-water delivery cursor."""
    from plugins.json_io import run_in_thread_to_completion
    rows = await run_in_thread_to_completion(
        discord_gaps.pending_channel, discord_gaps.current_owner(), str(channel_id), active_groups, limit,
    )
    grouped = {}
    for message_id, group, message in rows:
        item = grouped.setdefault(message_id, (message, []))
        item[1].append(group)
    attempted = 0
    for message_id, (message, groups) in grouped.items():
        if is_processing(channel_id, message_id):
            continue
        snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)
        if not snapshot["enabled"]:
            break
        current_groups = set(snapshot["groups_by_channel"].get(channel_id, []))
        groups = [group for group in groups if group in current_groups]
        if not groups:
            continue
        attempted += 1
        filter_config = await load_channel_filter(channel_id)
        if not check_message_filter(filter_config, message.get("author_username") or "",
                                    message.get("content") or "", message.get("embeds")):
            await discord_gaps.resolve_async(channel_id, message_id, groups)
            continue
        message["channel_name"] = channel_name
        rendered = await prepare_discord_message(message, channel_id, message_id,
                                                source_label="自动补发来自", filter_config=filter_config)
        if rendered is None:
            await discord_gaps.resolve_async(channel_id, message_id, groups)
            continue
        # Rendering can take time; honor route/enable changes before sending.
        snapshot = await asyncio.to_thread(get_backfill_runtime_snapshot)
        if not snapshot["enabled"]:
            break
        current_groups = set(snapshot["groups_by_channel"].get(channel_id, []))
        groups = [group for group in groups if group in current_groups]
        if groups and await _deliver_backfill_message(channel_id, message_id, message, groups, rendered, only_pending=True):
            break
    return attempted


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
        await discord_gaps.resolve_async(channel_id, message_id, target_groups)
        await update_last_messages(
            lambda last: apply_success(
                normalize_channel_map(last, channel_id),
                {group: True for group in target_groups},
                message_id,
            )
        )
        return False

    msg["channel_name"] = channel_name
    registered_groups = await discord_gaps.register_async(channel_id, message_id, target_groups, msg)
    already_processed = set(target_groups) - registered_groups
    if already_processed:
        await update_last_messages(
            lambda last: apply_success(
                normalize_channel_map(last, channel_id),
                {group: True for group in already_processed},
                message_id,
            )
        )
    target_groups = [group for group in target_groups if group in registered_groups]
    if not target_groups:
        return False
    rendered = await prepare_discord_message(
        msg,
        channel_id,
        message_id,
        source_label="自动补发来自",
        filter_config=filter_config,
    )
    if rendered is None:
        await discord_gaps.resolve_async(channel_id, message_id, target_groups)
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
