import os
import time
import re

import httpx

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
    load_json,
    atomic_write_json,
)
from plugins.paths import data_path


# =====================================================
# 私聊管理命令
#
# 白名单内的 QQ 用户私聊 Bot，通过管理面板的 8090 API
# 执行管理操作（状态/重启/模式/列表/补发），不在本进程
# 内重复实现管理逻辑；面板是唯一的管理入口。
#
# 无前缀触发：私聊直接发送命令词（如 status、backfill on），
# 帮助命令为 list；relay 消息由 plugins/command.py 处理。
# 面板只绑定 127.0.0.1，trust_env=False 避免走 HTTP_PROXY
# （代理仅用于 Discord 访问）。
# =====================================================

# 与管理面板 PANEL_PORT（panel/管理面板.pyw）保持一致
PANEL_API_BASE = "http://127.0.0.1:8090"
PANEL_AUTH_FILE = data_path("panel_auth.json")


def _panel_token():
    data = load_json(PANEL_AUTH_FILE, default=None)
    token = data.get("token") if isinstance(data, dict) else ""
    return token if isinstance(token, str) else ""

_panel_client = httpx.AsyncClient(
    base_url=PANEL_API_BASE,
    timeout=10.0,
    trust_env=False,
    headers={"X-WRelay-Token": _panel_token()},
)


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


def _write_restart_pending(openid):
    atomic_write_json(
        RESTART_PENDING_FILE,
        {
            "openid": openid,
            "time": int(time.time()),
        },
        indent=2,
    )


def _clear_restart_pending():
    try:
        os.remove(RESTART_PENDING_FILE)
    except OSError:
        pass


@get_driver().on_bot_connect
async def _confirm_restart(bot: Bot):
    """QQ 网关重连成功后，向触发 restart 的用户回复"重启完毕" """
    if not isinstance(bot, QQBot):
        return
    data = load_json(RESTART_PENDING_FILE, default=None)
    if not isinstance(data, dict) or not data.get("openid"):
        return
    # 过期的标记（如重启失败遗留）直接清除，不误发
    created = data.get("time")
    if (
        not isinstance(created, (int, float))
        or time.time() - created > RESTART_CONFIRM_MAX_AGE
    ):
        _clear_restart_pending()
        return
    try:
        # 与 adapter 回复路径一致，用 author.id 作为 send_to_c2c 的 openid
        await bot.send_to_c2c(
            openid=data["openid"],
            message="重启完毕",
        )
        logger.info(
            "已向 {} 发送重启完毕",
            data["openid"],
        )
    except Exception:
        logger.exception(
            "发送重启完毕失败: {}",
            data["openid"],
        )
    finally:
        _clear_restart_pending()


manage_command = on_message(
    priority=20,
    block=False
)


def _parse_response(resp):
    """把面板响应统一成 dict；非 2xx 也返回内容，便于展示面板的 msg"""
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if resp.status_code >= 400:
        data.setdefault("ok", False)
        data.setdefault("msg", f"面板返回 HTTP {resp.status_code}")
    return data


async def _panel_get(path):
    resp = await _panel_client.get(path)
    return _parse_response(resp)


async def _panel_post(path, body=None):
    resp = await _panel_client.post(path, json=body)
    return _parse_response(resp)


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
        "register {QQ号} {昵称} 用户私聊提交注册申请\n"
        "relay-list 查看可转发群列表\n"
        "relay {序号1} {序号2} 选择多个目标群，下一条消息转发\n"
        "relay all 选择全部已启用群，下一条消息转发\n"
        "timer 查看定时消息状态\n"
        "timer on/off 开启/关闭定时消息\n"
        "timer text {内容} 设置定时消息内容\n"
        "timer days {天数} [HH:MM] 每隔几天发送\n"
        "timer weeks {周数} [HH:MM] 每隔几周发送\n"
        "timer months {月数} [HH:MM] 每隔几月发送\n"
        "timer time {HH:MM} 设置发送时间\n"
        "timer groups 查看/设置定时消息目标群\n"
        "timer clear 清空定时消息内容\n"
        "list 查看命令清单"
    )


def _group_help_text():
    return (
        "【群聊命令】\n"
        "register-group {群号} {群名称} 群内提交群注册申请\n"
        "relay-list 查看可转发的QQ群列表\n"
        "relay {序号} 将下一条消息转发到指定QQ群\n"
        "list 查看命令清单"
    )


async def _cmd_status():
    data = await _panel_get("/api/status")
    settings = await _panel_get("/api/settings")

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


def _timer_time(value):
    if not isinstance(value, str) or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
        return None
    return value


async def _cmd_timer(args):
    """查询或字段级修改定时消息配置。"""
    if not args:
        settings = await _panel_get("/api/settings")
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
        if action == "on":
            settings = await _panel_get("/api/settings")
            config = settings.get("scheduled_message") or {}
            messages = config.get("messages") or []
            has_content = any(
                isinstance(item, dict) and str(item.get("content") or "").strip()
                for item in messages
            )
            if not has_content and not str(config.get("content") or "").strip():
                return "请先使用 timer text {内容} 设置定时消息内容"
            if not config.get("target_groups"):
                return "请先使用 timer groups {序号1} {序号2} 设置目标群"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"enabled": action == "on"},
        )
        if data.get("ok"):
            return "已" + ("开启" if action == "on" else "关闭") + "定时消息"
        return data.get("msg") or "设置失败"

    if action in ("text", "content", "set"):
        content = " ".join(args[1:]).strip()
        if not content:
            return "用法: timer text {内容}"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"content": content},
        )
        if data.get("ok"):
            return "定时消息内容已更新"
        return data.get("msg") or "设置失败"

    if action in ("days", "daily"):
        if len(args) not in (2, 3) or not args[1].isdigit():
            return "用法: timer days {天数} [HH:MM]（1-365）"
        every_days = int(args[1])
        if every_days < 1 or every_days > 365:
            return "天数必须是 1 到 365"
        send_time = _timer_time(args[2]) if len(args) == 3 else None
        if len(args) == 3 and send_time is None:
            return "时间必须是 HH:MM，例如 09:30"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {
                "schedule_mode": "days",
                "every_days": every_days,
                **({"send_time": send_time} if send_time else {}),
            },
        )
        if data.get("ok"):
            return "已设置为每 " + str(every_days) + " 天发送"
        return data.get("msg") or "设置失败"

    if action in ("weeks", "weekly", "week"):
        if len(args) not in (2, 3) or not args[1].isdigit():
            return "用法: timer weeks {周数} [HH:MM]（1-52）"
        every_weeks = int(args[1])
        if every_weeks < 1 or every_weeks > 52:
            return "周数必须是 1 到 52"
        send_time = _timer_time(args[2]) if len(args) == 3 else None
        if len(args) == 3 and send_time is None:
            return "时间必须是 HH:MM，例如 09:30"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {
                "schedule_mode": "weekly",
                "every_weeks": every_weeks,
                **({"send_time": send_time} if send_time else {}),
            },
        )
        if data.get("ok"):
            return "已设置为每 " + str(every_weeks) + " 周发送"
        return data.get("msg") or "设置失败"

    if action in ("months", "monthly", "month"):
        if len(args) not in (2, 3) or not args[1].isdigit():
            return "用法: timer months {月数} [HH:MM]（1-120）"
        every_months = int(args[1])
        if every_months < 1 or every_months > 120:
            return "月数必须是 1 到 120"
        send_time = _timer_time(args[2]) if len(args) == 3 else None
        if len(args) == 3 and send_time is None:
            return "时间必须是 HH:MM，例如 09:30"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {
                "schedule_mode": "monthly",
                "every_months": every_months,
                **({"send_time": send_time} if send_time else {}),
            },
        )
        if data.get("ok"):
            return "已设置为每 " + str(every_months) + " 月发送"
        return data.get("msg") or "设置失败"

    if action == "time":
        if len(args) != 2 or _timer_time(args[1]) is None:
            return "用法: timer time {HH:MM}"
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"send_time": args[1]},
        )
        if data.get("ok"):
            return "定时消息发送时间已设置为 " + args[1]
        return data.get("msg") or "设置失败"

    if action in ("interval", "every"):
        # 兼容旧命令：把秒数换算成天数，新的交互建议使用 timer days。
        if len(args) != 2 or not args[1].isdigit():
            return "用法: timer days {天数} [HH:MM]（旧 interval 也支持秒数）"
        interval = int(args[1])
        if interval < 60 or interval > 86400:
            return "旧间隔必须是 60 到 86400 秒"
        every_days = max(1, min(365, (interval + 86399) // 86400))
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {
                "schedule_mode": "days",
                "every_days": every_days,
            },
        )
        if data.get("ok"):
            return "旧间隔已换算为每 " + str(every_days) + " 天发送"
        return data.get("msg") or "设置失败"

    if action in ("groups", "group", "targets"):
        settings = await _panel_get("/api/settings")
        entries = [
            item for item in settings.get("qq_group_openids") or []
            if isinstance(item, dict) and item.get("openid")
        ]
        config = settings.get("scheduled_message") or {}
        selected = {
            str(openid)
            for openid in config.get("target_groups") or []
        }
        if len(args) == 1:
            if not entries:
                return "当前没有已审核的 QQ 群"
            lines = ["【定时消息目标群】"]
            for index, item in enumerate(entries, start=1):
                name = str(item.get("name") or item.get("remark") or "").strip()
                label = name or str(item["openid"])
                flags = []
                if str(item["openid"]) in selected:
                    flags.append("已选")
                if item.get("enabled") is not True:
                    flags.append("未启用")
                suffix = "（" + "，".join(flags) + "）" if flags else ""
                lines.append(f"{index}. {label}{suffix}")
            lines.append("用法：timer groups {序号1} {序号2}")
            return "\n".join(lines)

        indexes = args[1:]
        if not indexes or not all(index.isdigit() for index in indexes):
            return "用法: timer groups {序号1} {序号2}"
        target_groups = []
        seen = set()
        for index in indexes:
            position = int(index)
            if position < 1 or position > len(entries):
                return "无法找到对应的已审核群"
            openid = str(entries[position - 1]["openid"])
            if openid not in seen:
                target_groups.append(openid)
                seen.add(openid)
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"target_groups": target_groups},
        )
        if data.get("ok"):
            return "定时消息目标群已更新（" + str(len(target_groups)) + " 个）"
        return data.get("msg") or "设置失败"

    if action == "clear":
        data = await _panel_post(
            "/api/settings/scheduled-message",
            {"content": "", "enabled": False},
        )
        if data.get("ok"):
            return "定时消息已关闭并清空内容"
        return data.get("msg") or "设置失败"

    return (
        "用法: timer（查询）/ on/off / text {内容} / "
        "days {天数} [HH:MM] / weeks {周数} [HH:MM] / months {月数} [HH:MM] / "
        "time {HH:MM} / groups [序号...] / clear"
    )


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

    return "未知命令，输入 list 查看可用命令"


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

    # 群聊仅支持 list（查看群聊命令清单，不做白名单校验）；
    # 其余群消息保持静默，register-group 由 plugins/registration.py 处理
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
    if event.author.user_openid not in get_active_user_openids():

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

        _write_restart_pending(event.author.id)

        try:

            await _panel_post(
                "/api/bot/restart"
            )

        except Exception as exc:

            logger.warning(
                "调用面板 restart 失败: {}",
                exc
            )

            # 重启未发生，清除标记避免之后误发"重启完毕"
            _clear_restart_pending()

        return

    try:

        reply = await _dispatch(
            cmd,
            args
        )

    except httpx.HTTPError:

        logger.warning(
            "管理面板不可达: {}",
            message
        )

        reply = "管理面板未运行，无法执行管理操作（请先启动管理面板）"

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
# 本部署下 QQ 平台不下发 GROUP_AT_MESSAGE_CREATE 事件：@机器人的消息
# 以「整条消息只有空文本」的 GroupMessageCreateEvent 送达（@指向与
# @后的文字都不在 content / mentions 里，没有任何标记）。因此用该
# 形态识别被 @，命中时回复「未知命令，输入 list 查看可用命令」，
# 避免群里 @ 机器人后静默无响应。
#
# 正常文字消息、图片/语音等附件消息、@其他成员的消息、机器人自己的
# 消息都不会是这种形态，不会误触发；该用户存在进行中的 relay 转发
# （qq_fwd pending）时，本消息会被当作下一条转发内容消费，不提示。
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

    msg = event.get_message()
    segs = list(msg)

    if len(segs) != 1 or segs[0].type != "text":
        # 附件（图片/语音等）、@其他成员、表情等消息不提示
        return

    if (segs[0].data.get("text") or "").strip():
        # 有实际文本：交给 list / relay / register-group 等命令处理
        return

    if qq_fwd.has_pending(
        event.group_openid,
        event.author.member_openid
    ):
        return

    await bot.send(
        event,
        "未知命令，输入 list 查看可用命令"
    )
