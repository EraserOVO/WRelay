import asyncio
from io import BytesIO

from nonebot import get_bots
from nonebot import logger

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import Message as QQMessage
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.config import get_active_groups
from plugins.failure_notification import notify_delivery_failure
from plugins.media import make_text, make_media_link
from plugins.message_trace import record_delivery


# =====================================================
# QQ 发送
#
# 单一职责：把 (text_parts, media_items) 发给指定群（默认全部启用群）。
#   - 文字部分：拼成一条消息发送；超过 QQ 文本长度上限(4000字符)
#     时按 TEXT_CHUNK_LENGTH 分片为多条顺序发送
#   - 媒体部分：每条单独一条消息，避免 QQ 把富媒体卡片渲染到文字上方；
#     上传失败按 429/网络错误指数退避重试，仍失败则降级为文字 + 原链接
#   - 多群并行发送（asyncio.gather），一个群被限流不拖慢其他群
#   - send_relay_message 返回 {group_openid: 是否送达}，供 relay.py
#     按群记录去重 ID（失败的群不记录，重连补发时只补失败群，
#     已成功的群不会收到重复消息）
# =====================================================

RETRY_DELAY = 3.0
MAX_SEND_ATTEMPTS = 3
MAX_BACKOFF = 30.0
# 429 限流专用退避序列（第 1/2/3 次失败后的等待秒数）：
# 限流通常持续数秒到数十秒，比普通网络错误的 3→6→12 退避更保守
RATE_LIMIT_BACKOFFS = (3.0, 10.0, 30.0)
TEXT_CHUNK_LENGTH = 3900  # QQ 官方 bot 文本上限 4000 字符，留余量
EMOJI_TILE_SIZE = 64
EMOJI_TILE_GAP = 4
EMOJI_TILE_COLUMNS = 8


def get_qq_bot():

    bots = get_bots()

    for b in bots.values():

        if isinstance(
            b,
            QQBot
        ):

            return b

    return None


async def _record_trace(
    channel_id,
    message_id,
    text_parts,
    media_items,
    groups,
    ok_map,
    note="",
):
    """追踪写入绝不能影响真实投递。"""
    try:
        await record_delivery(
            channel_id,
            message_id,
            text_parts,
            media_items,
            groups,
            ok_map,
            note=note,
        )
    except Exception as exc:
        logger.warning("消息追踪记录失败: {}", exc)


def _is_rate_limit(exc):
    """判断异常是否为 QQ 频率限制(429)"""
    if getattr(exc, "status_code", None) == 429:
        return True
    try:
        response = getattr(exc, "response", None)
        if (
            response is not None
            and getattr(response, "status_code", None) == 429
        ):
            return True
    except Exception:
        pass
    msg = str(exc).lower()
    return (
        "429" in msg
        or "too many request" in msg
        or "rate limit" in msg
    )


def _retry_after(exc, fallback):
    """优先取响应头 Retry-After，否则用 fallback"""
    try:
        response = getattr(exc, "response", None)
        if response is not None:
            headers = getattr(response, "headers", None) or {}
            raw = headers.get("retry-after")
            if raw:
                return max(float(raw), fallback)
    except Exception:
        pass
    return fallback


async def _send_with_retry(qq_bot, group_openid, message, channel_id="", message_id=""):
    """发送一条消息，失败按指数退避重试（429/限流等待更长）；
    返回是否成功；失败日志附带 channel_id/message_id 便于定位原 Discord 消息"""
    delay = RETRY_DELAY
    rate_limited = False
    last_error = None
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        try:

            await qq_bot.send_to_group(

                group_openid=group_openid,

                message=message

            )

            return True

        except Exception as exc:

            is_rate = _is_rate_limit(exc)
            rate_limited = rate_limited or is_rate
            last_error = exc

            logger.warning(
                "发送失败(第{}次) 频道{} 消息{} 群{}: {}",
                attempt,
                channel_id or "-",
                message_id or "-",
                group_openid,
                exc
            )

            if attempt == MAX_SEND_ATTEMPTS:
                break

            if is_rate:
                # 429/限流：用专用退避序列（尊重 Retry-After 头），
                # 限流通常是暂时性的，值得多等而不是立刻放弃
                wait = _retry_after(
                    exc,
                    RATE_LIMIT_BACKOFFS[min(
                        attempt - 1,
                        len(RATE_LIMIT_BACKOFFS) - 1
                    )]
                )
            else:
                wait = _retry_after(exc, delay)
                delay = min(delay * 2, MAX_BACKOFF)
            await asyncio.sleep(wait)

    # 全部尝试失败：error 级别汇总，面板日志中更醒目
    logger.error(
        "发送失败已达上限({}次){} 频道{} 消息{} 群{}: {}",
        MAX_SEND_ATTEMPTS,
        "（限流）" if rate_limited else "（非限流）",
        channel_id or "-",
        message_id or "-",
        group_openid,
        last_error
    )

    return False


def split_long_text(text, limit=TEXT_CHUNK_LENGTH):
    """把长文本切成 ≤limit 的片段：优先按行切，单行超长再按字符兜底"""
    if len(text) <= limit:
        return [text]

    chunks = []
    buf = ""

    for line in text.splitlines(keepends=True):

        if buf and len(buf) + len(line) > limit:
            chunks.append(buf)
            buf = ""

        if len(line) > limit:
            # 超长单行（新闻全文常见）：按字符硬切
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]

        buf += line

    if buf:
        chunks.append(buf)

    return chunks


async def _send_to_groups(qq_bot, groups, message, channel_id="", message_id=""):
    """把同一条消息并行发到多个群；返回 {group_openid: 是否成功}"""
    results = await asyncio.gather(
        *(
            _send_with_retry(
                qq_bot,
                group,
                message,
                channel_id=channel_id,
                message_id=message_id,
            )
            for group in groups
        )
    )
    return dict(zip(groups, results))


async def send_text_parts(qq_bot, groups, text_parts, channel_id="", message_id=""):
    """发送文字到指定群；超长文本按 TEXT_CHUNK_LENGTH 分片；
    返回 {group_openid: 是否全部送达}"""
    ok_map = {group: True for group in groups}

    # QQ 官方接口一条消息只能上传一个图片媒体，且多图 Message 会被
    # 适配器取最后一张并把图片置于文字前面。Discord 自定义表情因此必须
    # 按原顺序拆成文字块/表情合成图块发送，连续表情合并为一张小图。
    if any(getattr(part, "type", "") == "file_image" for part in text_parts):
        return await _send_ordered_emoji_parts(
            qq_bot,
            groups,
            text_parts,
            channel_id=channel_id,
            message_id=message_id,
        )

    # QQ 表情是独立的 emoji 段，不能先拼成纯文本，否则会被丢弃。
    # 这类消息通常很短，保留原消息段一次性发送；纯文本仍走下面的
    # 分片逻辑，避免改变现有长文本转发行为。
    if any(getattr(part, "type", "") != "text" for part in text_parts):
        message = QQMessage([
            part.copy() if hasattr(part, "copy") else part
            for part in text_parts
        ])
        results = await _send_to_groups(
            qq_bot,
            groups,
            message,
            channel_id=channel_id,
            message_id=message_id,
        )
        for group, ok in results.items():
            if not ok:
                ok_map[group] = False
        return ok_map

    full_text = "".join(
        (getattr(part, "data", None) or {}).get("text", "")
        for part in text_parts
    )

    chunks = [
        chunk
        for chunk in split_long_text(full_text)
        if chunk
    ]

    if not chunks:
        return ok_map

    for chunk in chunks:

        message = QQMessage([make_text(chunk)])

        results = await _send_to_groups(
            qq_bot,
            groups,
            message,
            channel_id=channel_id,
            message_id=message_id,
        )

        for group, ok in results.items():
            if not ok:
                ok_map[group] = False

    return ok_map


def _compose_emoji_strip(segments):
    """把连续 Discord 表情合成为一张小尺寸 PNG，保持出现顺序。"""
    from PIL import Image

    if not segments:
        return None
    if len(segments) == 1:
        data = (getattr(segments[0], "data", None) or {}).get("content")
        return data if isinstance(data, bytes) else None

    images = []
    try:
        for segment in segments:
            data = (getattr(segment, "data", None) or {}).get("content")
            if not isinstance(data, bytes):
                continue
            with Image.open(BytesIO(data)) as source:
                # 合成图使用静态 PNG；单个表情走上面的原始字节路径，
                # 仍可保留 GIF 动画。
                source.seek(0)
                image = source.convert("RGBA")
                image.thumbnail(
                    (EMOJI_TILE_SIZE, EMOJI_TILE_SIZE),
                    Image.Resampling.LANCZOS,
                )
                tile = Image.new(
                    "RGBA",
                    (EMOJI_TILE_SIZE, EMOJI_TILE_SIZE),
                    (0, 0, 0, 0),
                )
                tile.paste(
                    image,
                    (
                        (EMOJI_TILE_SIZE - image.width) // 2,
                        (EMOJI_TILE_SIZE - image.height) // 2,
                    ),
                )
                images.append(tile)
    except Exception as exc:
        logger.warning("Discord 表情合成失败: {}", exc)
        return None

    if not images:
        return None

    columns = min(EMOJI_TILE_COLUMNS, len(images))
    rows = (len(images) + columns - 1) // columns
    width = columns * EMOJI_TILE_SIZE + (columns - 1) * EMOJI_TILE_GAP
    height = rows * EMOJI_TILE_SIZE + (rows - 1) * EMOJI_TILE_GAP
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for index, image in enumerate(images):
        col = index % columns
        row = index // columns
        canvas.paste(
            image,
            (
                col * (EMOJI_TILE_SIZE + EMOJI_TILE_GAP),
                row * (EMOJI_TILE_SIZE + EMOJI_TILE_GAP),
            ),
        )

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def _send_ordered_emoji_parts(
    qq_bot,
    groups,
    parts,
    channel_id="",
    message_id="",
):
    """按文字/表情顺序发送，连续表情合并为一张图片。"""
    ok_map = {group: True for group in groups}
    text_buffer = []
    emoji_buffer = []

    async def flush_text():
        nonlocal ok_map, text_buffer
        if not text_buffer:
            return
        result = await send_text_parts(
            qq_bot,
            groups,
            text_buffer,
            channel_id=channel_id,
            message_id=message_id,
        )
        ok_map = {
            group: ok_map[group] and result.get(group, False)
            for group in groups
        }
        text_buffer = []

    async def flush_emoji():
        nonlocal ok_map, emoji_buffer
        if not emoji_buffer:
            return
        data = _compose_emoji_strip(emoji_buffer)
        if data is None:
            logger.warning(
                "Discord 表情合成失败，跳过本组表情：频道{} 消息{}",
                channel_id or "-",
                message_id or "-",
            )
            emoji_buffer = []
            return
        result = await _send_to_groups(
            qq_bot,
            groups,
            QQMessageSegment.file_image(data, "discord-emoji-strip.png"),
            channel_id=channel_id,
            message_id=message_id,
        )
        ok_map = {
            group: ok_map[group] and result.get(group, False)
            for group in groups
        }
        emoji_buffer = []

    for part in parts:
        if getattr(part, "type", "") == "file_image":
            await flush_text()
            emoji_buffer.append(part)
        else:
            await flush_emoji()
            text_buffer.append(part)
    await flush_emoji()
    await flush_text()
    return ok_map


async def send_media_items(qq_bot, groups, media_items, channel_id="", message_id=""):
    """发送媒体到指定群；返回 {group_openid: 是否送达}
    媒体上传重试后仍失败时，降级为文字 + 原链接（该降级视为已送达）；
    只有降级也失败才记为失败。"""
    ok_map = {group: True for group in groups}

    for item in media_items:

        message = QQMessage([item["segment"]])

        results = await _send_to_groups(
            qq_bot,
            groups,
            message,
            channel_id=channel_id,
            message_id=message_id,
        )

        pending = [group for group, ok in results.items() if not ok]

        if not pending:
            continue

        # 重试后仍失败：降级为链接
        logger.warning(
            "富媒体上传失败，降级为链接: {} 频道{} 消息{} 失败群{}",
            item["kind"],
            channel_id or "-",
            message_id or "-",
            pending
        )

        link_message = QQMessage(
            [make_media_link(
                item["kind"],
                item["url"]
            )]
        )

        deg_results = await _send_to_groups(
            qq_bot,
            pending,
            link_message,
            channel_id=channel_id,
            message_id=message_id,
        )

        for group, ok in deg_results.items():
            if not ok:
                ok_map[group] = False

    return ok_map


async def send_relay_message(text_parts, media_items, groups=None, channel_id="", message_id=""):
    """发送文字 + 媒体到指定群（默认全部启用群）；
    返回 {group_openid: 是否全部送达}；QQ Bot 未连接返回空 dict"""
    if groups is None:
        groups = get_active_groups()
    else:
        groups = list(groups)

    qq_bot = get_qq_bot()

    if qq_bot is None:

        logger.error(
            "QQ Bot 未连接，本次消息未发送、不记录去重ID"
        )

        await _record_trace(
            channel_id, message_id, text_parts, media_items, groups, {},
            note="QQ Bot 未连接",
        )
        return {}

    if not groups:
        await _record_trace(
            channel_id, message_id, text_parts, media_items, groups, {},
            note="没有可用目标群",
        )
        return {}

    ok_map = {group: True for group in groups}

    if text_parts:

        text_ok = await send_text_parts(
            qq_bot,
            groups,
            text_parts,
            channel_id=channel_id,
            message_id=message_id,
        )

        for group, ok in text_ok.items():
            ok_map[group] = ok_map[group] and ok

    media_ok = await send_media_items(
        qq_bot,
        groups,
        media_items,
        channel_id=channel_id,
        message_id=message_id,
    )

    for group, ok in media_ok.items():
        ok_map[group] = ok_map[group] and ok

    await _record_trace(
        channel_id, message_id, text_parts, media_items, groups, ok_map,
    )

    failed_groups = [group for group, ok in ok_map.items() if not ok]
    if failed_groups:
        await notify_delivery_failure(
            qq_bot,
            failed_groups,
            channel_id=channel_id,
            message_id=message_id,
        )

    return ok_map


def log_partial_failure(ok_map, channel_id="", message_id=""):
    """多群部分失败时汇总打印失败群列表（全成功/全失败不打印，
    全失败由调用方原有的失败日志负责，避免重复告警）"""
    failed = [group for group, ok in ok_map.items() if not ok]
    if failed and len(failed) < len(ok_map):
        logger.warning(
            "部分群发送失败: 频道{} 消息{} 失败群: {}",
            channel_id or "-",
            message_id or "-",
            failed
        )
