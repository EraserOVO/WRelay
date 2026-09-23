# -*- coding: utf-8 -*-
"""
WRelay 管理面板（无窗口运行，pythonw 启动）

功能：
  - 机器人 启动 / 停止 / 重启
  - 管理哪些 QQ 群接收转发、哪些 Discord 频道参与转发
  - 实时查看机器人运行日志

启动方式：
  双击快捷方式「管理面板」，或：
    pythonw panel/管理面板.pyw   （在项目根目录下）
"""

import os
import sys
import json
import shutil
import time
import asyncio
import re
import threading
import subprocess
import webbrowser
import secrets
import uuid
from datetime import datetime

# ---------------- 路径 ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))      # .../panel
PROJECT_ROOT = os.path.dirname(BASE_DIR)                    # 项目根目录
sys.path.insert(0, PROJECT_ROOT)

from plugins.paths import DATA_DIR as SHARED_DATA_DIR, data_path

DATA_DIR = os.fspath(SHARED_DATA_DIR)
LOG_DIR = os.path.join(DATA_DIR, "logs")
SETTINGS_FILE = data_path("settings.json")
SETTINGS_BAK = SETTINGS_FILE + ".bak"
BOT_LOG = data_path(os.path.join("logs", "bot.log"))
PANEL_LOG = data_path(os.path.join("logs", "panel.log"))
PID_FILE = data_path("bot.pid")
RUNTIME_LEVEL_FILE = data_path("runtime_log_level.json")
STATS_FILE = data_path("stats.json")
PANEL_AUTH_FILE = data_path("panel_auth.json")
DISCORD_LAST_FILE = data_path("discord_last.json")
RETRY_PENDING_FILE = data_path("retry_pending.json")
RESTORE_BACKUP_DIR = data_path("restore_backups")
PANEL_LOG_MAX_BYTES = 5 * 1024 * 1024
PANEL_LOG_BACKUPS = 3

# 正式安装包使用 {项目根}\python\python.exe（standalone Python，无 .venv）；
# 开发环境若存在 .venv 则优先使用，否则回退到项目根的 python。
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = os.path.join(PROJECT_ROOT, "python", "python.exe")
BOT_ENTRY = os.path.join(PROJECT_ROOT, "bot.py")

PANEL_HOST = "127.0.0.1"
PANEL_PORT = 18090
BOT_PORT = 18082

# ---------------- 转发组 ----------------
# 与 plugins/config.py（bot 侧）保持一致：
#   默认组 id=default / name=转发组1；成员 = 当前全局 enabled 的频道/群。
# Discord 转发组只表达“频道→群”路由关系；QQ→QQ 使用独立的 qq_forwarding_groups。
# 磁盘迁移在 main() 启动时一次性执行，不能放 load_settings()：
# 测试用临时 settings 文件做"其他字段逐字节不变"断言，load 路径写盘会破坏它。
FORWARDING_GROUP_DEFAULT_ID = "default"
FORWARDING_GROUP_MAX = 10

# ---------------- 开机自启 ----------------
# 通过启动文件夹里的 WRelayAutostart.vbs（隐藏窗口）运行 panel/autostart.pyw，
# autostart.pyw 追加 --autostart 标志后调用本面板 main()：
#   后台启动面板(不开浏览器) + 自动拉起机器人
AUTOSTART_VBS_NAME = "WRelayAutostart.vbs"
AUTOSTART_FLAG = "--autostart"
RESTART_FLAG = "--restart"

os.makedirs(LOG_DIR, exist_ok=True)

# pythonw 下没有控制台窗口。面板输出写入可轮转日志，避免 panel.log
# 随运行时间无限增长；单个文件最多 5MB，保留最近 3 份历史文件。
class _RotatingPanelLog:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._file = None
        self._open()

    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return False

    def _open(self):
        self._file = open(self.path, "a", encoding="utf-8", buffering=1)

    def _rotate(self):
        if self._file is not None:
            self._file.close()
        for index in range(PANEL_LOG_BACKUPS - 1, 0, -1):
            source = "%s.%d" % (self.path, index)
            target = "%s.%d" % (self.path, index + 1)
            if os.path.exists(source):
                os.replace(source, target)
        if os.path.exists(self.path):
            os.replace(self.path, self.path + ".1")
        self._open()

    def write(self, text):
        if not text:
            return 0
        value = str(text)
        with self._lock:
            try:
                current_size = os.path.getsize(self.path)
                if current_size and current_size + len(value.encode("utf-8")) > PANEL_LOG_MAX_BYTES:
                    self._rotate()
            except OSError:
                # 日志轮转失败时继续写当前文件，不能影响面板功能。
                pass
            self._file.write(value)
        return len(value)

    def flush(self):
        with self._lock:
            self._file.flush()


try:
    _panel_log = _RotatingPanelLog(PANEL_LOG)
    sys.stdout = _panel_log
    sys.stderr = _panel_log
except Exception:
    pass

# 面板与 bot 共用的 JSON 原子写（唯一 tmp + os.replace 重试）
from plugins.json_io import (
    atomic_write_json,
    load_json,
    file_revision,
    json_file_lock,
    update_json,
    _atomic_write_json_unlocked,
    ConcurrentWriteError,
)
from plugins.settings_model import SettingsModel, validate_settings
from plugins.user_errors import localize_user_error
from panel.backup_restore import (
    BACKUP_MANIFEST_NAME,
    BACKUP_MAX_BYTES,
    BACKUP_RUNTIME_NAMES,
    BACKUP_SETTINGS_NAME,
    RESTORE_BACKUP_RETENTION,
    BackupRestoreService,
    RestoreRollbackError,
)
from panel.process_manager import BotProcessManager

# X 转发组：复用 bot 侧的归一化结果（GET 注入默认组）与 source key 派生
# （保存时清理悬空引用）。导入失败时用最小回退（等价于不做注入/清理）。
try:
    from plugins.config import (
        get_bilibili_forwarding_groups,
        _bilibili_source_id,
        get_x_forwarding_groups,
        _x_source_key,
    )
except Exception:
    def get_bilibili_forwarding_groups():
        return []

    def _bilibili_source_id(kind, value):
        return "%s:%s" % (kind, value)

    def get_x_forwarding_groups():
        return []

    def _x_source_key(raw):
        return str(raw or "").lstrip("@")


def _log(msg):
    """面板运行日志（panel.log），带时间戳"""
    try:
        _panel_log.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _load_panel_token():
    data = load_json(PANEL_AUTH_FILE, default=None)
    token = data.get("token") if isinstance(data, dict) else ""
    if isinstance(token, str) and len(token) >= 32:
        return token
    token = secrets.token_urlsafe(32)
    atomic_write_json(PANEL_AUTH_FILE, {"token": token}, indent=2)
    return token


PANEL_TOKEN = _load_panel_token()


# ---------------- 配置读写 ----------------
def load_settings():
    raw = load_json(SETTINGS_FILE, default=None)
    model = SettingsModel.from_data(
        raw,
        _default_discord_channels(),
        _default_qq_group_openids(),
    )
    # 旧测试属性 / 模式选择的磁盘迁移统一在 main() 启动时执行；
    # load 路径只补齐共享模型字段，不写盘。
    return model.to_dict()


def _settings_revision():
    """返回 settings.json 当前版本，用于阻止旧页面覆盖新配置。"""
    return file_revision(SETTINGS_FILE)


def _load_settings_snapshot():
    """读取配置及其写入前必须匹配的版本。

    先取版本、再读配置：若中间被其他进程修改，后续保存会因版本不匹配
    被拒绝，绝不把旧快照写回新文件。
    """
    revision = _settings_revision()
    return load_settings(), revision


def _default_discord_channels():
    try:
        from plugins.config import DISCORD_CHANNELS
        return DISCORD_CHANNELS
    except Exception:
        return {}


def _default_qq_group_openids():
    try:
        from plugins.config import QQ_GROUP_OPENIDS
        return QQ_GROUP_OPENIDS
    except Exception:
        return []


def get_default_settings():
    return SettingsModel.default(
        _default_discord_channels(),
        _default_qq_group_openids(),
    ).to_dict()


def _validate_settings(data):
    """保存前结构校验；规则与 Bot 侧共用。"""
    return validate_settings(
        data,
        max_forwarding_groups=FORWARDING_GROUP_MAX,
        require_nonempty_forwarding_groups=True,
        validate_forwarding_groups=True,
    )


# ---------------- 安全备份与恢复 ----------------
def _backup_service():
    return BackupRestoreService(
        settings_file=SETTINGS_FILE,
        discord_last_file=DISCORD_LAST_FILE,
        retry_pending_file=RETRY_PENDING_FILE,
        restore_backup_dir=RESTORE_BACKUP_DIR,
        file_lock=json_file_lock,
        atomic_write_json_unlocked=_atomic_write_json_unlocked,
        default_settings_factory=get_default_settings,
        settings_validator=_validate_settings,
        logger=_log,
    )


def _backup_targets():
    return _backup_service().backup_targets()


def _lock_backup_targets(names):
    return _backup_service().lock_backup_targets(names)


def _backup_manifest(names, include_runtime_state):
    return BackupRestoreService.backup_manifest(names, include_runtime_state)


def create_backup_archive(include_runtime_state=False):
    return _backup_service().create_backup_archive(include_runtime_state)


def _read_restore_archive(payload):
    return _backup_service().read_restore_archive(payload)


def _save_restore_rollback(names):
    return _backup_service().save_restore_rollback(names)


def _prune_restore_rollbacks(current_path):
    return _backup_service().prune_restore_rollbacks(current_path)


def restore_backup_archive(
    payload,
    include_runtime_state=False,
    confirmed_runtime_state=False,
):
    return _backup_service().restore_backup_archive(
        payload,
        include_runtime_state,
        confirmed_runtime_state,
        targets_provider=_backup_targets,
        lock_targets=_lock_backup_targets,
        save_rollback=_save_restore_rollback,
        read_archive=_read_restore_archive,
    )


def _sync_legacy_scheduled_message(scheduled, body):
    """把旧版 timer 字段同步到新版 messages 的第一条消息。"""
    legacy_keys = {
        "content", "schedule_mode", "every_days", "every_weeks",
        "every_months", "weekdays", "send_time", "anchor_date",
    }
    if not any(key in body for key in legacy_keys):
        return
    if "content" in body and not str(body.get("content") or "").strip():
        scheduled["messages"] = []
        return
    messages = scheduled.get("messages")
    if not isinstance(messages, list):
        messages = []
    if messages and isinstance(messages[0], dict):
        first = dict(messages[0])
    else:
        first = {
            "id": "manual-1",
            "content": "",
            "schedule_type": "interval",
            "interval_days": 1,
            "interval_hours": 0,
            "interval_minutes": 0,
            "anchor_at": "",
            "calendar_type": "weekly",
            "weekdays": [0],
            "day_of_month": 1,
            "month": 1,
            "day": 1,
            "send_time": "09:00",
        }
    def _body_int(key, default=1):
        try:
            return int(body.get(key) or default)
        except (TypeError, ValueError):
            return default
    if "content" in body:
        first["content"] = body["content"]
    mode = body.get("schedule_mode")
    if mode == "days":
        first["schedule_type"] = "interval"
        first["interval_days"] = _body_int("every_days")
        first["interval_hours"] = 0
        first["interval_minutes"] = 0
    elif mode == "weekly":
        first["schedule_type"] = "interval"
        first["interval_days"] = _body_int("every_weeks") * 7
        first["interval_hours"] = 0
        first["interval_minutes"] = 0
    elif mode == "monthly":
        first["schedule_type"] = "calendar"
        first["calendar_type"] = "monthly"
        first["day_of_month"] = 1
        anchor_date = body.get("anchor_date")
        if isinstance(anchor_date, str) and len(anchor_date) >= 10:
            try:
                first["day_of_month"] = int(anchor_date[8:10])
            except ValueError:
                pass
    if "send_time" in body:
        first["send_time"] = body["send_time"]
    if "anchor_date" in body and first.get("schedule_type") == "interval":
        anchor_date = body.get("anchor_date") or ""
        send_time = first.get("send_time") or "09:00"
        first["anchor_at"] = (anchor_date + "T" + send_time) if anchor_date else ""
    if "weekdays" in body and mode == "weekly":
        first["weekdays"] = body["weekdays"]
    scheduled["messages"] = [first] + [item for item in messages[1:] if isinstance(item, dict)]


def _normalize_forwarding_groups(data):
    """写盘前归一化转发组（fg 缺失时仅清理 is_test，兼容旧载荷/测试）。

    - 移除群/频道条目上的旧 is_test 属性（测试组概念已取消，不并入任何测试组）
    - 每项规整为 {id, name, channels[], groups[]}，id/name 取 str
    - channels/groups 去重并剔除已被删除的频道/群（不留悬空引用）
    - 不自动创建或强制保留任何特殊「测试组」；已存在的组（含普通 id 组）
      原样保留，不再赋予其特殊意义
    - 不按 enabled 裁剪：转发组保留路由关系，全局启用切换不丢勾选"""
    # 旧测试属性一律清理（写盘后不再出现该字段）
    for key in ("discord_channels", "qq_group_openids"):
        for item in data.get(key, []):
            if isinstance(item, dict):
                item.pop("is_test", None)

    fgs = data.get("forwarding_groups")
    if not isinstance(fgs, list) or not fgs:
        return
    known_channels = {
        str(c.get("id"))
        for c in data.get("discord_channels", [])
        if isinstance(c, dict) and c.get("id")
    }
    known_groups = {
        str(g.get("openid"))
        for g in data.get("qq_group_openids", [])
        if isinstance(g, dict) and g.get("openid")
    }
    clean = []
    for fg in fgs:
        if not isinstance(fg, dict):
            continue
        gid = str(fg.get("id") or "").strip()
        if not gid:
            continue
        clean.append({
            "id": gid,
            "name": str(fg.get("name") or "").strip() or gid,
            "enabled": fg.get("enabled", True) is not False,
            "channels": sorted({
                str(c) for c in fg.get("channels") or []
                if str(c) and str(c) in known_channels
            }),
            "groups": sorted({
                str(g) for g in fg.get("groups") or []
                if str(g) and str(g) in known_groups
            }),
        })
    data["forwarding_groups"] = clean


def _normalize_x_forwarding_groups(data):
    """写盘前归一化 X 转发组（x_forwarding_groups 缺失时不动，兼容旧载荷）。

    - 每项规整为 {id, name, sources[], groups[]}，id/name 取 str
    - sources/groups 去重并剔除悬空引用：
      source 不在 x_rss_sources（按 bot 侧 _x_source_key 派生 key）、
      群 openid 不在 qq_group_openids
    - 不按 enabled 裁剪：enabled 只影响运行，成员关系独立保存
    - 无固定测试组；空列表原样保留（bot 侧内存回退默认组）"""
    xfgs = data.get("x_forwarding_groups")
    if not isinstance(xfgs, list):
        return
    known_sources = {
        _x_source_key(str(s.get("source") or ""))
        for s in data.get("x_rss_sources", [])
        if isinstance(s, dict) and str(s.get("source") or "").strip()
    }
    known_sources.discard("")
    known_groups = {
        str(g.get("openid"))
        for g in data.get("qq_group_openids", [])
        if isinstance(g, dict) and g.get("openid")
    }
    clean = []
    seen_ids = set()
    for xfg in xfgs:
        if not isinstance(xfg, dict):
            continue
        xid = str(xfg.get("id") or "").strip()
        if not xid or xid in seen_ids:
            continue
        seen_ids.add(xid)
        clean.append({
            "id": xid,
            "name": str(xfg.get("name") or "").strip() or xid,
            "enabled": xfg.get("enabled", True) is not False,
            "sources": [
                k for k in dict.fromkeys(str(s) for s in xfg.get("sources") or [])
                if k and k in known_sources
            ],
            "groups": [
                o for o in dict.fromkeys(str(g) for g in xfg.get("groups") or [])
                if o and o in known_groups
            ],
        })
    data["x_forwarding_groups"] = clean


def _normalize_bilibili_forwarding_groups(data):
    """写盘前归一化 Bilibili 转发组，清理已删除来源/群的引用。"""
    groups = data.get("bilibili_forwarding_groups")
    if not isinstance(groups, list):
        return
    known_sources = set()
    for source in data.get("bilibili_sources", []):
        if not isinstance(source, dict):
            continue
        kind = str(source.get("kind") or "up").strip().lower()
        value = str(source.get("value") or source.get("uid") or "").strip()
        source_id = str(source.get("id") or _bilibili_source_id(kind, value)).strip()
        if source_id:
            known_sources.add(source_id)
    known_groups = {
        str(group.get("openid"))
        for group in data.get("qq_group_openids", [])
        if isinstance(group, dict) and group.get("openid")
    }
    clean = []
    seen_ids = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id") or "").strip()
        if not group_id or group_id in seen_ids:
            continue
        seen_ids.add(group_id)
        clean.append({
            "id": group_id,
            "name": str(group.get("name") or "").strip() or group_id,
            "enabled": group.get("enabled", True) is not False,
            "sources": [
                value for value in dict.fromkeys(str(item) for item in group.get("sources") or [])
                if value and value in known_sources
            ],
            "groups": [
                value for value in dict.fromkeys(str(item) for item in group.get("groups") or [])
                if value and value in known_groups
            ],
        })
    data["bilibili_forwarding_groups"] = clean


def _normalize_qq_forwarding_groups(data):
    """写盘前归一化 QQ→QQ 独立转发组，剔除已删除的 QQ 群。"""
    qfgs = data.get("qq_forwarding_groups")
    known_groups = {
        str(g.get("openid"))
        for g in data.get("qq_group_openids", [])
        if isinstance(g, dict) and g.get("openid")
    }
    # 空列表表示用户明确删除全部 QQ→QQ 转发组，允许保存为空。
    if isinstance(qfgs, list) and not qfgs:
        data["qq_forwarding_groups"] = []
        return
    if not isinstance(qfgs, list):
        qfgs = [{
            "id": "qq_default",
            "name": "转发组1",
            "enabled": True,
            "groups": [
                str(g.get("openid"))
                for g in data.get("qq_group_openids", [])
                if isinstance(g, dict) and g.get("openid") and g.get("enabled")
            ],
        }]
    clean = []
    seen_ids = set()
    for qfg in qfgs:
        if not isinstance(qfg, dict):
            continue
        qid = str(qfg.get("id") or "").strip()
        if not qid or qid in seen_ids:
            continue
        seen_ids.add(qid)
        clean.append({
            "id": qid,
            "name": str(qfg.get("name") or "").strip() or qid,
            "enabled": qfg.get("enabled", True) is not False,
            "groups": [
                value for value in dict.fromkeys(str(g) for g in qfg.get("groups") or [])
                if value and value in known_groups
            ],
        })
    data["qq_forwarding_groups"] = clean


def save_settings(data, expected_revision=None):
    # 备份上一版配置（任何误写/损坏都可从 settings.json.bak 恢复），
    # 再走统一原子写（唯一 tmp + os.replace，见 plugins/json_io.py）
    _normalize_forwarding_groups(data)
    _normalize_x_forwarding_groups(data)
    _normalize_bilibili_forwarding_groups(data)
    _normalize_qq_forwarding_groups(data)
    with json_file_lock(SETTINGS_FILE):
        if (
            expected_revision is not None
            and file_revision(SETTINGS_FILE) != expected_revision
        ):
            raise ConcurrentWriteError("settings.json 已被其他进程修改")
        try:
            if os.path.exists(SETTINGS_FILE):
                shutil.copyfile(SETTINGS_FILE, SETTINGS_BAK)
        except Exception as exc:
            _log("备份 settings.json.bak 失败: %s" % exc)
        _atomic_write_json_unlocked(SETTINGS_FILE, data, indent=2)


def migrate_forwarding_groups():
    """磁盘一次性迁移（幂等）：清理旧测试属性，不再创建/保留「测试组」。

    1. 旧 test_group_openid / test_channel_id / is_test 标记 → 仅清理，
       不再并入任何"测试组"（测试组概念已取消）。
    2. 移除群/频道条目的 is_test 属性与顶层 mode 字段（旧模式选择废弃）。
    3. 已存在 forwarding_groups → 原样保留（含普通 id 组，不合并、不重建）。
    4. 旧配置完全无 forwarding_groups → 生成单个默认转发组（全部启用频道/群），
       与旧的"全局启用即全量转发"行为一致。

    必须在 main() 启动时执行而非 load_settings()：迁移写盘会破坏
    test_backfill_toggle 临时文件的"其他字段逐字节不变"断言。重复执行结果不变（幂等）。"""
    data, revision = _load_settings_snapshot()
    before = json.dumps(data, sort_keys=True, ensure_ascii=False)

    # 旧顶层测试键（更早版本：单测试群/单测试频道）→ is_test 标记，随后统一清理
    legacy_g = str(data.get("test_group_openid") or "").strip()
    legacy_c = str(data.get("test_channel_id") or "").strip()
    if legacy_g or legacy_c:
        for g in data.get("qq_group_openids", []):
            if isinstance(g, dict) and str(g.get("openid")) == legacy_g:
                g["is_test"] = True
        for c in data.get("discord_channels", []):
            if isinstance(c, dict) and str(c.get("id")) == legacy_c:
                c["is_test"] = True
        data.pop("test_group_openid", None)
        data.pop("test_channel_id", None)

    # 清理旧 is_test 属性（不再使用频道/群自身测试属性）
    for key in ("qq_group_openids", "discord_channels"):
        for item in data.get(key, []):
            if isinstance(item, dict):
                item.pop("is_test", None)
    data.pop("mode", None)  # 旧模式选择（test/forward/custom）废弃

    fgs = data.get("forwarding_groups")
    if not (isinstance(fgs, list) and fgs):
        # 旧配置无转发组：默认转发组 = 全部启用频道/群（原"全局启用即全量转发"）
        data["forwarding_groups"] = [{
            "id": FORWARDING_GROUP_DEFAULT_ID,
            "name": "转发组1",
            "channels": [
                str(c["id"]) for c in data.get("discord_channels", [])
                if isinstance(c, dict) and c.get("enabled") and c.get("id")
            ],
            "groups": [
                str(g["openid"]) for g in data.get("qq_group_openids", [])
                if isinstance(g, dict) and g.get("enabled") and g.get("openid")
            ],
        }]

    if json.dumps(data, sort_keys=True, ensure_ascii=False) == before:
        return  # 已迁移过，无需写盘（幂等）

    try:
        save_settings(data, expected_revision=revision)
    except ConcurrentWriteError:
        _log("转发组迁移跳过：settings.json 已被其他进程修改")
        return
    fg0 = data["forwarding_groups"][0]
    _log("转发组迁移：默认「%s」（%d 频道 / %d 群）" % (
        fg0.get("name"), len(fg0.get("channels", [])), len(fg0.get("groups", []))))


def get_qq_appid():
    """返回 QQ 开放平台 AppID（settings.json 的 qq_appid）；未配置返回 "" """
    data = load_settings()
    return str(data.get("qq_appid") or "").strip()


# ---------------- 机器人进程管理 ----------------
_bot = {"proc": None}
# API 与看门狗可并发触发启停；重启/看门狗会重入包装函数，因此使用 RLock。
_BOT_CONTROL_LOCK = threading.RLock()

# 看门狗：_want_running 为 True 表示"面板期望机器人保持运行"（点过启动/重启/开机自启），
# 机器人崩溃或被杀后由后台线程自动拉起；手动停止（stop_bot）会置 False，不再自动重启。
_want_running = False
_last_auto_start = 0.0
WATCHDOG_INTERVAL = 10.0   # 每 10 秒检查一次机器人存活
MIN_AUTO_RESTART_GAP = 30.0  # 两次自动重启至少间隔 30 秒，连续崩溃时不刷屏


def port_open(port):
    return _process_manager().port_open(port)


PANEL_PING_URL = "http://%s:%d/api/status" % (PANEL_HOST, PANEL_PORT)


def panel_alive():
    """判断面板是否真正在运行：HTTP 探测 /api/status 成功才算。

    不能只用 TCP 探测（port_open）：一旦 18090 被其它进程（如系统里
    恰好占用的无关软件）监听，新面板会被误判为"面板已在运行"而直接
    退出，浏览器反而打不开管理页。只有返回 200 的 HTTP 响应才说明
    当前 18090 上确实是本面板。
    """
    return _process_manager().panel_alive()


def _proc_exists(pid):
    """判断 pid 是否仍在运行。
    用 WaitForSingleObject 检测进程是否已 signaled（终止）：
    即使其它进程（如面板持有的 Popen 句柄）仍持有该进程的对象句柄，
    进程终止后 WaitForSingleObject 也会返回 signaled，
    避免"进程已退出却被误判为运行中"导致看门狗不重启。"""
    return _process_manager().proc_exists(pid)


def read_pid():
    return _process_manager().read_pid()


def write_pid(pid):
    _process_manager().write_pid(pid)


def clear_pid():
    _process_manager().clear_pid()


def bot_status():
    return _process_manager().bot_status()


def _kill_tree(pid):
    _process_manager().kill_tree(pid)


def _process_manager():
    return BotProcessManager(
        project_root=PROJECT_ROOT,
        python_path=VENV_PYTHON,
        bot_entry=BOT_ENTRY,
        pid_file=PID_FILE,
        panel_ping_url=PANEL_PING_URL,
        panel_token=PANEL_TOKEN,
        process_state=_bot,
        logger=_log,
        localize_error=localize_user_error,
    )


def start_bot():
    global _want_running
    with _BOT_CONTROL_LOCK:
        result = _process_manager().start_bot()
        if result.get("ok"):
            _want_running = True
        return result


def stop_bot():
    global _want_running
    with _BOT_CONTROL_LOCK:
        _want_running = False
        return _process_manager().stop_bot()


def log_tail(path, n=400, max_bytes=262144):
    """从文件末尾读取最后 n 行（最多读 max_bytes 字节），
    避免整文件读入（bot.log 轮转后仍可能很大）"""
    if not os.path.exists(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


# ---------------- 同步机器人自动发现的新群 / 新用户 ----------------
def _read_discovered(filename, key):
    """读取自动发现文件中的 openid 列表（文件缺失/损坏返回空列表）"""
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [str(o) for o in (raw.get(key, []) if isinstance(raw, dict) else [])]
    except Exception:
        return []


def _read_discovered_group_bot_ids():
    """读取自动发现群与 QQ Bot 的归属映射；旧文件安全回退为空映射。"""
    path = os.path.join(DATA_DIR, "qq_group_openids.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        bindings = raw.get("group_bot_ids", {}) if isinstance(raw, dict) else {}
        if not isinstance(bindings, dict):
            return {}
        return {
            str(openid): str(bot_id).strip()
            for openid, bot_id in bindings.items()
            if str(openid).strip() and str(bot_id).strip()
        }
    except Exception:
        return {}


def sync_discovered_groups(data, openids=None):
    """把发现的群加入 settings；openids 指定时只添加这些（供勾选确认使用）"""
    groups = data.setdefault("qq_group_openids", [])
    known = {str(g.get("openid", "")) for g in groups}
    bot_ids = _read_discovered_group_bot_ids()
    if openids is None:
        openids = _read_discovered("qq_group_openids.json", "group_openids")
    else:
        allowed = set(_read_discovered("qq_group_openids.json", "group_openids"))
        openids = [o for o in openids if str(o) in allowed]
    added = 0
    for openid in openids:
        openid = str(openid)
        if not openid or openid in known:
            continue
        groups.append(
            {
                "openid": openid,
                "enabled": False,
                "remark": "自动发现，点击启用",
                "bot_id": bot_ids.get(openid, ""),
            }
        )
        known.add(openid)
        added += 1
    return added


def sync_discovered_users(data, openids=None):
    """把发现的用户加入 settings；openids 指定时只添加这些（供勾选确认使用）"""
    users = data.setdefault("qq_user_openids", [])
    known = {str(u.get("openid", "")) for u in users}
    if openids is None:
        openids = _read_discovered("qq_user_openids.json", "user_openids")
    else:
        allowed = set(_read_discovered("qq_user_openids.json", "user_openids"))
        openids = [o for o in openids if str(o) in allowed]
    added = 0
    for openid in openids:
        openid = str(openid)
        if not openid or openid in known:
            continue
        users.append(
            {"openid": openid, "enabled": False, "remark": "自动发现，点击启用"}
        )
        known.add(openid)
        added += 1
    return added


# ---------------- 开机自启 ----------------
def startup_dir():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )


def autostart_vbs_path():
    return os.path.join(startup_dir(), AUTOSTART_VBS_NAME)


def get_autostart():
    return os.path.exists(autostart_vbs_path())


def set_autostart(enabled):
    """开启/关闭开机自启：向启动文件夹写/删 WRelayAutostart.vbs
    内容全为 ASCII，避免中文路径在 VBS 里的编码问题（由 autostart.pyw 中转）"""
    path = autostart_vbs_path()
    if enabled:
        pyw = os.path.join(os.path.dirname(VENV_PYTHON), "pythonw.exe")
        launcher = os.path.join(BASE_DIR, "autostart.pyw")
        cmd = '"%s" "%s"' % (pyw, launcher)
        vbs = (
            'Set ws = CreateObject("Wscript.Shell")\r\n'
            'ws.Run "' + cmd.replace('"', '""') + '", 0, False\r\n'
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="ascii") as f:
                f.write(vbs)
        except Exception as exc:
            _log("写入开机自启失败: %s" % exc)
            return False
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            _log("删除开机自启失败: %s" % exc)
            return False
    ok = os.path.exists(path) if enabled else not os.path.exists(path)
    _log("开机自启 -> %s" % ("开启" if enabled else "关闭"))
    return ok


def _ensure_bot_running():
    st = bot_status()
    if st["running"]:
        _log("机器人已在运行 pid=%s" % st["pid"])
        return
    if not panel_alive():
        result = start_bot()
        _log("自动启动机器人结果: %s" % result)
        return
    # 面板端口已就绪：通过面板 API 启动，确保由面板托管（managed=True）
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://%s:%d/api/bot/start" % (PANEL_HOST, PANEL_PORT),
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-WRelay-Token": PANEL_TOKEN,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        _log("通过面板API启动机器人: %s" % result)
    except Exception as exc:
        _log("通过面板API启动机器人失败，改由本进程直接启动: %s" % exc)
        result = start_bot()
        _log("自动启动机器人结果: %s" % result)


def _autostart_bot_after_up():
    """后台自启模式：等面板端口起来后自动拉起机器人（不开浏览器）"""
    for _ in range(40):
        if panel_alive():
            break
        time.sleep(0.5)
    time.sleep(1)
    _ensure_bot_running()


def _watchdog_loop():
    """看门狗：_want_running（面板期望机器人运行）时，检测到机器人死了就自动拉起。
    手动停止后 _want_running=False 不会拉起；自动重启之间至少间隔 MIN_AUTO_RESTART_GAP，
    避免机器人反复崩溃时每 10 秒拉起一次的刷屏。"""
    global _last_auto_start
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        with _BOT_CONTROL_LOCK:
            if not _want_running:
                continue
            if bot_status()["running"]:
                continue
            if time.time() - _last_auto_start < MIN_AUTO_RESTART_GAP:
                continue
            _log("看门狗：检测到机器人已停止，自动重启")
            result = start_bot()
            if result.get("ok"):
                _last_auto_start = time.time()
                _log("看门狗自动重启成功 pid=%s" % result.get("pid"))
            else:
                _log("看门狗自动重启失败: %s" % result.get("msg"))


# ---------------- 转发统计 / 日志级别（机器人侧写入，面板只读） ----------------
def read_stats():
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def current_log_level():
    try:
        with open(RUNTIME_LEVEL_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("level", "")
    except Exception:
        return ""


# ---------------- bot HTTP 转发（面板 -> bot 18082） ----------------
# bot 的 NoneBot FastAPI 运行在 127.0.0.1:18082，补发相关查询/操作
# （待补发数量、手动补发、清除待补发）由 bot 进程完成，面板只做转发。
BOT_API_BASE = "http://127.0.0.1:%d" % BOT_PORT


def bot_api_get(path, timeout=10.0):
    """GET bot HTTP 端点，返回 dict；bot 不可用时返回 None"""
    import urllib.request
    try:
        req = urllib.request.Request(
            BOT_API_BASE + path,
            headers={"X-WRelay-Token": PANEL_TOKEN},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def bot_api_post(path, timeout=30.0):
    """POST bot HTTP 端点（无请求体），返回 dict；bot 不可用时返回 None"""
    import urllib.request
    try:
        req = urllib.request.Request(
            BOT_API_BASE + path,
            data=b"",
            method="POST",
            headers={"X-WRelay-Token": PANEL_TOKEN},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# 健康检查缓存：/api/status 会被多个页面（浏览器标签页/内嵌 webview）各自轮询，
# 若每个请求都实时转发给 bot，/api/health 就会被数倍放大（曾观测到 0.3~1 秒一次）。
# 这里做 3 秒 TTL 缓存：任意多个前端轮询源叠加，bot 的 /api/health 最多 3 秒请求一次。
# 返回结构与实时请求完全一致，不影响前端 Bot 状态判断逻辑。
_health_cache_lock = threading.Lock()
_health_cache = {"ts": 0.0, "value": None}
HEALTH_CACHE_TTL = 3.0


def _bot_health_cached():
    """返回 bot 健康状态；TTL 内复用缓存，防止多页面轮询打爆 bot 接口"""
    now = time.time()
    with _health_cache_lock:
        if now - _health_cache["ts"] < HEALTH_CACHE_TTL:
            return _health_cache["value"]
        value = bot_api_get("/api/health", timeout=2.0)
        _health_cache["ts"] = now
        _health_cache["value"] = value
        return value


# ---------------- FastAPI 面板 ----------------
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()


# ---------------- Origin 校验 ----------------
# 面板只绑定 127.0.0.1，但无请求体的 POST（/api/bot/stop 等）
# 可被任意网页用 mode:"no-cors" 的 fetch 触发（浏览器不发预检），从而停掉机器
# 人或重置配置。浏览器禁止自定义 Origin 头，故校验 Origin 即可堵住跨站请求：
# 只放行面板自身来源（http://127.0.0.1:18090）与无 Origin 的本地调用（curl/urllib）。
ALLOWED_ORIGIN = "http://%s:%s" % (PANEL_HOST, PANEL_PORT)
ALLOWED_ORIGINS = {
    ALLOWED_ORIGIN,
    "http://localhost:%s" % PANEL_PORT,
}


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"ok": False, "msg": "禁止访问"}, status_code=403)
    path = request.url.path
    if path == "/api/auth/bootstrap":
        # 本地浏览器、内嵌浏览器可能不发送 Origin；令牌本身仍保护后续 API。
        # 对带 Origin 的请求仍只允许面板自身的两个本地地址。
        pass
    elif path.startswith("/api/"):
        if request.headers.get("x-wrelay-token") != PANEL_TOKEN:
            return JSONResponse({"ok": False, "msg": "未授权"}, status_code=401)
    response = await call_next(request)
    # 面板是本地单页应用，禁止浏览器缓存页面/接口，
    # 否则改版后浏览器仍显示旧页面（曾导致"机器人状态检测中…"残留）
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/auth/bootstrap")
def api_auth_bootstrap():
    return {"ok": True, "token": PANEL_TOKEN}


@app.get("/api/status")
def api_status():
    st = bot_status()
    health = None
    if st["running"]:
        # bot 进程活着不代表网关连上；询问 bot 自身连接状态（带 3 秒缓存，防多页面轮询放大）
        health = _bot_health_cached()
    settings = load_settings()
    return {
        "running": st["running"],
        "pid": st["pid"],
        "managed": st["managed"],
        "health": health,
        "autostart": get_autostart(),
        "log": log_tail(BOT_LOG),
        "stats": read_stats(),
        "level": current_log_level(),
        # 补发相关：前端轮询发现 backfill_seq 变化时立即刷新待补发数量
        "backfill_seq": _backfill_seq,
        "backfill_enabled": settings.get("backfill_enabled", True),
        "backfill_limit": settings.get("backfill_limit", 10),
        "scheduled_message": settings.get("scheduled_message", {}),
    }


@app.get("/api/message-trace")
def api_message_trace(limit: int = 50):
    """代理 bot 的近期投递追踪接口；bot 不可用时返回可展示的空状态。"""
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    data = bot_api_get("/api/message-trace?limit=%d" % limit, timeout=2.0)
    if not isinstance(data, dict):
        return {"ok": False, "msg": "机器人未运行", "entries": []}
    return data


def _settings_payload():
    """load_settings() + 注入 X 转发组归一化结果（复用 bot 侧默认组兼容
    逻辑，仅内存不写盘）：旧配置缺省时前端也能拿到默认 X 转发组用于渲染，
    避免前端另写一套默认逻辑。"""
    data, revision = _load_settings_snapshot()
    try:
        data["x_forwarding_groups"] = get_x_forwarding_groups()
    except Exception:
        pass
    try:
        data["bilibili_forwarding_groups"] = get_bilibili_forwarding_groups()
    except Exception:
        pass
    data["_revision"] = revision
    return data


@app.get("/api/settings")
def api_settings():
    return _settings_payload()


@app.get("/api/settings/default")
def api_settings_default():
    return get_default_settings()


@app.get("/api/backup/export")
def api_backup_export(include_runtime_state: bool = False):
    try:
        payload = create_backup_archive(include_runtime_state)
    except ValueError as exc:
        return JSONResponse({"ok": False, "msg": str(exc)}, status_code=413)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="wrelay-backup-%s.zip"' % stamp},
    )


@app.post("/api/backup/restore")
async def api_backup_restore(request: Request, include_runtime_state: bool = False, confirm_runtime_state: bool = False):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > BACKUP_MAX_BYTES:
                return JSONResponse(
                    {"ok": False, "msg": "备份文件为空或超过 5 MB 限制"},
                    status_code=413,
                )
        except ValueError:
            return JSONResponse(
                {"ok": False, "msg": "上传请求大小无效"}, status_code=400
            )

    payload = bytearray()
    try:
        async for chunk in request.stream():
            if len(payload) + len(chunk) > BACKUP_MAX_BYTES:
                return JSONResponse(
                    {"ok": False, "msg": "备份文件为空或超过 5 MB 限制"},
                    status_code=413,
                )
            payload.extend(chunk)
    except Exception as exc:
        _log("读取备份上传失败: %s" % exc)
        return JSONResponse(
            {"ok": False, "msg": "读取备份上传失败"}, status_code=400
        )

    try:
        result = restore_backup_archive(
            bytes(payload), include_runtime_state, confirm_runtime_state,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "msg": localize_user_error(exc)}, status_code=400)
    except RestoreRollbackError as exc:
        _log("恢复备份失败且自动回滚不完整: %s" % exc)
        return JSONResponse(
            {
                "ok": False,
                "msg": "恢复失败，自动回滚不完整。恢复前备份保存在：%s"
                % exc.rollback_path,
            },
            status_code=500,
        )
    except Exception as exc:
        _log("恢复备份失败: %s" % exc)
        return JSONResponse(
            {"ok": False, "msg": "恢复失败，当前文件已恢复到操作前状态"},
            status_code=500,
        )
    _log("已恢复备份：%s；回退备份：%s" % (", ".join(result["restored"]), result["rollback"]))
    return {"ok": True, "restored": result["restored"], "msg": "恢复完成，已自动创建恢复前备份"}


@app.post("/api/settings/save")
async def api_settings_save(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "msg": "配置必须是对象"}, status_code=400)
    expected_revision = data.pop("_revision", None)
    if not isinstance(expected_revision, str):
        return JSONResponse(
            {"ok": False, "msg": "缺少配置版本，请刷新页面后重试"},
            status_code=409,
        )
    if expected_revision != _settings_revision():
        return JSONResponse(
            {
                "ok": False,
                "conflict": True,
                "msg": "配置已被其他页面或操作修改，请刷新后重试",
                "settings": _settings_payload(),
            },
            status_code=409,
        )
    err = _validate_settings(data)
    if err:
        # 校验失败绝不落盘，磁盘上的现有配置原样保留
        return JSONResponse(
            {"ok": False, "msg": "配置校验失败: " + err}, status_code=400
        )
    try:
        save_settings(data, expected_revision=expected_revision)
    except ConcurrentWriteError:
        return JSONResponse(
            {
                "ok": False,
                "conflict": True,
                "msg": "配置已被其他页面或进程修改，请刷新后重试",
                "settings": _settings_payload(),
            },
            status_code=409,
        )
    return {"ok": True, "settings": _settings_payload()}


@app.post("/api/settings/backfill-toggle")
async def api_settings_backfill_toggle(request: Request):
    """补发开关字段级更新（QQ 私聊 backfill on/off 专用）

    只接收 {"backfill_enabled": bool}，服务端读取当前配置后仅修改
    这一个字段再走统一保存流程（结构校验 + .bak + 原子替换），
    避免调用方回传整份配置覆盖面板刚保存的其他字段。
    请求值与当前值相同时不写盘，返回 changed=false。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    enabled = body.get("backfill_enabled") if isinstance(body, dict) else None
    if not isinstance(enabled, bool):
        return JSONResponse(
            {"ok": False, "msg": "backfill_enabled 必须是布尔值"}, status_code=400
        )
    data, revision = _load_settings_snapshot()
    if data.get("backfill_enabled") == enabled:
        return {
            "ok": True,
            "changed": False,
            "backfill_enabled": enabled,
        }
    data["backfill_enabled"] = enabled
    err = _validate_settings(data)
    if err:
        # 合并结果意外非法（如磁盘现有配置结构已坏）：拒绝落盘
        return JSONResponse(
            {"ok": False, "msg": "配置校验失败: " + err}, status_code=400
        )
    try:
        save_settings(data, expected_revision=revision)
    except ConcurrentWriteError:
        return JSONResponse(
            {"ok": False, "conflict": True, "msg": "配置已被其他进程修改，请刷新后重试"},
            status_code=409,
        )
    return {"ok": True, "changed": True, "backfill_enabled": enabled}


@app.post("/api/settings/scheduled-message")
async def api_settings_scheduled_message(request: Request):
    """定时消息字段级更新（QQ 私聊 timer 命令专用）。

    只更新请求中提供的定时消息字段，避免私聊操作用
    旧配置快照覆盖面板刚保存的其他设置。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)

    allowed = {
        "enabled", "content", "interval", "target_groups",
        "schedule_mode", "every_days", "every_weeks", "every_months",
        "weekdays", "send_time", "anchor_date", "messages",
    }
    if not any(key in body for key in allowed):
        return JSONResponse(
            {"ok": False, "msg": "至少提供一个定时消息字段"},
            status_code=400,
        )

    data, revision = _load_settings_snapshot()
    scheduled = data.get("scheduled_message")
    if not isinstance(scheduled, dict):
        scheduled = {
            "enabled": False,
            "content": "",
            "interval": 3600,
            "target_groups": [],
            "schedule_mode": "days",
            "every_days": 1,
            "every_weeks": 1,
            "every_months": 1,
            "weekdays": [0],
            "send_time": "09:00",
            "anchor_date": "",
            "messages": [],
        }
    else:
        scheduled = dict(scheduled)
    for key in allowed:
        if key in body:
            scheduled[key] = body[key]
    _sync_legacy_scheduled_message(scheduled, body)
    data["scheduled_message"] = scheduled

    err = _validate_settings(data)
    if err:
        return JSONResponse(
            {"ok": False, "msg": "配置校验失败: " + err}, status_code=400
        )
    try:
        save_settings(data, expected_revision=revision)
    except ConcurrentWriteError:
        return JSONResponse(
            {"ok": False, "conflict": True, "msg": "配置已被其他进程修改，请刷新后重试"},
            status_code=409,
        )
    return {"ok": True, "scheduled_message": scheduled}


@app.post("/api/settings/sync")
async def api_settings_sync(request: Request):
    """同步自动发现的新群/新用户到 settings.json

    body 可选 {"kind": "groups" | "users" | "all", "openids": [...]}：
    - kind 指定同步类型（默认 all）
    - openids 指定只添加这些 openid（前端勾选确认后传入选中的部分）"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = body.get("kind", "all") if isinstance(body, dict) else "all"
    openids = body.get("openids") if isinstance(body, dict) else None

    data, revision = _load_settings_snapshot()
    added = 0
    if kind in ("all", "groups"):
        added += sync_discovered_groups(data, openids)
    if kind in ("all", "users"):
        added += sync_discovered_users(data, openids)
    if added:
        try:
            save_settings(data, expected_revision=revision)
        except ConcurrentWriteError:
            return JSONResponse(
                {"ok": False, "conflict": True, "msg": "配置已被其他进程修改，请刷新后重试"},
                status_code=409,
            )
    return {"ok": True, "added": added, "settings": load_settings()}


@app.post("/api/settings/sync/preview")
def api_settings_sync_preview():
    """返回待添加的新群/新用户列表（只读，不写入 settings.json）

    供前端弹窗确认后再调用 /api/settings/sync 执行。"""
    data = load_settings()
    known_groups = {str(g.get("openid", "")) for g in data.get("qq_group_openids", [])}
    known_users = {str(u.get("openid", "")) for u in data.get("qq_user_openids", [])}
    groups = [
        o for o in _read_discovered("qq_group_openids.json", "group_openids")
        if o and o not in known_groups
    ]
    users = [
        o for o in _read_discovered("qq_user_openids.json", "user_openids")
        if o and o not in known_users
    ]
    return {"ok": True, "groups": groups, "users": users}


# ---------------- QQ OpenID 身份识别（辅助数据源） ----------------
# 身份资料库 data/qq_identities.json 由机器人自动写入（plugins/identities.py），
# 只作为「QQ 接收群」「私聊权限」卡片的辅助数据源：在 OpenID 旁展示已识别的
# 群名/昵称与管理员备注。与白名单/权限机制完全独立，不参与任何放行判断。
IDENTITIES_FILE = data_path("qq_identities.json")


def _read_identities():
    """读取身份资料 dict（缺失/损坏返回 {}）"""
    try:
        with open(IDENTITIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _identity_items(kind, name_key):
    """把身份库扁平化为列表 [{openid, name, admin_remark}]"""
    data = _read_identities()
    store = data.get(kind, {})
    if not isinstance(store, dict):
        store = {}
    items = []
    for openid, entry in store.items():
        if not isinstance(entry, dict):
            entry = {}
        items.append({
            "openid": str(openid),
            "name": str(entry.get(name_key) or ""),
            "admin_remark": str(entry.get("admin_remark") or ""),
        })
    return items


@app.get("/api/identities")
def api_identities():
    """返回身份库，供「QQ 接收群」「私聊权限」卡片在 OpenID 旁显示名称/备注"""
    return {
        "ok": True,
        "users": _identity_items("users", "nickname"),
        "groups": _identity_items("groups", "group_name"),
    }


# ---------------- QQ 注册审核 ----------------
# 注册申请由机器人写入 data/qq_registrations.json（plugins/registration.py）。
# 自动发现的 openid 只记录、不进同步名单；主动注册后才进入审核列表。
# 通过 = 按现有同步流程（sync_discovered_*）加入 settings（默认禁用，
# 是否放行仍由管理员勾选）；拒绝 = 移除申请，重新注册会再次出现。
# 注册数据与白名单数据独立存储。
REGISTRATIONS_FILE = data_path("qq_registrations.json")


def _read_registrations():
    try:
        with open(REGISTRATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_registrations(data, expected_revision=None):
    atomic_write_json(
        REGISTRATIONS_FILE,
        data,
        indent=2,
        expected_revision=expected_revision,
    )


def _registration_items(kind):
    """把注册申请扁平化为列表 [{openid, qq_id, nickname/group_name, ...}]，
    按提交时间排序（旧在前）"""
    store = _read_registrations().get(kind, {})
    if not isinstance(store, dict):
        store = {}
    items = []
    for openid, entry in store.items():
        if not isinstance(entry, dict):
            continue
        item = {"openid": str(openid)}
        for field in ("qq_id", "nickname", "group_name", "operator_openid", "bot_id", "time"):
            if field in entry:
                item[field] = entry[field]
        items.append(item)
    items.sort(key=lambda x: x.get("time") or 0)
    return items


@app.get("/api/registrations")
def api_registrations():
    """返回待审核的注册申请（用户/群），供两个 QQ 同步弹窗展示"""
    return {
        "ok": True,
        "users": _registration_items("users"),
        "groups": _registration_items("groups"),
    }


def _ensure_discovered(filename, key, openid, bot_id=""):
    """确保 openid 在自动发现记录中：注册必经真实消息事件（openid 已被
    自动发现过），若发现记录被清理则按注册申请补回，保证 sync_discovered_*
    能正常把该 openid 加入 settings。"""
    path = data_path(filename)
    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        openids = data.setdefault(key, [])
        if not isinstance(openids, list):
            openids = []
            data[key] = openids
        if str(openid) in openids:
            changed = False
        else:
            openids.append(str(openid))
            changed = True
        if key == "group_openids" and str(bot_id).strip():
            bindings = data.setdefault("group_bot_ids", {})
            if not isinstance(bindings, dict):
                bindings = {}
                data["group_bot_ids"] = bindings
            if bindings.get(str(openid)) != str(bot_id).strip():
                bindings[str(openid)] = str(bot_id).strip()
                changed = True
        return changed

    update_json(path, mutate, default={}, indent=4, write_if=bool)


def _fill_registration_info(data, list_key, openid, reg_entry):
    """审核通过后把注册信息带入对应权限条目（可选字段 name=名称，
    qq_id=账号）：用户取昵称/QQ号，群取群名/群号。条目不在列表时
    忽略；旧条目/手动添加的条目没有注册信息时无此字段，面板显示
    为空，不影响旧配置。remark 等原有字段一律保留不动。"""
    name = str(reg_entry.get("nickname") or reg_entry.get("group_name") or "")
    qq_id = str(reg_entry.get("qq_id") or "")
    for item in data.get(list_key, []):
        if isinstance(item, dict) and str(item.get("openid")) == str(openid):
            item["name"] = name
            item["qq_id"] = qq_id
            if list_key == "qq_group_openids" and str(reg_entry.get("bot_id") or "").strip():
                item["bot_id"] = str(reg_entry["bot_id"]).strip()
            return True
    return False


@app.post("/api/registrations/review")
async def api_registrations_review(request: Request):
    """审核注册申请：approve=进入现有同步流程（默认禁用），并把注册
    信息（名称/账号）带入对应权限条目；reject=移除申请。同一 openid
    重新注册会再次进入待审。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    kind = str((body or {}).get("kind") or "")
    openid = str((body or {}).get("openid") or "").strip()
    action = str((body or {}).get("action") or "")
    if kind not in ("users", "groups"):
        return JSONResponse({"ok": False, "msg": "无效的注册类型"}, status_code=400)
    if not openid:
        return JSONResponse({"ok": False, "msg": "缺少 openid"}, status_code=400)
    if action not in ("approve", "reject"):
        return JSONResponse({"ok": False, "msg": "无效的审核操作"}, status_code=400)

    registrations_revision = file_revision(REGISTRATIONS_FILE)
    regs = _read_registrations()
    store = regs.get(kind)
    entry = store.pop(openid, None) if isinstance(store, dict) else None
    if entry is None:
        return JSONResponse(
            {"ok": False, "msg": "注册申请不存在或已处理"}, status_code=404
        )

    added = 0
    if action == "approve":
        if kind == "users":
            _ensure_discovered("qq_user_openids.json", "user_openids", openid)
            data, settings_revision = _load_settings_snapshot()
            added = sync_discovered_users(data, [openid])
            _fill_registration_info(data, "qq_user_openids", openid, entry)
        else:
            _ensure_discovered(
                "qq_group_openids.json",
                "group_openids",
                openid,
                entry.get("bot_id"),
            )
            data, settings_revision = _load_settings_snapshot()
            added = sync_discovered_groups(data, [openid])
            _fill_registration_info(data, "qq_group_openids", openid, entry)
        try:
            save_settings(data, expected_revision=settings_revision)
        except ConcurrentWriteError:
            return JSONResponse(
                {"ok": False, "msg": "settings 已被其他进程修改，请刷新后重试"},
                status_code=409,
            )

    try:
        _write_registrations(regs, expected_revision=registrations_revision)
    except ConcurrentWriteError:
        return JSONResponse(
            {"ok": False, "msg": "注册申请已被其他进程修改，请刷新后重试"},
            status_code=409,
        )
    _log("注册审核 kind=%s action=%s openid=%s 加入settings=%s" % (kind, action, openid, added))
    return {"ok": True, "added": added, "settings": load_settings()}



@app.get("/api/autostart")
def api_autostart():
    return {"ok": True, "enabled": get_autostart()}


@app.post("/api/autostart/set")
async def api_autostart_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    enabled = bool((body or {}).get("enabled"))
    ok = set_autostart(enabled)
    return {"ok": ok, "enabled": get_autostart()}


@app.post("/api/bot/start")
def api_bot_start():
    return start_bot()


@app.post("/api/bot/stop")
def api_bot_stop():
    return stop_bot()


@app.post("/api/bot/restart")
def api_bot_restart():
    global _want_running
    with _BOT_CONTROL_LOCK:
        stop_result = stop_bot()
        if not stop_result.get("ok"):
            _want_running = True
            return stop_result
        time.sleep(1.0)
        return start_bot()


# ---------------- Bot 凭据配置（.env.prod 的 QQ_BOTS / DISCORD_BOTS） ----------------
# 面板只负责把凭据安全写回 .env.prod，生效依赖受控重启
# （/api/bot/restart）：新 Bot 进程启动时由 nonebot.init() 重新读取 .env.prod，
# 适配器据此建连。本 API 绝不返回/打印 AppSecret 与 Discord Token 等敏感凭据。
ENV_PROD = os.path.join(PROJECT_ROOT, ".env.prod")
_ENV_WRITE_LOCK = threading.Lock()


def _dotenv_line_value(raw):
    """去掉 QQ_BOTS= 后字符串值的引号包裹，返回待 json 解析的原文；失败返回 None"""
    s = raw.strip()
    if not s:
        return None
    q = s[0]
    if q in ("'", '"') and s[-1] == q:
        s = s[1:-1]
    return s


def _brackets_balanced(s):
    """粗略判断字符串内 [ ] { } 是否配平（忽略字符串字面量内的括号）"""
    depth = 0
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth <= 0


def _read_bots(key):
    """解析 .env.prod 的某 BOTS 键（QQ_BOTS / DISCORD_BOTS）；返回 (bots_list, line_idx, lines, end_idx)。

    该 BOTS 的 JSON 可能是多行 JSON（值横跨多个物理行）也可能是单行，
    这里从 key= 起把连续物理行收集到括号配平为止再解析。
    end_idx 是值占用的最后一个物理行下标，供写回时删除被折叠
    掉的多行续行。只改第一个 bot（当前适配器按此连接）。
    解析失败/缺文件返回 None。"""
    if not os.path.exists(ENV_PROD):
        return None
    with open(ENV_PROD, "r", encoding="utf-8") as f:
        lines = f.readlines()
    prefix = key + "="
    for i, line in enumerate(lines):
        if line.lstrip().startswith(prefix):
            raw_val = line.split("=", 1)[1] if "=" in line else ""
            buf = [raw_val]
            end = i
            while not _brackets_balanced("".join(buf)) and end + 1 < len(lines):
                end += 1
                buf.append(lines[end])
            txt = _dotenv_line_value("".join(buf))
            if txt is None:
                return None
            try:
                bots = json.loads(txt)
            except Exception:
                return None
            if not isinstance(bots, list) or not bots:
                return None
            return bots, i, lines, end
    return None


def _write_bots(key, bots, lines, line_idx, end_idx):
    """把新的 BOTS 序列化回首行（保留该行原换行符），其余行原样写回。

    若原 BOTS 为多行 JSON，折叠成单行后需删除被占用的续行
    （line_idx+1 .. end_idx），避免残留孤儿行污染 .env.prod。
    用唯一 tmp + os.replace 原子替换，避免写坏 .env.prod。"""
    new_val = json.dumps(bots, ensure_ascii=False)
    orig = lines[line_idx]
    ending = "\r\n" if orig.endswith("\r\n") else ("\r" if orig.endswith("\r") else "\n")
    lines[line_idx] = key + "='" + new_val + "'" + ending
    if end_idx > line_idx:
        del lines[line_idx + 1:end_idx + 1]
    # 令牌和 AppSecret 属于敏感配置：使用随机临时文件名并串行化写入，
    # 避免并发保存 QQ/Discord 配置时共用固定 .tmp 文件。
    tmp = "%s.%s.%s.tmp" % (ENV_PROD, os.getpid(), uuid.uuid4().hex)
    with _ENV_WRITE_LOCK:
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            os.replace(tmp, ENV_PROD)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _read_qq_bots():
    """解析 .env.prod 的 QQ_BOTS；返回 (bots_list, line_idx, lines, end_idx)。"""
    return _read_bots("QQ_BOTS")


def _public_qq_bots(bots):
    """把 QQ Bot 凭据转换为可安全返回给浏览器的 AppID 列表。"""
    return [
        {"appid": str(bot.get("id") or bot.get("token") or "").strip()}
        for bot in bots
        if isinstance(bot, dict) and str(bot.get("id") or bot.get("token") or "").strip()
    ]


def _prepare_qq_bots(payload, existing_bots):
    """校验并构造待写入 QQ_BOTS，绝不返回或记录 Secret。"""
    if not isinstance(payload, list) or not payload:
        raise ValueError("至少保留一个 QQ Bot")
    if len(payload) > 20:
        raise ValueError("QQ Bot 数量不能超过 20 个")
    existing = {
        str(bot.get("id") or bot.get("token") or "").strip(): dict(bot)
        for bot in existing_bots if isinstance(bot, dict)
    }
    result = []
    seen = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("QQ Bot 配置格式不正确")
        appid = str(item.get("appid") or "").strip()
        secret = str(item.get("secret") or "").strip()
        if not appid:
            raise ValueError("AppID 不能为空")
        if appid in seen:
            raise ValueError("AppID 不能重复")
        seen.add(appid)
        bot = existing.get(appid)
        if bot is None:
            if not secret:
                raise ValueError("新增 QQ Bot 必须填写 AppSecret")
            bot = {"id": appid, "token": appid, "secret": secret}
        else:
            bot["id"] = appid
            bot["token"] = appid
            if secret:
                bot["secret"] = secret
        result.append(bot)
    return result


def _bound_qq_bot_ids():
    """读取当前群绑定，删除仍在使用的 Bot 前必须先解绑或改绑。"""
    return {
        str(item.get("bot_id") or "").strip()
        for item in load_settings().get("qq_group_openids", [])
        if isinstance(item, dict) and str(item.get("bot_id") or "").strip()
    }


def _write_qq_bots(bots, lines, line_idx, end_idx):
    """把新 QQ_BOTS 序列化回首行（保留该行原换行符），其余行原样写回。"""
    _write_bots("QQ_BOTS", bots, lines, line_idx, end_idx)


def _read_discord_bots():
    """解析 .env.prod 的 DISCORD_BOTS；返回 (bots_list, line_idx, lines, end_idx)。"""
    return _read_bots("DISCORD_BOTS")


def _write_discord_bots(bots, lines, line_idx, end_idx):
    """把新 DISCORD_BOTS 序列化回首行（保留该行原换行符），其余行原样写回。"""
    _write_bots("DISCORD_BOTS", bots, lines, line_idx, end_idx)


_TRANSLATION_ENV_KEYS = (
    "WRELAY_TENCENT_SECRET_ID",
    "WRELAY_TENCENT_SECRET_KEY",
)


def _env_value_present(raw):
    """判断 .env 标量值是否非空，不返回实际密钥内容。"""
    value = str(raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return bool(value)


def _read_translation_env_state():
    """只返回腾讯密钥是否已配置，绝不把密钥返回给浏览器。"""
    state = {key: False for key in _TRANSLATION_ENV_KEYS}
    if not os.path.exists(ENV_PROD):
        return state
    with open(ENV_PROD, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in state:
                state[key] = _env_value_present(value)
    return state


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _valid_env_name(name):
    return bool(_ENV_NAME_RE.fullmatch(str(name or "").strip()))


def _read_env_secret(name):
    """只供本机检测请求读取密钥，绝不返回、记录或写入该值。"""
    key = str(name or "").strip()
    if not _valid_env_name(key):
        return ""
    value = os.environ.get(key, "").strip()
    if value:
        return value
    if not os.path.exists(ENV_PROD):
        return ""
    with open(ENV_PROD, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            env_key, raw_value = stripped.split("=", 1)
            if env_key.strip() == key:
                return str(_dotenv_line_value(raw_value) or "").strip()
    return ""


def _ai_chat_endpoint(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("AI API 地址不能为空")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _ai_models_endpoint(base_url):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("AI API 地址不能为空")
    suffix = "/chat/completions"
    if base.endswith(suffix):
        base = base[:-len(suffix)].rstrip("/")
    return base + "/models"


def _fetch_ai_models(base_url, api_key, timeout):
    """读取当前兼容接口的模型目录；只返回无凭据的模型 ID。"""
    import urllib.request

    request = urllib.request.Request(
        _ai_models_endpoint(base_url),
        headers={"Authorization": "Bearer " + api_key},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return []
    model_ids = []
    for item in data["data"]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids[:20]


class AiProbeError(RuntimeError):
    """可安全展示给面板的 AI 检测错误。"""

    def __init__(self, message, category="unknown", detail="", http_status=None):
        super().__init__(message)
        self.category = str(category or "unknown")
        self.detail = str(detail or "").strip()
        self.http_status = http_status


def _ai_probe_diagnostic(category, detail=""):
    """构造可持久化的检测诊断，不包含 API 密钥。"""
    detail = localize_user_error(detail, "未提供更详细的服务端说明")
    return {
        "category": str(category or "unknown")[:40],
        "detail": str(detail or "")[:500],
    }


def _probe_ai_api(config):
    """用最小 OpenAI 兼容请求验证配置；不返回模型回复或密钥。"""
    import urllib.error
    import urllib.request

    if not isinstance(config, dict):
        raise ValueError("检测配置格式错误")
    model = str(config.get("model") or "").strip()
    api_key_env = str(config.get("api_key_env") or "").strip()
    if not model:
        raise ValueError("AI 模型不能为空")
    if not api_key_env:
        raise ValueError("API 密钥环境变量名不能为空")
    api_key = str(config.get("api_key") or "").strip() or _read_env_secret(api_key_env)
    if not api_key:
        raise ValueError("AI API 密钥未配置")
    try:
        timeout = int(config.get("timeout_seconds") or 25)
    except (TypeError, ValueError):
        timeout = 25
    timeout = min(30, max(1, timeout))
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly OK."}],
        "temperature": 0,
        "max_tokens": 8,
    }).encode("utf-8")
    request = urllib.request.Request(
        _ai_chat_endpoint(config.get("base_url")),
        data=payload,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_data = json.loads(exc.read().decode("utf-8"))
            if isinstance(error_data, dict):
                error_value = error_data.get("error")
                if isinstance(error_value, dict):
                    detail = error_value.get("message") or error_value.get("detail") or ""
                elif isinstance(error_value, str):
                    detail = error_value
                if not detail:
                    detail = error_data.get("message") or error_data.get("detail") or ""
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        detail = str(detail or "").replace("\r", " ").replace("\n", " ").strip()
        if api_key and api_key in detail:
            detail = detail.replace(api_key, "[已隐藏]")
        detail = detail[:240]
        message = "AI API 返回 HTTP %s" % exc.code
        if detail:
            message += "：" + detail
        detail_lower = detail.lower()
        if "model" in detail_lower and (
            "unsupported" in detail_lower or "not supported" in detail_lower
        ):
            try:
                available_models = _fetch_ai_models(
                    config.get("base_url"), api_key, timeout
                )
            except Exception:
                available_models = []
            if available_models:
                message += "；当前可用模型：" + ", ".join(available_models)
        raise AiProbeError(
            message,
            category="http",
            detail=detail or "服务商未提供具体说明",
            http_status=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = str(getattr(exc, "reason", "") or exc).lower()
        if "timed out" in reason or "timeout" in reason:
            detail = "连接超时"
        elif "name or service not known" in reason or "getaddrinfo failed" in reason:
            detail = "无法解析服务器地址"
        elif "certificate" in reason or "ssl" in reason:
            detail = "TLS/证书验证失败"
        elif "refused" in reason:
            detail = "连接被服务器拒绝"
        elif "reset" in reason:
            detail = "连接被服务器重置"
        else:
            detail = "网络连接错误"
        raise AiProbeError("无法连接 AI API", category="network", detail=detail) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AiProbeError(
            "AI API 返回了无效响应",
            category="invalid_json",
            detail="响应内容不是有效 JSON",
        ) from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiProbeError(
            "AI API 响应格式不兼容",
            category="response_format",
            detail="缺少 choices[0].message.content 字段",
        ) from exc
    if not str(content or "").strip():
        raise AiProbeError(
            "AI API 未返回检测结果",
            category="empty_content",
            detail="choices[0].message.content 为空",
        )
    return model


def _repair_translation_env_file():
    """修复密钥被追加到上一行的旧配置，仅处理腾讯翻译变量。"""
    if not os.path.exists(ENV_PROD):
        raise RuntimeError(".env.prod 不存在")
    markers = tuple("%s=" % key for key in _TRANSLATION_ENV_KEYS)
    with _ENV_WRITE_LOCK:
        with open(ENV_PROD, "r", encoding="utf-8", newline="") as f:
            original_lines = f.readlines()
        ending = "\r\n"
        for line in original_lines:
            if line.endswith("\r\n"):
                break
            if line.endswith("\n"):
                ending = "\n"
                break

        lines = []
        changed = False
        for line in original_lines:
            positions = [line.find(marker) for marker in markers]
            position = min((item for item in positions if item > 0), default=-1)
            prefix = line[:position] if position > 0 else ""
            if position <= 0 or prefix.lstrip().startswith("#") or "=" not in prefix:
                lines.append(line)
                continue
            if not prefix.endswith(("\r\n", "\n", "\r")):
                prefix += ending
            lines.extend((prefix, line[position:]))
            changed = True

        if not changed:
            return False
        tmp = "%s.%s.%s.tmp" % (ENV_PROD, os.getpid(), uuid.uuid4().hex)
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            os.replace(tmp, ENV_PROD)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return True


def _write_translation_env(values):
    """原子写回腾讯密钥；空值表示保持原值，不创建密钥备份。"""
    if not os.path.exists(ENV_PROD):
        raise RuntimeError(".env.prod 不存在")
    updates = {
        key: str(values.get(key) or "").strip()
        for key in _TRANSLATION_ENV_KEYS
        if str(values.get(key) or "").strip()
    }
    if not updates:
        return
    if any("\r" in value or "\n" in value for value in updates.values()):
        raise ValueError("密钥不能包含换行")

    with _ENV_WRITE_LOCK:
        with open(ENV_PROD, "r", encoding="utf-8", newline="") as f:
            lines = f.readlines()
        ending = "\r\n"
        for line in lines:
            if line.endswith("\r\n"):
                ending = "\r\n"
                break
            if line.endswith("\n"):
                ending = "\n"
                break
        seen = set()
        for index, line in enumerate(lines):
            raw = line.lstrip()
            if raw.startswith("#") or "=" not in raw:
                continue
            key = raw.split("=", 1)[0].strip()
            if key not in updates:
                continue
            line_ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            lines[index] = "%s=%s%s" % (key, json.dumps(updates[key], ensure_ascii=False), line_ending)
            seen.add(key)
        for key, value in updates.items():
            if key not in seen:
                if lines and not lines[-1].endswith(("\r\n", "\n", "\r")):
                    lines[-1] += ending
                lines.append("%s=%s%s" % (key, json.dumps(value, ensure_ascii=False), ending))

        tmp = "%s.%s.%s.tmp" % (ENV_PROD, os.getpid(), uuid.uuid4().hex)
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            os.replace(tmp, ENV_PROD)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _write_ai_env(env_name, api_key):
    """原子保存 AI 密钥；空值表示保留现有值，密钥不进入 settings.json。"""
    key = str(env_name or "").strip()
    value = str(api_key or "").strip()
    if not _valid_env_name(key):
        raise ValueError("API 密钥环境变量名格式不正确")
    if not value:
        return
    if "\r" in value or "\n" in value:
        raise ValueError("API 密钥不能包含换行")
    if not os.path.exists(ENV_PROD):
        raise RuntimeError(".env.prod 不存在")

    with _ENV_WRITE_LOCK:
        with open(ENV_PROD, "r", encoding="utf-8", newline="") as f:
            lines = f.readlines()
        ending = "\r\n"
        for line in lines:
            if line.endswith("\r\n"):
                ending = "\r\n"
                break
            if line.endswith("\n"):
                ending = "\n"
                break
        seen = False
        for index, line in enumerate(lines):
            raw = line.lstrip()
            if raw.startswith("#") or "=" not in raw:
                continue
            line_key = raw.split("=", 1)[0].strip()
            if line_key != key:
                continue
            line_ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            lines[index] = "%s=%s%s" % (key, json.dumps(value, ensure_ascii=False), line_ending)
            seen = True
            break
        if not seen:
            if lines and not lines[-1].endswith(("\r\n", "\n", "\r")):
                lines[-1] += ending
            lines.append("%s=%s%s" % (key, json.dumps(value, ensure_ascii=False), ending))

        tmp = "%s.%s.%s.tmp" % (ENV_PROD, os.getpid(), uuid.uuid4().hex)
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
            os.replace(tmp, ENV_PROD)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


@app.get("/api/translation/env")
def api_translation_env():
    """返回腾讯密钥配置状态，不返回密钥内容。"""
    return {"ok": True, "configured": _read_translation_env_state()}


@app.post("/api/translation/env/repair")
def api_translation_env_repair():
    """修复旧版本密钥保存造成的末行拼接，不返回密钥。"""
    try:
        repaired = _repair_translation_env_file()
    except Exception:
        _log("修复腾讯翻译密钥配置失败")
        return JSONResponse({"ok": False, "msg": "修复翻译密钥配置失败"}, status_code=500)
    if repaired:
        _log("已修复腾讯翻译密钥配置的换行")
    return {"ok": True, "repaired": repaired, "configured": _read_translation_env_state()}


@app.post("/api/translation/env")
async def api_translation_env_save(request: Request):
    """保存腾讯 SecretId/SecretKey 到 .env.prod，成功后由前端重启 Bot。"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    values = {
        "WRELAY_TENCENT_SECRET_ID": data.get("secret_id"),
        "WRELAY_TENCENT_SECRET_KEY": data.get("secret_key"),
    }
    try:
        _write_translation_env(values)
    except ValueError as exc:
        return JSONResponse({"ok": False, "msg": localize_user_error(exc)}, status_code=400)
    except Exception:
        _log("写入腾讯翻译密钥失败")
        return JSONResponse({"ok": False, "msg": "保存腾讯 TMT 密钥失败"}, status_code=500)
    _log("腾讯 TMT 密钥配置已更新")
    return {"ok": True, "configured": _read_translation_env_state()}


@app.post("/api/translation/ai-test")
async def api_translation_ai_test(request: Request):
    """检测 AI API 当前编辑中的地址、模型与密钥，不写入配置。"""
    try:
        config = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "检测配置格式错误"}, status_code=400)
    if not isinstance(config, dict):
        return JSONResponse({"ok": False, "msg": "检测配置格式错误"}, status_code=400)
    try:
        model = await asyncio.to_thread(_probe_ai_api, config)
    except ValueError as exc:
        return JSONResponse(
            {
                "ok": False,
                "model": str(config.get("model") or "").strip(),
                "msg": localize_user_error(exc),
                "diagnostic": _ai_probe_diagnostic("config", str(exc)),
            },
            status_code=400,
        )
    except AiProbeError as exc:
        return JSONResponse(
            {
                "ok": False,
                "model": str(config.get("model") or "").strip(),
                "msg": localize_user_error(exc, "AI API 检测失败"),
                "diagnostic": _ai_probe_diagnostic(exc.category, exc.detail or str(exc)),
            },
            status_code=502,
        )
    except RuntimeError as exc:
        _log("AI API 检测失败: %s" % type(exc).__name__)
        return JSONResponse(
            {
                "ok": False,
                "model": str(config.get("model") or "").strip(),
                "msg": localize_user_error(exc, "AI API 检测失败"),
                "diagnostic": _ai_probe_diagnostic("unknown", type(exc).__name__),
            },
            status_code=502,
        )
    except Exception:
        _log("AI API 检测异常")
        return JSONResponse(
            {
                "ok": False,
                "model": str(config.get("model") or "").strip(),
                "msg": "AI API 检测失败",
                "diagnostic": _ai_probe_diagnostic("unknown", "检测过程发生未分类异常"),
            },
            status_code=502,
        )
    return {
        "ok": True,
        "model": model,
        "msg": "AI API 检测成功（%s）" % model,
        "diagnostic": _ai_probe_diagnostic("success", "接口返回了有效内容"),
    }


@app.post("/api/translation/ai-test-record")
async def api_translation_ai_test_record(request: Request):
    """只持久化 AI API 检测结果，不保存密钥或编辑中的其他字段。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "检测记录格式错误"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "msg": "检测记录格式错误"}, status_code=400)

    profile_id = str(body.get("profile_id") or "").strip()
    record = body.get("record")
    if not profile_id or not isinstance(record, dict):
        return JSONResponse({"ok": False, "msg": "检测记录格式错误"}, status_code=400)

    model = str(record.get("model") or "").strip()[:240]
    message = str(record.get("message") or "").strip()[:500]
    at = record.get("at")
    try:
        at = int(at)
    except (TypeError, ValueError):
        at = int(time.time() * 1000)
    safe_record = {
        "ok": record.get("ok") is True,
        "model": model,
        "message": message,
        "at": at,
    }
    diagnostic = record.get("diagnostic")
    if isinstance(diagnostic, dict):
        safe_record["diagnostic"] = {
            "category": str(diagnostic.get("category") or "unknown")[:40],
            "detail": str(diagnostic.get("detail") or "").strip()[:500],
        }

    data, revision = _load_settings_snapshot()
    profiles = data.get("ai_profiles")
    if not isinstance(profiles, list):
        profiles = []
        data["ai_profiles"] = profiles
    profile = next(
        (item for item in profiles if isinstance(item, dict) and str(item.get("id") or "") == profile_id),
        None,
    )
    if profile is None and profile_id == "default" and not profiles:
        legacy_ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
        profile = {
            "id": "default",
            "name": "默认 AI",
            "base_url": str(legacy_ai.get("base_url") or ""),
            "model": str(legacy_ai.get("model") or ""),
            "api_key_env": str(legacy_ai.get("api_key_env") or "WRELAY_AI_API_KEY"),
            "timeout_seconds": int(legacy_ai.get("timeout_seconds") or 25),
            "max_output_tokens": int(legacy_ai.get("max_output_tokens") or 800),
        }
        profiles.append(profile)
        data["ai_active_profile"] = "default"
    if profile is None:
        return JSONResponse({"ok": False, "msg": "该 AI 配置尚未保存，请先保存配置"}, status_code=409)

    profile["last_test"] = safe_record
    if safe_record["ok"]:
        profile["last_tested_model"] = model
    err = _validate_settings(data)
    if err:
        return JSONResponse({"ok": False, "msg": "检测记录保存失败: " + err}, status_code=400)
    try:
        save_settings(data, expected_revision=revision)
    except ConcurrentWriteError:
        return JSONResponse({"ok": False, "conflict": True, "msg": "配置已被其他操作修改，请稍后重试"}, status_code=409)
    return {"ok": True}


@app.post("/api/translation/ai-env")
async def api_translation_ai_env_save(request: Request):
    """保存 AI 密钥到指定环境变量；密钥本身绝不回传。"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    env_name = str(data.get("env_name") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not _valid_env_name(env_name):
        return JSONResponse({"ok": False, "msg": "API 密钥环境变量名格式不正确"}, status_code=400)
    try:
        _write_ai_env(env_name, api_key)
    except ValueError as exc:
        return JSONResponse({"ok": False, "msg": localize_user_error(exc)}, status_code=400)
    except Exception:
        _log("保存 AI API 密钥失败")
        return JSONResponse({"ok": False, "msg": "保存 AI API 密钥失败"}, status_code=500)
    return {"ok": True, "configured": bool(_read_env_secret(env_name))}


@app.get("/api/bot/qq-config")
def api_bot_qq_config():
    """返回 QQ Bot 的 AppID 列表，绝不返回 AppSecret。"""
    parsed = _read_qq_bots()
    if parsed is None:
        return {"ok": False, "msg": "无法读取 .env.prod 的 QQ_BOTS 配置"}
    bots, _line_idx, _lines, _end = parsed
    public_bots = _public_qq_bots(bots)
    return {
        "ok": True,
        "appid": public_bots[0]["appid"] if public_bots else "",
        "bot_ids": [item["appid"] for item in public_bots],
        "bots": public_bots,
    }


@app.post("/api/bot/qq-config")
async def api_bot_qq_config_save(request: Request):
    """保存 QQ Bot 列表到 .env.prod，绝不返回或记录 AppSecret。"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    parsed = _read_qq_bots()
    if parsed is None:
        return JSONResponse({"ok": False, "msg": "无法读取 .env.prod 的 QQ_BOTS 配置"}, status_code=400)
    bots, line_idx, lines, end_idx = parsed
    # 兼容旧版单 Bot 请求体，避免旧页面或自动化调用升级后失效。
    payload_bots = data.get("bots")
    if payload_bots is None:
        payload_bots = [{"appid": data.get("appid"), "secret": data.get("secret")}]
    try:
        new_bots = _prepare_qq_bots(payload_bots, bots)
    except ValueError as exc:
        return JSONResponse({"ok": False, "msg": str(exc)}, status_code=400)
    removed_ids = {
        str(bot.get("id") or bot.get("token") or "").strip()
        for bot in bots if isinstance(bot, dict)
    } - {
        str(bot.get("id") or bot.get("token") or "").strip()
        for bot in new_bots
    }
    still_bound = sorted(removed_ids & _bound_qq_bot_ids())
    if still_bound:
        return JSONResponse({"ok": False, "msg": "仍有 QQ 群绑定以下 Bot，请先改绑：" + "、".join(still_bound)}, status_code=400)
    # 原子写回 .env.prod；绝不创建含 AppSecret 的持久化备份。
    # 失败时清理 .tmp 并返回失败（不重启 Bot），磁盘上旧 .env.prod 保持不变。
    try:
        _write_qq_bots(new_bots, lines, line_idx, end_idx)
    except Exception as exc:
        _log("写入 .env.prod 失败: %s" % exc)
        try:
            if os.path.exists(ENV_PROD + ".tmp"):
                os.remove(ENV_PROD + ".tmp")
        except Exception:
            pass
        return JSONResponse({"ok": False, "msg": "保存 QQ Bot 配置失败"}, status_code=500)
    _log("QQ Bot 配置已更新（数量=%s）" % len(new_bots))
    public_bots = _public_qq_bots(new_bots)
    return {"ok": True, "appid": public_bots[0]["appid"], "bot_ids": [item["appid"] for item in public_bots]}


@app.get("/api/bot/discord-config")
def api_bot_discord_config():
    """返回当前 Discord Bot 是否已配置 Token。

    只返回 configured 布尔，绝不返回 Discord Token 内容。"""
    parsed = _read_discord_bots()
    if parsed is None:
        return {"ok": False, "msg": "无法读取 .env.prod 的 DISCORD_BOTS 配置"}
    bots, _line_idx, _lines, _end = parsed
    bot0 = bots[0]
    configured = bool(str(bot0.get("token") or "").strip())
    return {"ok": True, "configured": configured}


@app.post("/api/bot/discord-config")
async def api_bot_discord_config_save(request: Request):
    """保存新的 Discord Bot Token 到 .env.prod。

    请求体 {token}：token 为空串表示保持原值。只改 DISCORD_BOTS 第一个
    对象的 "token" 字段，保留 intent 等其他字段。失败时绝不落盘，
    磁盘上现有 .env.prod 保持原样。成功只返回状态，不返回 Token。"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    new_token = str(data.get("token") or "").strip()
    parsed = _read_discord_bots()
    if parsed is None:
        return JSONResponse({"ok": False, "msg": "无法读取 .env.prod 的 DISCORD_BOTS 配置"}, status_code=400)
    bots, line_idx, lines, end_idx = parsed
    bot0 = bots[0]
    if new_token:
        bot0["token"] = new_token
    # 原子写回 .env.prod；绝不创建含 Token 的持久化备份。
    # 失败时清理 .tmp 并返回失败（不重启 Bot），磁盘上旧 .env.prod 保持不变。
    try:
        _write_discord_bots(bots, lines, line_idx, end_idx)
    except Exception as exc:
        _log("写入 .env.prod 失败: %s" % exc)
        try:
            if os.path.exists(ENV_PROD + ".tmp"):
                os.remove(ENV_PROD + ".tmp")
        except Exception:
            pass
        return JSONResponse({"ok": False, "msg": "保存 Discord Bot 配置失败"}, status_code=500)
    _log("Discord Bot 配置已更新")
    return {"ok": True}


@app.post("/api/panel/restart")
def api_panel_restart():
    """重启面板自身：拉起独立 worker 进程，待本进程退出并释放
    18090 后由 worker 按原启动方式（autostart/双击）拉起新面板。

    面板的路由与静态资源在进程启动时载入内存，改代码后必须
    重启面板进程才生效；此接口提供一键入口，免去手动杀进程。"""
    worker = os.path.join(BASE_DIR, "restart_worker.pyw")
    if not os.path.exists(worker):
        return {"ok": False, "msg": "缺少 restart_worker.pyw"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    args = [sys.executable, worker]
    if AUTOSTART_FLAG in sys.argv:
        args.append(AUTOSTART_FLAG)
    try:
        subprocess.Popen(
            args,
            cwd=PROJECT_ROOT,
            creationflags=flags,
            env=env,
        )
    except Exception as exc:
        _log("重启面板 worker 启动失败: %s" % exc)
        return {"ok": False, "msg": localize_user_error(f"启动重启进程失败: {exc}", "启动重启进程失败")}
    _log("面板重启：worker 已拉起，本进程 2 秒后退出")
    # 让 HTTP 响应先返回，再退出当前面板进程
    threading.Timer(2.0, os._exit, args=(0,)).start()
    return {"ok": True, "msg": "面板正在重启，请稍候刷新页面"}


@app.get("/api/bot/loglevel")
def api_bot_loglevel():
    return {"ok": True, "level": current_log_level()}


@app.post("/api/bot/loglevel")
async def api_bot_loglevel_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    level = str((body or {}).get("level", "")).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        return JSONResponse({"ok": False, "msg": "无效的日志级别"}, status_code=400)
    try:
        atomic_write_json(
            RUNTIME_LEVEL_FILE,
            {"level": level},
            indent=2,
        )
    except Exception as exc:
        _log("设置日志级别失败: %s" % exc)
        return {"ok": False, "msg": "写入失败"}
    _log("日志级别 -> %s" % level)
    return {"ok": True, "level": level}


@app.post("/api/bot/log/clear")
def api_bot_log_clear():
    try:
        with open(BOT_LOG, "w", encoding="utf-8"):
            pass
        _log("清空机器人日志")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "msg": localize_user_error(exc)}


# ---------------- 离线补发（转发到 bot 18082） ----------------
# _backfill_seq：补发/清除成功一次递增一次，前端轮询 /api/status
# 发现变化后立即刷新待补发数量（否则最长 5 分钟才更新）。
_backfill_seq = 0


@app.get("/api/backfill/pending")
def api_backfill_pending():
    running = bot_status()["running"]
    if not running:
        return {"ok": False, "running": False, "msg": "机器人未运行"}
    data = bot_api_get("/api/backfill/pending")
    if data is None:
        return {"ok": False, "running": True, "msg": "bot 接口不可用"}
    data["running"] = True
    return data


@app.post("/api/backfill/run")
def api_backfill_run():
    global _backfill_seq
    if not bot_status()["running"]:
        return {"ok": False, "msg": "机器人未运行"}
    data = bot_api_post("/api/backfill/run")
    if data is None:
        return {"ok": False, "msg": "bot 接口不可用"}
    if data.get("ok"):
        _backfill_seq += 1
    return data


@app.post("/api/backfill/clear")
def api_backfill_clear():
    global _backfill_seq
    if not bot_status()["running"]:
        return {"ok": False, "msg": "机器人未运行"}
    data = bot_api_post("/api/backfill/clear")
    if data is None:
        return {"ok": False, "msg": "bot 接口不可用"}
    if data.get("ok"):
        _backfill_seq += 1
    return data


# ---------------- X RSS 运行状态（代理到 bot 18082） ----------------
@app.get("/api/xrss/status")
def api_xrss_status():
    """X → QQ 页状态卡片数据源：bot 未运行直接返回未运行，
    避免前端把「bot 停止」误判成 X RSS 的其他状态"""
    if not bot_status()["running"]:
        return {"ok": False, "bot_running": False}
    data = bot_api_get("/api/x-rss/status")
    if data is None:
        return {"ok": False, "bot_running": True, "msg": "bot 接口不可用"}
    data["bot_running"] = True
    return data


@app.get("/api/bilibili/status")
def api_bilibili_status():
    """代理 bot 的 Bilibili RSS 运行状态。"""
    if not bot_status()["running"]:
        return {"ok": False, "bot_running": False}
    data = bot_api_get("/api/bilibili/status")
    if data is None:
        return {"ok": False, "bot_running": True, "msg": "bot 接口不可用"}
    data["bot_running"] = True
    return data


@app.get("/api/channel-names")
def api_channel_names():
    """代理 bot 的频道真实名称接口（bot 离线时返回空映射）"""
    if not bot_status()["running"]:
        return {"ok": False, "running": False, "names": {}}
    data = bot_api_get("/api/channel-names")
    if data is None:
        return {"ok": False, "running": True, "names": {}}
    data["running"] = True
    return data


@app.get("/api/channels/audit")
def api_channels_audit():
    """代理 bot 的频道权限快照接口（读取上次扫描结果）"""
    if not bot_status()["running"]:
        return {"ok": False, "running": False, "audit": {}}
    data = bot_api_get("/api/channels/audit")
    if data is None:
        return {"ok": False, "running": True, "audit": {}}
    data["running"] = True
    return data


@app.post("/api/channels/refresh")
def api_channels_refresh():
    """代理 bot 的频道权限刷新接口（重新扫描并更新记录，耗时较长）"""
    if not bot_status()["running"]:
        return {"ok": False, "msg": "机器人未运行"}
    data = bot_api_post("/api/channels/refresh", timeout=120)
    if data is None:
        return {"ok": False, "msg": "bot 接口不可用"}
    return data


# ---------------- 静态页面 ----------------
app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "web"), html=True), name="web")


# ---------------- 启动 ----------------
def _open_browser():
    time.sleep(1.2)
    try:
        webbrowser.open("http://%s:%d/" % (PANEL_HOST, PANEL_PORT))
    except Exception:
        pass


def main():
    autostart = AUTOSTART_FLAG in sys.argv
    restarting = RESTART_FLAG in sys.argv

    _log("面板启动 autostart=%s" % autostart)

    # 转发组磁盘一次性迁移（旧配置生成默认组）；失败不阻断启动，
    # bot 侧 plugins/config.py 仍会内存归一化兜底路由
    try:
        migrate_forwarding_groups()
    except Exception as exc:
        _log("转发组迁移失败: %s" % exc)

    # 清理过期的 bot.pid（重启后残留 PID）
    pid = read_pid()
    if pid and not _proc_exists(pid):
        clear_pid()
        _log("清理过期 bot.pid: %s" % pid)

    if autostart:
        # 开机自启：后台运行，不弹浏览器
        if panel_alive():
            # 面板已由其它实例启动：只需确保机器人在跑
            _log("面板已在运行，仅确保机器人启动")
            _ensure_bot_running()
            return
        threading.Thread(target=_autostart_bot_after_up, daemon=True).start()
    else:
        # 正常双击打开
        if panel_alive():
            # 面板已在运行，只需打开浏览器
            if not restarting:
                webbrowser.open("http://%s:%d/" % (PANEL_HOST, PANEL_PORT))
            return
        if not restarting:
            threading.Thread(target=_open_browser, daemon=True).start()

    # 面板接管时若机器人已在运行，视为期望运行：看门狗继续守护，崩溃后自动拉起
    if bot_status()["running"]:
        _want_running = True
    # 看门狗：面板期望机器人运行时，崩溃/被杀后自动拉起
    threading.Thread(target=_watchdog_loop, daemon=True).start()

    try:
        uvicorn.run(app, host=PANEL_HOST, port=PANEL_PORT, log_level="info")
    except Exception as exc:
        _log("面板启动失败: %s" % exc)
        if not autostart and not restarting:
            # 双击容错：端口被占（面板已由其它实例启动）时也打开浏览器
            try:
                webbrowser.open("http://%s:%d/" % (PANEL_HOST, PANEL_PORT))
            except Exception:
                pass


if __name__ == "__main__":
    main()
