import asyncio
import secrets
import time

from nonebot import logger
from nonebot import on_message
from nonebot.adapters import Bot, Event

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupMessageCreateEvent,
)

from plugins.config import (
    get_active_user_openids,
    get_qq_group_entries,
    is_user_registered,
)
from plugins.json_io import (
    load_json,
    run_in_thread_to_completion,
    update_json,
)
from plugins.paths import data_path
from plugins.c2c_consumed import is_consumed
from plugins.qq_keyboard import send_titled_reply


# =====================================================
# QQ 用户/群 注册审核
#
# 自动发现的 openid 只记录（data/qq_group_openids.json /
# data/qq_user_openids.json），不进入面板同步待选名单；只有
# 主动注册后（本模块）才进入面板同步弹窗的待审核名单。
#
# 注册数据独立存储（data/qq_registrations.json），不混入白名单：
#   {
#     "users":  {"<user_openid>":  {"qq_id": "...", "nickname": "...",
#                                    "application_code": "...", "time": ...}},
#     "groups": {"<group_openid>": {"qq_id": "...", "group_name": "...",
#                                   "operator_openid": "...", "bot_id": "...",
#                                   "application_code": "...", "time": ...}}
#   }
# 以 openid 为键，同一 openid 同时只有一条注册记录（重新注册覆盖旧申请；
# 被拒绝后重新注册会再次出现在审核列表）。
#
# 注册 ≠ 授权：qq_user_openids / qq_group_openids 仍是唯一权限来源。
# 审核通过只是按现有同步流程把 openid 加入 settings（默认禁用），
# 是否放行仍由管理员在面板勾选。
# =====================================================

REGISTRATIONS_FILE = data_path("qq_registrations.json")
_APPLICATION_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _new_application_code(data):
    """生成跨用户/群申请唯一、易辨认的人工核对码。"""
    used = set()
    for kind in ("users", "groups"):
        bucket = data.get(kind, {})
        if not isinstance(bucket, dict):
            continue
        for entry in bucket.values():
            if isinstance(entry, dict):
                code = str(entry.get("application_code") or "").strip().upper()
                if code:
                    used.add(code)
    while True:
        raw = "".join(secrets.choice(_APPLICATION_CODE_ALPHABET) for _ in range(8))
        code = raw[:4] + "-" + raw[4:]
        if code not in used:
            return code


def load_registrations():
    """读取注册申请 dict（缺失/损坏返回空结构）"""
    data = load_json(REGISTRATIONS_FILE, default={})
    if not isinstance(data, dict):
        data = {}
    for kind in ("users", "groups"):
        if not isinstance(data.get(kind), dict):
            data[kind] = {}
    return data


def register_user(user_openid, qq_id="", nickname=""):
    """提交/更新用户注册申请，绑定当前 User OpenID，返回申请识别码。"""
    openid = str(user_openid or "").strip()
    if not openid:
        return False
    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        for kind in ("users", "groups"):
            if not isinstance(data.get(kind), dict):
                data[kind] = {}
        previous = data["users"].get(openid)
        previous = previous if isinstance(previous, dict) else {}
        code = (
            str(previous.get("application_code") or "").strip()
        ) or _new_application_code(data)
        data["users"][openid] = {
            "qq_id": str(qq_id).strip() or str(previous.get("qq_id") or "").strip(),
            "nickname": str(nickname).strip() or str(previous.get("nickname") or "").strip(),
            "application_code": code,
            "time": int(time.time()),
        }
        return code

    return update_json(REGISTRATIONS_FILE, mutate, default={}, indent=4)


def _qq_bot_id(bot):
    return str(getattr(bot, "self_id", "") or getattr(bot, "id", "") or "").strip()


def register_group(
    group_openid, qq_id="", group_name="", operator_openid="", bot_id=""
):
    """提交/更新群注册申请，绑定当前 Group OpenID 与操作人，返回识别码。"""
    openid = str(group_openid or "").strip()
    if not openid:
        return False
    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        for kind in ("users", "groups"):
            if not isinstance(data.get(kind), dict):
                data[kind] = {}
        previous = data["groups"].get(openid)
        previous = previous if isinstance(previous, dict) else {}
        code = (
            str(previous.get("application_code") or "").strip()
        ) or _new_application_code(data)
        data["groups"][openid] = {
            "qq_id": str(qq_id).strip() or str(previous.get("qq_id") or "").strip(),
            "group_name": str(group_name).strip() or str(previous.get("group_name") or "").strip(),
            "operator_openid": str(operator_openid or "").strip(),
            "bot_id": str(bot_id or "").strip(),
            "application_code": code,
            "time": int(time.time()),
        }
        return code

    return update_json(REGISTRATIONS_FILE, mutate, default={}, indent=4)


def remove_registration(kind, openid):
    """删除注册申请（审核通过/拒绝后由面板调用）；返回是否存在"""
    if kind not in ("users", "groups"):
        return False
    openid = str(openid or "").strip()
    if not openid:
        return False
    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        bucket = data.setdefault(kind, {})
        if not isinstance(bucket, dict):
            data[kind] = bucket = {}
        return bucket.pop(openid, None) is not None

    existed = update_json(
        REGISTRATIONS_FILE,
        mutate,
        default={},
        indent=4,
        write_if=bool,
    )
    return existed


# =====================================================
# QQ 注册指令
#
# 用户注册（私聊）：/注册 —— 任何用户可用
#   （注册 ≠ 授权，审核通过后仍需管理员在面板勾选放行）
# 群注册（群聊）：/注册群 —— 仅白名单用户可用
#   （qq_user_openids 是唯一权限来源；QQ 群消息事件中发送者
#   标识为 member_openid，按其校验白名单）
#
# 仅识别带斜杠的中文注册命令；英文旧命令不再触发注册。
# /转发前缀消息由 plugins/command.py 处理；注册前缀消息由本模块
# 处理（manage.py 已跳过，避免双重回复）。
# =====================================================

register_command = on_message(
    priority=15,
    block=False
)


@register_command.handle()
async def handle_register(
    bot: Bot,
    event: Event
):

    if not isinstance(
        bot,
        QQBot
    ):
        return

    # 已被 QQ 私聊 relay 消费的消息不再触发注册（stop_propagation 的兜底）。
    if is_consumed(event):
        return

    message = event.get_plaintext().strip()

    if message == "/注册" or message.startswith("/注册 "):

        if not isinstance(event, C2CMessageCreateEvent):
            # 用户注册只接受私聊提交
            await send_titled_reply(
                bot,
                event,
                "用户注册",
                "请私聊机器人点击菜单中的“提交用户注册”按钮；无需填写 QQ 号或昵称。",
            )
            return

        await _handle_user_register(bot, event, message)

    elif message == "/注册群" or message.startswith("/注册群 "):

        if not isinstance(event, GroupMessageCreateEvent):
            # 群注册只接受群聊提交（绑定当前群 openid）
            await send_titled_reply(
                bot,
                event,
                "群注册",
                "请在目标群点击菜单中的“注册本群”按钮；无需填写群号或群名称。",
            )
            return

        await _handle_group_register(bot, event, message)


async def _handle_user_register(bot: QQBot, event: C2CMessageCreateEvent, message):

    parts = message.split(maxsplit=2)
    # 菜单按钮发送裸 /注册 即提交申请。用户也可附带可选资料，
    # 但不要求提供 QQ 号或昵称；申请身份以当前消息的 User OpenID 为准。
    qq_id = parts[1].strip() if len(parts) >= 3 else ""
    nickname = parts[2].strip() if len(parts) >= 3 else ""
    if not qq_id.isdigit() or not nickname:
        qq_id = nickname = ""

    openid = event.author.user_openid

    # 与群注册一致：已登记用户不再重复提交申请，待审核申请重复提交时返回原识别码。
    # 登记唯一来源为 settings.json 的 qq_user_openids（不看勾选状态）。
    if await asyncio.to_thread(is_user_registered, openid):
        await send_titled_reply(
            bot,
            event,
            "用户注册",
            "你的 QQ 账号已经登记，无需重复申请；如需调整权限，请联系管理员。",
        )
        return

    registrations = await asyncio.to_thread(load_registrations)
    pending = registrations.get("users", {}).get(openid)
    if isinstance(pending, dict):
        application_code = str(pending.get("application_code") or "").strip()
        if application_code:
            await send_titled_reply(
                bot,
                event,
                "用户注册",
                "你的账号已有待审核申请。申请识别码：{}。请将此码发给管理员以便核对。".format(
                    application_code
                ),
            )
            return

    application_code = await run_in_thread_to_completion(
        register_user, openid, qq_id, nickname
    )

    logger.info(
        "[QQ 用户注册] openid={} qq={} 昵称={}",
        openid,
        qq_id,
        nickname,
    )

    await send_titled_reply(
        bot,
        event,
        "用户注册",
        _registration_submitted_text(application_code)
    )


async def _handle_group_register(bot: QQBot, event: GroupMessageCreateEvent, message):

    parts = message.split(maxsplit=2)
    # 菜单按钮发送裸 /注册群 即提交当前群申请；群身份直接取事件 OpenID。
    # 群号/名称作为可选资料，不参与申请归属或识别。
    qq_id = parts[1].strip() if len(parts) >= 3 else ""
    group_name = parts[2].strip() if len(parts) >= 3 else ""
    if not qq_id.isdigit() or not group_name:
        qq_id = group_name = ""

    # 权限校验：仅白名单用户可提交群注册（qq_user_openids 唯一权限来源）
    operator_openid = event.author.member_openid
    active_users = await asyncio.to_thread(get_active_user_openids)

    if operator_openid not in active_users:

        logger.info(
            "拒绝非白名单用户群注册: {} @ 群 {}",
            operator_openid,
            event.group_openid,
        )

        await send_titled_reply(
            bot,
            event,
            "群注册",
            "仅白名单用户可提交群注册申请。请先私聊发送 /注册 提交用户注册申请，"
            "等待管理员审核并获授权后再申请群注册。",
        )
        return

    group_openid = str(event.group_openid or "").strip()
    bot_id = _qq_bot_id(bot)
    registered_groups = await asyncio.to_thread(get_qq_group_entries)
    if any(
        str(item.get("openid") or "").strip() == group_openid
        and (
            not str(item.get("bot_id") or "").strip()
            or str(item.get("bot_id") or "").strip() == bot_id
        )
        for item in registered_groups
        if isinstance(item, dict)
    ):
        await send_titled_reply(
            bot,
            event,
            "群注册",
            "本群已经登记，无需重复申请；如需调整，请联系管理员。",
        )
        return

    registrations = await asyncio.to_thread(load_registrations)
    pending = registrations.get("groups", {}).get(group_openid)
    if isinstance(pending, dict):
        application_code = str(pending.get("application_code") or "").strip()
        if application_code:
            await send_titled_reply(
                bot,
                event,
                "群注册",
                "本群已有待审核申请。申请识别码：{}。请将此码发给管理员以便核对。".format(
                    application_code
                ),
            )
            return

    application_code = await run_in_thread_to_completion(
        register_group,
        group_openid,
        qq_id,
        group_name,
        operator_openid,
        bot_id,
    )

    logger.info(
        "[QQ 群注册] group_openid={} 群号={} 群名={} 操作人={} Bot={}",
        event.group_openid,
        qq_id,
        group_name,
        operator_openid,
        bot_id,
    )

    await send_titled_reply(
        bot,
        event,
        "群注册",
        _registration_submitted_text(application_code, group=True)
    )


def _registration_submitted_text(application_code, group=False):
    code = str(application_code or "").strip()
    prefix = "群注册申请" if group else "注册申请"
    if not code:
        return "无法生成申请识别码，请稍后重新提交或联系管理员。"
    return (
        "{}已提交，等待管理员审核。申请识别码：{}。"
        "请将此码发给管理员以便核对；识别码不代表授权。"
    ).format(prefix, code)
