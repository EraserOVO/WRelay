"""Discord 自动转发共用的消息准备流程。

实时转发、启动补发和失败重试都使用同一套 Discord 消息渲染与翻译规则，
这里只负责把规范化消息准备成可交给 sender 的文本段和媒体项；目标群、
游标、统计与重试仍由各自调用方负责。
"""

from plugins.config import get_channel_filter, get_channel_translation_config
from plugins.fetch import build_parts
from plugins.filter import get_enabled_message_types
from plugins.translation import translate_text_parts


async def prepare_discord_message(
    message,
    channel_id,
    message_id,
    source_label="自动转发来自",
    filter_config=None,
):
    """渲染并翻译一条 Discord 消息，返回 ``(text_parts, media_items)``。

    消息经过当前消息类型筛选后没有可发送内容时返回 ``None``；调用方
    应将这类消息标记为已处理，但不要计入发送成功。``filter_config``
    可由实时/补发调用方传入，避免在一次处理内重复读取配置。
    """
    if filter_config is None:
        filter_config = get_channel_filter(channel_id)
    translation_config = get_channel_translation_config(channel_id)

    text_parts, media_items, has_content = await build_parts(
        message,
        source_label=source_label,
        enabled_types=get_enabled_message_types(filter_config),
        show_datetime=translation_config.get("show_datetime") is True,
    )
    if not has_content:
        return None

    text_parts = await translate_text_parts(
        text_parts,
        ("discord", str(channel_id), str(message_id)),
        skip_leading_parts=1,
        config=translation_config,
    )
    return text_parts, media_items
