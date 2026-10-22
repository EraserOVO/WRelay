"""QQ 按钮回调（INTERACTION_CREATE）处理。

回调按钮（action.type=1）被点击后，平台通过事件通道下发 INTERACTION_CREATE。
本模块做两件事：

1. 立即调用 PUT /interactions/{id} 回应，否则客户端会一直转圈直到超时；
2. 把按钮携带的 data（就是命令文本）还原成一条消息事件，交给现有命令处理器。
   因此按钮点击与手输命令共用同一套权限与执行逻辑，回复以「被动回复」的形式
   落在按钮所在消息下，用户账号本身不会多出一条消息。

需要机器人在开放平台订阅 interaction 事件（QQ_BOTS 的 intent.interaction = true）。

实测结论：开放平台「接入方式」为 WebSocket 时，点击回调按钮客户端直接提示
「请求第三方失败」，机器人收不到 INTERACTION_CREATE——平台会把回调点击发往
开发者服务器（Webhook 地址），长连接模式下没有这个地址。因此本模块只在把接入
方式切成 Webhook（需要公网 HTTPS 服务）时才有意义，默认配置
（QQ_BUTTON_MODE=enter）不会走这条路径。
"""

import time

from nonebot import logger
from nonebot import on_notice
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq import InteractionCreateEvent
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from nonebot.adapters.qq.event import GroupMessageCreateEvent
from nonebot.adapters.qq.models import FriendAuthor
from nonebot.adapters.qq.models import GroupMemberAuthor

interaction_handler = on_notice(priority=1, block=False)


def _command_text(event):
    """取出按钮 data（命令文本）。"""
    resolved = getattr(event.data, "resolved", None)
    return str(getattr(resolved, "button_data", "") or "").strip()


def _message_id(event):
    """按钮所在消息的 id，用作被动回复的 msg_id。"""
    resolved = getattr(event.data, "resolved", None)
    return str(getattr(resolved, "message_id", "") or event.id)


def _build_event(event, text):
    """把交互事件还原成等价的私聊/群聊消息事件。

    返回 None 表示无法定位会话（例如频道场景），调用方只做回应不执行命令。
    """
    message_id = _message_id(event)
    timestamp = str(int(time.time()))

    group_openid = getattr(event, "group_openid", None)
    if group_openid:
        member_openid = getattr(event, "group_member_openid", None) or event.get_user_id()
        return GroupMessageCreateEvent(
            id=message_id,
            content=text,
            timestamp=timestamp,
            author=GroupMemberAuthor(
                id=member_openid,
                bot=False,
                member_openid=member_openid,
                member_role="member",
            ),
            group_id=group_openid,
            group_openid=group_openid,
            to_me=True,
        )

    user_openid = getattr(event, "user_openid", None) or event.get_user_id()
    if not user_openid:
        return None
    return C2CMessageCreateEvent(
        id=message_id,
        content=text,
        timestamp=timestamp,
        author=FriendAuthor(id=user_openid, user_openid=user_openid),
        to_me=True,
    )


@interaction_handler.handle()
async def handle_interaction(bot: Bot, event: InteractionCreateEvent):
    text = _command_text(event)

    logger.info(
        "收到按钮回调: chat_type={} user={} button={} data={}",
        getattr(event, "chat_type", None),
        event.get_user_id(),
        getattr(getattr(event.data, "resolved", None), "button_id", None),
        text,
    )

    # 先回应，避免客户端等待超时（回应失败不影响后续执行）
    try:
        await bot.put_interaction(interaction_id=event.id, code=0)
    except Exception:
        logger.exception("回应按钮交互失败")

    if not text:
        logger.warning("按钮回调缺少 data，忽略")
        return

    synthetic = _build_event(event, text)
    if synthetic is None:
        logger.warning("按钮回调无法定位会话，仅回应不执行: {}", text)
        return

    try:
        await bot.handle_event(synthetic)
    except Exception:
        logger.exception("按钮回调执行命令失败: {}", text)
