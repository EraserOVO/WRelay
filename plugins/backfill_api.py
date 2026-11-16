import asyncio

from nonebot import get_app
from nonebot import logger

from plugins.config import (
    get_all_channels,
    get_backfill_enabled,
    get_backfill_routing_snapshot,
)
from plugins.history import (
    load_last_messages,
    load_last_messages_async,
    update_last_messages,
)
from plugins.json_io import load_json
from plugins.paths import data_path
from plugins.user_errors import localize_user_error
from plugins.fetch import (
    fetch_channel_latest,
    _resolve_channel_metadata,
)
from plugins.backfill import _backfill_missed, backfill_operation_lock, get_backfill_status
from plugins import discord_gaps
from plugins.retry import clear_retry_through
from plugins.json_io import run_in_thread_to_completion
from plugins.dedup import (
    normalize_channel_map,
)


# =====================================================
# bot HTTP 接口（供管理面板调用）
#
# 面板与 bot 通过 HTTP 通信（bot 的 NoneBot FastAPI 运行在
# 127.0.0.1:18082）。提供端点：
#   - GET  /api/backfill/pending  各启用频道的待补发缺口数
#   - POST /api/backfill/run      手动触发一轮补发（尊重总开关）
#   - POST /api/backfill/clear    清除待补发（把记录推进到最新，不发送）
#   - GET  /api/channel-names     各频道 Discord 真实名称（面板展示用）
#   - GET  /api/channels/audit    上次频道权限扫描快照（可读频道列表）
#   - POST /api/channels/refresh  重新扫描频道权限并更新记录
# =====================================================

app = get_app()


def _is_newer_message_id(current, baseline):
    if current is None:
        return False
    try:
        return int(current) > int(baseline)
    except (TypeError, ValueError, OverflowError):
        return False


def _apply_clear_baseline(channel_map, active_groups, latest):
    """Advance only lagging cursors while preserving newer concurrent sends."""
    changed = False

    for group in active_groups:
        current = channel_map.get(group) or channel_map.get("*")
        if not _is_newer_message_id(current, latest) and channel_map.get(group) != latest:
            channel_map[group] = latest
            changed = True

    current_star = channel_map.get("*")
    if not _is_newer_message_id(current_star, latest) and current_star != latest:
        channel_map["*"] = latest
        changed = True

    return changed


@app.get("/api/backfill/pending")
async def api_backfill_pending():
    """统计已登记的不同消息，并标识扫描未完成和查询失败。"""
    snapshot = await asyncio.to_thread(get_backfill_routing_snapshot)
    channels = snapshot["channels"]
    groups_by_channel = snapshot["groups_by_channel"]

    if not channels:
        return {"ok": True, "total": 0, "channels": {}, "unknown": False, "scanning": False, "capped": False, "status": get_backfill_status()}

    last_messages = await load_last_messages_async(load_last_messages)

    result = {}
    total = 0
    unknown = False
    scanning = False
    capped = False

    for channel_id, channel_name in channels.items():

        # 每个频道独立路由：待补发统计只覆盖该频道命中转发组的群
        active_groups = groups_by_channel.get(channel_id, [])

        if not active_groups:

            result[channel_id] = {"name": channel_name, "count": 0}
            continue

        try:
            state = await run_in_thread_to_completion(
                discord_gaps.channel_status, discord_gaps.current_owner(), str(channel_id), active_groups,
            )
            latest = await fetch_channel_latest(channel_id)
            is_unknown = not latest
            in_progress = state["scanning"]
            if latest:
                channel_map = normalize_channel_map(last_messages, channel_id)
                for group, cursor in state["cursors"].items():
                    cursor = cursor or channel_map.get(group) or channel_map.get("*")
                    if cursor is None or int(latest) > int(cursor):
                        in_progress = True
            count = state["count"]
            unknown = unknown or is_unknown
            scanning = scanning or in_progress
            capped = capped or state["capped"]
            result[channel_id] = {"name": channel_name, "count": count,
                                  "unknown": is_unknown, "scanning": in_progress,
                                  "capped": state["capped"]}
            total += count
        except Exception:
            logger.exception(
                "统计待补发数量失败: {}",
                channel_id
            )
            result[channel_id] = {"name": channel_name, "count": 0, "unknown": True}
            unknown = True

    return {
        "ok": True,
        "total": total,
        "channels": result,
        "unknown": unknown,
        "scanning": scanning,
        "capped": capped,
        "status": get_backfill_status(),
    }


@app.post("/api/backfill/run")
async def api_backfill_run():
    """手动触发一轮补发（尊重补发总开关）"""
    if not await asyncio.to_thread(get_backfill_enabled):
        return {"ok": False, "msg": "补发功能已关闭"}
    await _backfill_missed()
    return {"ok": True, "msg": "补发已触发"}


@app.post("/api/backfill/clear")
async def api_backfill_clear():
    """清除待补发：把各启用频道的记录推进到最新消息 ID（不发送消息）"""
    async with backfill_operation_lock:
        return await _clear_backfill_locked()


async def _clear_backfill_locked():
    snapshot = await asyncio.to_thread(get_backfill_routing_snapshot)
    channels = snapshot["channels"]
    groups_by_channel = snapshot["groups_by_channel"]

    if not channels:
        return {"ok": True, "cleared": 0}

    baselines = []

    for channel_id in channels:

        # 只清除该频道命中转发组的群的待补发
        active_groups = groups_by_channel.get(channel_id, [])

        if not active_groups:
            continue

        latest = await fetch_channel_latest(
            channel_id
        )

        if not latest:
            continue

        baselines.append((channel_id, active_groups, str(latest)))

    cleared = 0
    if baselines:
        def apply_clear_baselines(last_messages):
            nonlocal cleared
            for channel_id, active_groups, latest in baselines:
                channel_map = normalize_channel_map(last_messages, channel_id)
                if _apply_clear_baseline(channel_map, active_groups, latest):
                    cleared += 1
            return cleared > 0

        await update_last_messages(apply_clear_baselines)
        # 同一账号的持久缺口及扫描游标必须一起处理；仅清到查询到的
        # 频道最新 ID，之后到达的消息和停用群的记录保留。
        await run_in_thread_to_completion(discord_gaps.clear_through, discord_gaps.current_owner(), baselines)
        await clear_retry_through(baselines)

    if cleared:
        logger.info(
            "清除待补发: {} 个频道记录已推进到最新",
            cleared
        )

    return {"ok": True, "cleared": cleared}


@app.get("/api/channel-names")
async def api_channel_names():
    """返回设置文件中全部频道的 Discord 真实名称（供面板展示）

    返回 {"ok": True, "names": {频道ID: 真实名或null},
    "guild_ids": {频道ID: 服务器ID或null}}；
    API 拉取失败的频道值为 null（复用 fetch.py 进程内缓存）"""
    channels = await asyncio.to_thread(get_all_channels)

    if not channels:
        return {"ok": True, "names": {}, "guild_ids": {}}

    results = await asyncio.gather(
        *(_resolve_channel_metadata(channel_id) for channel_id in channels)
    )

    return {
        "ok": True,
        "names": {
            channel_id: (metadata or {}).get("name")
            for channel_id, metadata in zip(channels, results)
        },
        "guild_ids": {
            channel_id: (metadata or {}).get("guild_id")
            for channel_id, metadata in zip(channels, results)
        },
    }


# =====================================================
# 频道权限审计（供面板"刷新频道记录"）
#
# GET  /api/channels/audit    读取上次扫描的结构化快照
# POST /api/channels/refresh  运行诊断脚本重新扫描并更新记录
# =====================================================

AUDIT_FILE = data_path("channels_audit.json")
CHANNEL_REFRESH_TIMEOUT_SECONDS = 120
CHANNEL_REFRESH_CLEANUP_TIMEOUT_SECONDS = 5


def _read_audit():
    """读取 data/channels_audit.json；不存在/损坏返回空结构"""
    data = load_json(
        AUDIT_FILE,
        default=None
    )
    if isinstance(data, dict):
        return data
    return {
        "generated_at": "",
        "visible_total": 0,
        "readable_total": 0,
        "readable": [],
        "visible": [],
    }


@app.get("/api/channels/audit")
async def api_channels_audit():
    """返回上次频道权限扫描的结构化快照（面板打开时展示可读频道）"""
    return {"ok": True, "audit": await asyncio.to_thread(_read_audit)}


@app.post("/api/channels/refresh")
async def api_channels_refresh():
    """重新扫描频道权限并更新本机 data/channels_audit.md 与 data/channels_audit.json

    通过 subprocess 复用 scripts/diagnose_channels.py（避免重复实现），
    返回最新可读频道列表供面板展示。"""
    import os
    import sys

    from plugins.paths import PROJECT_ROOT, COMPILED
    base = os.fspath(PROJECT_ROOT)
    script = os.path.join(base, "scripts", "diagnose_channels.py")
    command = [sys.executable, script]
    if COMPILED:
        script = os.path.join(base, "WRelay.Bot.exe")
        command = [script, "--diagnose-channels"]

    if not os.path.exists(script):
        return {"ok": False, "msg": "诊断脚本不存在"}

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=base,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=CHANNEL_REFRESH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.warning("频道权限刷新超时后终止诊断进程失败: {}", exc)
            try:
                await asyncio.wait_for(
                    proc.wait(),
                    timeout=CHANNEL_REFRESH_CLEANUP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error("频道权限刷新诊断进程终止后仍未退出")
            except Exception as exc:
                logger.warning("等待频道权限刷新诊断进程退出失败: {}", exc)
        return {"ok": False, "msg": "刷新超时"}
    except Exception as exc:
        return {"ok": False, "msg": localize_user_error(f"执行失败: {exc}", "执行失败")}

    if proc.returncode != 0:
        stderr_text = stderr.decode("utf-8", "ignore").strip()
        stdout_text = stdout.decode("utf-8", "ignore").strip()
        logger.warning(
            "频道权限刷新失败（退出码 {}）：stderr={} stdout={}",
            proc.returncode,
            stderr_text[-1000:],
            stdout_text[-1000:],
        )
        # 诊断脚本的部分错误写在 stdout，stderr 为空时也要把原因带回面板。
        detail = (stderr_text or stdout_text or "执行失败")[-300:]
        return {"ok": False, "msg": localize_user_error(detail, "执行失败")}

    out = stdout.decode("utf-8", "ignore").strip()
    summary = out.splitlines()[-1] if out else "已刷新"
    return {
        "ok": True,
        "msg": summary,
        "audit": _read_audit(),
    }
