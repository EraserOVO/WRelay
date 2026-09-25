# -*- coding: utf-8 -*-
"""Windows 桌面入口：在原生窗口中运行现有本地管理面板。"""

import ctypes
import importlib
import os
import sys
import threading
import time
import traceback
import webbrowser


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

WINDOW_TITLE = "WRelay 桌面端"
MUTEX_NAME = r"Local\WRelayDesktopApp"
ICON_PATH = os.path.join(BASE_DIR, "wrelay_panel.ico")


def _message(message):
    ctypes.windll.user32.MessageBoxW(None, message, "WRelay", 0x10)


def _log_error():
    try:
        log_dir = os.path.join(PROJECT_ROOT, "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "desktop.log"), "a", encoding="utf-8") as log:
            log.write("%s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
    except OSError:
        pass


def _focus_existing_window():
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE：也能重新显示隐藏的窗口。
        user32.SetForegroundWindow(hwnd)


def _single_instance_handle():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    restarting = "--restart" in sys.argv
    for _ in range(40 if restarting else 1):
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            raise OSError("无法创建桌面程序单实例锁")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            return handle
        kernel32.CloseHandle(handle)
        if not restarting:
            _focus_existing_window()
            return None
        time.sleep(0.25)
    raise RuntimeError("旧桌面窗口尚未退出，无法完成重启")


class DesktopApi:
    """只向本地页面暴露窗口操作与安全的外部链接打开能力。"""

    def __init__(self):
        self._window = None
        self._maximized = False

    def minimize(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()

    def hide(self):
        self._window.hide()

    @staticmethod
    def open_external(url):
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return False
        return bool(webbrowser.open(url))


def main():
    try:
        mutex = _single_instance_handle()
    except Exception as exc:
        _log_error()
        _message("桌面程序启动失败：%s" % exc)
        return
    if mutex is None:
        return

    tray = None
    try:
        try:
            import pystray
            import webview
            from PIL import Image
        except ImportError as exc:
            _message("桌面组件未安装：%s\n请重新运行 install.bat 或安装最新版 WRelay。" % exc)
            return

        panel = importlib.import_module("管理面板")
        if panel.DESKTOP_FLAG not in sys.argv:
            sys.argv.append(panel.DESKTOP_FLAG)
        autostart = panel.AUTOSTART_FLAG in sys.argv
        server_thread = threading.Thread(target=panel.main, name="wrelay-panel", daemon=True)
        server_thread.start()
        for _ in range(60):
            if panel.panel_alive():
                break
            if not server_thread.is_alive():
                break
            time.sleep(0.25)
        else:
            _message("管理服务启动超时。请查看 data/logs/panel.log。")
            return
        if not panel.panel_alive():
            _message("管理服务未能启动。请检查端口 18090 和 data/logs/panel.log。")
            return

        api = DesktopApi()
        window = webview.create_window(
            WINDOW_TITLE,
            "http://%s:%d/" % (panel.PANEL_HOST, panel.PANEL_PORT),
            width=1360,
            height=880,
            min_size=(960, 640),
            hidden=autostart,
            frameless=True,
            easy_drag=False,
            background_color="#1b2434",
            js_api=api,
        )
        api._window = window
        exiting = False

        def show_window(icon=None, item=None):
            window.show()
            window.restore()

        def quit_desktop(icon=None, item=None):
            nonlocal exiting
            exiting = True
            if tray is not None:
                tray.stop()
            window.destroy()

        def on_closing():
            if exiting:
                return True
            window.hide()
            return False

        window.events.closing += on_closing
        window.events.maximized += lambda: setattr(api, "_maximized", True)
        window.events.restored += lambda: setattr(api, "_maximized", False)

        icon_image = Image.open(ICON_PATH).convert("RGBA")
        tray = pystray.Icon(
            "WRelay",
            icon_image,
            "WRelay",
            pystray.Menu(
                pystray.MenuItem("打开 WRelay", show_window, default=True),
                pystray.MenuItem("退出管理程序（机器人继续运行）", quit_desktop),
            ),
        )
        panel.DESKTOP_RESTART_CALLBACK = quit_desktop
        tray.run_detached()
        webview.start(gui="edgechromium", icon=ICON_PATH)
    except Exception as exc:
        _log_error()
        _message("桌面窗口启动失败：%s\n请确认已安装 WebView2 Runtime。" % exc)
    finally:
        if tray is not None:
            tray.stop()
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
