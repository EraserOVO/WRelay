"""RSSHub X 用户 RSS → 统一推文 dict（纯函数，无 I/O）。

单一职责：把 RSSHub /twitter/user/{handle} 返回的 RSS XML 解析为
x_rss.py 可直接消费的推文 dict。不碰网络、不碰文件、不碰 nonebot，
与 dedup.py / filter.py 同样的纯函数风格，可独立测试。

解析规则全部基于对当前真实 RSS（http://localhost:1200/twitter/user/xx_Chon_xx）
的实际观察，不依赖 RSSHub 文档：

- <description> 内是整段实体转义后的 HTML；ElementTree 读出的文本
  已解开 XML 层转义，得到 HTML 源码，HTML 层转义在文本/属性提取时
  再各做一次 html.unescape（共两层）
- RT：title 与 description 均以 "RT " 开头；description 首行为
  "RT\\xa0{作者显示名}"（&nbsp;），其后为被转推原文；原推媒体内联
- Quote：正文后跟 <hr ...> 分隔线 + <div class="rsshub-quote">
  {被引作者}:&nbsp;{被引全文} + 被引媒体 </div>（位于 description 末尾）
- 图片：<img ... src="https://pbs.twimg.com/media/...?format=jpg&amp;name=orig">
- 视频：<video ... src="https://video.twimg.com/....mp4" ...
  poster="https://pbs.twimg.com/....jpg"></video>（mp4 直链 + 封面）
- guid isPermaLink="false" = https://twitter.com/{handle}/status/{id}，
  status ID 是全局单调递增整数，去重游标即用该 ID
- <category> 为推文 hashtag（无 # 号）；<author> 恒为被订阅者显示名
- 无 <enclosure> / <media:*> / <dc:*>，媒体全部在 description HTML 内
"""

import html
import re
import xml.etree.ElementTree as ET

# guid/link 形如 https://(twitter|x).com/{handle}/status/{id}
_STATUS_ID_RE = re.compile(r"/status/(\d+)")

# RT 前缀（首行）：RT\xa0{作者}，作者显示名可能包含空格，
# 因此取整行（不能按空格切分）
_RT_LINE_RE = re.compile(r"^RT[\s\u00a0]+([^\n]+)\n")

# 引用块作者前缀：{作者}:&nbsp;（冒号后跟空白/nbsp）
_QUOTE_AUTHOR_RE = re.compile(r"^(.*?):[\s\u00a0]+")

# 引用块边界：<hr 分隔线 + <div class="rsshub-quote">（无嵌套 div）
_QUOTE_DIV_OPEN = '<div class="rsshub-quote">'

# 引用正文显示上限：QQ 端引用行不宜过长（与 fetch.py
# REFERENCE_TEXT_LIMIT=50 的回复引用行区分开，引用推文允许更长）
QUOTE_TEXT_LIMIT = 200


def parse_status_id(guid):
    """guid → 推文 status ID 字符串；解析失败返回 None"""
    if not guid:
        return None
    m = _STATUS_ID_RE.search(guid)
    return m.group(1) if m else None


def _unescape_url(url):
    """HTML 属性里的 URL：解开 &amp; 等实体（第二层转义）"""
    return html.unescape(url or "").strip()


def _split_quote(desc_html):
    """description HTML → (主推 HTML, 引用块 HTML | None)

    引用块固定出现在 description 末尾：<hr ...><div class="rsshub-quote">...</div>。
    引用块内部无嵌套 div（实测），用最后一个 </div> 收尾即可。"""
    idx = desc_html.find(_QUOTE_DIV_OPEN)
    if idx < 0:
        return desc_html, None

    # 分隔线紧贴引用块之前；找不到 hr 时以 div 开头处切分（宽松匹配，
    # 只丢引用块，不丢主推内容）
    hr_idx = desc_html.rfind("<hr", 0, idx)
    main_html = desc_html[:hr_idx] if hr_idx >= 0 else desc_html[:idx]

    quote_html = desc_html[idx + len(_QUOTE_DIV_OPEN):]
    quote_html = quote_html.rsplit("</div>", 1)[0]
    return main_html, quote_html


def _extract_media(html_src):
    """HTML → [{kind, url, poster}]（按出现顺序；图片无 poster）"""
    media = []

    for m in re.finditer(r"<img\b[^>]*>", html_src):
        sm = re.search(r'\ssrc="([^"]+)"', m.group(0))
        if sm:
            media.append({
                "kind": "图片",
                "url": _unescape_url(sm.group(1)),
                "poster": None,
            })

    for m in re.finditer(r"<video\b([^>]*)>", html_src):
        attrs = m.group(1)
        sm = re.search(r'\ssrc="([^"]+)"', attrs)
        if not sm:
            continue
        pm = re.search(r'\sposter="([^"]*)"', attrs)
        media.append({
            "kind": "视频",
            "url": _unescape_url(sm.group(1)),
            "poster": _unescape_url(pm.group(1)) if pm else None,
        })

    return media


def _html_to_text(html_src):
    """推文 HTML → 纯文本：<br>→换行，去 img/video 等标签，最后解实体

    顺序必须是先剥标签再解实体：推文里的 "<3" 之类字符在 HTML 层
    被转义为 &lt;3，先解实体会让剥标签的正则误伤正文。"""
    s = re.sub(r"<br\s*/?>", "\n", html_src)
    s = re.sub(r"<img\b[^>]*>", "", s)
    s = re.sub(r"<video\b[^>]*>.*?</video>", "", s, flags=re.S)
    s = re.sub(r"<video\b[^>]*/?>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    # &nbsp; 渲染成普通空格；RSS 每个换行都是 <br>，连续换行压成一段
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_main(main_html, title):
    """主推 HTML + title → (is_retweet, rt_author, 正文, media)

    RT 判定同时要求 title 与 description 以 RT 开头（RSSHub 对转推
    两者都会加前缀），避免正文碰巧以 "RT " 开头的原创推文误判。
    description 首行 "RT\\xa0{作者}" 整行为作者（显示名可含空格）。"""
    text = _html_to_text(main_html)
    media = _extract_media(main_html)

    if title.startswith("RT ") and text.startswith("RT"):
        m = _RT_LINE_RE.match(text)
        if m and m.group(1).strip():
            return True, m.group(1).strip(), text[m.end():].strip(), media

    return False, None, text, media


def _parse_quote(quote_html):
    """引用块 HTML → {author, text, media}；解析失败返回 None"""
    if not quote_html or not quote_html.strip():
        return None

    text = _html_to_text(quote_html)
    media = _extract_media(quote_html)

    author = None
    m = _QUOTE_AUTHOR_RE.match(text)
    if m:
        author = m.group(1).strip()
        text = text[m.end():].strip()

    if not text and not media:
        return None

    return {
        "author": author or "",
        "text": text,
        "media": media,
    }


def parse_item(item_el):
    """RSS <item> Element → 推文 dict；缺少 guid/status ID 等关键字段
    时返回 None（调用方跳过该条，不影响其他 item）"""
    guid = (item_el.findtext("guid") or "").strip()
    status_id = parse_status_id(guid)
    if not status_id:
        return None

    title = (item_el.findtext("title") or "").strip()
    desc_html = item_el.findtext("description") or ""
    link = (item_el.findtext("link") or "").strip()

    main_html, quote_html = _split_quote(desc_html)
    is_rt, rt_author, text, media = _parse_main(main_html, title)

    quote = _parse_quote(quote_html) if quote_html is not None else None

    author = (item_el.findtext("author") or "").strip()
    hashtags = [
        (el.text or "").strip()
        for el in item_el.findall("category")
        if (el.text or "").strip()
    ]

    return {
        "id": status_id,
        "guid": guid,
        "link": link,
        "author": author,
        "pub_date": (item_el.findtext("pubDate") or "").strip(),
        "title": title,
        "is_retweet": is_rt,
        "rt_author": rt_author,
        "text": text,
        "media": media,
        "quote": quote,
        "hashtags": hashtags,
    }


def parse_feed(xml_bytes):
    """RSS XML bytes → [推文 dict]（保持 feed 顺序，通常新→旧）

    单条 item 解析异常只跳过该条（记入返回值之外的日志由调用方
    通过返回条数对比发现），不让整份 feed 失败；XML 根解析失败
    （RSSHub 挂了/返回错误页）时抛出异常，由调用方整轮放弃且
    不推进游标。"""
    root = ET.fromstring(xml_bytes)
    posts = []
    for item_el in root.findall(".//item"):
        try:
            post = parse_item(item_el)
        except Exception:
            continue
        if post is not None:
            posts.append(post)
    return posts
