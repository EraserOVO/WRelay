"""Bilibili RSS 条目解析与稳定游标生成。

本模块只处理 RSS XML 和条目筛选所需的纯数据转换，不碰网络、配置或 QQ。
RSSHub 的 Bilibili 投稿、搜索和标签路由都归一为同一种 video dict，供
plugins.bilibili 统一去重、渲染和投递。
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse


_IMG_RE = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)
_VIDEO_RE = re.compile(r"<video\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BVID_RE = re.compile(r"(?:/video/|\b)(BV[0-9A-Za-z]{10})\b", re.I)
_AVID_RE = re.compile(r"(?:/video/|\b)av(\d+)\b", re.I)
_TIE_BASE = 10**16


def _unescape_url(value):
    return html.unescape(str(value or "")).strip()


def html_to_text(value):
    """把 RSS description 中的 HTML 转换为 QQ 可读的纯文本。"""
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = _IMG_RE.sub("", text)
    text = _VIDEO_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_post_text(post, source=None):
    """格式化 Bilibili 投稿的 QQ 文本，只保留来源、标题和投稿地址。"""
    return format_forward_text(post, source, source_label="自动转发来自")


def format_forward_text(post, source=None, source_label="自动转发来自"):
    """格式化自动或手动 Bilibili 转发文本。"""
    source = source if isinstance(source, dict) else {}
    source_name = str(source.get("name") or source.get("value") or "").strip()
    author = str(post.get("author") or "").strip()
    display_source = author or source_name
    lines = []
    if display_source:
        lines.append("%s Bilibili 中[%s]的投稿：" % (source_label, display_source))

    title = str(post.get("title") or "").strip()
    link = str(post.get("link") or "").strip()

    if title:
        lines.append(title)
    if link:
        lines.append(link)
    return "\n".join(lines)


def parse_video_url(value):
    """解析受支持的 Bilibili 投稿链接，短链接由调用方跟随重定向。"""
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    is_bilibili = host == "bilibili.com" or host.endswith(".bilibili.com")
    is_short = host in {"b23.tv", "bili2233.cn"}
    if not (is_bilibili or is_short):
        return None

    query = parse_qs(parsed.query)
    bvid, avid = _extract_video_id(parsed.path, " ".join(query.get("bvid", [])))
    if not avid:
        aid_values = query.get("aid", [])
        if aid_values and str(aid_values[0]).isdigit():
            avid = str(aid_values[0])
    try:
        page = int(query.get("p", [1])[0])
    except (TypeError, ValueError):
        page = 1

    if not bvid and not avid and not is_short:
        return None
    return {
        "bvid": bvid,
        "avid": avid,
        "page": max(1, page),
        "short_url": raw if is_short and not (bvid or avid) else "",
    }


def _parse_timestamp(value):
    """解析 RSS 日期，返回毫秒时间戳；失败返回 0。"""
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int(parsed.timestamp() * 1000))


def _stable_key(guid, link, title, pub_date):
    value = str(guid or link or "").strip()
    if value:
        return value
    return "fallback:" + hashlib.sha1(
        (str(title or "") + "\n" + str(pub_date or "")).encode("utf-8")
    ).hexdigest()


def make_cursor_id(stable_id, pub_date):
    """生成可供现有整数游标使用的稳定排序 ID。

    Bilibili 投稿链接通常是 BV 号，而项目历史去重游标要求整数且单调比较。
    这里以发布时间为主序、稳定 ID 的哈希为同一时间的确定性次序；同一条
    内容在不同轮询中会得到相同游标，不需要改动 Discord/X 的去重格式。
    """
    timestamp = _parse_timestamp(pub_date)
    tie = int(hashlib.sha1(str(stable_id).encode("utf-8")).hexdigest()[:13], 16)
    return str(timestamp * _TIE_BASE + tie)


def _extract_media(description_html, item_el):
    media = []
    seen = set()

    def add(kind, url):
        url = _unescape_url(url)
        if not url or url in seen:
            return
        seen.add(url)
        media.append({"kind": kind, "url": url})

    for match in _IMG_RE.finditer(description_html or ""):
        add("图片", match.group(1))

    # 视频本体通常是临时 CDN 地址；解析后由渲染层按来源设置尝试下载，
    # 下载失败或超过媒体上限时改发视频链接。
    for match in _VIDEO_RE.finditer(description_html or ""):
        add("视频", match.group(1))

    for child in list(item_el):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag == "enclosure":
            add("文件", child.attrib.get("url"))
        elif tag == "content" and child.attrib.get("url"):
            add("文件", child.attrib.get("url"))
    return media


def _extract_video_id(link, guid):
    value = str(link or guid or "")
    bvid = None
    avid = None
    match = _BVID_RE.search(value)
    if match:
        # BV 号大小写敏感（base58 变体）。大写化会得到另一个不存在的投稿号，
        # 让 view / playurl 接口返回 -400「请求错误」；_BVID_RE 已带 re.I，
        # 匹配不受影响，这里必须保留链接中的原始大小写。
        bvid = match.group(1)
    match = _AVID_RE.search(value)
    if match:
        avid = match.group(1)
    return bvid, avid


def parse_item(item_el, post_type="视频"):
    guid = (item_el.findtext("guid") or "").strip()
    link = html.unescape((item_el.findtext("link") or "").strip())
    title = html_to_text(item_el.findtext("title") or "")
    description_html = item_el.findtext("description") or ""
    description = html_to_text(description_html)
    pub_date = (item_el.findtext("pubDate") or "").strip()
    author = html_to_text(item_el.findtext("author") or "")
    categories = []
    for element in item_el:
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag == "category":
            value = html_to_text(element.text or "")
            if value and value not in categories:
                categories.append(value)

    stable_id = _stable_key(guid, link, title, pub_date)
    bvid, avid = _extract_video_id(link, guid)
    return {
        "id": stable_id,
        "cursor_id": make_cursor_id(stable_id, pub_date),
        "guid": guid,
        "link": link or guid,
        "title": title,
        "description": description,
        "author": author,
        "pub_date": pub_date,
        "post_type": post_type or "视频",
        "tags": categories,
        "bvid": bvid,
        "avid": avid,
        "media": _extract_media(description_html, item_el),
    }


def parse_feed(xml_bytes, post_type="视频"):
    """RSS XML bytes → 按旧到新排列的唯一 Bilibili 投稿列表。"""
    root = ET.fromstring(xml_bytes)
    root_name = (
        root.tag.rsplit("}", 1)[-1].lower()
        if isinstance(root.tag, str)
        else ""
    )
    if root_name != "rss":
        raise ValueError("Bilibili RSS 文档根节点无效: {}".format(root_name or "<empty>"))
    posts = []
    seen = set()
    for item_el in root.findall(".//item"):
        try:
            post = parse_item(item_el, post_type=post_type)
        except Exception:
            continue
        if not post or post["id"] in seen:
            continue
        if not post["link"] and not post["title"]:
            continue
        seen.add(post["id"])
        posts.append(post)
    return sorted(posts, key=lambda item: int(item["cursor_id"]))
