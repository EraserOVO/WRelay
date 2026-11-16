"""Steam game news, price and creator release monitoring for QQ."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from pathlib import Path

import httpx
from nonebot import get_app, get_driver, logger

from plugins import bot_profiles, config, personal_qq
from plugins.dedup import normalize_channel_map, select_target_groups
from plugins.history import load_last_messages, load_last_messages_async
from plugins.json_io import atomic_write_json
from plugins.media import make_text
from plugins.paths import data_path
from plugins.retry import send_and_record
from plugins.sender import get_qq_bots
from plugins.stats import record
from plugins.steam_data import (app_id, bundle_details, bundle_ids, creator_app_ids, creator_clan_id,
                                creator_url, game_details, parse_announcements, parse_news, store_items)
from plugins import steam_state as logic

_states = {}
_locks = {}
_delivery_locks = {}
_status = {}
_next_poll = {}
_retry_after = {}
_request_lock = asyncio.Lock()
_last_request = 0.0
_client = None
_proxy_client = None
_proxy_address = None
_tasks = []
_poll_jobs = {}
_previous_connected = set()
_poll_suspended = set()
_stopping = False


def _path(owner):
    if owner:
        return bot_profiles.runtime_path(config.SETTINGS_FILE, owner, "steam.json")
    return data_path("steam.json")


def _load(owner):
    path = Path(_path(owner))
    try:
        with path.open(encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return logic.empty_state()
    except (OSError, ValueError) as exc:
        raise RuntimeError("Steam 运行状态读取失败，已保留原文件: " + str(exc)) from exc
    return logic.validate_state(state)


async def _get_state(owner):
    if owner not in _states:
        _states[owner] = await asyncio.to_thread(_load, owner)
    return _states[owner]


async def _change(owner, operation):
    async with _locks.setdefault(owner, asyncio.Lock()):
        old = await _get_state(owner)
        new = copy.deepcopy(old)
        result = operation(new)
        if new != old:
            logic.validate_state(new)
            write = asyncio.create_task(asyncio.to_thread(atomic_write_json, _path(owner), new, indent=2))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError as cancellation:
                # A file write cannot be cancelled once its worker starts.
                # Keep the lock and publish a successful write before another
                # operation can replace it with an older in-memory snapshot.
                while not write.done():
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not write.cancelled() and write.exception() is None:
                    _states[owner] = new
                raise cancellation
            else:
                _states[owner] = new
        return result


async def _snapshot(owner):
    async with _locks.setdefault(owner, asyncio.Lock()):
        return copy.deepcopy(await _get_state(owner))


async def _client_instance(use_proxy=False):
    global _client, _proxy_client, _proxy_address
    if not use_proxy:
        if _client is None:
            _client = httpx.AsyncClient(follow_redirects=True, timeout=30, trust_env=False,
                                        headers={"User-Agent": "WRelay Steam monitor"})
        return _client
    proxy = str(getattr(get_driver().config, "discord_proxy", "") or "").strip()
    if not proxy:
        return await _client_instance(False)
    if _proxy_client is None or _proxy_address != proxy:
        if _proxy_client is not None:
            await _proxy_client.aclose()
        _proxy_client = httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=30, trust_env=False,
                                          headers={"User-Agent": "WRelay Steam monitor"})
        _proxy_address = proxy
    return _proxy_client


async def _get(url, params=None):
    """Bound request rate across accounts and retry transient failures."""
    global _last_request
    use_proxy = False
    for attempt in range(3):
        try:
            async with _request_lock:
                delay = 1.0 - (time.monotonic() - _last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
                _last_request = time.monotonic()
                response = await (await _client_instance(use_proxy)).get(url, params=params)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 0
            if attempt == 2 or (status and status != 429 and status < 500):
                raise
            if not status and getattr(get_driver().config, "discord_proxy", None):
                use_proxy = True
            await asyncio.sleep((3, 10)[attempt])
    raise RuntimeError("Steam 请求失败")


async def _news(appid, watermark, feeds=None):
    items, known, enddate = [], set(), None
    for page in range(10):
        params = {"appid": appid, "count": 100, "maxlength": 1}
        if feeds:
            params["feeds"] = feeds
        if enddate is not None:
            params["enddate"] = enddate
        response = await _get("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/", params)
        batch = parse_news(response.json(), appid)
        for item in batch:
            if item["id"] not in known:
                known.add(item["id"])
                items.append(item)
        if not batch or len(batch) < 100 or watermark is None:
            return items
        oldest = min(logic.timestamp(item["published"]) for item in batch)
        if oldest <= watermark:
            return items
        if enddate is not None and oldest >= enddate:
            raise RuntimeError("Steam 新闻分页未推进，保留当前游标")
        enddate = oldest
    raise RuntimeError("Steam 新闻积压超过单轮读取上限，保留当前游标")


def _watermark(state, source_id, groups, event, sig):
    values = []
    for group in groups:
        entry = state["routes"].get(source_id, {}).get(group, {}).get(event, {})
        if entry.get("signature") == sig and isinstance(entry.get("watermark"), int):
            values.append(entry["watermark"])
    return min(values) if values else None


async def _poll_game_feeds(owner, source, groups, limit):
    events = source.get("events", [])
    appid = source["value"]
    if "announcements" in events:
        sig = logic.signature(source["kind"], appid)
        state = await _snapshot(owner)
        watermark = _watermark(state, source["id"], groups, "announcements", sig)
        official_error = rss_error = None
        try:
            official = await _news(appid, watermark, "steam_community_announcements")
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            official_error = exc
            official = []
        # Either public feed can temporarily be unavailable. Keep the
        # authoritative watermark unchanged during an API outage so the API
        # can later fill gaps older than the limited RSS window.
        try:
            rss = await _get("https://steamcommunity.com/games/%s/rss" % appid)
            official.extend(parse_announcements(rss.content))
        except (httpx.HTTPError, ValueError) as exc:
            rss_error = exc
            logger.info("Steam 公告 RSS 不可用，使用官方新闻公告来源: {} {}", appid, exc)
        if official_error and rss_error:
            raise RuntimeError("Steam 公告接口和 RSS 均不可用：%s；%s" % (official_error, rss_error))
        for group in groups:
            await _change(owner, lambda state, g=group: logic.feed_update(state, source, g, "announcements", official, sig, limit, advance_watermark=official_error is None))
        if official_error:
            raise RuntimeError("Steam 公告接口暂不可用，已通过 RSS 检查并保留补查游标：%s" % official_error)
    if "news" in events:
        feeds = sorted(set(source.get("news_feeds", [])))
        sig = logic.signature(source["kind"], appid, feeds)
        state = await _snapshot(owner)
        watermark = _watermark(state, source["id"], groups, "news", sig)
        news = await _news(appid, watermark, ",".join(feeds) if feeds else None)
        if feeds:
            news = [item for item in news if item["feed"] in feeds]
        for group in groups:
            await _change(owner, lambda state, g=group: logic.feed_update(state, source, g, "news", news, sig, limit))


async def _creator_games(source):
    page = await _get(source["value"])
    clan = creator_clan_id(page.text)
    response = await _get("https://store.steampowered.com/curator/%s/ajaxgetcreatorhomeinfo" % clan,
                          {"get_appids": 1, "l": "english"})
    role = "developers" if source["value"].split("/")[3].lower() in {"developer", "dev"} else "publishers"
    return creator_app_ids(response.json(), clan), clan, role


async def _details(appid, region, cache):
    key = (appid, region)
    if key not in cache:
        response = await _get("https://store.steampowered.com/api/appdetails",
                              {"appids": appid, "cc": region.lower(), "l": "english"})
        cache[key] = game_details(response.json(), appid)
    return cache[key]


async def _browse(ids, field, region):
    request = {"ids": [{field: int(value)} for value in ids],
               "context": {"country_code": region, "language": "english", "steam_realm": 1},
               "data_request": {"include_basic_info": True, "include_all_purchase_options": True}}
    response = await _get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
                          {"input_json": json.dumps(request, separators=(",", ":"))})
    return store_items(response.json(), ids, field)


async def _poll_bundles(owner, source, groups, region, limit, candidate_apps, clan=None, role=None):
    """Inspect bundle offers attached to observable games, retaining state on incomplete batches."""
    found = set()
    errors = []
    for start in range(0, len(candidate_apps), 25):
        batch = candidate_apps[start:start + 25]
        try:
            rows = await _browse(batch, "appid", region)
            for row in rows.values():
                if row.get("visible") is True:
                    found.update(bundle_ids(row))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            errors.append("游戏购买选项：" + str(exc)[:120])
    ids = sorted(found, key=int)
    for start in range(0, len(ids), 25):
        batch = ids[start:start + 25]
        try:
            rows = await _browse(batch, "bundleid", region)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            errors.append("捆绑包数据：" + str(exc)[:120])
            continue
        for bundleid, row in rows.items():
            try:
                bundle = bundle_details(row, bundleid, clan, role)
                if bundle is not None:
                    for group in groups:
                        await _change(owner, lambda state, g=group: logic.bundle_update(state, source, g, bundle, region, limit))
                if len((await _snapshot(owner))["queue"]) >= limit:
                    raise OverflowError("Steam 待投递队列已满，等待队列发送")
            except OverflowError:
                raise
            except ValueError as exc:
                errors.append("捆绑包 %s：%s" % (bundleid, str(exc)[:120]))
    if errors:
        raise RuntimeError("%d 项 Steam 捆绑包检查失败：%s" % (len(errors), "; ".join(errors[:3])))


async def _poll_source(owner, source, groups, region, limit, cache):
    clan = role = None
    if source["kind"] == "game":
        source["value"] = app_id(source["value"])
        # A game name is useful even when only its announcements are enabled.
        # Feed delivery can still proceed when the store is temporarily down.
        try:
            details = await _details(source["value"], region, cache)
            if not source.get("name"):
                source["name"] = details["name"]
        except (httpx.HTTPError, ValueError):
            pass
        await _poll_game_feeds(owner, source, groups, limit)
        appids = [source["value"]] if "discount" in source["events"] else []
    else:
        source["value"] = creator_url(source["value"])
        appids, clan, role = await _creator_games(source)
        if any(event in source["events"] for event in ("new_listing", "early_access", "release", "full_release")):
            sig = logic.signature(source["kind"], source["value"])
            for group in groups:
                await _change(owner, lambda state, g=group: logic.catalog_update(state, source, g, appids, sig))
        if not source["events"]:
            return 0
    failures = []
    candidate_apps = []
    visited = set(appids)
    for index, appid in enumerate(appids):
        _status.setdefault(owner, {}).update(scanning=True, current_source=source.get("name") or source["value"],
                                             scanned=index, scan_total=len(appids))
        try:
            game = await _details(appid, region, cache)
            if "discount" in source["events"] and source.get("include_dlc") is True and game["type"] == "game":
                for dlc in game.get("dlc", []):
                    if dlc not in visited:
                        visited.add(dlc)
                        appids.append(dlc)
            if game["type"] == "game" or (game["type"] == "dlc" and source.get("include_dlc") is True):
                candidate_apps.append(appid)
            for group in groups:
                await _change(owner, lambda state, g=group: logic.game_update(state, source, g, game, region, limit))
            if len((await _snapshot(owner))["queue"]) >= limit:
                raise OverflowError("Steam 待投递队列已满，等待队列发送")
        except asyncio.CancelledError:
            raise
        except OverflowError:
            raise
        except Exception as exc:
            failures.append("%s: %s" % (appid, str(exc)[:120]))
    if "discount" in source["events"] and source.get("include_bundles") is True:
        try:
            await _poll_bundles(owner, source, groups, region, limit, candidate_apps, clan, role)
        except OverflowError:
            raise
        except Exception as exc:
            failures.append(str(exc)[:160])
    if failures:
        raise RuntimeError("%d 个 Steam 游戏读取失败：%s" % (len(failures), "; ".join(failures[:3])))
    return len(appids) if source["kind"] == "creator" else 1


async def _config(owner):
    with bot_profiles.scope(owner):
        return await asyncio.to_thread(config.get_steam_polling_snapshot)


async def _poll_owner(owner, settings):
    started = time.time()
    sources, routes = settings["sources"], settings["routes"]
    initial_state = await _snapshot(owner)
    sequence_before = initial_state["sequence"]
    _status[owner] = {"ok": True, "running": True, "scanning": True, "last_poll_ts": int(started),
                      "last_error": "", "source_health": [],
                      "sources_total": settings["sources_total"], "sources_enabled": settings["sources_enabled"],
                      "last_new_count": 0, "last_sent_count": 0}
    await _change(owner, lambda state: logic.reconcile_routes(state, sources, routes))
    failures, health, cache = [], [], {}
    limit = settings["protection"].get("backlog_limit", 500)
    for source in sources:
        groups = routes.get(source["id"], [])
        if not groups or not source.get("events"):
            continue
        if len((await _snapshot(owner))["queue"]) >= limit:
            failures.append("Steam 待投递队列已满，等待队列发送")
            break
        try:
            count = await _poll_source(owner, source, groups, settings["region"], limit, cache)
            health.append({"id": source["id"], "name": source.get("name") or source["value"], "error": "", "games": count})
            if len((await _snapshot(owner))["queue"]) >= limit:
                failures.append("Steam 待投递队列已满，等待队列发送")
                break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc)[:240]
            logger.warning("Steam 来源检查失败 {}: {}", source.get("value"), message)
            failures.append(message)
            health.append({"id": source["id"], "name": source.get("name") or source["value"], "error": message})
    state = await _snapshot(owner)
    current_status = _status.get(owner) or {}
    _status[owner] = {"ok": not failures, "running": True, "scanning": False, "last_poll_ts": int(started),
                      "last_error": "; ".join(failures[:3]), "source_health": health,
                      "sources_total": settings["sources_total"], "sources_enabled": settings["sources_enabled"],
                      "last_new_count": max(0, state["sequence"] - sequence_before),
                      "last_sent_count": int(current_status.get("last_sent_count", 0) or 0),
                      "queue_pending": len(state["queue"])}


def _routable(item, settings, sources=None):
    if item["group"] not in settings["routes"].get(item["source"], []):
        return False
    sources = sources if sources is not None else {source["id"]: source for source in settings.get("sources", [])}
    source = sources.get(item["source"])
    if not source or not set([item["event"], *item.get("also_events", [])]).intersection(source.get("events", [])):
        return False
    if item.get("category") == "dlc" and source.get("include_dlc") is not True:
        return False
    if item.get("category") == "bundle" and source.get("include_bundles") is not True:
        return False
    value = app_id(source["value"]) if source["kind"] == "game" else creator_url(source["value"])
    return value == item["source_value"]


async def _dispatch_owner(owner, settings=None):
    async with _delivery_locks.setdefault(owner, asyncio.Lock()):
        await _dispatch_locked(owner, settings if settings is not None else await _config(owner))


async def _dispatch_locked(owner, settings):
    state = await _snapshot(owner)
    now = time.time()
    protection = settings["protection"]
    sent = [value for value in state["sent_at"] if now - value < 3600]
    if sent and now - sent[-1] < protection.get("min_send_interval", 60):
        return
    if len(sent) >= protection.get("hourly_limit", 30):
        return
    # Preserve sequence order for each source/group while letting other routes
    # proceed if one destination has failed or was disabled.
    blocked = set()
    chosen = None
    sources = {source["id"]: source for source in settings.get("sources", [])}
    for item in state["queue"]:
        route = (item["source"], item["group"])
        if route in blocked:
            continue
        blocked.add(route)
        if not _routable(item, settings, sources):
            continue
        if _retry_after.get((owner, item["seq"]), 0) > now:
            continue
        chosen = item
        break
    if chosen is None:
        return
    channel = "steam:" + hashlib.sha256((owner + ":" + state["epoch"] + ":" + chosen["source"]).encode()).hexdigest()
    msg_id = str(chosen["seq"])
    with bot_profiles.scope(owner):
        last = await load_last_messages_async(load_last_messages)
        targets = select_target_groups(normalize_channel_map(last, channel), [chosen["group"]], msg_id)
        if targets:
            try:
                result = await send_and_record(channel, msg_id, [make_text(chosen["text"])], [], targets,
                                               idempotency_key=chosen["identity"])
            except Exception:
                _retry_after[(owner, chosen["seq"])] = now + 60
                raise
            delivered = bool(result.get(chosen["group"]))
            await record(delivered)
        else:
            delivered = True
    if delivered:
        def finish(current):
            current["queue"] = [item for item in current["queue"] if item["seq"] != chosen["seq"]]
            current["sent_at"] = [stamp for stamp in current["sent_at"] if now - stamp < 3600] + [now]
        await _change(owner, finish)
        current_status = _status.setdefault(owner, {})
        current_status["last_sent_count"] = int(current_status.get("last_sent_count", 0) or 0) + 1
        _retry_after.pop((owner, chosen["seq"]), None)
    else:
        _retry_after[(owner, chosen["seq"])] = now + 60


async def _connected_owners():
    owners = await asyncio.to_thread(bot_profiles.ids, config.SETTINGS_FILE)
    connected = set(get_qq_bots()) | set(personal_qq.connected_bots())
    return [owner for owner in owners if owner != "unassigned" and owner in connected] if owners else ([""] if connected else [])


async def _poll_job(owner):
    try:
        settings = await _config(owner)
        await _poll_owner(owner, settings)
        _next_poll[owner] = time.monotonic() + (settings["interval"] if _status.get(owner, {}).get("ok") else min(60, settings["interval"]))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Steam 轮询失败: {}", owner or "默认账号")
        _status[owner] = {"ok": False, "running": True, "scanning": False, "last_error": str(exc)[:240]}
        _next_poll[owner] = time.monotonic() + 60


async def _poll_loop():
    global _previous_connected
    while True:
        try:
            owners = await _connected_owners()
            current = set(owners)
            for owner in current - _previous_connected:
                _next_poll.pop(owner, None)
            _previous_connected = current
            for owner in owners:
                if owner in _poll_suspended:
                    continue
                now = time.monotonic()
                job = _poll_jobs.get(owner)
                if (job is not None and not job.done()) or now < _next_poll.get(owner, 0):
                    continue
                _poll_jobs[owner] = asyncio.create_task(_poll_job(owner))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Steam 轮询调度失败")
        await asyncio.sleep(5)


async def _dispatch_loop():
    while True:
        try:
            for owner in await _connected_owners():
                try:
                    await _dispatch_owner(owner)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Steam 待投递队列处理失败 {}: {}", owner or "默认账号", exc)
                    _status.setdefault(owner, {}).update(ok=False, last_error=str(exc)[:240])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Steam 投递调度失败")
        await asyncio.sleep(5)


@get_driver().on_startup
async def _start():
    global _stopping, _previous_connected
    _stopping = False
    _previous_connected = set()
    if not _tasks:
        _tasks.extend([asyncio.create_task(_poll_loop()), asyncio.create_task(_dispatch_loop())])


@get_driver().on_shutdown
async def _stop():
    global _client, _proxy_client, _proxy_address, _stopping
    _stopping = True
    for task in _tasks:
        task.cancel()
    for task in _poll_jobs.values():
        task.cancel()
    await asyncio.gather(*_tasks, *_poll_jobs.values(), return_exceptions=True)
    _tasks.clear()
    _poll_jobs.clear()
    if _client is not None:
        await _client.aclose()
        _client = None
    if _proxy_client is not None:
        await _proxy_client.aclose()
        _proxy_client = None
        _proxy_address = None


@get_app().get("/api/steam/status")
async def api_steam_status(bot_id: str = ""):
    if bot_id:
        try:
            bot_profiles.filename(bot_id)
        except ValueError:
            return {"ok": False, "last_error": "账号标识无效"}
    try:
        state = await _snapshot(bot_id)
        result = dict(_status.get(bot_id) or {"ok": True, "running": bool(_tasks), "last_error": ""})
        result["bot_running"] = bool(_tasks)
        result["queue_pending"] = len(state["queue"])
        result["protection_queue_pending"] = len(state["queue"])
        settings = await _config(bot_id)
        result["sources_total"] = settings["sources_total"]
        result["sources_enabled"] = settings["sources_enabled"]
        result.setdefault("last_new_count", 0)
        result.setdefault("last_sent_count", 0)
        connected = set(get_qq_bots()) | set(personal_qq.connected_bots())
        result["qq_connected"] = bot_id in connected if bot_id else bool(connected)
        sources = {source["id"]: source for source in settings["sources"]}
        result["queue_paused"] = sum(not _routable(item, settings, sources) for item in state["queue"])
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        return {"ok": False, "running": bool(_tasks), "last_error": str(exc)[:240]}


@get_app().post("/api/steam/clear-paused")
async def api_steam_clear_paused(bot_id: str = ""):
    try:
        owners = await asyncio.to_thread(bot_profiles.ids, config.SETTINGS_FILE)
        if (owners and (not bot_id or bot_id not in owners or bot_id == "unassigned")) or (not owners and bot_id):
            raise ValueError("请选择有效的 QQ 账号")
        async with _delivery_locks.setdefault(bot_id, asyncio.Lock()):
            _poll_suspended.add(bot_id)
            _next_poll[bot_id] = time.monotonic() + 60
            try:
                job = _poll_jobs.get(bot_id)
                if job is not None and not job.done():
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                settings = await _config(bot_id)
                sources = {source["id"]: source for source in settings["sources"]}
                def discard(state):
                    before = len(state["queue"])
                    state["queue"] = [item for item in state["queue"] if _routable(item, settings, sources)]
                    return before - len(state["queue"])
                removed = await _change(bot_id, discard)
            finally:
                _next_poll[bot_id] = 0
                _poll_suspended.discard(bot_id)
        return {"ok": True, "removed": removed}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"ok": False, "msg": str(exc)[:240]}
