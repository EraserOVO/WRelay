"""Install an exact test artifact into an isolated directory, validate, uninstall.

Never touches an existing WRelay installation or uses real credentials. Refuses
to run if the test edition already has an uninstall entry on this machine.
"""
import argparse
import base64
import ctypes
import json
from multiprocessing.connection import Client
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_compiled import audit
from panel.local_bridge import compiled_pipe_address


AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_run_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def delete_autostart_run_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def desktop_smoke(folder, base, edition="test"):
    """Exercise the actual hidden desktop, autostart and restart without accounts."""
    for port in (18082,):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Desktop smoke test needs free port %s" % port)
    value_name = "WRelay" if edition == "release" else "WRelay Test"
    legacy_startup_names = (
        ("WRelay.lnk", "WRelayAutostart.vbs") if edition == "release"
        else ("WRelay Test.lnk", "WRelayTestAutostart.vbs")
    )
    window_title = "WRelay" if edition == "release" else "WRelay 测试版"
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    legacy_startup_paths = [startup_dir / name for name in legacy_startup_names]
    if autostart_run_value(value_name) is not None or any(path.exists() for path in legacy_startup_paths):
        raise RuntimeError("Existing test autostart must not be overwritten")
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]

    def windows():
        owned = []
        def visit(hwnd, _):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value != window_title:
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    size = ctypes.c_ulong(32768)
                    path = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                        if Path(path.value).resolve() == (folder / "WRelay.exe").resolve():
                            owned.append((hwnd, pid.value))
                finally:
                    kernel32.CloseHandle(handle)
            return True
        user32.EnumWindows(callback_type(visit), 0)
        return owned

    def process_running(pid):
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)

    def test_desktop_process(pid):
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            size = ctypes.c_ulong(32768)
            path = ctypes.create_unicode_buffer(size.value)
            return bool(kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))) and (
                Path(path.value).resolve() == (folder / "WRelay.exe").resolve())
        finally:
            kernel32.CloseHandle(handle)

    def request(path, payload=None, timeout=5):
        data = json.dumps(payload).encode() if payload is not None else b""
        with Client(compiled_pipe_address(folder), family="AF_PIPE", authkey=token.encode()) as connection:
            connection.send({"method": "POST" if payload is not None else "GET", "path": path,
                             "headers": {"content-type": "application/json"},
                             "body": base64.b64encode(data).decode("ascii")})
            if not connection.poll(timeout):
                raise TimeoutError("Compiled desktop request timed out: " + path)
            result = connection.recv()
        return json.loads(base64.b64decode(result["body"]))

    def ready(old_pid=None):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            owned = windows()
            if owned and (old_pid is None or all(pid != old_pid for _, pid in owned)):
                try:
                    request("/api/status")
                    return owned[0][1]
                except (OSError, ValueError, TimeoutError):
                    pass
            time.sleep(0.3)
        raise RuntimeError("Compiled desktop did not become ready")

    token = json.loads((folder / "data" / "panel_auth.json").read_text(encoding="utf-8"))["token"]
    process = subprocess.Popen([str(folder / "WRelay.exe"), "--autostart"], cwd=base,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    desktop_pids = {process.pid}
    try:
        pid = ready()
        desktop_pids.add(pid)
        request("/api/onboarding")
        assert (folder / "panel/web/index.html").stat().st_size > 1000
        assert (folder / "panel/web/local-api.js").stat().st_size > 1000
        assert not request("/api/status")["running"]
        autostart = request("/api/autostart/set", {"enabled": True})
        run_value = autostart_run_value(value_name)
        assert autostart["ok"] and autostart["enabled"] and run_value
        assert str(folder / "WRelay.exe") in run_value and run_value.endswith("--autostart")
        assert request("/api/autostart/set", {"enabled": False})["ok"]
        assert autostart_run_value(value_name) is None
        assert not any(path.exists() for path in legacy_startup_paths)
        assert request("/api/panel/restart", {}, timeout=25)["ok"]
        process.wait(timeout=45)
        desktop_pids.add(ready(old_pid=pid))
    finally:
        # The test starts a hidden tray session. Stop only the exact processes
        # launched from this temporary installation before checking uninstall.
        for pid in desktop_pids:
            if process_running(pid):
                if not test_desktop_process(pid):
                    raise RuntimeError("Test desktop PID changed image; refusing to stop it")
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
        deadline = time.monotonic() + 10
        while any(process_running(pid) for pid in desktop_pids) and time.monotonic() < deadline:
            time.sleep(0.2)
        if any(process_running(pid) for pid in desktop_pids):
            raise RuntimeError("Test desktop process did not exit; test installation retained")
        if windows():
            raise RuntimeError("Test desktop window remained after process exit")
        for path in legacy_startup_paths:
            path.unlink(missing_ok=True)
        delete_autostart_run_value(value_name)
    error_log = folder / "data" / "logs" / "desktop.log"
    if error_log.exists() and error_log.stat().st_size:
        raise RuntimeError("Compiled desktop reported an error: " + error_log.read_text(encoding="utf-8")[-4000:])
    print("PASS: compiled hidden desktop, native management bridge, autostart and restart")


def run(command, cwd, expected=0, timeout=120):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    try:
        result = subprocess.run([str(x) for x in command], cwd=cwd, capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"") + (exc.stderr or b"")
        raise RuntimeError("Timed out: %s\n%s" % (command[0], output.decode("utf-8", "replace")[-6000:])) from exc
    if result.returncode != expected:
        # Only subprocess test output, never existing user runtime logs/configs.
        raise RuntimeError("Command failed (%s, expected %s): %s\n%s" % (
            result.returncode, expected, command[0],
            (result.stdout + result.stderr).decode("utf-8", "replace")[-6000:]))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=Path)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--edition", choices=("test", "release"), default="test")
    parser.add_argument("--skip-desktop", action="store_true", help="Only validate install/components/uninstall; report desktop checks as skipped")
    args = parser.parse_args()
    app_id = "WRelay" if args.edition == "release" else "WRelay.CompiledTest"
    registry = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + app_id + "_is1"
    if args.edition == "release":
        startup_dir = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
        if (autostart_run_value("WRelay") is not None
                or (startup_dir / "WRelay.lnk").exists()
                or (startup_dir / "WRelayAutostart.vbs").exists()):
            raise RuntimeError("Existing WRelay autostart would be changed by the smoke installer; use a clean user profile")
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry, 0, winreg.KEY_READ | view):
                raise RuntimeError("This edition is already installed; use a clean machine for smoke testing")
        except FileNotFoundError:
            pass
    base = (ROOT / "build" / "installer-smoke").resolve()
    folder = base / ("测试 安装 " + uuid.uuid4().hex)
    base.mkdir(parents=True, exist_ok=True)
    setup_log = folder.with_suffix(".setup.log")
    uninstall_log = folder.with_suffix(".uninstall.log")
    installed = False
    passed = False
    try:
        install_args = [args.installer.resolve(), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        "/SP-", "/DIR=" + str(folder), "/LOG=" + str(setup_log)]
        if args.edition == "test":
            install_args.append("/NOICONS")
        run(install_args, ROOT)
        installed = True
        shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/WRelay/WRelay.lnk"
        if args.edition == "release":
            assert shortcut.is_file(), "release Start menu shortcut missing"
        for name in ("WRelay.exe", "WRelay.Bot.exe", "WRelay.Restart.exe", "build-info.json",
                     "panel/web/onboarding.js", "panel/web/local-api.js", "panel/WRelay.WindowChrome.dll", ".env.prod"):
            assert (folder / name).is_file(), name
        audit(folder, installed=True)
        # Run from outside the installation; the launcher resolves its own root.
        info = run([folder / "WRelay.exe", "--build-info"], base)
        assert json.loads(info.stdout)["nuitka"] == "4.2.2"
        run([folder / "WRelay.exe", "--check-components"], base)
        run([folder / "WRelay.Bot.exe"], base, expected=2)
        if args.skip_desktop:
            print("SKIP: desktop/autostart/restart checks (--skip-desktop)")
        else:
            desktop_smoke(folder, base, args.edition)
        # Test-source markers are only generated under this temporary installation.
        marker = folder / "data" / "smoke-preserve.txt"
        marker.write_text("preserve", encoding="ascii")
        passed = True
        print("PASS: installed source audit, relocated/Chinese paths, compiled dependencies/plugins, startup guard")
    except BaseException:
        print("Test logs retained for diagnosis: " + str(folder), file=sys.stderr)
        raise
    finally:
        if installed and not args.keep:
            uninstallers = list(folder.glob("unins*.exe"))
            if len(uninstallers) != 1:
                raise RuntimeError("Unable to locate smoke-test uninstaller: " + str(folder))
            run([uninstallers[0], "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                 "/LOG=" + str(uninstall_log)], base)
            # Inno's initial uninstaller process may return before its temporary
            # worker finishes. Confirm that worker's result before inspecting data.
            deadline = time.monotonic() + 60
            while True:
                log_text = uninstall_log.read_text(encoding="utf-8-sig", errors="replace") if uninstall_log.exists() else ""
                if "Log closed." in log_text:
                    if "Uninstallation process succeeded." not in log_text:
                        raise RuntimeError("Uninstall failed; see " + str(uninstall_log))
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Uninstaller worker did not finish; see " + str(uninstall_log))
                time.sleep(0.2)
            if passed:
                assert (folder / "data" / "smoke-preserve.txt").read_text() == "preserve"
                assert not (folder / "WRelay.exe").exists()
                if args.edition == "release":
                    assert not shortcut.exists(), "release Start menu shortcut remained after uninstall"
                print("PASS: uninstall preserves test user data")
            # Only remove our UUID directory after a successful uninstall.
            resolved = folder.resolve()
            if resolved.parent != base or not resolved.name.startswith("测试 安装 "):
                raise RuntimeError("Unsafe smoke-test cleanup path")
            if passed and resolved.exists():
                # Windows may retain an executed image briefly after uninstall.
                # Retry only this already-validated temporary test directory.
                deadline = time.monotonic() + 30
                while resolved.exists():
                    try:
                        shutil.rmtree(resolved)
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(1)
            if passed:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        setup_log.unlink(missing_ok=True)
                        uninstall_log.unlink(missing_ok=True)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)


if __name__ == "__main__":
    main()
