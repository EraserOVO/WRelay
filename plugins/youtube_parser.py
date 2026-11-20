"""YouTube 频道视频解析与来源归一化（纯函数）。

第一期只处理频道视频：频道 ID（``UC`` 开头）或 ``@handle`` → Data API 上传列表
或官方 ``feeds/videos.xml`` → 标题、封面与视频链接。不解析视频本体、不做搜索来源。
"""

from __future__ import annotations

import re
import json
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
_CHANNEL_IMAGE_RE = re.compile(
    r'''<meta\b(?=[^>]*\sproperty=["']og:image["'])(?=[^>]*\scontent=(["'])(.*?)\1)[^>]*>''',
    re.IGNORECASE,
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


def parse_channel_avatar(html):
    """频道主页的 og:image 对应发布频道的头像。"""
    match = _CHANNEL_IMAGE_RE.search(str(html or ""))
    return unescape(match.group(2)).strip() if match else ""


def parse_channel_videos_page(html, channel_id):
    """读取频道视频页的列表，格式不明确时拒绝当作空频道。"""
    text = str(html or "")
    info = parse_channel_page(text)
    if info["channel_id"] != channel_id:
        raise ValueError("频道视频页的频道 ID 不匹配")
    match = re.search(r"(?:var\s+)?ytInitialData\s*=\s*", text)
    if not match:
        raise ValueError("频道视频页缺少视频数据")
    data, _ = json.JSONDecoder().raw_decode(text[match.end():])
    tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
    selected = next((t.get("tabRenderer", {}) for t in tabs if t.get("tabRenderer", {}).get("selected")), None)
    if not selected or not selected.get("content"):
        raise ValueError("频道视频列表不可用")
    videos = []
    seen = set()

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            item = node.get("lockupViewModel")
            if isinstance(item, dict) and item.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                video_id = str(item.get("contentId") or "")
                meta = item.get("metadata", {}).get("lockupMetadataViewModel", {})
                title = str(meta.get("title", {}).get("content") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not title:
                    raise ValueError("频道视频条目不完整")
                if video_id not in seen:
                    seen.add(video_id)
                    videos.append({"video_id": video_id, "title": title})
                return
            for value in node.values():
                visit(value)

    visit(selected["content"])
    if not videos:
        raise ValueError("频道视频页未识别到视频")
    return videos[:15], info["title"]


def parse_video_page_post(html, video, channel_id, author):
    text = str(html or "")
    match = re.search(r'"publishDate"\s*:\s*"([^"]+)"', text)
    channel = re.search(r'"channelId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"', text)
    video_id = video["video_id"]
    if not match or not channel or channel.group(1) != channel_id:
        raise ValueError("视频发布时间或频道 ID 无法确认: " + video_id)
    published = match.group(1)
    cursor = _entry_cursor(published, video_id)
    if not cursor:
        raise ValueError("视频发布时间无效: " + video_id)
    return {"id": video_id, "video_id": video_id, "cursor_id": cursor,
            "title": video["title"], "link": "https://www.youtube.com/watch?v=" + video_id,
            "published": published, "thumbnail": "https://i.ytimg.com/vi/" + video_id + "/hqdefault.jpg",
            "description": "", "author": author, "channel_id": channel_id, "post_type": "视频"}


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


def parse_api_playlist_items(items, channel_id):
    """把 YouTube Data API 上传列表条目转换为统一视频结构。"""
    channel_id = str(channel_id or "").strip()
    if not CHANNEL_ID_RE.fullmatch(channel_id):
        raise ValueError("YouTube Data API 返回了无效频道 ID")
    if not isinstance(items, list):
        raise ValueError("YouTube Data API 上传列表格式无效")

    posts = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        snippet = item.get("snippet")
        if not isinstance(snippet, dict):
            continue
        resource = snippet.get("resourceId")
        if not isinstance(resource, dict):
            continue
        video_id = str(resource.get("videoId") or "").strip()
        title = str(snippet.get("title") or "").strip()
        details = item.get("contentDetails")
        if not isinstance(details, dict):
            details = {}
        published = str(
            details.get("videoPublishedAt") or snippet.get("publishedAt") or ""
        ).strip()
        cursor_id = _entry_cursor(published, video_id)
        if not video_id or not title or not cursor_id or video_id in seen:
            continue
        seen.add(video_id)

        thumbnails = snippet.get("thumbnails")
        if not isinstance(thumbnails, dict):
            thumbnails = {}
        thumbnail = ""
        for size in ("maxres", "standard", "high", "medium", "default"):
            image = thumbnails.get(size)
            if isinstance(image, dict) and image.get("url"):
                thumbnail = str(image["url"]).strip()
                if thumbnail:
                    break
        if not thumbnail:
            thumbnail = "https://i.ytimg.com/vi/%s/hqdefault.jpg" % quote(
                video_id, safe=""
            )

        posts.append({
            "id": video_id,
            "video_id": video_id,
            "cursor_id": cursor_id,
            "title": title,
            "link": "https://www.youtube.com/watch?v=" + quote(video_id, safe=""),
            "published": published,
            "thumbnail": thumbnail,
            "description": " ".join(str(snippet.get("description") or "").split()),
            "author": str(snippet.get("channelTitle") or "").strip(),
            "channel_id": channel_id,
            "post_type": "视频",
        })
    return sorted(posts, key=lambda post: int(post["cursor_id"]))


def format_forward_text(post, source=None, source_label="自动转发来自"):
    """格式化自动转发的 YouTube 视频文本。"""
    source = source if isinstance(source, dict) else {}
    post = post if isinstance(post, dict) else {}
    display_name = str(
        post.get("author") or source.get("name") or source.get("value") or ""
    ).strip()
    lines = []
    if display_name:
        lines.append("【YouTube · 视频】%s" % display_name)
    title = str(post.get("title") or "").strip()
    link = str(post.get("link") or "").strip()
    if title:
        lines.append(title)
    if link:
        lines.append(link)
    return "\n".join(lines)
