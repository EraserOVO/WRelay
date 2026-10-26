import asyncio
import math
import os
import time

from nonebot import logger
from nonebot import on_message
from nonebot import get_driver
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupMessageCreateEvent,
)

from plugins import qq_fwd
from plugins.config import (
    get_active_user_openids,
    get_qq_group_entries,
    is_group_registered,
    USER_NOT_AUTHORIZED_TEXT,
    GROUP_NOT_REGISTERED_TEXT,
)
from plugins.json_io import (
    atomic_write_json,
    load_json,
    run_in_thread_to_completion,
)
from plugins.paths import data_path
from plugins.c2c_consumed import is_consumed
from plugins.qq_keyboard import (
    command_button,
    command_rows,
    send_with_buttons,
    send_titled_reply,
    titled_markdown,
)
from panel.local_bridge import request_manager


# =====================================================
# 私聊管理命令
#
# 白名单内的 QQ 用户私聊 Bot，通过 Windows 本机管道请求桌面管理进程
# 执行管理操作（状态/重启/模式/列表/补发）。
#
# 私聊命令统一以 /中文命令名触发；转发命令由 plugins/command.py 处理。
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
                message=Message([
                    MessageSegment.markdown(
                        titled_markdown("机器人重启", "重启完毕")
                    )
                ]),
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
        "WRelay菜单\n"
        "/运行状态 运行总览\n"
        "/转发 列表 手动转发目标与方法\n"
        "/定时消息 查询或开关定时消息\n"
        "/QQ账号与群 用户/群注册申请\n"
        "/重启 重启机器人"
    )


def _help_markdown():
    """私聊主菜单。"""
    return "# WRelay菜单\n点击下方按钮执行常用命令:"


def _private_help_rows():
    """私聊主菜单只保留手机上常用的直达操作。"""
    return command_rows([
        command_button("overview", "运行总览", "/运行状态"),
        command_button("manual-forward", "手动转发", "/转发 列表"),
        command_button("timer", "定时消息", "/定时消息"),
        command_button("registration", "注册申请", "/QQ账号与群"),
        command_button("restart", "重启", "/重启"),
    ], per_row=3)


def _section_rows(commands, back="/菜单"):
    buttons = [command_button(key, label, command) for key, label, command in commands]
    if back:
        buttons.append(command_button("back-menu", "返回菜单", back))
    return command_rows(buttons, per_row=3)


def _auto_forward_rows():
    return _section_rows([
        ("route-discord", "Discord→QQ", "/转发组 discord"),
        ("route-x", "X→QQ", "/转发组 x"),
        ("route-bilibili", "Bilibili→QQ", "/转发组 bilibili"),
    ])


def _accounts_groups_rows():
    return _section_rows([
        ("pending-users", "用户注册申请", "/注册申请 用户"),
        ("pending-groups", "群注册申请", "/注册申请 群"),
        ("register-user", "提交用户注册", "/注册"),
    ])


def _maintenance_rows():
    return _section_rows([
        ("backfill", "离线补发", "/补发状态"),
        ("timer", "定时消息", "/定时消息"),
        ("restart", "重启", "/重启"),
    ])


def _list_page_rows(command, page, total_pages, back="/QQ账号与群"):
    total_pages = max(1, int(total_pages))
    page = min(max(1, int(page)), total_pages)
    buttons = []
    buttons.append(command_button("page-refresh", "刷新", f"{command} {page}"))
    if page > 1:
        buttons.append(command_button("page-prev", "上一页", f"{command} {page - 1}"))
    if page < total_pages:
        buttons.append(command_button("page-next", "下一页", f"{command} {page + 1}"))
    buttons.append(command_button("page-back", "返回上级", back))
    return command_rows(buttons, per_row=3)


def _page_number(args):
    if not args:
        return 1
    raw = str(args[-1])
    if not raw.isascii() or not raw.isdecimal():
        return 1
    digits = raw.lstrip("0") or "0"
    if len(digits) > 9:
        return 999999999
    return max(1, int(digits))


async def _settings_or_error():
    data = await _panel_get("/api/settings")
    error = _management_api_error(data, "读取设置失败")
    return data, error


def _route_items(settings, kind):
    key = {
        "discord": "forwarding_groups",
        "x": "x_forwarding_groups",
        "bilibili": "bilibili_forwarding_groups",
        "qq": "qq_forwarding_groups",
    }.get(kind)
    return list(settings.get(key) or []) if key else []


async def _cmd_route_groups(kind, page=1):
    labels = {"discord": "Discord→QQ", "x": "X→QQ", "bilibili": "Bilibili→QQ", "qq": "QQ→QQ"}
    if kind not in labels:
        return "未知转发类型，请使用 discord、x、bilibili 或 qq。"
    settings, error = await _settings_or_error()
    if error:
        return error
    groups = _route_items(settings, kind)
    page_size = 6
    total_pages = max(1, (len(groups) + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    lines = [labels[kind] + "转发组", f"第 {page}/{total_pages} 页，共 {len(groups)} 个转发组"]
    for index, item in enumerate(groups[start:start + page_size], start=start + 1):
        name = str(item.get("name") or f"转发组 {index}")
        enabled = "启用" if item.get("enabled", True) else "停用"
        sources = item.get("channels" if kind == "discord" else "sources") or []
        targets = item.get("groups") or []
        lines.append(f"{index}. {name}（{enabled}）｜来源 {len(sources)}｜目标群 {len(targets)}")
    if not groups:
        lines.append("当前没有配置转发组")
    return "\n".join(lines)


async def _cmd_route_group_detail(kind, group_index, page=1):
    labels = {"discord": "Discord→QQ", "x": "X→QQ", "bilibili": "Bilibili→QQ", "qq": "QQ→QQ"}
    if kind not in labels:
        return "未知转发类型，请使用 discord、x、bilibili 或 qq。"
    settings, error = await _settings_or_error()
    if error:
        return error
    groups = _route_items(settings, kind)
    if group_index < 1 or group_index > len(groups):
        return "找不到对应的转发组"
    item = groups[group_index - 1]
    name = str(item.get("name") or f"转发组 {group_index}")
    sources = item.get("channels" if kind == "discord" else "sources") or []
    targets = item.get("groups") or []
    names_by_id = {
        str(value.get("id") or value.get("openid") or ""): (
            str(value.get("name") or value.get("remark") or value.get("id") or value.get("openid") or "")
            + ("（启用）" if value.get("enabled") else "（未启用）")
        )
        for key in ("discord_channels", "qq_group_openids")
        for value in settings.get(key) or [] if isinstance(value, dict)
    }
    x_names = {
        str(source.get("source") or "").lstrip("@"): (
            str(source.get("name") or source.get("source") or "")
            + ("（启用）" if source.get("enabled") else "（未启用）")
        )
        for source in settings.get("x_rss_sources") or [] if isinstance(source, dict)
    }
    bili_names = {
        str(source.get("id") or ""): (
            str(source.get("name") or source.get("value") or source.get("id") or "")
            + ("（启用）" if source.get("enabled") else "（未启用）")
        )
        for source in settings.get("bilibili_sources") or [] if isinstance(source, dict)
    }
    if kind == "discord":
        source_labels = [names_by_id.get(str(source), "") or str(source) for source in sources]
    elif kind == "qq":
        source_labels = []
    elif kind == "x":
        source_labels = [x_names.get(str(source), "") or str(source) for source in sources]
    else:
        source_labels = [bili_names.get(str(source), "") or str(source) for source in sources]
    target_labels = [names_by_id.get(str(target), "") or str(target) for target in targets]
    records = []
    if kind != "qq":
        records.extend(("来源", label) for label in source_labels)
    records.extend(("目标群", label) for label in target_labels)
    page_size = 8
    total_pages = max(1, (len(records) + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    lines = [f"{labels[kind]}转发组：{name}", "状态：" + ("启用" if item.get("enabled", True) else "停用"), f"第 {page}/{total_pages} 页，共 {len(records)} 项"]
    for role, label in records[start:start + page_size]:
        lines.append(f"{role}：{label}")
    if not records:
        lines.append("没有配置来源或目标群")
    return "\n".join(lines)


async def _cmd_bot_accounts(page=1):
    bots = await _panel_get("/api/bot/qq-config")
    error = _management_api_error(bots, "读取 Bot 账号失败")
    if error:
        return error
    settings, error = await _settings_or_error()
    if error:
        return error
    status = await _panel_get("/api/status")
    health = status.get("health") if isinstance(status, dict) else {}
    online = set(str(value) for value in (health or {}).get("qq_bots", []))
    groups = settings.get("qq_group_openids") or []
    bot_ids = [str(item.get("appid") or "") for item in bots.get("bots") or [] if isinstance(item, dict)]
    page_size = 8
    total_pages = max(1, (len(bot_ids) + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    lines = ["Bot账号及群归属", f"第 {page}/{total_pages} 页，共 {len(bot_ids)} 个账号"]
    for appid in bot_ids[start:start + page_size]:
        assigned = [g for g in groups if isinstance(g, dict) and str(g.get("bot_id") or "") == appid]
        lines.append(f"{appid}（{'在线' if appid in online else '离线'}，归属群 {len(assigned)} 个）")
    if not bot_ids:
        lines.append("当前没有配置 QQ Bot 账号")
    return "\n".join(lines)


async def _cmd_registration_requests(kind, page=1):
    from plugins.registration import load_registrations
    data = await asyncio.to_thread(load_registrations)
    key = "users" if kind in ("用户", "users") else "groups" if kind in ("群", "groups") else None
    if key is None:
        return "用法：/注册申请 用户 或 /注册申请 群"
    entries = list((data.get(key) or {}).items())
    page_size = 8
    total_pages = max(1, (len(entries) + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    title = "用户注册申请" if key == "users" else "群注册申请"
    lines = [title, f"第 {page}/{total_pages} 页，共 {len(entries)} 项"]
    for index, (openid, item) in enumerate(entries[start:start + page_size], start=start + 1):
        item = item if isinstance(item, dict) else {}
        label = item.get("nickname") if key == "users" else item.get("group_name")
        detail = f"QQ号 {item.get('qq_id')}" if item.get("qq_id") else ""
        code = str(item.get("application_code") or "未生成识别码")
        lines.append(
            f"{index}. {label or '未提供名称'} {detail} 识别码 {code}（{openid}）".strip()
        )
    if not entries:
        lines.append("当前没有待处理申请")
    return "\n".join(lines)


def _timer_rows():
    """定时消息查询后的操作按钮。"""
    return command_rows([
        command_button("timer-on", "开启", "/定时消息 开启"),
        command_button("timer-off", "关闭", "/定时消息 关闭"),
        command_button("timer-clear", "清空", "/定时消息 清空"),
    ], per_row=3)


def _backfill_rows():
    """补发状态与操作回复的快捷按钮。"""
    return command_rows([
        command_button("backfill-on", "开启", "/补发状态 开启"),
        command_button("backfill-off", "关闭", "/补发状态 关闭"),
        command_button("backfill-run", "执行", "/补发状态 执行"),
        command_button("backfill-clear", "清空", "/补发状态 清空"),
        command_button("backfill-refresh", "刷新", "/补发状态 刷新"),
    ], per_row=3)


async def _reply_rows(cmd, args=None, reply=None):
    """各管理结果页提供该功能的再次操作入口。"""
    args = args or []
    if cmd == "/转发组":
        page = _page_number(args[1:])
    elif cmd == "/转发组详情":
        page = _page_number(args[2:])
    elif cmd == "/注册申请":
        page = _page_number(args[1:])
    else:
        page = _page_number(args)
    if cmd == "/运行状态":
        buttons = [
            command_button("status-refresh", "刷新状态", "/运行状态"),
        ]
    elif cmd == "/自动转发":
        return _auto_forward_rows()
    elif cmd == "/QQ账号与群":
        return _accounts_groups_rows()
    elif cmd == "/维护":
        return _maintenance_rows()
    elif cmd == "/转发组":
        kind = args[0] if args else "discord"
        settings, error = await _settings_or_error()
        if error:
            return _section_rows([], back="/自动转发")
        groups = _route_items(settings, kind)
        page_size = 6
        total_pages = max(1, (len(groups) + page_size - 1) // page_size)
        page = min(max(1, page), total_pages)
        start = (page - 1) * page_size
        buttons = [
            command_button(
                f"route-detail-{idx}", f"查看{idx}组",
                f"/转发组详情 {kind} {idx}",
            )
            for idx in range(start + 1, min(start + page_size, len(groups)) + 1)
        ]
        if page > 1:
            buttons.append(command_button("route-prev", "上一页", f"/转发组 {kind} {page - 1}"))
        if page < total_pages:
            buttons.append(command_button("route-next", "下一页", f"/转发组 {kind} {page + 1}"))
        if kind == "discord":
            buttons.append(command_button("discord-channels", "Discord频道", "/频道列表"))
        buttons.append(command_button("route-back", "返回自动转发", "/自动转发"))
        return command_rows(buttons, per_row=3)
    elif cmd == "/群列表":
        settings, _ = await _settings_or_error()
        total = len(settings.get("qq_group_openids") or []) if isinstance(settings, dict) else 0
        return _list_page_rows("/群列表", page, max(1, (total + 7) // 8), "/QQ账号与群")
    elif cmd == "/频道列表":
        settings, _ = await _settings_or_error()
        total = len(settings.get("discord_channels") or []) if isinstance(settings, dict) else 0
        return _list_page_rows("/频道列表", page, max(1, (total + 7) // 8), "/转发组 discord")
    elif cmd == "/权限列表":
        settings, _ = await _settings_or_error()
        total = len(settings.get("qq_user_openids") or []) if isinstance(settings, dict) else 0
        return _list_page_rows("/权限列表", page, max(1, (total + 7) // 8), "/QQ账号与群")
    elif cmd == "/Bot账号":
        data = await _panel_get("/api/bot/qq-config")
        total = len(data.get("bots") or []) if isinstance(data, dict) else 0
        return _list_page_rows("/Bot账号", page, max(1, (total + 7) // 8), "/QQ账号与群")
    elif cmd == "/注册申请":
        kind = args[0] if args else "用户"
        from plugins.registration import load_registrations
        data = await asyncio.to_thread(load_registrations)
        key = "users" if kind in ("用户", "users") else "groups"
        total = len(data.get(key) or {})
        return _list_page_rows(f"/注册申请 {kind}", page, max(1, (total + 7) // 8), "/QQ账号与群")
    elif cmd == "/转发组详情":
        kind = args[0] if args else "discord"
        group_index = _page_number([args[1]]) if len(args) > 1 else 0
        settings, _ = await _settings_or_error()
        groups = _route_items(settings, kind) if isinstance(settings, dict) else []
        records = []
        if 0 < group_index <= len(groups):
            route = groups[group_index - 1]
            if kind != "qq":
                records.extend(route.get("channels" if kind == "discord" else "sources") or [])
            records.extend(route.get("groups") or [])
        total_pages = max(1, (len(records) + 7) // 8)
        page = min(max(1, page), total_pages)
        page_command = f"/转发组详情 {kind} {group_index}"
        buttons = []
        if page > 1:
            buttons.append(command_button("detail-prev", "上一页", f"{page_command} {page - 1}"))
        if page < total_pages:
            buttons.append(command_button("detail-next", "下一页", f"{page_command} {page + 1}"))
        buttons.append(command_button("detail-groups", "返回转发组", f"/转发组 {kind}"))
        buttons.append(command_button("detail-auto", "返回自动转发", "/自动转发"))
        return command_rows(buttons, per_row=3)
    elif cmd == "/补发状态":
        return _backfill_rows()
    elif cmd == "/定时消息":
        return _timer_rows()
    else:
        return None
    return command_rows(buttons, per_row=3)


def _reply_title(cmd, reply=None):
    first_line = str(reply or "").splitlines()
    known_titles = {
        "机器人状态",
        "自动转发",
        "QQ账号与群",
        "维护",
        "Bot账号及群归属",
        "用户注册申请",
        "群注册申请",
        "注册申请",
        "群列表",
        "频道列表",
        "权限列表",
        "离线补发状态",
        "Discord 实时状态",
        "定时消息",
    }
    if first_line and first_line[0].strip() in known_titles:
        return first_line[0].strip()
    return {
        "/运行状态": "机器人状态",
        "/自动转发": "自动转发",
        "/QQ账号与群": "QQ账号与群",
        "/维护": "维护",
        "/Bot账号": "Bot账号及群归属",
        "/转发组": "转发组列表",
        "/转发组详情": "转发组详情",
        "/注册申请": "注册申请",
        "/群列表": "群列表",
        "/频道列表": "频道列表",
        "/权限列表": "权限列表",
        "/补发状态": "离线补发状态",
        "/定时消息": "定时消息",
        "/重启": "机器人重启",
    }.get(cmd, "WRelay")


async def _cmd_status():
    data = await _panel_get("/api/status")
    error = _management_api_error(data, "读取机器人状态失败")
    if error:
        return error

    settings = await _panel_get("/api/settings")
    error = _management_api_error(settings, "读取设置失败")
    if error:
        return error

    lines = ["机器人状态"]

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

    route_counts = {
        "Discord→QQ": len(settings.get("forwarding_groups") or []),
        "X→QQ": len(settings.get("x_forwarding_groups") or []),
        "Bilibili→QQ": len(settings.get("bilibili_forwarding_groups") or []),
        "QQ→QQ": len(settings.get("qq_forwarding_groups") or []),
    }
    lines.append("转发组：" + "，".join(f"{name} {count}" for name, count in route_counts.items()))

    return "\n".join(lines)


async def _cmd_list(kind, page=1):
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

    page_size = 8
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    lines = [title, f"第 {page}/{total_pages} 页，共 {len(items)} 项"]

    for item in items[start:start + page_size]:
        if not isinstance(item, dict):
            continue
        flag = "（启用）" if item.get("enabled") else "（未启用）"
        desc = str(item.get(desc_key) or "").strip()
        if not desc:
            # 群/用户条目未注册时无 name，回退显示原 remark（如"自动发现，点击启用"）
            desc = str(item.get("remark") or "").strip()
        ident = str(item.get(id_key) or "?")
        bot_info = (
            " Bot " + str(item.get("bot_id") or "未分配")
            if kind == "qq_group_openids" else ""
        )
        lines.append(flag + (desc + " " if desc else "") + ident + bot_info)

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
            "离线补发状态\n"
            "当前"
            + ("开启" if enabled else "关闭")
            + "\n"
            "单次补发上限："
            + str(settings.get("backfill_limit", 10))
            + "条/频道\n"
            "待补发：" + pending_text
        )

    if arg in ("开启", "关闭"):
        value = arg == "开启"
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

    if arg == "执行":
        # 先查待补发缺口，无消息时直接告知，避免空触发
        pending = await _panel_get("/api/backfill/pending")
        if pending.get("ok") and not pending.get("total"):
            return "当前无可补发的消息"
        data = await _panel_post("/api/backfill/run")
        if data.get("ok"):
            return "补发已触发"
        return data.get("msg") or "补发触发失败"

    if arg == "清空":
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

    if arg == "刷新":
        # 立即读取一次 Discord 实时状态（各频道待补发缺口）
        pending = await _panel_get("/api/backfill/pending")
        if not pending.get("ok"):
            return pending.get("msg") or "刷新失败"
        lines = ["Discord 实时状态"]
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

    return "用法：/补发状态（查询）/ 开启 / 关闭 / 执行 / 清空 / 刷新"


SCHEDULED_TARGET_LABELS = {
    "groups": "指定群",
    "forward_group": "转发组",
    "all": "全部群",
}


def _scheduled_target_summary(messages, default_targets):
    """定时消息目标的一行摘要：区分「各自配置」与「统一默认」。"""
    messages = [item for item in messages if isinstance(item, dict)]
    if not messages:
        return "默认目标 {} 个群".format(len(default_targets or []))
    if all(
        str(item.get("target_mode") or "groups") == "groups"
        and not item.get("target_groups")
        for item in messages
    ):
        return "统一默认目标 {} 个群".format(len(default_targets or []))
    counts = {}
    for item in messages:
        mode = str(item.get("target_mode") or "groups")
        counts[mode] = counts.get(mode, 0) + 1
    parts = [
        "{} {}".format(SCHEDULED_TARGET_LABELS.get(mode, mode), counts[mode])
        for mode in ("groups", "forward_group", "all")
        if counts.get(mode)
    ]
    return "按消息配置（" + "、".join(parts) + "）"


def _scheduled_has_any_target(messages, default_targets):
    """开启前的目标校验：任一条消息能解析出目标即可（群启用状态在发送时判断）。"""
    if not messages:
        return bool(default_targets)
    for item in messages:
        mode = str(item.get("target_mode") or "groups")
        if mode == "all":
            return True
        if mode == "forward_group":
            if item.get("forward_groups"):
                return True
            continue
        if item.get("target_groups") or default_targets:
            return True
    return False


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
        targets = _scheduled_target_summary(messages, config.get("target_groups") or [])
        return (
            "定时消息\n"
            "状态：" + ("开启" if enabled else "关闭") + "\n"
            "计划：" + schedule + "\n"
            "时间：" + str(send_time) + "\n"
            "目标：" + targets + "\n"
            "内容：" + (content if content else "（未设置）")
        )

    action = args[0]
    if action in ("开启", "关闭"):
        if len(args) != 1:
            return "用法：/定时消息 开启 或 /定时消息 关闭"
        if action == "开启":
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
            if not _scheduled_has_any_target(
                [item for item in messages if isinstance(item, dict)],
                config.get("target_groups") or [],
            ):
                return "请先在管理面板为定时消息设置目标群或转发组"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"enabled": action == "开启"},
        )
        if data.get("ok"):
            return "已" + ("开启" if action == "开启" else "关闭") + "定时消息"
        return data.get("msg") or "设置失败"

    if action == "清空":
        if len(args) != 1:
            return "用法：/定时消息 清空"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"content": "", "enabled": False},
        )
        if data.get("ok"):
            return "定时消息已关闭并清空内容"
        return data.get("msg") or "设置失败"

    return "仅支持：/定时消息（查询）或 /定时消息 <开启/关闭/清空>"


PRIVATE_UNKNOWN_COMMAND_REPLY = "该命令未知，点击下方按钮展开菜单："


def _unknown_command_rows():
    return command_rows([
        command_button("menu", "展开菜单", "/菜单"),
    ], per_row=1)


def _stop_group_hint_propagation():
    """Keep group hint messages from reaching lower-priority matchers."""
    try:
        from nonebot.internal.matcher import current_matcher
        current_matcher.get().stop_propagation()
    except Exception:
        pass


async def _dispatch(cmd, args):
    if cmd == "/菜单":
        return _help_text()

    if cmd == "/运行状态":
        return await _cmd_status()

    if cmd == "/自动转发":
        return "自动转发\n查看各来源类型的转发组及状态。"

    if cmd == "/QQ账号与群":
        return "注册申请\n查看用户/群待审申请，或提交用户注册申请。"

    if cmd == "/维护":
        return "维护\n离线补发、定时消息与机器人重启。"

    if cmd == "/转发组":
        kind = args[0] if args else "discord"
        page = _page_number(args[1:]) if len(args) > 1 else 1
        return await _cmd_route_groups(kind, page)

    if cmd == "/转发组详情":
        if len(args) < 2 or not str(args[1]).isascii() or not str(args[1]).isdecimal():
            return "用法：/转发组详情 类型 序号"
        page = _page_number(args[2:]) if len(args) > 2 else 1
        return await _cmd_route_group_detail(args[0], int(args[1]), page)

    if cmd == "/Bot账号":
        return await _cmd_bot_accounts(_page_number(args))

    if cmd == "/注册申请":
        kind = args[0] if args else "用户"
        page = _page_number(args[1:]) if len(args) > 1 else 1
        return await _cmd_registration_requests(kind, page)

    if cmd == "/群列表":
        return await _cmd_list("qq_group_openids", _page_number(args))

    if cmd == "/频道列表":
        return await _cmd_list("discord_channels", _page_number(args))

    if cmd == "/权限列表":
        return await _cmd_list("qq_user_openids", _page_number(args))

    if cmd == "/补发状态":
        return await _cmd_backfill(args)

    if cmd == "/定时消息":
        return await _cmd_timer(args)

    return PRIVATE_UNKNOWN_COMMAND_REPLY


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

    # 群聊 /菜单 直接显示转发目标列表；转发 / 注册群由各自处理器处理。
    # 单独 @机器人 的目标列表提示由下方 group_at_hint 处理。
    if isinstance(
        event,
        GroupMessageCreateEvent
    ):
        message = event.get_plaintext().strip()

        if message == "/菜单":
            await qq_fwd.send_relay_list(bot, event)

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
    if cmd == "/转发" or cmd == "/私聊转发":
        return

    # /注册 /注册群 注册指令由 plugins/registration.py 处理
    # （注册对未授权用户开放，不做白名单校验，避免这里回复"未授权"
    # 造成双重回复）
    if cmd in ("/注册", "/注册群"):
        return

    # 白名单校验：与 relay 命令一致，仅允许管理面板勾选放行的用户
    active_users = await asyncio.to_thread(get_active_user_openids)
    if event.author.user_openid not in active_users:

        logger.info(
            "拒绝未授权用户私聊命令: {}",
            event.author.user_openid
        )

        await send_titled_reply(
            bot,
            event,
            "准入提示",
            USER_NOT_AUTHORIZED_TEXT,
        )

        return

    logger.info(
        "收到私聊命令: {}",
        message
    )

    # 菜单用带按钮的 markdown 消息：按钮点击后发送的就是现有命令文本，
    # 不引入新的解析路径；按钮消息发送失败时回退为原纯文本清单。
    if cmd == "/菜单":

        await send_with_buttons(
            bot,
            event,
            _help_markdown(),
            _private_help_rows(),
            fallback=_help_text(),
        )

        return

    # restart 由管理面板结束本进程（taskkill 进程树），
    # 确认回复必须先于进程退出发出；同时写标记文件，
    # 供重启后的新进程 QQ 重连时回复"重启完毕"
    if cmd == "/重启":

        await send_titled_reply(
            bot,
            event,
            "机器人重启",
            "正在重启机器人...",
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
            await send_titled_reply(
                bot,
                event,
                "机器人重启",
                "机器人重启失败，请检查桌面端日志",
            )

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

    if reply == PRIVATE_UNKNOWN_COMMAND_REPLY:

        rows = _unknown_command_rows()
        # 不认识的具体命令追加成按原文执行的按钮（enter 模式），误触也是原命令，
        # 不会改动原有命令语义。
        if cmd.startswith("/") and len(cmd) <= 20 and len(rows) < 4:
            rows = rows + command_rows([
                command_button("try-cmd", cmd, cmd),
            ], per_row=3)

        await send_titled_reply(
            bot,
            event,
            "WRelay菜单",
            reply,
            rows,
        )

    elif reply:

        # 管理结果页附上对应快捷操作，省去手输常用命令。
        rows = await _reply_rows(cmd, args, reply)

        if rows:

            await send_titled_reply(
                bot,
                event,
                _reply_title(cmd, reply),
                reply,
                rows,
            )

        else:

            await send_titled_reply(
                bot,
                event,
                _reply_title(cmd, reply),
                reply,
            )


# =====================================================
# 群内 @ 机器人显示转发目标列表
#
# 优先使用 QQ 事件的 to-me 判定，同时兼容不同适配器版本的提及消息段。
# 只有「单独 @ 机器人、不附带任何内容」才回应；普通聊天、@其他成员、
# @机器人后带文字/附件/引用以及未知命令都不回复。
# =====================================================

_MENTION_SEGMENT_TYPES = ("mention_user", "at", "mention", "at_user", "to_me")


def _is_mention_segment(segment):
    return str(getattr(segment, "type", None) or "").lower() in _MENTION_SEGMENT_TYPES


def _has_mention_content(event, segs, message):
    """@机器人 的消息里，除提及段和空白文字外是否还有别的内容。"""
    if message:
        return True
    if getattr(event, "reply", None) is not None:
        return True
    if getattr(event, "attachments", None) or []:
        return True
    for segment in segs:
        segment_type = str(getattr(segment, "type", None) or "").lower()
        if segment_type in _MENTION_SEGMENT_TYPES:
            continue
        if segment_type != "text":
            return True
        data = getattr(segment, "data", None)
        text = str(data.get("text", "")) if isinstance(data, dict) else str(segment)
        if text.strip():
            return True
    return False


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
    is_tome = getattr(event, "is_tome", None)
    try:
        adapter_mentions_bot = bool(is_tome() if callable(is_tome) else is_tome)
    except Exception:
        adapter_mentions_bot = False
    if adapter_mentions_bot:
        # 适配器的 to-me 判定使用自己的账号标识，可能与 bot.self_id
        # 的格式不同；该判定也能覆盖适配器未暴露 mention_user 段的事件。
        mentions_bot = True

    for segment in segs:
        segment_type = str(getattr(segment, "type", None) or "").lower()
        if segment_type == "to_me":
            mentions_bot = True
            break
        if segment_type not in ("mention_user", "at", "mention", "at_user"):
            continue
        data = getattr(segment, "data", None)
        if not isinstance(data, dict):
            continue

        # is_bot 是适配器提供的直接目标标记，优先于可能采用另一套
        # 标识格式的 user_id/self_id 比较。
        if data.get("is_bot") is True:
            mentions_bot = True
            break

        mention_id = str(
            data.get("user_id") or data.get("qq") or data.get("target")
            or data.get("openid") or data.get("id") or ""
        ).strip()
        if bot_id and mention_id:
            if mention_id == bot_id:
                mentions_bot = True
                break

    if not mentions_bot:
        # 事件类型可能覆盖普通成员提及；必须确认提及目标确实是机器人。
        if any(_is_mention_segment(segment) for segment in segs):
            logger.debug(
                "忽略未确认目标的群聊提及: is_tome={} segment_types={}",
                adapter_mentions_bot,
                [str(getattr(segment, "type", None) or "") for segment in segs],
            )
        return

    # 群聊只对「单独 @ 机器人」作出回应，避免打断正常聊天。
    if _has_mention_content(event, segs, message):
        logger.debug(
            "忽略附带内容的群聊 @机器人消息: plain_length={} mention_segments={}",
            len(message),
            sum(1 for segment in segs if _is_mention_segment(segment)),
        )
        return

    logger.info(
        "收到群聊单独 @机器人消息: mention_segments={}",
        sum(1 for segment in segs if _is_mention_segment(segment)),
    )

    # 群未登记：单独 @ 机器人属于「功能请求」，提示先登记群，
    # 不展示转发目标列表；普通群消息、@其他成员和 @机器人后带内容已在上方排除。
    registered = await asyncio.to_thread(
        is_group_registered, event.group_openid, _qq_bot_id(bot)
    )
    if not registered:
        await send_titled_reply(
            bot,
            event,
            "群准入提示",
            GROUP_NOT_REGISTERED_TEXT,
        )
        _stop_group_hint_propagation()
        return
    await qq_fwd.send_relay_list(bot, event)
    _stop_group_hint_propagation()
