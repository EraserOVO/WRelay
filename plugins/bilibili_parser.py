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


_OPUS_PATH_RE = re.compile(r"/(?:opus|dynamic)/(\d{6,})", re.I)


def parse_manual_post_url(value):
    """解析私聊手动转发的 Bilibili 链接，返回内容类型与引用信息。

    支持投稿视频（含 b23.tv / bili2233.cn 短链、av/BV 号、?p= 分 P）和
    opus 链接。专栏与动态共用 /opus/{id} 路径（历史链接为 /dynamic/{id}），
    仅凭链接无法区分二者，因此这里统一返回 kind="opus"，真实类型由详情
    接口的 item.type 判定。短链接由调用方跟随重定向后再次调用本函数。

    无法识别时返回 None，由调用方给出用户提示。
    """
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

    opus_match = _OPUS_PATH_RE.search(parsed.path or "")
    if opus_match:
        return {"kind": "opus", "opus_id": opus_match.group(1)}

    video_ref = parse_video_url(raw)
    if video_ref:
        return {"kind": "视频", "video_ref": video_ref}
    return None


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


# =====================================================
# opus 详情（专栏 / 动态）→ 统一 post 结构
#
# 数据来自 x/polymer/web-dynamic/v1/detail 的 data.item。专栏与动态共用
# 该接口，由 item.type 区分：
#   DYNAMIC_TYPE_ARTICLE → 专栏，正文在 major.article
#   DYNAMIC_TYPE_DRAW / WORD / 其他 → 动态，正文在 module_dynamic.desc
# 本函数只做纯数据转换，不碰网络与配置。
# =====================================================

# 视为「专栏」的动态类型；其余按动态渲染。
_OPUS_ARTICLE_TYPES = {"DYNAMIC_TYPE_ARTICLE"}


def _opus_media_urls(src):
    """规范化 B 站图片地址：补全协议并按项目约定升级为 https。"""
    value = str(src or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("http://"):
        value = "https://" + value[len("http://"):]
    return value


def _opus_cover(item, major):
    """取封面：专栏用 major.article.covers，动态用 major.draw 首图。"""
    article = (major or {}).get("article") or {}
    covers = article.get("covers") or []
    for cover in covers:
        url = _opus_media_urls(cover)
        if url:
            return url
    draw = (major or {}).get("draw") or {}
    for entry in draw.get("items") or []:
        url = _opus_media_urls(entry.get("src"))
        if url:
            return url
    return ""


def _opus_archive_title(major):
    """取转发内容中的视频（archive）标题。"""
    archive = (major or {}).get("archive") or {}
    return str(archive.get("title") or "").strip()


def parse_opus_detail(item, opus_id=""):
    """把 opus 详情接口的 item 归一化为项目统一的 post 结构。

    返回 None 表示该 item 没有可转发内容（无标题、无正文、无媒体）。
    ``post_type`` 为「专栏」或「动态」，供渲染层区分正文位置。

    转发动态（DYNAMIC_TYPE_FORWARD）的原内容在 ``item.orig`` 中，外层
    ``desc.text`` 通常只是「分享视频」这类转发语。此时以原内容的标题和
    封面为准，并把转发语作为正文保留；原内容仍取不到标题时只发转发语。
    """
    if not isinstance(item, dict):
        return None
    modules = item.get("modules") or {}
    author_module = modules.get("module_author") or {}
    dynamic_module = modules.get("module_dynamic") or {}
    major = dynamic_module.get("major") or {}

    item_type = str(item.get("type") or "")
    is_article = item_type in _OPUS_ARTICLE_TYPES
    article = major.get("article") or {}

    author = str(author_module.get("name") or "").strip()

    if is_article:
        title = str(article.get("title") or "").strip()
        # 专栏正文在接口里只有简介摘要（article.desc），完整正文不在该接口。
        body = str(article.get("desc") or "").strip()
        jump_url = str(article.get("jump_url") or "").strip()
        if jump_url.startswith("//"):
            jump_url = "https:" + jump_url
        elif jump_url.startswith("/"):
            jump_url = "https://www.bilibili.com" + jump_url
        link = jump_url
        if not link:
            link = "https://www.bilibili.com/opus/%s" % (
                str(item.get("id_str") or opus_id or "").strip()
            )
        if not title:
            # 专栏缺标题时退回首行正文，避免整条无法转发。
            title = body.splitlines()[0].strip() if body else ""
    else:
        desc = dynamic_module.get("desc") or {}
        body = str(desc.get("text") or "").strip()
        link = "https://www.bilibili.com/opus/%s" % (
            str(item.get("id_str") or opus_id or "").strip()
        )
        cover = _opus_cover(item, major)
        # 转发动态：外层通常只有「分享视频」这类转发语，真实内容在同结构
        # 的 orig 中。原内容能取到标题时以它为准，并把转发语合成一行。
        title = body
        if isinstance(item.get("orig"), dict):
            orig = item["orig"]
            orig_modules = orig.get("modules") or {}
            orig_dynamic = orig_modules.get("module_dynamic") or {}
            orig_major = orig_dynamic.get("major") or {}
            if not cover:
                cover = _opus_cover(orig, orig_major)
            orig_desc = (orig_dynamic.get("desc") or {}).get("text") or ""
            orig_title = _opus_archive_title(orig_major) or str(orig_desc).strip()
            if orig_title:
                title = "%s：%s" % (body, orig_title) if body else orig_title
                body = ""
        return _opus_post(
            item, opus_id, title=title, body=body, link=link,
            author=author, cover=cover, is_article=False,
        )

    cover = _opus_cover(item, major)
    return _opus_post(
        item, opus_id, title=title, body=body, link=link,
        author=author, cover=cover, is_article=is_article,
    )


def _opus_post(item, opus_id, title, body, link, author, cover, is_article):
    """组装统一的 post 结构；无可转发内容时返回 None。"""
    if not title and not body and not cover:
        return None
    return {
        "id": str(item.get("id_str") or opus_id or "").strip(),
        "link": link,
        "title": title,
        "description": body,
        "author": author,
        "pub_date": str(
            (item.get("modules") or {}).get("module_author", {}).get("pub_time") or ""
        ).strip(),
        "post_type": "专栏" if is_article else "动态",
        "tags": [],
        "bvid": "",
        "avid": "",
        "media": ([{"kind": "图片", "url": cover}] if cover else []),
    }
