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
import ctypes
import socket
import threading
import subprocess
import webbrowser
import secrets
import uuid

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

# 正式安装包使用 {项目根}\python\python.exe（standalone Python，无 .venv）；
# 开发环境若存在 .venv 则优先使用，否则回退到项目根的 python。
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = os.path.join(PROJECT_ROOT, "python", "python.exe")
BOT_ENTRY = os.path.join(PROJECT_ROOT, "bot.py")

PANEL_HOST = "127.0.0.1"
PANEL_PORT = 8090
BOT_PORT = 8082

# ---------------- 转发组 ----------------
# 与 plugins/config.py（bot 侧）保持一致：
#   默认组 id=default / name=转发组1；成员 = 当前全局 enabled 的频道/群。
# 转发组只表达"频道→群"路由关系，不参与 enabled 语义。
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

# pythonw 下没有控制台窗口，把面板自身的输出重定向到 panel.log
try:
    _panel_log = open(PANEL_LOG, "a", encoding="utf-8", buffering=1)
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

# X 转发组：复用 bot 侧的归一化结果（GET 注入默认组）与 source key 派生
# （保存时清理悬空引用）。导入失败时用最小回退（等价于不做注入/清理）。
try:
    from plugins.config import (
        get_x_forwarding_groups,
        _x_source_key,
    )
except Exception:
    def get_x_forwarding_groups():
        return []

    def _x_source_key(raw):
        return str(raw or "").lstrip("@")


def _log(msg):
    """面板运行日志（panel.log），带时间戳"""
    try:
        with open(PANEL_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
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


def save_settings(data, expected_revision=None):
    # 备份上一版配置（任何误写/损坏都可从 settings.json.bak 恢复），
    # 再走统一原子写（唯一 tmp + os.replace，见 plugins/json_io.py）
    _normalize_forwarding_groups(data)
    _normalize_x_forwarding_groups(data)
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

# 看门狗：_want_running 为 True 表示"面板期望机器人保持运行"（点过启动/重启/开机自启），
# 机器人崩溃或被杀后由后台线程自动拉起；手动停止（stop_bot）会置 False，不再自动重启。
_want_running = False
_last_auto_start = 0.0
WATCHDOG_INTERVAL = 10.0   # 每 10 秒检查一次机器人存活
MIN_AUTO_RESTART_GAP = 30.0  # 两次自动重启至少间隔 30 秒，连续崩溃时不刷屏


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except Exception:
        return False


PANEL_PING_URL = "http://%s:%d/api/status" % (PANEL_HOST, PANEL_PORT)


def panel_alive():
    """判断面板是否真正在运行：HTTP 探测 /api/status 成功才算。

    不能只用 TCP 探测（port_open）：一旦 8090 被其它进程（如系统里
    恰好占用的无关软件）监听，新面板会被误判为"面板已在运行"而直接
    退出，浏览器反而打不开管理页。只有返回 200 的 HTTP 响应才说明
    当前 8090 上确实是本面板。
    """
    import urllib.request
    try:
        req = urllib.request.Request(
            PANEL_PING_URL,
            headers={"X-WRelay-Token": PANEL_TOKEN},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _proc_exists(pid):
    """判断 pid 是否仍在运行。
    用 WaitForSingleObject 检测进程是否已 signaled（终止）：
    即使其它进程（如面板持有的 Popen 句柄）仍持有该进程的对象句柄，
    进程终止后 WaitForSingleObject 也会返回 signaled，
    避免"进程已退出却被误判为运行中"导致看门狗不重启。"""
    if pid <= 0:
        return False
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            # 0 = WAIT_OBJECT_0（进程已终止）；0x102 = WAIT_TIMEOUT（仍在运行）
            alive = (
                ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                == 0x102
            )
            if not alive:
                return False

            # PID 可能在 Bot 退出后被 Windows 复用。只有进程镜像确实是
            # 当前 WRelay 使用的 Python 解释器时，才允许面板接管/结束它。
            size = ctypes.c_ulong(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            query = ctypes.windll.kernel32.QueryFullProcessImageNameW
            query.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            query.restype = ctypes.c_int
            if not query(handle, 0, buf, ctypes.byref(size)):
                return False
            return os.path.normcase(os.path.abspath(buf.value)) == os.path.normcase(
                os.path.abspath(VENV_PYTHON)
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def read_pid():
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def write_pid(pid):
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def clear_pid():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def bot_status():
    proc = _bot["proc"]
    if proc is not None and proc.poll() is None:
        return {"running": True, "pid": proc.pid, "managed": True}
    pid = read_pid()
    if pid:
        if _proc_exists(pid):
            return {"running": True, "pid": pid, "managed": False}
        # 过期 pid（重启后进程已不存在）自动清理，避免被其它进程复用 PID 误判
        clear_pid()
    # 不再用端口探测判断存活：BOT_PORT 可能被其它程序（如 QQ 客户端）
    # 占用，导致 Bot 未运行却被误判为 running、start_bot 拒绝重启。
    # 存活只依据进程本身（_bot["proc"] 或 bot.pid）。
    return {"running": False, "pid": 0, "managed": False}


def _kill_tree(pid):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        try:
            ctypes.windll.kernel32.TerminateProcess(
                ctypes.windll.kernel32.OpenProcess(0x0001, False, pid), 1
            )
        except Exception:
            pass


def start_bot():
    global _want_running
    st = bot_status()
    if st["running"]:
        return {"ok": False, "msg": "机器人已在运行"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    # 日志改由 bot.py 内的 loguru 直接写 data/logs/bot.log（带 5MB 轮转），
    # 因此不再把 stdout/stderr 重定向到 bot.log，避免双写同一文件。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.Popen(
            [VENV_PYTHON, BOT_ENTRY],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            env=env,
        )
    except Exception as exc:
        _log("启动机器人失败: %s" % exc)
        return {"ok": False, "msg": f"启动失败: {exc}"}
    _bot["proc"] = proc
    write_pid(proc.pid)
    _want_running = True
    _log("启动机器人 pid=%s" % proc.pid)
    return {"ok": True, "pid": proc.pid}


def stop_bot():
    global _want_running
    _want_running = False
    stopped = False
    proc = _bot["proc"]
    if proc is not None and proc.poll() is None:
        _kill_tree(proc.pid)
        stopped = True
    _bot["proc"] = None
    pid = read_pid()
    if pid and _proc_exists(pid):
        _kill_tree(pid)
        stopped = True
    clear_pid()
    if stopped:
        _log("停止机器人")
    return {"ok": True, "stopped": stopped}


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


def sync_discovered_groups(data, openids=None):
    """把发现的群加入 settings；openids 指定时只添加这些（供勾选确认使用）"""
    groups = data.setdefault("qq_group_openids", [])
    known = {str(g.get("openid", "")) for g in groups}
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
            {"openid": openid, "enabled": False, "remark": "自动发现，点击启用"}
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


# ---------------- bot HTTP 转发（面板 -> bot 8082） ----------------
# bot 的 NoneBot FastAPI 运行在 127.0.0.1:8082，补发相关查询/操作
# （待补发数量、手动补发、清除待补发）由 bot 进程完成，面板只做转发。
BOT_API_BASE = "http://127.0.0.1:%d" % BOT_PORT


def bot_api_get(path, timeout=10.0):
    """GET bot HTTP 端点，返回 dict；bot 不可用时返回 None"""
    import urllib.request
    try:
        with urllib.request.urlopen(BOT_API_BASE + path, timeout=timeout) as resp:
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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()


# ---------------- Origin 校验 ----------------
# 面板只绑定 127.0.0.1，但无请求体的 POST（/api/bot/stop 等）
# 可被任意网页用 mode:"no-cors" 的 fetch 触发（浏览器不发预检），从而停掉机器
# 人或重置配置。浏览器禁止自定义 Origin 头，故校验 Origin 即可堵住跨站请求：
# 只放行面板自身来源（http://127.0.0.1:8090）与无 Origin 的本地调用（curl/urllib）。
ALLOWED_ORIGIN = "http://%s:%s" % (PANEL_HOST, PANEL_PORT)
ALLOWED_ORIGINS = {
    ALLOWED_ORIGIN,
    "http://localhost:%s" % PANEL_PORT,
}


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"ok": False, "msg": "Forbidden"}, status_code=403)
    path = request.url.path
    if path == "/api/auth/bootstrap":
        # 本地浏览器、内嵌浏览器可能不发送 Origin；令牌本身仍保护后续 API。
        # 对带 Origin 的请求仍只允许面板自身的两个本地地址。
        pass
    elif path.startswith("/api/"):
        if request.headers.get("x-wrelay-token") != PANEL_TOKEN:
            return JSONResponse({"ok": False, "msg": "Unauthorized"}, status_code=401)
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
    }


def _settings_payload():
    """load_settings() + 注入 X 转发组归一化结果（复用 bot 侧默认组兼容
    逻辑，仅内存不写盘）：旧配置缺省时前端也能拿到默认 X 转发组用于渲染，
    避免前端另写一套默认逻辑。"""
    data, revision = _load_settings_snapshot()
    try:
        data["x_forwarding_groups"] = get_x_forwarding_groups()
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
        for field in ("qq_id", "nickname", "group_name", "operator_openid", "time"):
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


def _ensure_discovered(filename, key, openid):
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
            return False
        openids.append(str(openid))
        return True

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
            _ensure_discovered("qq_group_openids.json", "group_openids", openid)
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
    stop_bot()
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


def _write_qq_bots(bots, lines, line_idx, end_idx):
    """把新 QQ_BOTS 序列化回首行（保留该行原换行符），其余行原样写回。"""
    _write_bots("QQ_BOTS", bots, lines, line_idx, end_idx)


def _read_discord_bots():
    """解析 .env.prod 的 DISCORD_BOTS；返回 (bots_list, line_idx, lines, end_idx)。"""
    return _read_bots("DISCORD_BOTS")


def _write_discord_bots(bots, lines, line_idx, end_idx):
    """把新 DISCORD_BOTS 序列化回首行（保留该行原换行符），其余行原样写回。"""
    _write_bots("DISCORD_BOTS", bots, lines, line_idx, end_idx)


@app.get("/api/bot/qq-config")
def api_bot_qq_config():
    """返回当前 QQ Bot 的 AppID（从 .env.prod 读取真实配置）。

    绝不返回 AppSecret；未配置时 appid 为空字符串。"""
    parsed = _read_qq_bots()
    if parsed is None:
        return {"ok": False, "msg": "无法读取 .env.prod 的 QQ_BOTS 配置"}
    bots, _line_idx, _lines, _end = parsed
    bot0 = bots[0]
    appid = str(bot0.get("id") or bot0.get("token") or "").strip()
    return {"ok": True, "appid": appid}


@app.post("/api/bot/qq-config")
async def api_bot_qq_config_save(request: Request):
    """保存新的 AppID / AppSecret 到 .env.prod。

    请求体 {appid, secret}：secret 为空串表示保持原值。失败时绝不落盘，
    磁盘上现有 .env.prod 保持原样。成功返回 appid，不返回 secret。"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "数据格式错误"}, status_code=400)
    new_appid = str(data.get("appid") or "").strip()
    new_secret = str(data.get("secret") or "").strip()
    if not new_appid:
        return JSONResponse({"ok": False, "msg": "AppID 不能为空"}, status_code=400)
    parsed = _read_qq_bots()
    if parsed is None:
        return JSONResponse({"ok": False, "msg": "无法读取 .env.prod 的 QQ_BOTS 配置"}, status_code=400)
    bots, line_idx, lines, end_idx = parsed
    bot0 = bots[0]
    bot0["id"] = new_appid
    bot0["token"] = new_appid
    if new_secret:
        bot0["secret"] = new_secret
    # 原子写回 .env.prod；绝不创建含 AppSecret 的持久化备份。
    # 失败时清理 .tmp 并返回失败（不重启 Bot），磁盘上旧 .env.prod 保持不变。
    try:
        _write_qq_bots(bots, lines, line_idx, end_idx)
    except Exception as exc:
        _log("写入 .env.prod 失败: %s" % exc)
        try:
            if os.path.exists(ENV_PROD + ".tmp"):
                os.remove(ENV_PROD + ".tmp")
        except Exception:
            pass
        return JSONResponse({"ok": False, "msg": "保存 QQ Bot 配置失败"}, status_code=500)
    _log("QQ Bot 配置已更新（AppID=%s）" % new_appid)
    return {"ok": True, "appid": new_appid}


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
    8090 后由 worker 按原启动方式（autostart/双击）拉起新面板。

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
        return {"ok": False, "msg": f"启动重启进程失败: {exc}"}
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
        return {"ok": False, "msg": str(exc)}


# ---------------- 离线补发（转发到 bot 8082） ----------------
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


# ---------------- X RSS 运行状态（代理到 bot 8082） ----------------
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
