"""Independent Windows desktop supervisor; deliberate exits are never revived."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from plugins.paths import COMPILED, EDITION, PROJECT_ROOT, data_path
from plugins.json_io import atomic_write_json


INTENT_FILE = data_path('desktop_recovery.json')


def remember_running(wanted):
    atomic_write_json(INTENT_FILE, {'want_running': bool(wanted)}, indent=2)


def running_intent():
    try:
        data = json.loads(Path(INTENT_FILE).read_text(encoding='utf-8'))
        return isinstance(data, dict) and data.get('want_running') is True
    except (OSError, ValueError):
        return False


def _kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateEventW.restype = wintypes.HANDLE
    api.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenEventW.restype = wintypes.HANDLE
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.SetEvent.argtypes = [wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    return api


def _launch(arguments, guardian=False):
    if COMPILED:
        command = [str(PROJECT_ROOT / ('WRelay.Guardian.exe' if guardian else 'WRelay.exe')), *arguments]
    else:
        command = [sys.executable, str(PROJECT_ROOT / 'panel' / 'desktop_app.pyw'), *arguments]
    return subprocess.Popen(command, cwd=str(PROJECT_ROOT), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class GuardianSession:
    def __init__(self):
        self.api = _kernel()
        self.name = 'Local\\WRelay-' + EDITION + '-quit-' + secrets.token_hex(16)
        self.handle = self.api.CreateEventW(None, True, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.proc = None

    def start(self):
        self.proc = _launch(['--guardian', str(os.getpid()), self.name], guardian=True)

    def stop(self):
        if self.handle:
            self.api.SetEvent(self.handle)


def _log(message):
    # Use the existing bounded desktop log format without storing private data.
    try:
        path = Path(data_path('logs/guardian.log'))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1024 * 1024:
            os.replace(path, path.with_suffix('.log.1'))
        with path.open('a', encoding='utf-8') as file:
            file.write(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message + '\n')
    except OSError:
        pass


def supervise(pid, event_name):
    api = _kernel()
    quit_event = api.OpenEventW(0x100000, False, event_name)
    # This bound handle refers to the original process even if its PID is reused.
    parent = api.OpenProcess(0x100000 | 1, False, pid)
    if not quit_event or not parent:
        for handle in (quit_event, parent):
            if handle:
                api.CloseHandle(handle)
        return
    try:
        from panel.local_bridge import request_manager
        last_ok = time.monotonic()
        probe_done = threading.Event()
        probe = None
        result = {}
        def ping():
            try:
                result['ok'] = request_manager('GET', '/api/maintenance/ping').get('ok') is True
            except Exception:
                result['ok'] = False
            finally:
                probe_done.set()
        while True:
            if api.WaitForSingleObject(quit_event, 0) == 0:
                return
            if api.WaitForSingleObject(parent, 0) == 0:
                break
            if probe is None or probe_done.is_set():
                if result.get('ok'):
                    last_ok = time.monotonic()
                probe_done.clear()
                result.clear()
                probe = threading.Thread(target=ping, daemon=True)
                probe.start()
            if time.monotonic() - last_ok > 120:
                if api.WaitForSingleObject(quit_event, 0) == 0:
                    return
                _log('桌面管理连续 2 分钟无响应，恢复桌面进程')
                if not api.TerminateProcess(parent, 1):
                    _log('无法结束无响应桌面进程，停止自动恢复')
                    return
                if api.WaitForSingleObject(parent, 10000) != 0:
                    return
                break
            if api.WaitForSingleObject(quit_event, 10000) == 0:
                return
        # Quit may be signalled just as the parent exits.
        if api.WaitForSingleObject(quit_event, 0) == 0:
            return
        record = Path(data_path('guardian_restarts.json'))
        try:
            attempts = json.loads(record.read_text(encoding='utf-8')).get('attempts', [])
            attempts = [v for v in attempts if type(v) in (int, float) and time.time() - v < 600]
        except (OSError, ValueError, TypeError, AttributeError):
            attempts = []
        if len(attempts) >= 5:
            _log('10 分钟内恢复达到 5 次，停止重启并保留故障日志')
            return
        attempts.append(time.time())
        atomic_write_json(record, {'attempts': attempts}, indent=2)
        if api.WaitForSingleObject(quit_event, 30000) == 0:
            return
        _launch(['--autostart', '--restart', '--recover-desktop'])
        _log('已重新启动桌面管理程序，保留机器人运行意图')
    except Exception:
        _log('桌面自动恢复失败，请重新打开 WRelay')
    finally:
        api.CloseHandle(parent)
        api.CloseHandle(quit_event)


def guardian_main():
    index = sys.argv.index('--guardian')
    pid, name = int(sys.argv[index + 1]), sys.argv[index + 2]
    if not name.startswith('Local\\WRelay-' + EDITION + '-quit-'):
        raise ValueError('Invalid guardian event')
    supervise(pid, name)
