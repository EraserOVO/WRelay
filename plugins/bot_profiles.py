"""Account-owned routing storage. Credentials and authorization remain global."""
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

from plugins.json_io import json_file_lock, file_revision, _atomic_write_json_unlocked, ConcurrentWriteError

CURRENT = ContextVar("wrelay_bot_profile", default="")
LIST_KEYS = ("discord_channels", "x_rss_sources", "bilibili_sources", "youtube_sources",
             "forwarding_groups", "x_forwarding_groups", "bilibili_forwarding_groups",
             "youtube_forwarding_groups", "qq_forwarding_groups")
GROUP_KEYS = LIST_KEYS[4:]
VALUE_KEYS = ("x_rss_poll_interval", "x_rsshub_base", "x_protection", "bilibili_poll_interval",
              "bilibili_rsshub_base", "bilibili_protection", "youtube_poll_interval", "youtube_protection",
              "backfill_enabled", "backfill_limit", "bilibili_auto_resolve")
OWNED = (*LIST_KEYS, *VALUE_KEYS)
INDEX = "bot_profile_ids"


@contextmanager
def scope(bot_id):
    token = CURRENT.set(str(bot_id or ""))
    try:
        yield
    finally:
        CURRENT.reset(token)


def filename(bot_id):
    if re.fullmatch(r"[1-9][0-9]{0,19}", bot_id):
        return "official_" + bot_id + ".json"
    if re.fullmatch(r"personal:[1-9][0-9]{4,19}", bot_id):
        return "personal_" + bot_id.split(":")[1] + ".json"
    if bot_id == "unassigned":
        return "unassigned.json"
    raise ValueError("Bot 标识无效")


def profile_path(settings_file, bot_id):
    return str(Path(settings_file).parent / "bot_profiles" / filename(bot_id))


def runtime_path(settings_file, bot_id, name):
    return str(Path(settings_file).parent / "bot_profile_runtime" / Path(filename(bot_id)).stem / name)


def strict_read(path):
    with open(path, encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("配置文件顶层必须是对象")
    return value


def validate_profile(value, bot_id=None):
    if not isinstance(value, dict):
        raise ValueError("Bot 配置必须是对象")
    if value.get("format") != "wrelay-bot-profile" or type(value.get("version")) is not int or value.get("version") != 1:
        raise ValueError("不支持的 Bot 配置格式或版本")
    owner = value.get("bot_id")
    filename(owner or "")
    if bot_id is not None and owner != bot_id:
        raise ValueError("Bot 配置文件与账号归属不一致")
    data = value.get("config")
    if not isinstance(data, dict) or set(data) - set(OWNED):
        raise ValueError("Bot 配置包含无效字段")
    from plugins.settings_model import SettingsModel, validate_settings
    candidate = SettingsModel.default().to_dict()
    candidate.update(deepcopy(data))
    error = validate_settings(candidate, validate_forwarding_groups=True)
    if error:
        raise ValueError(error)
    return value


def ids(settings_file):
    try:
        data = strict_read(settings_file)
    except FileNotFoundError:
        return []
    values = data.get(INDEX, [])
    if not isinstance(values, list) or len(values) != len(set(values)):
        raise ValueError("Bot 配置索引损坏")
    for value in values:
        filename(value)
    return values


def revision(settings_file):
    base = file_revision(settings_file)
    values = ids(settings_file)
    if not values:
        return base
    raw = [base, *[(owner, file_revision(profile_path(settings_file, owner))) for owner in values]]
    return hashlib.sha256(json.dumps(raw).encode()).hexdigest()


def read(settings_file, bot_id=None):
    """Single locked snapshot; a broken profile never becomes a default route."""
    with json_file_lock(settings_file):
        data = strict_read(settings_file)
        owners = data.get(INDEX)
        if not owners:
            return data
        selected = bot_id if bot_id is not None else CURRENT.get()
        if selected and selected not in owners:
            raise ValueError("该账号尚未创建独立配置")
        for key in OWNED:
            data.pop(key, None)
        for key in LIST_KEYS:
            data[key] = []
        for owner in owners:
            if selected and owner != selected:
                continue
            if not selected and owner == "unassigned":
                continue
            try:
                profile = validate_profile(strict_read(profile_path(settings_file, owner)), owner)
            except (OSError, ValueError, TypeError):
                if selected:
                    raise
                data.setdefault("_profile_errors", []).append(owner)
                continue
            config = deepcopy(profile["config"])
            own_groups = {g.get("openid") for g in data.get("qq_group_openids", []) if g.get("bot_id") == owner}
            for key in GROUP_KEYS[:-1]:
                for group in config.get(key, []):
                    group["groups"] = [g for g in group.get("groups", []) if g in own_groups]
            for key in LIST_KEYS:
                data[key].extend(config.get(key, []))
            for key in VALUE_KEYS:
                if key in config and (selected or key not in data):
                    data[key] = config[key]
        # Global summaries and scheduled references deduplicate equal legacy group ids.
        if not selected:
            for key in GROUP_KEYS:
                merged = {}
                for group in data[key]:
                    identity = group["id"]
                    if identity not in merged:
                        merged[identity] = group
                    else:
                        merged[identity]["groups"] = list(dict.fromkeys(
                            merged[identity]["groups"] + group.get("groups", [])))
                data[key] = list(merged.values())
        data["_profile_bot"] = selected
        if "unassigned" in owners:
            data["_profile_warnings"] = ["无账号归属的旧配置保留在 bot_profiles/unassigned.json，未参与自动投递，请核对迁移前备份。"]
        return data


def save(settings_file, data, expected_revision=None):
    """Serialize settings + selected profile with rollback under their common lock."""
    with json_file_lock(settings_file):
        if expected_revision is not None and revision(settings_file) != expected_revision:
            raise ConcurrentWriteError("配置已变化，请刷新后重试")
        original = strict_read(settings_file)
        owners = original.get(INDEX, [])
        selected = data.get("_profile_bot") or CURRENT.get()
        if CURRENT.get() and selected != CURRENT.get():
            raise ValueError("当前账号与待保存配置不同，请重新加载")
        if not owners:
            _atomic_write_json_unlocked(settings_file, data, indent=2)
            return
        if selected and selected not in owners:
            raise ValueError("目标 Bot 配置不存在")
        payload = deepcopy(data)
        payload.pop("_profile_bot", None)
        payload.pop("_revision", None)
        payload.pop("_profile_errors", None)
        payload.pop("_profile_warnings", None)
        payload[INDEX] = owners
        config = {key: payload.pop(key) for key in OWNED if key in payload}
        path = profile_path(settings_file, selected) if selected else None
        old_profile = strict_read(path) if path else None
        shutil.copyfile(settings_file, str(settings_file) + ".bak")
        if path:
            profile = dict(format="wrelay-bot-profile", version=1, bot_id=selected, config=config)
            validate_profile(profile, selected)
            shutil.copyfile(path, path + ".bak")
            _atomic_write_json_unlocked(path, profile, indent=2)
        try:
            _atomic_write_json_unlocked(settings_file, payload, indent=2)
        except BaseException:
            if path:
                _atomic_write_json_unlocked(path, old_profile, indent=2)
            raise


def migrate(settings_file, configured_ids):
    """Preserve legacy ids/state. Commit index only after all validated files exist."""
    with json_file_lock(settings_file):
        raw = strict_read(settings_file)
        if raw.get(INDEX):
            from plugins.settings_model import SettingsModel
            changed = False
            for owner in configured_ids:
                filename(owner)
                if owner not in raw[INDEX]:
                    defaults = SettingsModel.default().to_dict()
                    empty = {k: deepcopy(defaults[k]) for k in OWNED if k in defaults}
                    for key in GROUP_KEYS:
                        empty[key] = []
                    _atomic_write_json_unlocked(profile_path(settings_file, owner),
                        dict(format="wrelay-bot-profile", version=1, bot_id=owner, config=empty), indent=2)
                    raw[INDEX].append(owner)
                    changed = True
            if changed:
                _atomic_write_json_unlocked(settings_file, raw, indent=2)
            return {"migrated": False, "bot_ids": raw[INDEX]}
        owners = list(dict.fromkeys(str(x) for x in configured_ids if x))
        for owner in owners:
            filename(owner)
        groups = raw.get("qq_group_openids", [])
        known = {str(g.get("bot_id")) for g in groups if g.get("bot_id")}
        for owner in sorted(known - set(owners)):
            filename(owner)
            owners.append(owner)
        default = next((owner for owner in owners if not owner.startswith("personal:")), "unassigned")
        if any(not g.get("bot_id") for g in groups) and default not in owners:
            owners.append(default)
        if not owners:
            return {"migrated": False, "bot_ids": []}
        from plugins.settings_model import SettingsModel
        legacy = SettingsModel.from_data(raw).to_dict()
        # Reuse current normalization, including legacy automatic routing groups.
        from plugins import config as model
        model._ensure_forwarding_groups(legacy)
        model._ensure_x_forwarding_groups(legacy)
        model._ensure_bilibili_forwarding_groups(legacy)
        model._ensure_youtube_forwarding_groups(legacy)
        model._ensure_qq_forwarding_groups(legacy)
        profiles = {}
        for owner in owners:
            own_groups = {str(g["openid"]) for g in groups
                          if (g.get("bot_id") or default) == owner}
            config = {key: deepcopy(legacy[key]) for key in OWNED if key in legacy}
            for key in GROUP_KEYS:
                if key == "qq_forwarding_groups":
                    config[key] = [g for g in config.get(key, []) if own_groups.intersection(g.get("groups", []))]
                else:
                    for group in config.get(key, []):
                        group["groups"] = [x for x in group.get("groups", []) if x in own_groups]
            profiles[owner] = validate_profile(dict(format="wrelay-bot-profile", version=1, bot_id=owner, config=config), owner)
        backup = str(settings_file) + ".before-bot-profiles.json"
        if os.path.exists(backup):
            if strict_read(backup) != raw:
                digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
                backup = str(settings_file) + ".before-bot-profiles." + digest + ".json"
        if not os.path.exists(backup):
            shutil.copyfile(settings_file, backup)
        for owner, profile in profiles.items():
            path = profile_path(settings_file, owner)
            if os.path.exists(path):
                shutil.copyfile(path, path + ".bak")
            _atomic_write_json_unlocked(path, profile, indent=2)
            # Copy state without deleting legacy files. History remains keyed by channel × target group.
            for name in ("x_rss.json", "bilibili.json", "youtube.json", "x_protection.json", "bilibili_protection.json", "youtube_protection.json"):
                source = Path(settings_file).parent / name
                if source.exists():
                    target = Path(runtime_path(settings_file, owner, name))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copyfile(source, target)
        for group in groups:
            if not group.get("bot_id") and default != "unassigned":
                group["bot_id"] = default
        for key in OWNED:
            raw.pop(key, None)
        raw[INDEX] = owners
        _atomic_write_json_unlocked(settings_file, raw, indent=2)
        return {"migrated": True, "bot_ids": owners,
                "warnings": ["无账号归属的配置保留在 unassigned，暂停自动投递"] if "unassigned" in owners else []}
