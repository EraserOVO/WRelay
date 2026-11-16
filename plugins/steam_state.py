"""Steam change detection and durable queue state, without network or NoneBot."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import timezone
from email.utils import parsedate_to_datetime


def empty_state():
    return {"version": 1, "epoch": uuid.uuid4().hex, "sequence": 0, "routes": {}, "queue": [], "sent_at": []}


def validate_state(state):
    """Reject damaged state before it can be overwritten or restored."""
    def fail():
        raise ValueError("Steam 运行状态格式无效")
    if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1 or type(state.get("sequence")) is not int or state["sequence"] < 0:
        fail()
    if not isinstance(state.get("epoch"), str) or not re.fullmatch(r"[0-9a-f]{32}", state["epoch"]):
        fail()
    if not isinstance(state.get("routes"), dict) or not isinstance(state.get("queue"), list) or len(state["queue"]) > 10000:
        fail()
    stamps = state.get("sent_at")
    if not isinstance(stamps, list) or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in stamps):
        fail()
    seen = set()
    previous_seq = 0
    for item in state["queue"]:
        if not isinstance(item, dict) or type(item.get("seq")) is not int or not 0 < item["seq"] <= state["sequence"]:
            fail()
        if item["seq"] <= previous_seq or item["seq"] in seen or any(not isinstance(item.get(k), str) or not item[k] or len(item[k]) > 5000 for k in ("source", "group", "identity", "text", "event", "source_value")):
            fail()
        if item["event"] not in {"announcements", "news", "discount", "new_listing", "early_access", "release", "full_release"}:
            fail()
        if not isinstance(item.get("category", "game"), str) or item.get("category", "game") not in {"game", "dlc", "bundle"}:
            fail()
        alternatives = item.get("also_events", [])
        if (not isinstance(alternatives, list) or len(alternatives) > 7
                or any(not isinstance(x, str) or x not in {"announcements", "news", "discount", "new_listing", "early_access", "release", "full_release"} for x in alternatives)
                or len(alternatives) != len(set(alternatives)) or item["event"] in alternatives):
            fail()
        seen.add(item["seq"])
        previous_seq = item["seq"]
    for source, groups in state["routes"].items():
        if not isinstance(source, str) or not source or not isinstance(groups, dict):
            fail()
        for group, events in groups.items():
            if not isinstance(group, str) or not group or not isinstance(events, dict):
                fail()
            for event, value in events.items():
                if event not in {"announcements", "news", "discount", "bundle_discount", "catalog", "early_access", "release", "full_release"} or not isinstance(value, dict):
                    fail()
                if not isinstance(value.get("signature"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["signature"]):
                    fail()
                if event in {"announcements", "news"}:
                    if type(value.get("watermark")) is not int or value["watermark"] < 0 or not isinstance(value.get("seen"), list) or any(not isinstance(x, str) for x in value["seen"]):
                        fail()
                elif event == "catalog":
                    for key in ("known", "pending"):
                        if not isinstance(value.get(key), list) or any(not isinstance(x, str) or not x.isascii() or not x.isdecimal() for x in value[key]):
                            fail()
                else:
                    apps = value.get("apps")
                    if not isinstance(apps, dict):
                        fail()
                    for appid, snap in apps.items():
                        if not isinstance(appid, str) or not appid.isascii() or not appid.isdecimal():
                            fail()
                        if event == "discount":
                            if not isinstance(snap, dict) or any(type(snap.get(k)) is not int or snap[k] < 0 for k in ("initial", "final", "discount")) or snap["discount"] > 100 or not isinstance(snap.get("currency"), str):
                                fail()
                            if not isinstance(snap.get("kind", "game"), str) or snap.get("kind", "game") not in {"game", "dlc"}:
                                fail()
                        elif event == "bundle_discount":
                            if (not isinstance(snap, dict) or type(snap.get("final")) is not int or snap["final"] < 0
                                    or type(snap.get("discount")) is not int or not 0 <= snap["discount"] <= 100
                                    or not isinstance(snap.get("final_formatted"), str)
                                    or not isinstance(snap.get("initial_formatted"), str)
                                    or not isinstance(snap.get("initial_label"), str)
                                    or snap.get("initial_label") not in {"原价", "捆绑优惠前"}):
                                fail()
                        elif not isinstance(snap, str) or snap not in {"upcoming", "early", "released"}:
                            fail()
    return state


def signature(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def route_state(state, source_id, group_id, event, sig):
    sources = state["routes"].setdefault(source_id, {})
    group = sources.setdefault(group_id, {})
    current = group.get(event)
    if not isinstance(current, dict) or current.get("signature") != sig:
        current = {"signature": sig}
        group[event] = current
    return current


def reconcile_routes(state, sources, routes):
    """Forget inactive route baselines so enabling again starts at the current state."""
    active = {source["id"]: source for source in sources}
    for source_id in list(state["routes"]):
        if source_id not in active:
            del state["routes"][source_id]
            continue
        groups = state["routes"][source_id]
        for group in list(groups):
            if group not in routes.get(source_id, []):
                del groups[group]
                continue
            for event in list(groups[group]):
                events = active[source_id].get("events", [])
                source = active[source_id]
                if event == "catalog" and active[source_id].get("kind") == "creator" and any(
                        item in events for item in ("new_listing", "early_access", "release", "full_release")):
                    continue
                if event == "bundle_discount" and "discount" in events and source.get("include_bundles") is True:
                    continue
                if event == "discount" and "discount" in events and source.get("include_dlc") is not True:
                    prices = groups[group][event].get("apps", {})
                    groups[group][event]["apps"] = {key: value for key, value in prices.items() if value.get("kind") != "dlc"}
                if event not in events:
                    del groups[group][event]


def enqueue(state, source_id, group_id, identity, text, limit, event, source_value, category="game"):
    for item in state["queue"]:
        if (item["identity"] == identity and item["group"] == group_id
                and item["source"] == source_id and item["source_value"] == source_value):
            if item["event"] != event and event not in item.get("also_events", []):
                item.setdefault("also_events", []).append(event)
            return False
    if len(state["queue"]) >= limit:
        raise OverflowError("Steam 待投递队列已满，保留当前来源状态等待发送")
    state["sequence"] += 1
    state["queue"].append({"seq": state["sequence"], "source": source_id, "group": group_id,
                           "identity": identity, "text": text, "event": event, "source_value": source_value, "category": category})
    return True


def timestamp(value):
    if type(value) in (int, float) and value > 0:
        return int(value)
    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Steam 消息发布时间无效") from None


def post_identity(appid, item):
    # The same official announcement can appear in RSS and ISteamNews with
    # different links and IDs. Both expose its title and publication time.
    title = " ".join(str(item["title"]).split()).casefold()
    digest = signature(str(appid), title, timestamp(item["published"]))
    return "steam:post:" + digest


def feed_update(state, source, group, kind, items, sig, limit, advance_watermark=True):
    current = route_state(state, source["id"], group, kind, sig)
    normalized = [(timestamp(item["published"]), item) for item in items]
    if "watermark" not in current:
        current["watermark"] = max((stamp for stamp, _ in normalized), default=0)
        current["seen"] = [post_identity(source["value"], item) for _, item in normalized][-500:]
        return 0
    watermark = current["watermark"]
    seen = set(current.get("seen", []))
    count = 0
    processed = []
    examined = watermark
    for stamp, item in sorted(normalized, key=lambda pair: pair[0]):
        identity = post_identity(source["value"], item)
        if stamp >= watermark and identity not in seen:
            label = "公告" if kind == "announcements" else "新闻"
            text = "Steam 游戏%s\n%s\n%s\n%s" % (label, source.get("name") or source["value"], item["title"], item["link"])
            try:
                count += int(enqueue(state, source["id"], group, identity, text, limit, kind, source["value"]))
            except OverflowError:
                break
            processed.append((stamp, identity))
        seen.add(identity)
        examined = max(examined, stamp)
    if advance_watermark:
        current["watermark"] = examined
    # Do not mark unqueued items seen. A full queue must drain before the
    # remaining feed window can be committed in a later poll.
    current["seen"] = list(dict.fromkeys(current.get("seen", []) + [identity for _, identity in processed]))[-10000:]
    return count


def price_snapshot(game):
    price = game.get("price")
    if price is None:
        return {"final": 0, "initial": 0, "discount": 0, "currency": ""} if game.get("free") else None
    if (not isinstance(price, dict) or any(type(price.get(k)) is not int or price[k] < 0 for k in ("initial", "final", "discount_percent"))
            or price["discount_percent"] > 100 or not isinstance(price.get("currency"), str)):
        raise ValueError("Steam 价格数据不完整")
    return {"final": price["final"], "initial": price["initial"], "discount": price["discount_percent"],
            "currency": price["currency"], "initial_formatted": str(price.get("initial_formatted") or ""),
            "final_formatted": str(price.get("final_formatted") or "")}


def release_snapshot(game):
    release = game.get("release")
    genres = game.get("genres")
    if not isinstance(release, dict) or not isinstance(release.get("coming_soon"), bool) or not isinstance(genres, list):
        return None
    if release["coming_soon"]:
        return "upcoming"
    return "early" if any(isinstance(g, dict) and str(g.get("id")) == "70" for g in genres) else "released"


def catalog_update(state, source, group, appids, sig):
    current = route_state(state, source["id"], group, "catalog", sig)
    if "known" not in current:
        current.update(known=list(appids), pending=[])
        return
    known = set(current["known"])
    current["pending"] = list(dict.fromkeys(current.get("pending", []) + [app for app in appids if app not in known]))
    # Never interpret a missing item as removal; creator lists can be incomplete.
    current["known"] = list(dict.fromkeys(current["known"] + list(appids)))


def game_update(state, source, group, game, region, limit):
    appid = game["appid"]
    is_game = game["type"] == "game"
    if not is_game:
        # Resolve non-game entries without treating them as new games.
        listing = state["routes"].get(source["id"], {}).get(group, {}).get("catalog", {})
        if appid in listing.get("pending", []):
            listing["pending"].remove(appid)
        if game["type"] != "dlc" or source.get("include_dlc") is not True:
            return 0
    count = 0
    listing = state["routes"].get(source["id"], {}).get(group, {}).get("catalog", {})
    if is_game and appid in listing.get("pending", []):
        first_status = release_snapshot(game)
        if first_status == "early" and "early_access" in source["events"]:
            event, label = "early_access", "游戏抢先体验开售"
        elif first_status == "released" and "release" in source["events"]:
            event, label = "release", "游戏正式发售"
        elif "new_listing" in source["events"]:
            event, label = "new_listing", "新游戏商店页面"
        elif first_status is None:
            event, label = "", ""
        else:
            event, label = "", ""
        if event:
            identity = ("steam:listing:" + appid if event == "new_listing"
                        else "steam:release:" + signature(appid, first_status))
            text = "Steam %s\n%s\nhttps://store.steampowered.com/app/%s/" % (label, game["name"], appid)
            try:
                count += int(enqueue(state, source["id"], group, identity, text, limit, event, source["value"]))
            except OverflowError:
                return count
        if event or first_status is not None:
            listing["pending"].remove(appid)
    if "discount" in source["events"]:
        snap = price_snapshot(game)
        if snap is not None:
            current = route_state(state, source["id"], group, "discount", signature(source["kind"], source["value"], region))
            old = current.setdefault("apps", {}).get(appid)
            if old is not None and snap["discount"] > 0 and (old["final"], old["discount"], old["currency"]) != (snap["final"], snap["discount"], snap["currency"]):
                original = snap.get("initial_formatted") or "%s %.2f" % (snap["currency"], snap["initial"] / 100)
                final = snap.get("final_formatted") or "%s %.2f" % (snap["currency"], snap["final"] / 100)
                label = "游戏" if is_game else "DLC"
                text = "Steam %s折扣\n%s\n原价：%s\n现价：%s（-%s%%）\n地区：%s\nhttps://store.steampowered.com/app/%s/" % (label, game["name"], original, final, snap["discount"], region, appid)
                identity = "steam:discount:" + signature(appid, region, old, snap)
                try:
                    count += int(enqueue(state, source["id"], group, identity, text, limit, "discount", source["value"], game["type"]))
                except OverflowError:
                    return count
            current["apps"][appid] = dict(snap, kind=game["type"])
    if not is_game:
        return count
    status = release_snapshot(game)
    if status is not None:
        for event, pair, label in (("early_access", ("upcoming", "early"), "抢先体验开售"),
                                   ("release", ("upcoming", "released"), "正式发售"),
                                   ("full_release", ("early", "released"), "抢先体验转正式版")):
            if event not in source["events"]:
                continue
            current = route_state(state, source["id"], group, event, signature(source["kind"], source["value"]))
            old = current.setdefault("apps", {}).get(appid)
            if (old, status) == pair:
                text = "Steam 游戏%s\n%s\nhttps://store.steampowered.com/app/%s/" % (label, game["name"], appid)
                try:
                    count += int(enqueue(state, source["id"], group, "steam:release:" + signature(appid, status), text, limit, event, source["value"]))
                except OverflowError:
                    return count
            current["apps"][appid] = status
    return count


def bundle_update(state, source, group, bundle, region, limit):
    current = route_state(state, source["id"], group, "bundle_discount", signature(source["kind"], source["value"], region))
    appid = bundle["id"]
    snap = {key: bundle[key] for key in ("final", "discount", "final_formatted", "initial_formatted", "initial_label")}
    old = current.setdefault("apps", {}).get(appid)
    count = 0
    if old is not None and snap["discount"] > 0 and (old["final"], old["discount"]) != (snap["final"], snap["discount"]):
        lines = ["Steam 捆绑包折扣", bundle["name"]]
        if snap["initial_formatted"]:
            lines.append(snap["initial_label"] + "：" + snap["initial_formatted"])
        lines.extend(["现价：%s（-%s%%）" % (snap["final_formatted"], snap["discount"]),
                      "地区：" + region, "https://store.steampowered.com/bundle/%s/" % appid])
        try:
            count += int(enqueue(state, source["id"], group, "steam:bundle-discount:" + signature(appid, region, old, snap),
                                 "\n".join(lines), limit, "discount", source["value"], "bundle"))
        except OverflowError:
            return count
    current["apps"][appid] = snap
    return count
