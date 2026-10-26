"""YouTube 频道 RSS 解析与来源归一化（纯函数）。

第一期只处理频道视频：频道 ID（``UC`` 开头）或 ``@handle`` → 官方
``feeds/videos.xml`` → 标题、封面与视频链接。不解析视频本体、不做搜索来源。
"""

from __future__ import annotations

import re
import zlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote, urlsplit

ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"
MEDIA_NS = "http://search.yahoo.com/mrss/"

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_HANDLE_RE = re.compile(r"^[0-9A-Za-z._-]{3,100}$")
_CHANNEL_ID_PATTERNS = (
    re.compile(r'"externalId":"(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'"channelId":"(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'itemprop="identifier"[^>]+content="(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'channel_id=(UC[0-9A-Za-z_-]{22})'),
)
_PAGE_TITLE_PATTERNS = (
    re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"'),
    re.compile(r"<title>([^<]+)</title>"),
)


def normalize_channel_value(raw):
    """频道输入 → ``UC...`` 频道 ID 或 ``@handle``；无法识别返回空串。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if CHANNEL_ID_RE.fullmatch(text):
        return text
    if text.startswith("@"):
        handle = text[1:].strip()
        return "@" + handle if _HANDLE_RE.fullmatch(handle) else ""
    if "://" in text:
        try:
            parsed = urlsplit(text)
        except ValueError:
            return ""
        host = (parsed.hostname or "").lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            return ""
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= 2 and segments[0].lower() == "channel":
            return segments[1] if CHANNEL_ID_RE.fullmatch(segments[1]) else ""
        if segments and segments[0].startswith("@"):
            handle = segments[0][1:]
            return "@" + handle if _HANDLE_RE.fullmatch(handle) else ""
        if len(segments) >= 2 and segments[0].lower() in {"c", "user"}:
            handle = segments[1]
            return "@" + handle if _HANDLE_RE.fullmatch(handle) else ""
        return ""
    return "@" + text if _HANDLE_RE.fullmatch(text) else ""


def youtube_source_id(value):
    """来源标识：``channel:UC...`` / ``channel:@handle``。"""
    normalized = normalize_channel_value(value)
    return "channel:" + normalized if normalized else ""


def channel_feed_url(channel_id):
    """频道 ID → YouTube 官方 RSS 地址。"""
    value = str(channel_id or "").strip()
    if not CHANNEL_ID_RE.fullmatch(value):
        raise ValueError("YouTube 频道 ID 无效")
    return (
        "https://www.youtube.com/feeds/videos.xml?channel_id="
        + quote(value, safe="")
    )


def channel_page_url(value):
    """频道输入 → 可用于解析频道 ID 的主页地址。"""
    normalized = normalize_channel_value(value)
    if not normalized:
        raise ValueError("YouTube 频道地址无效")
    if normalized.startswith("@"):
        return "https://www.youtube.com/" + quote(normalized, safe="@._-")
    return "https://www.youtube.com/channel/" + quote(normalized, safe="")


def parse_channel_page(html):
    """从频道主页 HTML 提取频道 ID 与标题。"""
    text = str(html or "")
    channel_id = ""
    for pattern in _CHANNEL_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            channel_id = match.group(1)
            break
    title = ""
    for pattern in _PAGE_TITLE_PATTERNS:
        match = pattern.search(text)
        if match:
            title = unescape(match.group(1)).strip()
            break
    if title.endswith(" - YouTube"):
        title = title[: -len(" - YouTube")].strip()
    return {"channel_id": channel_id, "title": title}


def _entry_cursor(published, video_id):
    """发布时间（秒）×1000 + 视频 ID 稳定后缀，生成纯十进制游标。"""
    text = str(published or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        epoch = int(parsed.timestamp())
    except (OverflowError, OSError, ValueError):
        return ""
    suffix = zlib.crc32(video_id.encode("utf-8")) % 1000
    return str(epoch * 1000 + suffix)


def _text(node, path):
    return str(node.findtext(path) or "").strip()


def parse_feed(xml_bytes):
    """YouTube 频道 RSS → 按发布时间从旧到新排列的视频列表。"""
    root = ET.fromstring(xml_bytes)
    root_name = (
        root.tag.rsplit("}", 1)[-1].lower()
        if isinstance(root.tag, str)
        else ""
    )
    if root_name != "feed":
        raise ValueError("YouTube RSS 文档根节点无效: {}".format(root_name or "<empty>"))
    posts = []
    seen = set()
    for entry in root.findall("{%s}entry" % ATOM_NS):
        video_id = _text(entry, "{%s}videoId" % YT_NS)
        title = _text(entry, "{%s}title" % ATOM_NS)
        if not video_id or not title:
            continue
        link = ""
        for link_el in entry.findall("{%s}link" % ATOM_NS):
            if link_el.get("rel") == "alternate" and link_el.get("href"):
                link = link_el.get("href").strip()
                break
        if not link:
            link = "https://www.youtube.com/watch?v=" + quote(video_id, safe="")
        published = _text(entry, "{%s}published" % ATOM_NS)
        cursor_id = _entry_cursor(published, video_id)
        if not cursor_id:
            continue
        if video_id in seen:
            continue
        seen.add(video_id)
        group = entry.find("{%s}group" % MEDIA_NS)
        thumbnail = ""
        description = ""
        if group is not None:
            thumb_el = group.find("{%s}thumbnail" % MEDIA_NS)
            if thumb_el is not None:
                thumbnail = str(thumb_el.get("url") or "").strip()
            description = _text(group, "{%s}description" % MEDIA_NS)
        author = _text(entry, "{%s}author/{%s}name" % (ATOM_NS, ATOM_NS))
        posts.append({
            "id": video_id,
            "video_id": video_id,
            "cursor_id": cursor_id,
            "title": title,
            "link": link,
            "published": published,
            "thumbnail": thumbnail,
            "description": " ".join(description.split()),
            "author": author,
            "channel_id": _text(entry, "{%s}channelId" % YT_NS),
            "post_type": "视频",
        })
    return sorted(posts, key=lambda item: int(item["cursor_id"]))


def format_forward_text(post, source=None, source_label="自动转发来自"):
    """格式化自动转发的 YouTube 视频文本。"""
    source = source if isinstance(source, dict) else {}
    post = post if isinstance(post, dict) else {}
    display_name = str(
        post.get("author") or source.get("name") or source.get("value") or ""
    ).strip()
    lines = []
    if display_name:
        lines.append("%s YouTube 中[%s]的视频：" % (source_label, display_name))
    title = str(post.get("title") or "").strip()
    link = str(post.get("link") or "").strip()
    if title:
        lines.append(title)
    if link:
        lines.append(link)
    return "\n".join(lines)
