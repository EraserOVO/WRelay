import asyncio
import math
import re
import threading
from urllib.parse import urlsplit

import httpx

from nonebot import get_driver, logger
from nonebot.adapters.qq import MessageSegment as QQMessageSegment


# =====================================================
# Discord 文本/Emoji 处理 + QQ 消息段构建 + 媒体下载
#
# 单一职责：不依赖任何事件对象，只做纯转换与字节下载。
# 供 relay.py / command.py 等插件复用。
# =====================================================

IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp"
}

AUDIO_EXTS = {
    ".wav",
    ".mp3",
    ".ogg",
    ".flac",
    ".m4a"
}

VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
}

# Discord 自定义表情通常原图只有 128px 左右，但 QQ 将独立图片段展示得
# 更大。通过 Discord CDN 的 size 参数请求小图，避免表情在 QQ 中占屏。
DISCORD_EMOJI_SIZE = 64


# =====================================================
# Discord 文本美化（B2）
#
# 把 Discord Markdown 转成 QQ 里可读的纯文本：
#   - @everyone / @here → 可读文本
#   - <@用户> <@&角色> <#频道> 提及 → 可读文本；用户提及优先使用
#     Discord API 返回的真实用户名/显示名
#   - 粗体/斜体/删除线/行内代码/代码块围栏 → 去掉标记
#   - [文字](链接) → 文字（链接）
# =====================================================

def convert_mentions(text: str):

    if not text:
        return ""

    text = text.replace(
        "@everyone",
        "[@全体成员]"
    )

    text = text.replace(
        "@here",
        "[@在线成员]"
    )

    return text


def convert_mention_ids(text: str, mention_names=None):

    if not text:
        return ""

    mention_names = mention_names or {}

    def replace_user_mention(match):
        user_id = match.group(1)
        name = str(mention_names.get(user_id) or "").strip()
        return "[@{}]".format(name or user_id)

    text = re.sub(
        r"<@!?(\d+)>",
        replace_user_mention,
        text
    )

    text = re.sub(
        r"<@&(\d+)>",
        r"[角色@\1]",
        text
    )

    text = re.sub(
        r"<#(\d+)>",
        r"[#\1]",
        text
    )

    return text


def cleanup_markdown(text: str):

    if not text:
        return ""

    # 代码块围栏 ```lang ... ```（去掉围栏行，保留内容）
    text = re.sub(
        r"```[^\n]*\n?",
        "",
        text
    )

    # 行内代码
    text = re.sub(
        r"`([^`\n]+)`",
        r"\1",
        text
    )

    # 粗体
    text = re.sub(
        r"\*\*([^*\n]+)\*\*",
        r"\1",
        text
    )

    # 斜体（前后非单词字符，避免误伤 1*2*3 之类）
    text = re.sub(
        r"(?<!\w)\*([^*\n]+)\*(?!\w)",
        r"\1",
        text
    )

    # 删除线
    text = re.sub(
        r"~~([^~\n]+)~~",
        r"\1",
        text
    )

    # [文字](链接) → 文字（链接）
    text = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)",
        r"\1（\2）",
        text
    )

    return text


_DISCORD_EMOJI_RE = re.compile(
    r"<(?P<animated>a)?:[^:]+:(?P<id>\d+)>"
)


def _discord_emoji_url(match):
    suffix = "gif" if match.group("animated") else "png"
    return (
        f"https://cdn.discordapp.com/emojis/{match.group('id')}.{suffix}"
        f"?size={DISCORD_EMOJI_SIZE}&quality=lossless"
    )


def parse_discord_emoji_parts(text: str):
    """返回按原文顺序排列的 ``[(kind, value), ...]``。

    kind 为 ``text`` 或 ``emoji``。保留表情位置后，fetch.build_parts 可以
    把文字和对应表情放进同一条 QQ 消息，避免每个表情单独发一条消息。
    """
    if not text:
        return []

    parts = []
    cursor = 0
    for match in _DISCORD_EMOJI_RE.finditer(text):
        if match.start() > cursor:
            parts.append(("text", text[cursor:match.start()]))
        parts.append(("emoji", _discord_emoji_url(match)))
        cursor = match.end()
    if cursor < len(text):
        parts.append(("text", text[cursor:]))
    return parts


def parse_discord_emoji(text: str):
    """兼容旧调用：移除表情并返回表情 URL 列表。"""
    text_parts = []
    emoji_urls = []
    for kind, value in parse_discord_emoji_parts(text):
        if kind == "emoji":
            emoji_urls.append(value)
        else:
            text_parts.append(value)
    return "".join(text_parts).strip(), emoji_urls


def make_text(text):

    return QQMessageSegment.text(text)


def make_file_link(filename, url):

    return make_text(
        f"\n[文件: {filename}]\n{url}"
    )


def make_media_link(kind, url):

    return make_text(
        f"\n[{kind}] {url}"
    )


def _get_proxy(url):
    """Discord/X media CDNs use DISCORD_PROXY; domestic media stays on system routing."""
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except (TypeError, ValueError):
        return None
    if not (
        host == "discord.com"
        or host.endswith((".discord.com", ".discordapp.com", ".discordapp.net"))
        or host == "twimg.com"
        or host.endswith(".twimg.com")
    ):
        return None
    try:
        proxy = getattr(get_driver().config, "discord_proxy", None)
    except Exception:
        return None
    if isinstance(proxy, str):
        proxy = proxy.strip()
        return proxy or None
    return None


# =====================================================
# 媒体下载并发控制 + 超时 + 大小上限
#
# 突发转发时若不加限制，每条消息内的媒体下载会无限并发，
# 打爆 CDN 与内存；这里用全局信号量限制同时下载数。
# 下载上限默认 30MB：超过的文件 QQ 富媒体基本也传不上去，
# 直接放弃下载，由调用方降级为文字 + 原链接。
# 超时/并发数/大小上限均可通过 NoneBot 配置覆盖
# （.env.prod 的 API_TIMEOUT / MEDIA_CONCURRENCY / MEDIA_MAX_BYTES）。
# =====================================================

DEFAULT_MEDIA_TIMEOUT = 120
DEFAULT_MEDIA_CONCURRENCY = 4
DEFAULT_MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
_media_client = None
_discord_media_client = None
_media_download_tasks = set()
_media_stopping = False


def _get_media_client(proxy=None):
    global _media_client, _discord_media_client
    if proxy:
        if _discord_media_client is None:
            _discord_media_client = httpx.AsyncClient(
                proxy=proxy,
                timeout=_get_timeout(),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        return _discord_media_client
    if _media_client is None:
        # 非代理媒体走系统路由，不读取环境/系统代理。
        # 若保留 trust_env，Windows 代理绕过列表中的裸 IPv6 条目（如 ::1）会
        # 让 httpx 在构造客户端时把 ":1]" 当作非法端口抛 InvalidURL，使
        # Bilibili 等直连媒体下载全部降级为链接。
        _media_client = httpx.AsyncClient(
            timeout=_get_timeout(),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
            trust_env=False,
        )
    return _media_client


@get_driver().on_shutdown
async def _close_media_client():
    global _media_client, _discord_media_client, _media_stopping
    _media_stopping = True
    current_task = asyncio.current_task()
    download_tasks = [
        task for task in _media_download_tasks
        if task is not current_task and not task.done()
    ]
    for task in download_tasks:
        task.cancel()
    if download_tasks:
        await asyncio.gather(*download_tasks, return_exceptions=True)
    _media_download_tasks.clear()
    if _media_client is not None:
        await _media_client.aclose()
        _media_client = None
    if _discord_media_client is not None:
        await _discord_media_client.aclose()
        _discord_media_client = None

_semaphore = None
_semaphore_lock = threading.Lock()


def _get_semaphore():
    """懒初始化全局下载信号量（避免在事件循环创建前绑定 loop）"""
    global _semaphore
    if _semaphore is None:
        with _semaphore_lock:
            if _semaphore is None:
                concurrency = DEFAULT_MEDIA_CONCURRENCY
                try:
                    configured = getattr(
                        get_driver().config, "media_concurrency", None
                    )
                    concurrency = int(configured)
                    if concurrency <= 0:
                        concurrency = DEFAULT_MEDIA_CONCURRENCY
                except Exception:
                    concurrency = DEFAULT_MEDIA_CONCURRENCY
                _semaphore = asyncio.Semaphore(concurrency)
    return _semaphore


def _get_timeout():
    """媒体下载超时（秒）：读 NoneBot 配置 api_timeout，默认 120"""
    try:
        timeout = float(getattr(
            get_driver().config, "api_timeout", DEFAULT_MEDIA_TIMEOUT
        ))
        if not math.isfinite(timeout) or timeout <= 0:
            return DEFAULT_MEDIA_TIMEOUT
        return timeout
    except Exception:
        return DEFAULT_MEDIA_TIMEOUT


def _get_max_download_bytes():
    """媒体下载大小上限（字节）：读 NoneBot 配置 media_max_bytes，默认 30MB"""
    try:
        value = int(getattr(
            get_driver().config,
            "media_max_bytes",
            DEFAULT_MAX_DOWNLOAD_BYTES,
        ))
        return value if value > 0 else DEFAULT_MAX_DOWNLOAD_BYTES
    except Exception:
        return DEFAULT_MAX_DOWNLOAD_BYTES


def get_max_download_bytes():
    """返回媒体下载大小上限，供调用方在下载前检查已知文件大小。"""
    return _get_max_download_bytes()


async def fetch_bytes(url, headers=None):
    """下载媒体字节（走代理、受全局信号量限流、有大小上限）；
    失败或超限返回 None（调用方降级为链接）。可选 headers 用于需要来源页
    Referer 的 CDN 媒体；不传时保持既有请求行为。"""
    if _media_stopping:
        return None
    download_task = asyncio.current_task()
    if download_task is not None:
        _media_download_tasks.add(download_task)
    try:
        proxy = _get_proxy(url)
        timeout = _get_timeout()
        max_bytes = _get_max_download_bytes()
        async with _get_semaphore():
            client = _get_media_client(proxy)
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    logger.warning(
                        "媒体下载失败: {} {}",
                        resp.status_code,
                        url
                    )
                    return None

                length = resp.headers.get("content-length")
                if (
                    length
                    and length.isdigit()
                    and int(length) > max_bytes
                ):
                    logger.warning(
                        "媒体超过大小上限({}>{})，放弃下载: {}",
                        length,
                        max_bytes,
                        url
                    )
                    return None

                data = bytearray()
                async for chunk in resp.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        logger.warning(
                            "媒体超过大小上限，放弃下载: {}",
                            url
                        )
                        return None
                return bytes(data)
    except Exception as exc:
        logger.warning(
            "媒体下载异常: {}",
            exc
        )
        return None
    finally:
        if download_task is not None:
            _media_download_tasks.discard(download_task)


async def fetch_bytes_many(urls):
    """并发下载多个 URL（受同一信号量限流）；返回 {url: bytes|None}

    解决消息内媒体逐个 await 串行下载的问题；重复 URL 只下载一次。"""
    if not urls:
        return {}
    unique_urls = list(dict.fromkeys(urls))
    results = await asyncio.gather(
        *(fetch_bytes(url) for url in unique_urls)
    )
    return dict(zip(unique_urls, results))
