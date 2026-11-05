"""Serialize legacy RSS worker state while selecting independent account profiles."""
import asyncio
from copy import deepcopy
from pathlib import Path
from plugins import bot_profiles as profiles, config


class RSSProfiles:
    def __init__(self, ns, prefix):
        self.ns, self.prefix = ns, prefix
        self.lock = asyncio.Lock()
        names = ("_posts_cache", "_protection_runtime_state", "_protection_load_error", "_protection_runtime_error",
                 "_state_load_error", "_runtime_status", "_dispatch_sent_since_poll", "_dispatch_failed_since_poll")
        self.defaults = {k: deepcopy(ns[k]) for k in names if k in ns}
        self.paths = {k: ns[k] for k in ns if k.endswith("_FILE") and ("STATE" in k or "PROTECTION" in k)}
        self.states, self.next, self.connected = {}, {}, set()

    def owners(self):
        return [x for x in profiles.ids(config.SETTINGS_FILE) if x != "unassigned"]

    async def call(self, owner, operation):
        async with self.lock:
            if "_runtime_status" not in self.defaults and "_runtime_status" in self.ns:
                self.defaults["_runtime_status"] = deepcopy(self.ns["_runtime_status"])
            old = {k: self.ns[k] for k in self.defaults}
            self.ns.update(self.states.setdefault(owner, deepcopy(self.defaults)))
            self.ns.update({k: profiles.runtime_path(config.SETTINGS_FILE, owner, Path(p).name) for k, p in self.paths.items()})
            try:
                with profiles.scope(owner):
                    task = asyncio.create_task(operation())
                    try:
                        return await asyncio.shield(task)
                    except asyncio.CancelledError as cancellation:
                        while not task.done():
                            try:
                                await asyncio.shield(task)
                            except asyncio.CancelledError:
                                if task.cancelled():
                                    raise
                            except Exception:
                                break
                        try:
                            await task
                        except Exception:
                            pass
                        raise cancellation
            finally:
                state = {}
                for key in self.defaults:
                    state[key] = self.ns[key]
                self.states[owner] = state
                self.ns.update(old)
                self.ns.update(self.paths)

    async def poll(self, operation):
        owners = await asyncio.to_thread(self.owners)
        if not owners:
            return await operation()
        from plugins.sender import get_qq_bots
        from plugins.personal_qq import connected_bots
        connected = set(get_qq_bots()) | set(connected_bots())
        for owner in connected - self.connected:
            self.next.pop(owner, None)
        self.connected = connected
        loop = asyncio.get_running_loop()
        for owner in owners:
            if owner not in connected or loop.time() < self.next.get(owner, 0):
                continue
            try:
                await self.call(owner, operation)
                with profiles.scope(owner):
                    interval = await self.ns["_read_poll_interval"]()
                self.next[owner] = loop.time() + interval
            except Exception:
                self.next[owner] = loop.time() + 30
                self.ns["logger"].exception("Bot {} 来源配置异常，暂停本轮", owner)

    async def interval(self, reader):
        return 5 if await asyncio.to_thread(self.owners) else await reader()

    async def dispatch(self, operation):
        waits = []
        from plugins.sender import get_qq_bots
        from plugins.personal_qq import connected_bots
        connected = set(get_qq_bots()) | set(connected_bots())
        for owner in await asyncio.to_thread(self.owners):
            if owner not in connected:
                continue
            async def run():
                async with self.ns["_protection_lock"]:
                    return await operation()
            try:
                value = await self.call(owner, run)
                if value is not None:
                    waits.append(value)
            except Exception:
                self.ns["logger"].exception("Bot {} 队列异常，保留待重试", owner)
        return min(waits, default=5)

    async def status(self, owner):
        if not await asyncio.to_thread(self.owners):
            return deepcopy(self.ns.get("_runtime_status", {}))
        state = self.states.get(owner, self.defaults)
        data = deepcopy(state.get("_runtime_status", {}))
        try:
            with profiles.scope(owner):
                settings = await asyncio.to_thread(config._load_settings)
            key = "x_rss_sources" if self.prefix == "x" else self.prefix + "_sources"
            sources = settings.get(key, [])
            queue = state.get("_protection_runtime_state") or {}
            from plugins.sender import get_qq_bots
            from plugins.personal_qq import connected_bots
            data.update(ok=True, running=bool(self.ns.get("_loop_started")), sources_total=len(sources),
                sources_enabled=sum(s.get("enabled") is True for s in sources),
                forwarding_groups=len(settings.get("x_forwarding_groups" if self.prefix == "x" else self.prefix + "_forwarding_groups", [])),
                poll_interval=settings.get("x_rss_poll_interval" if self.prefix == "x" else self.prefix + "_poll_interval", 300),
                protection=settings.get(self.prefix + "_protection", {}),
                protection_queue_pending=sum(len(b.get("items", [])) for b in queue.get("queues", {}).values()),
                protection_error=state.get("_protection_load_error", "") or state.get("_protection_runtime_error", ""),
                qq_connected=owner in (set(get_qq_bots()) | set(connected_bots())))
        except Exception:
            data.update(ok=False, last_result="config_failed", last_error="该账号配置损坏，已暂停来源",
                        protection_error="该账号配置损坏，已暂停来源")
        return data

    async def status_all(self):
        values = [(owner, await self.status(owner)) for owner in await asyncio.to_thread(self.owners)]
        result = dict(ok=True, running=bool(self.ns.get("_loop_started")), source_health=[])
        for key in ("sources_total", "sources_enabled", "forwarding_groups", "protection_queue_pending",
                    "last_new_count", "last_sent_count", "last_failed_count"):
            result[key] = sum(value.get(key, 0) for _, value in values)
        result["last_poll_ts"] = max((value.get("last_poll_ts", 0) for _, value in values), default=0)
        result["qq_connected"] = any(value.get("qq_connected") for _, value in values)
        failures = []
        for owner, value in values:
            for item in value.get("source_health", []):
                result["source_health"].append(dict(item, key=owner + ":" + str(item.get("key", "")),
                    id=owner + ":" + str(item.get("id", item.get("key", ""))),
                    name=owner + " / " + str(item.get("name", ""))))
            if value.get("last_error") or value.get("protection_error"):
                failures.append(owner + "：" + str(value.get("last_error") or value.get("protection_error")))
        failure_result = next((value.get("last_result") or "protection_failed" for _, value in values
                               if value.get("last_error") or value.get("protection_error")), "ok")
        result.update(last_result=failure_result, last_error="；".join(failures),
                      protection_error="；".join(value["protection_error"] for _, value in values if value.get("protection_error")))
        return result
