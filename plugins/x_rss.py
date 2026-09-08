"""X（Twitter）RSS 订阅推送（RSSHub → QQ）。

独立于 Discord forwarding_groups 的链路，使用独立的 X 转发组
（settings.x_forwarding_groups，X 源 → X 组 → QQ 群）路由目标群：
RSSHub 拉取 → x_rss_parser 解析 → 去重 → 渲染 → sender.send_relay_message。

设计要点（全部沿用现有机制，不改动任何现有模块）：
- 轮询：QQ Bot 连接后启动，默认 300 秒/次（config.x_rss_poll_interval，
  对齐 RSSHub 缓存 max-age=300）；请求带 If-None-Match，304 时复用
  上次解析结果重跑发送循环（游标幂等，发送失败的群自动补投）
- 去重：tweet status ID（单调递增整数）做消息 ID，复用
  data/discord_last.json 的伪频道键 "x_rss:{handle}" 与 dedup.py
  纯函数（select_target_groups / apply_baseline / apply_success），
  按群记录游标，重启后天然恢复
- 首次订阅：该源在 discord_last.json 无任何记录时只建基线
  （基线 = 当前 feed 最大 status ID），不发送历史内容
- 新启用群：源已有记录但某启用群无游标时，先为该群建基线再发送，
  避免新群收到 feed 窗口内（最多 20 条）的历史刷屏
- 媒体：图片/视频统一 media.fetch_bytes_many 并发下载（走代理、
  30MB 上限）；视频 ≤30MB 用 file_video(bytes) 发本体，
  下载失败/超限降级为文字链接（sender 现有降级路径）
- 容错：单个源、单条 item、单次请求失败都只记日志，不影响
  其他源与轮询循环；RSS 拉取失败/解析失败绝不推进游标
"""

import asyncio
import re
import threading
import time

import httpx

from nonebot import get_app, get_bots, get_driver
from nonebot import logger

from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq import MessageSegment as QQMessageSegment

from plugins.config import (
    get_x_rss_sources,
    get_x_rss_poll_interval,
    get_x_groups_for_source,
)
from plugins.media import fetch_bytes_many, make_text, make_media_link
from plugins.sender import get_qq_bot
from plugins.stats import record
from plugins.history import (
    load_last_messages,
    update_last_messages,
)
from plugins.dedup import (
    apply_baseline,
    normalize_channel_map,
    select_target_groups,
)
from plugins.retry import send_and_record
from plugins.json_io import (
    load_json,
    atomic_write_json,
)
from plugins.paths import data_path
from plugins.x_rss_parser import parse_feed, QUOTE_TEXT_LIMIT


# RSS 拉取超时（RSSHub 为本机/内网服务，无需代理）
FEED_TIMEOUT = 30
# RSSHub 请求属于外部 I/O：有限并发避免慢源拖住整轮，
# 同时避免一次性并发过多触发 RSSHub/代理限流。
X_POLL_CONCURRENCY = 4

# 运行时状态：每源的 ETag / 最近轮询时间（data/ 已在 .gitignore，
# 文件首次轮询时自动创建，不进版本库）
X_STATE_FILE = data_path("x_rss.json")

# 每源最近一次解析结果（内存缓存）：304 时复用并重跑发送循环。
# 进程重启后缓存丢失，此时收到 304 会强制重取一次全量（自愈）。
_posts_cache = {}
_state_file_lock = threading.Lock()


# =====================================================
# 运行时状态（仅供管理面板 X → QQ 页状态卡片展示）
#
# 内存态，重启清零，不写入 settings.json 等用户配置。字段：
# - loop_started      轮询循环是否已启动（QQ Bot 连接后才启动）
# - last_poll_ts      上一轮轮询完成时间（epoch 秒；0 = 本进程尚未轮询过）
# - last_result       上一轮结果："ok" / "fetch_failed" / "parse_failed"
# - last_error        上一轮失败的简短原因（成功轮清空）
# - last_new_count    上一轮发现的新帖数（存在该帖未送达的目标群）
# - last_sent_count   上一轮发送成功帖数（至少一个群送达）
# - last_failed_count 上一轮发送失败帖数（有目标群但全部未送达，下轮自动补投）
# =====================================================

_runtime_status = {
    "loop_started": False,
    "last_poll_ts": 0,
    "last_result": "",
    "last_error": "",
    "last_new_count": 0,
    "last_sent_count": 0,
    "last_failed_count": 0,
}


def runtime_status():
    """返回 X RSS 运行时状态快照（面板状态接口使用）"""
    return dict(_runtime_status)


def _short_error(err, limit=120):
    """压成单行并截断的错误摘要（面板展示用，不带完整堆栈）"""
    return " ".join(str(err or "").split())[:limit]


def _load_state():
    data = load_json(X_STATE_FILE, default={})
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    return data


def _save_state(data):
    try:
        atomic_write_json(X_STATE_FILE, data, indent=2)
    except Exception as exc:
        # 状态文件写失败只影响 ETag 命中率，不影响去重正确性
        # （去重游标在 discord_last.json，由 history.py 负责落盘）
        logger.warning("X RSS 状态文件写入失败: {}", exc)


def _update_source_state(key, etag=None, clear_etag=False):
    """更新单个源的 ETag / 最近轮询时间并落盘。"""
    # 多源并发时保护 read-modify-write，避免互相覆盖 ETag。
    with _state_file_lock:
        state = _load_state()
        src_state = state["sources"].setdefault(key, {})
        src_state["last_poll_ts"] = int(time.time())
        if clear_etag:
            src_state.pop("etag", None)
        elif etag:
            src_state["etag"] = etag
        _save_state(state)


# =====================================================
# RSS 拉取（ETag / If-None-Match / 304）
# =====================================================

async def _fetch_feed(url, etag=None):
    """拉取 RSS；返回 (xml_bytes | None(304), 新etag | None, 错误 | None)

    - 200：返回 (内容, 本次ETag, None)
    - 304：返回 (None, 原etag, None) —— 内容未变
    - 其他/异常：返回 (None, None, 错误) —— 调用方不推进任何状态

    实测（RSSHub/Express）：带 gzip 时 compression 中间件会把强 ETag
    弱化为 W/"..."，而该服务端只对强形式回 304；因此请求 identity
    编码，并在回传 If-None-Match 时剥掉 W/ 前缀（弱比较语义下等价，
    对规范服务器同样成立）。"""
    headers = {"Accept-Encoding": "identity"}
    if etag:
        headers["If-None-Match"] = etag.removeprefix("W/")
    try:
        async with httpx.AsyncClient(
            timeout=FEED_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        return None, None, f"请求异常: {exc}"

    if resp.status_code == 304:
        return None, etag, None
    if resp.status_code != 200:
        return None, None, f"HTTP {resp.status_code}"
    return resp.content, resp.headers.get("etag"), None


# =====================================================
# 渲染：推文 dict → (text_parts, media_items)
# =====================================================

_IMAGE_EXT_RE = re.compile(r"[?&]format=(\w+)")


def _image_filename(url):
    """从 pbs.twimg.com URL 的 format= 参数推断文件名扩展名"""
    m = _IMAGE_EXT_RE.search(url or "")
    ext = m.group(1).lower() if m else "jpg"
    return f"image.{ext}"


async def _render_post(post, source_name):
    """推文 dict → (text_parts, media_items) 或 None（无可发送内容）

    文字层级与 RSS 顺序一致：header → RT 行 → 主推文字 → 引用行；
    媒体单独成条（主推媒体在前、引用媒体在后）。"""
    text_parts = []
    media_items = []

    text_parts.append(
        make_text(f"自动转发来自 X 中 [{source_name}] 的消息：")
    )

    has_body = False

    if post["is_retweet"] and post["rt_author"]:
        text_parts.append(
            make_text(f"\n↻ RT {post['rt_author']}：")
        )
        has_body = True

    if post["text"]:
        text_parts.append(
            make_text("\n" + post["text"])
        )
        has_body = True

    quote = post.get("quote")
    if quote:
        quote_text = quote.get("text") or ""
        if len(quote_text) > QUOTE_TEXT_LIMIT:
            quote_text = quote_text[:QUOTE_TEXT_LIMIT] + "…"
        text_parts.append(
            make_text(
                f"\n┃ 引用 {quote.get('author') or '（未知作者）'}：{quote_text}"
            )
        )
        has_body = True

    all_media = list(post["media"])
    if quote:
        all_media.extend(quote.get("media") or [])

    if all_media:
        has_body = True
        urls = [m["url"] for m in all_media]
        bytes_map = await fetch_bytes_many(urls)
        for m in all_media:
            data = bytes_map.get(m["url"])
            if data and m["kind"] == "图片":
                segment = QQMessageSegment.file_image(
                    data,
                    _image_filename(m["url"]),
                )
            elif data and m["kind"] == "视频":
                # ≤30MB（fetch_bytes 上限）：直接发视频本体
                segment = QQMessageSegment.file_video(
                    data,
                    "video.mp4",
                )
            else:
                # 下载失败/超限：降级为文字链接
                logger.warning(
                    "X 媒体下载失败，降级为链接: {} {}",
                    m["kind"],
                    m["url"],
                )
                segment = make_media_link(m["kind"], m["url"])
            media_items.append({
                "kind": m["kind"],
                "url": m["url"],
                "segment": segment,
            })

    if not has_body:
        # 纯 header 无任何内容（正文与媒体全为空的异常 item）不发送
        return None

    return text_parts, media_items


# =====================================================
# 去重游标（复用 discord_last.json + dedup.py）
# =====================================================

def _source_channel_key(source_key):
    """X 源在 discord_last.json 中的伪频道键"""
    return f"x_rss:{source_key}"


async def _ensure_baselines(channel_key, groups, latest_id):
    """确保所有启用群都有游标基线（只建游标，不触发发送）。

    - 源完全无记录（首次订阅）：全部群建基线，不发送历史内容
    - 源已有记录但个别群无游标（中途新启用的群）：只为这些群建基线，
      避免新群收到 feed 窗口内（最多 20 条）的历史刷屏
    返回是否产生变更。"""
    def _mutate(last):
        channel_map = normalize_channel_map(last, channel_key)
        if not channel_map:
            return apply_baseline(channel_map, groups, latest_id)
        missing = [
            group for group in groups
            if not (channel_map.get(group) or channel_map.get("*"))
        ]
        if missing:
            return apply_baseline(channel_map, missing, latest_id)
        return False

    return await update_last_messages(_mutate)


# =====================================================
# 发送循环（200 新内容与 304 缓存复用共用）
# =====================================================

async def _send_posts(source, posts):
    """对一份已解析的推文列表执行去重判断与发送（旧→新）。

    返回 (new_count, sent_count, failed_count)（仅供运行时状态统计，
    不参与任何转发决策）：new = 存在未送达目标群的帖数；
    sent = 至少一个群送达的帖数；failed = 有目标群但全部未送达
    （含渲染失败/发送异常，游标不动，下轮自动补投）。"""
    name = source["name"]
    channel_key = _source_channel_key(source["key"])

    if not posts:
        return (0, 0, 0)

    enabled_groups = get_x_groups_for_source(source["key"])
    if not enabled_groups:
        # 源未启用 / 未加入任何 X 转发组 / 组内群全部未启用
        logger.debug("X RSS 无可用目标群，跳过发送: {}", name)
        return (0, 0, 0)
    active_groups = enabled_groups

    latest_id = max(posts, key=lambda p: int(p["id"]))["id"]
    await _ensure_baselines(channel_key, active_groups, latest_id)

    sent_count = 0
    new_count = 0
    failed_count = 0
    for post in sorted(posts, key=lambda p: int(p["id"])):
        try:
            # 每条发送前重读最新游标（与 backfill 相同的实时性：
            # 并发路径已成功送达的群立即跳过）
            channel_map = normalize_channel_map(
                load_last_messages(),
                channel_key,
            )
            targets = select_target_groups(
                channel_map,
                active_groups,
                post["id"],
            )
            if not targets:
                continue

            rendered = await _render_post(post, name)
            if rendered is None:
                # 空 item（正文与媒体全为空）本轮无法投递，计失败
                failed_count += 1
                continue
            text_parts, media_items = rendered

            # 组状态可能在渲染期间被面板修改；发送前重新计算目标，避免暂停后仍投递一条。
            active_groups = get_x_groups_for_source(source["key"])
            if not active_groups:
                continue
            targets = select_target_groups(
                normalize_channel_map(load_last_messages(), channel_key),
                active_groups,
                post["id"],
            )
            if not targets:
                continue
            new_count += 1

            ok_map = await send_and_record(
                channel_key,
                post["id"],
                text_parts,
                media_items,
                targets,
            )

            if not ok_map:
                # QQ Bot 未连接：游标不动，下轮轮询补投
                # （不计发送失败：消息未尝试投递，实时 qq_connected 反映原因）
                logger.warning(
                    "QQ Bot 未连接，X 推文未发送: {} {}",
                    name,
                    post["id"],
                )
                return (new_count, sent_count, failed_count)

            if any(ok_map.values()):
                sent_count += 1
                await record(True)
                logger.info(
                    "X 推文已发送: {} {} -> {}",
                    name,
                    post["id"],
                    [g for g, ok in ok_map.items() if ok],
                )
            else:
                failed_count += 1
                await record(False)
                logger.warning(
                    "X 推文全部目标群发送失败（游标不动，下轮补投）: {} {}",
                    name,
                    post["id"],
                )
        except Exception as exc:
            failed_count += 1
            # 单条失败不影响本源后续条目
            logger.warning(
                "X 推文处理失败，跳过该条: {} {}: {}",
                name,
                post.get("id"),
                exc,
            )

    if sent_count:
        logger.success("X RSS 本轮发送 {} 条: {}", sent_count, name)

    return (new_count, sent_count, failed_count)


# =====================================================
# 单源轮询
# =====================================================

async def _poll_source(source):
    """轮询一个 X 源。任何失败只记日志返回，绝不推进去重游标。

    返回单源结果 dict（供轮次汇总运行时状态，不影响转发行为）：
    {result: "ok"/"fetch_failed"/"parse_failed", error: str,
     new/sent/failed: int}"""
    key = source["key"]
    url = source["url"]
    name = source["name"]

    state = _load_state()
    etag = state["sources"].get(key, {}).get("etag")
    cached_posts = _posts_cache.get(key)

    body, new_etag, err = await _fetch_feed(url, etag)

    if err is None and body is None:
        if cached_posts is not None:
            # 304：内容未变，复用上次解析结果重跑发送循环
            # （游标幂等：上次发送失败的群在此自动补投）
            logger.debug("X RSS 304 未变化: {}", name)
            new_n, sent_n, failed_n = await _send_posts(source, cached_posts)
            return {"result": "ok", "error": "",
                    "new": new_n, "sent": sent_n, "failed": failed_n}
        # 重启后缓存丢失且命中 304：强制重取一次全量
        body, new_etag, err = await _fetch_feed(url, None)

    if err:
        logger.warning("X RSS 拉取失败，本轮跳过: {} ({}): {}", name, key, err)
        return {"result": "fetch_failed", "error": _short_error(err),
                "new": 0, "sent": 0, "failed": 0}

    try:
        posts = parse_feed(body)
    except Exception as exc:
        # XML 损坏/RSSHub 路由错误页：本轮放弃，游标不动；
        # 丢弃 ETag，下轮强制 200 重新拉取
        logger.warning("X RSS 解析失败，本轮跳过: {} ({}): {}", name, key, exc)
        _update_source_state(key, clear_etag=True)
        return {"result": "parse_failed", "error": _short_error(exc),
                "new": 0, "sent": 0, "failed": 0}

    # 解析成功才更新 ETag 与缓存
    _update_source_state(key, etag=new_etag)
    _posts_cache[key] = posts

    new_n, sent_n, failed_n = await _send_posts(source, posts)
    return {"result": "ok", "error": "",
            "new": new_n, "sent": sent_n, "failed": failed_n}


# =====================================================
# 轮询循环与启动
# =====================================================

async def _poll_all():
    """有限并发轮询全部启用源；单源异常不影响其他源。

    一轮结束后把聚合结果（完成时间/结果/新帖与发送计数）写入
    _runtime_status 供面板展示。状态文件的 read-modify-write 由锁保护。"""
    sources = [
        s for s in get_x_rss_sources()
        if s["enabled"]
    ]
    if not sources:
        return

    sem = asyncio.Semaphore(X_POLL_CONCURRENCY)

    async def poll_one(source):
        async with sem:
            try:
                return await _poll_source(source)
            except Exception as exc:
                # 单源任何未预期异常都不拖垮其他源与轮询循环
                logger.exception("X RSS 源轮询异常: {} ({})", source["name"], source["key"])
                return {"result": "fetch_failed", "error": _short_error(exc),
                        "new": 0, "sent": 0, "failed": 0}

    results = await asyncio.gather(*(poll_one(source) for source in sources))
    new_total = sent_total = failed_total = 0
    bad_result = ""
    bad_error = ""
    for res in results:
        new_total += res.get("new", 0)
        sent_total += res.get("sent", 0)
        failed_total += res.get("failed", 0)
        # 取本轮第一个失败源作为轮次结果（全部成功则记 ok）
        if not bad_result and res.get("result") not in (None, "ok"):
            bad_result = res["result"]
            bad_error = res.get("error") or ""

    st = _runtime_status
    st["last_poll_ts"] = int(time.time())
    st["last_new_count"] = new_total
    st["last_sent_count"] = sent_total
    st["last_failed_count"] = failed_total
    st["last_result"] = bad_result or "ok"
    st["last_error"] = bad_error


async def _poll_loop():
    while True:
        try:
            await _poll_all()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("X RSS 轮询异常")
        await asyncio.sleep(get_x_rss_poll_interval())


_loop_started = False


@get_driver().on_bot_connect
async def _start_poll_loop(bot):
    """QQ Bot 连接后启动 X RSS 轮询（仅启动一次，QQ 未连接时不发送）"""
    global _loop_started
    if not isinstance(bot, QQBot):
        return
    if _loop_started:
        return
    _loop_started = True
    _runtime_status["loop_started"] = True
    logger.info("X RSS 轮询已启动（间隔 {}s）", get_x_rss_poll_interval())
    asyncio.create_task(_poll_loop())


# =====================================================
# 面板状态接口（GET /api/x-rss/status）
#
# 供管理面板 X → QQ 页状态卡片轮询：运行时状态 + 实时 QQ 连接
# + 当前生效的源数/轮询间隔。与 /api/health 的 Discord/QQ 连接
# 状态相互独立，不把 X RSS 状态塞进 Discord/QQ 的连接布尔值里。
# =====================================================

_xrss_app = get_app()


@_xrss_app.get("/api/x-rss/status")
async def api_x_rss_status():
    bots = get_bots()
    sources = get_x_rss_sources()
    data = runtime_status()
    data.update({
        "ok": True,
        "running": bool(data.get("loop_started")),
        "qq_connected": any(isinstance(b, QQBot) for b in bots.values()),
        "poll_interval": get_x_rss_poll_interval(),
        "sources_total": len(sources),
        "sources_enabled": sum(1 for s in sources if s["enabled"]),
    })
    return data
