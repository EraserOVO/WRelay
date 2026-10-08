import asyncio
import math
import os
import time

from nonebot import logger
from nonebot import on_message
from nonebot import get_driver
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupMessageCreateEvent,
)

from plugins import qq_fwd
from plugins.config import get_active_user_openids
from plugins.json_io import (
    atomic_write_json,
    load_json,
    run_in_thread_to_completion,
)
from plugins.paths import data_path
from plugins.c2c_consumed import is_consumed
from panel.local_bridge import request_manager


# =====================================================
# 私聊管理命令
#
# 白名单内的 QQ 用户私聊 Bot，通过 Windows 本机管道请求桌面管理进程
# 执行管理操作（状态/重启/模式/列表/补发）。
#
# 无前缀触发：私聊直接发送命令词（如 status、backfill on），
# 帮助命令为 list；relay 消息由 plugins/command.py 处理。
# =====================================================


# =====================================================
# restart 跨进程确认
#
# restart 由管理面板结束本进程再拉起新进程，新进程对
# "谁触发了重启"一无所知。因此触发 restart 前先把目标
# openid 写入标记文件，新进程 QQ 网关重连成功后据此
# 私聊回复"重启完毕"，并清除标记。
# =====================================================

RESTART_PENDING_FILE = data_path("restart_pending.json")
RESTART_CONFIRM_MAX_AGE = 300  # 标记超过 5 分钟视为过期，避免误发
RESTART_CONFIRM_MAX_FUTURE_SKEW = 60  # 允许系统时钟短暂回拨
_restart_confirm_lock = asyncio.Lock()


def _write_restart_pending(openid, bot_id=""):
    atomic_write_json(
        RESTART_PENDING_FILE,
        {
            "openid": openid,
            "time": int(time.time()),
            "bot_id": str(bot_id or "").strip(),
        },
        indent=2,
    )


async def _write_restart_pending_async(openid, bot_id=""):
    await run_in_thread_to_completion(_write_restart_pending, openid, bot_id)


async def _load_restart_pending_async():
    return await asyncio.to_thread(load_json, RESTART_PENDING_FILE, default=None)


def _qq_bot_id(bot):
    return str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()


def _clear_restart_pending():
    try:
        os.remove(RESTART_PENDING_FILE)
    except OSError:
        pass


async def _clear_restart_pending_async():
    await run_in_thread_to_completion(_clear_restart_pending)


@get_driver().on_bot_connect
async def _confirm_restart(bot: Bot):
    """QQ 网关重连成功后，向触发 restart 的用户回复"重启完毕" """
    if not isinstance(bot, QQBot):
        return
    async with _restart_confirm_lock:
        data = await _load_restart_pending_async()
        if not isinstance(data, dict):
            return
        openid = data.get("openid")
        bot_id = data.get("bot_id", "")
        if (
            not isinstance(openid, str)
            or not openid.strip()
            or not isinstance(bot_id, str)
        ):
            await _clear_restart_pending_async()
            return
        openid = openid.strip()
        # 过期的标记（如重启失败遗留）直接清除，不误发
        created = data.get("time")
        created_timestamp = None
        try:
            valid_created = (
                isinstance(created, (int, float))
                and not isinstance(created, bool)
                and math.isfinite(float(created))
            )
            if valid_created:
                created_timestamp = float(created)
        except (OverflowError, TypeError, ValueError):
            valid_created = False
        current_time = time.time()
        if (
            not valid_created
            or current_time - created_timestamp > RESTART_CONFIRM_MAX_AGE
            or created_timestamp - current_time > RESTART_CONFIRM_MAX_FUTURE_SKEW
        ):
            await _clear_restart_pending_async()
            return
        pending_bot_id = bot_id.strip()
        if pending_bot_id and pending_bot_id != _qq_bot_id(bot):
            return
        try:
            # 与 adapter 回复路径一致，用 author.id 作为 send_to_c2c 的 openid
            await bot.send_to_c2c(
                openid=openid,
                message="重启完毕",
            )
            logger.info(
                "已向 {} 发送重启完毕",
                openid,
            )
        except Exception:
            logger.exception(
                "发送重启完毕失败: {}",
                openid,
            )
        finally:
            await _clear_restart_pending_async()


manage_command = on_message(
    priority=20,
    block=False
)


async def _panel_get(path):
    return await asyncio.to_thread(request_manager, "GET", path)


async def _panel_post(path, body=None):
    return await asyncio.to_thread(request_manager, "POST", path, body)


def _management_api_error(data, fallback):
    if not isinstance(data, dict):
        return fallback + "（桌面端返回无效响应）"
    if data.get("ok") is False:
        return str(data.get("msg") or fallback)
    return None


def _help_text():
    return (
        "【私聊命令】\n"
        "status 机器人状态\n"
        "restart 重启机器人\n"
        "groups 群列表\n"
        "channels 频道列表\n"
        "users 权限列表\n"
        "backfill 补发状态\n"
        "backfill <on/off/run/clear/refresh> 开/关/触发/清空/刷新\n"
        "timer 查看定时消息状态\n"
        "timer <on/off/clear> 开/关/清空定时消息\n"
        "relay list 查看可转发群列表\n"
        "relay {序号1} {序号2} 选择多个目标群，下一条消息转发\n"
        "relay all 选择全部已启用群，下一条消息转发\n"
        "register {QQ号} {昵称} 用户私聊提交注册申请\n"
        "list 查看命令清单"
    )


def _group_help_text():
    return (
        "【群聊命令】\n"
        "register-group {群号} {群名称} 群内提交群注册申请\n"
        "relay list 查看可转发的QQ群列表\n"
        "relay {序号} 将下一条消息转发到指定QQ群\n"
        "list 查看命令清单"
    )


async def _cmd_status():
    data = await _panel_get("/api/status")
    error = _management_api_error(data, "读取机器人状态失败")
    if error:
        return error

    settings = await _panel_get("/api/settings")
    error = _management_api_error(settings, "读取设置失败")
    if error:
        return error

    lines = ["【机器人状态】"]

    if data.get("running"):
        lines.append("运行中 PID:" + str(data.get("pid") or "?"))
    else:
        lines.append("已停止")

    stats = data.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    lines.append(
        "今日转发{}条 累计{}条 失败{}条".format(
            stats.get("today_forwarded", 0),
            stats.get("total_forwarded", 0),
            stats.get("total_failed", 0),
        )
    )
    health = data.get("health")
    retry = health.get("retry") if isinstance(health, dict) else None
    if isinstance(retry, dict) and retry.get("pending_entries"):
        lines.append("待重试：{}条（{}个群）".format(
            retry.get("pending_entries", 0),
            retry.get("pending_groups", 0),
        ))

    lines.append("")

    enabled = settings.get("backfill_enabled")
    if enabled is None:
        enabled = True
    lines.append("离线补发：" + ("开启" if enabled else "关闭"))
    lines.append("开机自启：" + ("开启" if data.get("autostart") else "关闭"))

    return "\n".join(lines)


async def _cmd_list(kind):
    """查询群/频道/用户列表（复用面板 /api/settings 的配置与 getter）"""
    if kind == "qq_group_openids":
        title, id_key, desc_key = "群列表", "openid", "name"
    elif kind == "discord_channels":
        title, id_key, desc_key = "频道列表", "id", "name"
    else:
        title, id_key, desc_key = "权限列表", "openid", "name"

    settings = await _panel_get("/api/settings")
    error = _management_api_error(settings, "读取列表失败")
    if error:
        return error
    items = settings.get(kind) or []

    lines = [f"【{title}】"]

    for item in items:
        if not isinstance(item, dict):
            continue
        flag = "（启用）" if item.get("enabled") else "（未启用）"
        desc = str(item.get(desc_key) or "").strip()
        if not desc:
            # 群/用户条目未注册时无 name，回退显示原 remark（如"自动发现，点击启用"）
            desc = str(item.get("remark") or "").strip()
        ident = str(item.get(id_key) or "?")
        lines.append(flag + (desc + " " if desc else "") + ident)

    lines.append(f"（总计{len(items)}个）")

    return "\n".join(lines)


async def _cmd_backfill(args):
    arg = args[0] if args else ""

    if not arg:
        # 查询：总开关 + 单次补发上限 + 待补发缺口数
        settings = await _panel_get("/api/settings")
        error = _management_api_error(settings, "读取补发设置失败")
        if error:
            return error

        enabled = settings.get("backfill_enabled")
        if enabled is None:
            enabled = True

        pending = await _panel_get("/api/backfill/pending")
        if isinstance(pending, dict) and pending.get("ok"):
            pending_text = str(pending.get("total", 0))
        else:
            pending_text = "未知"

        return (
            "【离线补发状态】\n"
            "当前"
            + ("开启" if enabled else "关闭")
            + "\n"
            "单次补发上限："
            + str(settings.get("backfill_limit", 10))
            + "条/频道\n"
            "待补发：" + pending_text
        )

    if arg in ("on", "off"):
        value = arg == "on"
        # 字段级接口：只提交 backfill_enabled，不再 GET 全量配置后
        # 回传，避免覆盖面板刚保存的其他字段；已处于目标状态时
        # 服务端不写盘并返回 changed=false，直接告知用户
        data = await _panel_post(
            "/api/settings/backfill-toggle",
            {"backfill_enabled": value},
        )
        if data.get("ok"):
            if data.get("changed") is False:
                return (
                    "离线补发已处于"
                    + ("开启" if value else "关闭")
                    + "状态"
                )
            return "已" + ("开启" if value else "关闭") + "离线补发"
        return data.get("msg") or "设置失败"

    if arg == "run":
        # 先查待补发缺口，无消息时直接告知，避免空触发
        pending = await _panel_get("/api/backfill/pending")
        if pending.get("ok") and not pending.get("total"):
            return "当前无可补发的消息"
        data = await _panel_post("/api/backfill/run")
        if data.get("ok"):
            return "补发已触发"
        return data.get("msg") or "补发触发失败"

    if arg == "clear":
        data = await _panel_post("/api/backfill/clear")
        if data.get("ok"):
            cleared = data.get("cleared", 0)
            # 记录都已推进到最新，无待清除内容
            if not cleared:
                return "当前无可清空的待补发"
            return (
                "已清空待补发（"
                + str(cleared)
                + " 个频道记录推进到最新）"
            )
        return data.get("msg") or "清空失败"

    if arg == "refresh":
        # 立即读取一次 Discord 实时状态（各频道待补发缺口）
        pending = await _panel_get("/api/backfill/pending")
        if not pending.get("ok"):
            return pending.get("msg") or "刷新失败"
        lines = ["【Discord 实时状态】"]
        channels = pending.get("channels") or {}
        lines.append("待补发合计：" + str(pending.get("total", 0)) + " 条")
        for info in list(channels.values())[:15]:
            if not isinstance(info, dict):
                continue
            count = info.get("count")
            name = info.get("name")
            if count is None or count < 0:
                lines.append(f"{name or '?'}：统计失败")
            elif count:
                lines.append(f"{name or '?'}：{count} 条")
        if len(channels) > 15:
            lines.append("…等共 " + str(len(channels)) + " 个频道")
        return "\n".join(lines)

    return "用法: backfill（查询）/ on/off / run / clear / refresh"


async def _cmd_timer(args):
    """查询或开关定时消息。具体内容和计划由管理面板维护。"""
    if not args:
        settings = await _panel_get("/api/settings")
        error = _management_api_error(settings, "读取定时消息设置失败")
        if error:
            return error
        config = settings.get("scheduled_message") or {}
        messages = [
            item for item in config.get("messages") or []
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ]
        content = str(messages[0].get("content") or "").strip() if messages else str(config.get("content") or "").strip()
        enabled = config.get("enabled") is True
        if messages:
            schedule = str(len(messages)) + " 条消息"
            send_time = messages[0].get("send_time") or messages[0].get("anchor_at") or "未设置"
        else:
            schedule = "未设置"
            send_time = config.get("send_time") or "09:00"
        return (
            "【定时消息】\n"
            "状态：" + ("开启" if enabled else "关闭") + "\n"
            "计划：" + schedule + "\n"
            "时间：" + str(send_time) + "\n"
            "目标群：" + str(len(config.get("target_groups") or [])) + " 个\n"
            "内容：" + (content if content else "（未设置）")
        )

    action = args[0].lower()
    if action in ("on", "off"):
        if len(args) != 1:
            return "用法: timer on 或 timer off"
        if action == "on":
            settings = await _panel_get("/api/settings")
            error = _management_api_error(settings, "读取定时消息设置失败")
            if error:
                return error
            config = settings.get("scheduled_message") or {}
            messages = config.get("messages") or []
            has_content = any(
                isinstance(item, dict) and str(item.get("content") or "").strip()
                for item in messages
            )
            if not has_content and not str(config.get("content") or "").strip():
                return "请先在管理面板设置定时消息内容"
            if not config.get("target_groups"):
                return "请先在管理面板设置定时消息目标群"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"enabled": action == "on"},
        )
        if data.get("ok"):
            return "已" + ("开启" if action == "on" else "关闭") + "定时消息"
        return data.get("msg") or "设置失败"

    if action == "clear":
        if len(args) != 1:
            return "用法: timer clear"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"content": "", "enabled": False},
        )
        if data.get("ok"):
            return "定时消息已关闭并清空内容"
        return data.get("msg") or "设置失败"

    return "仅支持：timer（查询）或 timer <on/off/clear>"


UNKNOWN_COMMAND_REPLY = "未知命令，输入 list 查看可用命令"


async def _dispatch(cmd, args):
    if cmd == "list":
        return _help_text()

    if cmd == "status":
        return await _cmd_status()

    if cmd == "groups":
        return await _cmd_list("qq_group_openids")

    if cmd == "channels":
        return await _cmd_list("discord_channels")

    if cmd == "users":
        return await _cmd_list("qq_user_openids")

    if cmd == "backfill":
        return await _cmd_backfill(args)

    if cmd == "timer":
        return await _cmd_timer(args)

    return UNKNOWN_COMMAND_REPLY


@manage_command.handle()
async def handle(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    # 群聊 list 由本处理器响应；relay / register-group 分别由各自处理器处理。
    # @机器人的未知消息提示由下方 group_at_hint 处理。
    if isinstance(
        event,
        GroupMessageCreateEvent
    ):
        message = event.get_plaintext().strip()

        if message == "list":
            await bot.send(
                event,
                _group_help_text()
            )

        return

    if not isinstance(
        event,
        C2CMessageCreateEvent
    ):
        return

    # 已被 QQ 私聊 relay 消费的消息不再按管理命令处理。
    # stop_propagation 是首选机制；它失效的运行环境下由该标记兜底，
    # 避免同一条消息既被转发/报错又被回复一次「未知命令」。
    if is_consumed(event):
        return

    message = event.get_plaintext()

    if not message:
        return

    parts = message.split()

    if not parts:
        return

    cmd = parts[0]

    args = parts[1:]

    # relay 私聊转发由 plugins/command.py 处理
    if cmd == "relay":
        return

    # register / register-group 注册指令由 plugins/registration.py 处理
    # （注册对未授权用户开放，不做白名单校验，避免这里回复"未授权"
    # 造成双重回复）；中文"注册"已删除，按未知命令正常回复
    if cmd in ("register", "register-group"):
        return

    # 白名单校验：与 relay 命令一致，仅允许管理面板勾选放行的用户
    active_users = await asyncio.to_thread(get_active_user_openids)
    if event.author.user_openid not in active_users:

        logger.info(
            "拒绝未授权用户私聊命令: {}",
            event.author.user_openid
        )

        await bot.send(
            event,
            "未授权使用管理命令（请在管理面板勾选放行）"
        )

        return

    logger.info(
        "收到私聊命令: {}",
        message
    )

    # restart 由管理面板结束本进程（taskkill 进程树），
    # 确认回复必须先于进程退出发出；同时写标记文件，
    # 供重启后的新进程 QQ 重连时回复"重启完毕"
    if cmd == "restart":

        await bot.send(
            event,
            "正在重启机器人..."
        )

        try:
            await _write_restart_pending_async(event.author.id, _qq_bot_id(bot))
        except asyncio.CancelledError:
            # The write helper waits for the atomic replace before propagating
            # cancellation; remove the orphan marker before leaving the handler.
            await _clear_restart_pending_async()
            raise

        try:
            result = await _panel_post("/api/bot/restart")
        except Exception as exc:
            result = {"ok": False, "msg": str(exc)}

        if not isinstance(result, dict) or not result.get("ok"):
            reason = (
                result.get("msg", "桌面端返回无效响应")
                if isinstance(result, dict)
                else "桌面端返回无效响应"
            )
            logger.warning("调用面板 restart 失败: {}", reason)
            # 重启未发生，清除标记避免之后误发"重启完毕"
            await _clear_restart_pending_async()
            await bot.send(event, "机器人重启失败，请检查桌面端日志")

        return

    try:

        reply = await _dispatch(
            cmd,
            args
        )

    except (ConnectionError, OSError, EOFError):

        logger.warning(
            "桌面管理进程不可达: {}",
            message
        )

        reply = "桌面端未运行或未响应，无法执行管理操作（请检查桌面端）"

    except Exception:

        logger.exception(
            "QQ管理命令执行失败: {}",
            message
        )

        reply = "管理命令执行失败（请查看日志）"

    if reply:

        await bot.send(
            event,
            reply
        )


# =====================================================
# 群内 @ 机器人的未知命令提示
#
# 只接受 QQ 适配器明确标记为机器人目标的 mention_user 消息段。
# 事件类型或空文本段无法证明提及对象是机器人，不能作为回退触发条件。
# 进行中的 relay pending 继续优先消费消息。
# =====================================================

group_at_hint = on_message(
    priority=10,
    block=False
)


@group_at_hint.handle()
async def handle_group_at_hint(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    if not isinstance(
        event,
        GroupMessageCreateEvent
    ):
        return

    if getattr(
        event.author,
        "bot",
        False
    ):
        # 机器人发送的消息（含转发）不提示
        return

    message = event.get_plaintext().strip()
    msg = event.get_message()
    segs = list(msg)

    bot_id = _qq_bot_id(bot)
    mentions_bot = False
    for segment in segs:
        if getattr(segment, "type", None) != "mention_user":
            continue

        data = getattr(segment, "data", None)
        if not isinstance(data, dict):
            continue

        mention_id = str(data.get("user_id") or "").strip()
        if bot_id and mention_id:
            if mention_id == bot_id:
                mentions_bot = True
                break
            # 目标 ID 已明确且不是机器人时，不采信可能不一致的辅助标记。
            continue

        # 兼容缺少目标 ID 的适配器事件。
        if data.get("is_bot") is True:
            mentions_bot = True
            break

    if not mentions_bot:
        # 事件类型可能覆盖普通成员提及；必须确认提及目标确实是机器人。
        return

    if qq_fwd.has_pending(
        event.group_openid,
        event.author.member_openid
    ):
        # 下一条消息继续由 QQ 群手动转发流程消费。
        return

    # 已知命令交给原有处理器，避免重复回复或打断待转发消息。
    if message == "list":
        return
    if qq_fwd.parse_relay(message)[0] is not None:
        return
    if (
        message == "register"
        or message.startswith("register ")
        or message == "register-group"
        or message.startswith("register-group ")
    ):
        return

    await bot.send(
        event,
        UNKNOWN_COMMAND_REPLY
    )
