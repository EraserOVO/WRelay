"""Steam source parsing helpers for the planned Steam delivery integration.

The creator-home and store-detail endpoints are not documented public APIs;
callers must treat incomplete or malformed responses as failures, not empty data.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit


def app_id(value):
    text = str(value or "").strip()
    if text.isdecimal() and int(text) > 0:
        return str(int(text))
    try:
        url = urlsplit(text)
    except ValueError:
        return ""
    if url.scheme != "https" or url.hostname not in {"store.steampowered.com", "steamcommunity.com"}:
        return ""
    match = re.match(r"^/(?:app|games)/(\d+)(?:/|$)", url.path)
    return str(int(match.group(1))) if match and int(match.group(1)) else ""


def creator_url(value):
    try:
        url = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if url.scheme != "https" or url.hostname != "store.steampowered.com":
        return ""
    match = re.fullmatch(r"/(developer|publisher|dev|pub)/([A-Za-z0-9_-]+)/?", url.path, re.I)
    return "https://store.steampowered.com/%s/%s" % match.groups() if match else ""


def creator_clan_id(page):
    text = html.unescape(str(page or ""))
    ids = set(re.findall(r'["\']clanAccountID["\']\s*:\s*(\d+)', text))
    if len(ids) != 1:
        raise ValueError("无法确定 Steam 开发商或发行商主页标识")
    return next(iter(ids))


def creator_app_ids(data, clan_id):
    if not isinstance(data, dict) or data.get("success") != 1 or str(data.get("creator_clan_id")) != str(clan_id):
        raise ValueError("Steam 主页游戏列表返回无效")
    ids = data.get("appids")
    if not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) or i <= 0 for i in ids):
        raise ValueError("Steam 主页游戏列表不完整")
    return list(dict.fromkeys(str(i) for i in ids))


def game_details(payload, appid):
    row = payload.get(str(appid)) if isinstance(payload, dict) else None
    if not isinstance(row, dict) or row.get("success") is not True or not isinstance(row.get("data"), dict):
        raise ValueError("Steam 游戏详情不可用")
    data = row["data"]
    if str(data.get("steam_appid")) != str(appid) or not data.get("name") or not data.get("type"):
        raise ValueError("Steam 游戏详情不完整")
    price = data.get("price_overview")
    if price is not None and not isinstance(price, dict):
        raise ValueError("Steam 价格数据无效")
    release = data.get("release_date")
    if release is not None and (not isinstance(release, dict) or not isinstance(release.get("coming_soon"), bool)):
        raise ValueError("Steam 发行状态无效")
    dlc = data.get("dlc", [])
    if not isinstance(dlc, list) or any(type(item) is not int or item <= 0 for item in dlc):
        raise ValueError("Steam DLC 列表无效")
    return {"appid": str(appid), "name": data["name"], "type": data["type"],
            "price": price, "release": release, "free": bool(data.get("is_free")),
            "genres": data.get("genres") if isinstance(data.get("genres"), list) else None,
            "dlc": list(dict.fromkeys(str(item) for item in dlc))}


def store_items(payload, expected, field):
    """Read a complete StoreBrowse batch; never treat missing rows as an empty result."""
    response = payload.get("response") if isinstance(payload, dict) else None
    rows = response.get("store_items") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Steam 商店购买选项返回无效")
    result = {}
    expected_type = {"appid": 0, "bundleid": 2}.get(field)
    if expected_type is None:
        raise ValueError("Steam 商店商品类型无效")
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row.get("success") != 1 or row.get("item_type") != expected_type:
            raise ValueError("Steam 商店购买选项不完整")
        if str(row["id"]) in result:
            raise ValueError("Steam 商店购买选项存在重复条目")
        result[str(row["id"])] = row
    if set(result) != set(expected):
        raise ValueError("Steam 商店购买选项缺少请求的商品")
    return result


def bundle_ids(item):
    # StoreBrowse uses protobuf JSON and omits repeated fields when empty.
    options = item.get("purchase_options", [])
    if not isinstance(options, list):
        raise ValueError("Steam 商店购买选项不完整")
    result = []
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("Steam 商店购买选项无效")
        value = option.get("bundleid")
        if value is not None:
            if type(value) is not int or value <= 0:
                raise ValueError("Steam 捆绑包标识无效")
            result.append(str(value))
    return list(dict.fromkeys(result))


def bundle_details(item, bundleid, creator_id=None, creator_role=None):
    """Extract the public, region-specific bundle offer; None means unobservable."""
    if item.get("visible") is not True:
        return None
    basic = item.get("basic_info")
    if creator_id is not None:
        if not isinstance(basic, dict) or creator_role not in {"developers", "publishers"}:
            raise ValueError("无法确认捆绑包开发商或发行商")
        owners = basic.get(creator_role)
        if not isinstance(owners, list) or not any(isinstance(owner, dict) and owner.get("creator_clan_account_id") for owner in owners):
            raise ValueError("无法确认捆绑包开发商或发行商")
        if not any(isinstance(owner, dict) and str(owner.get("creator_clan_account_id") or "") == str(creator_id) for owner in owners):
            return None
    options = item.get("purchase_options", [])
    if not isinstance(options, list):
        raise ValueError("Steam 捆绑包购买选项无效")
    matches = [option for option in options if isinstance(option, dict) and str(option.get("bundleid")) == str(bundleid)]
    if len(matches) != 1:
        return None
    option = matches[0]
    price = option.get("final_price_in_cents")
    percent = option.get("combined_discount_pct", option.get("bundle_discount_pct", 0))
    if not isinstance(price, str) or not price.isascii() or not price.isdecimal() or type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError("Steam 捆绑包价格无效")
    name = option.get("purchase_option_name") or item.get("name")
    formatted = option.get("formatted_final_price")
    if not isinstance(name, str) or not name.strip() or not isinstance(formatted, str) or not formatted:
        raise ValueError("Steam 捆绑包名称或价格不完整")
    original = option.get("formatted_original_price")
    before_bundle = option.get("formatted_price_before_bundle_discount")
    return {"id": str(bundleid), "name": name, "final": int(price), "discount": percent,
            "final_formatted": formatted, "initial_formatted": str(original or before_bundle or ""),
            "initial_label": "原价" if original else "捆绑优惠前"}


def parse_announcements(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("Steam 公告 RSS 返回的不是有效 XML") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Steam 公告 RSS 无效")
    result = []
    for item in root.findall("./channel/item"):
        guid = (item.findtext("guid") or "").strip()
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not guid or not title or not link or not (item.findtext("pubDate") or "").strip():
            raise ValueError("Steam 公告条目不完整")
        result.append({"id": guid, "title": title, "link": link,
                       "published": (item.findtext("pubDate") or "").strip()})
    return result


def parse_news(data, appid):
    news = data.get("appnews") if isinstance(data, dict) else None
    if not isinstance(news, dict) or str(news.get("appid")) != str(appid) or not isinstance(news.get("newsitems"), list):
        raise ValueError("Steam 新闻数据无效")
    result = []
    for item in news["newsitems"]:
        if not isinstance(item, dict) or not item.get("gid") or not item.get("title") or not item.get("url"):
            raise ValueError("Steam 新闻条目不完整")
        result.append({"id": str(item["gid"]), "title": str(item["title"]),
                       "link": str(item["url"]), "feed": str(item.get("feedname") or ""),
                       "published": item.get("date")})
    return result
