# -*- coding: utf-8 -*-
"""Windows 桌面入口：在原生窗口中运行现有本地管理面板。"""

import ctypes
import importlib
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
import urllib.request


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from desktop_controls import DesktopPreferences, DesktopControls, build_tray_menu

WINDOW_TITLE = "WRelay"
MUTEX_NAME = r"Local\WRelayDesktopApp"
ICON_PATH = os.path.join(BASE_DIR, "wrelay_panel.ico")
WINDOW_STATE_PATH = os.path.join(PROJECT_ROOT, "data", "desktop_window.json")
PREFERENCES_PATH = os.path.join(PROJECT_ROOT, "data", "desktop_preferences.json")
WINDOW_MIN_SIZE = (960, 640)
WINDOW_DEFAULT_SIZE = (1360, 880)


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
    user32.IsIconic.argtypes = [ctypes.c_void_p]
    user32.IsIconic.restype = ctypes.c_bool
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        # Hidden windows retain their normal/maximized state; only restore a minimized window.
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)  # SW_RESTORE / SW_SHOW
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


def _read_saved_window_state():
    try:
        with open(WINDOW_STATE_PATH, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        if not isinstance(state, dict):
            return {}
        width = int(state.get("width", WINDOW_DEFAULT_SIZE[0]))
        height = int(state.get("height", WINDOW_DEFAULT_SIZE[1]))
        if not (WINDOW_MIN_SIZE[0] <= width <= 8192 and WINDOW_MIN_SIZE[1] <= height <= 8192):
            return {}
        x = state.get("x")
        y = state.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return {}
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "maximized": bool(state.get("maximized", False)),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _window_start_options(webview):
    state = _read_saved_window_state()
    if not state:
        state = {"width": WINDOW_DEFAULT_SIZE[0], "height": WINDOW_DEFAULT_SIZE[1], "maximized": False}
        try:
            screens = list(webview.screens)
            if screens:
                screen = screens[0]
                state["width"] = int(min(state["width"], max(WINDOW_MIN_SIZE[0], screen.width - 40)))
                state["height"] = int(min(state["height"], max(WINDOW_MIN_SIZE[1], screen.height - 80)))
                state["x"] = int(screen.x + max(0, (screen.width - state["width"]) // 2))
                state["y"] = int(screen.y + max(0, (screen.height - state["height"]) // 2))
        except Exception:
            pass
        return state

    try:
        screens = list(webview.screens)
        x, y, width, height = state["x"], state["y"], state["width"], state["height"]
        titlebar_height = 48
        visible_titlebar_width = min(160, width)
        still_visible = any(
            min(x + width, screen.x + screen.width) - max(x, screen.x) >= visible_titlebar_width
            and max(y, screen.y) < min(y + titlebar_height, screen.y + screen.height)
            for screen in screens
        )
        if still_visible:
            return state
        if screens:
            screen = screens[0]
            state["x"] = int(screen.x + max(0, (screen.width - width) // 2))
            state["y"] = int(screen.y + max(0, (screen.height - height) // 2))
            return state
    except Exception:
        pass

    return {
        "width": WINDOW_DEFAULT_SIZE[0],
        "height": WINDOW_DEFAULT_SIZE[1],
        "maximized": False,
    }


def _write_window_state(window, maximized, fallback_state=None):
    state = _read_saved_window_state()
    if not maximized:
        try:
            state.update({
                "x": int(window.x),
                "y": int(window.y),
                "width": int(window.width),
                "height": int(window.height),
            })
        except (AttributeError, TypeError, ValueError):
            return
    elif not state and fallback_state:
        state.update({key: fallback_state[key] for key in ("x", "y", "width", "height") if key in fallback_state})
    state["maximized"] = bool(maximized)
    if not all(key in state for key in ("x", "y", "width", "height")):
        return

    try:
        os.makedirs(os.path.dirname(WINDOW_STATE_PATH), exist_ok=True)
        temporary_path = WINDOW_STATE_PATH + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False)
        os.replace(temporary_path, WINDOW_STATE_PATH)
    except OSError:
        pass


def _is_window_maximized(window, fallback=False):
    try:
        user32 = ctypes.windll.user32
        user32.IsZoomed.argtypes = [ctypes.c_void_p]
        user32.IsZoomed.restype = ctypes.c_bool
        return bool(user32.IsZoomed(ctypes.c_void_p(window.native.Handle.ToInt64())))
    except Exception:
        return bool(fallback)


def _native_choice(window, kind, form_factory=None):
    """Run an owned WinForms dialog on the UI thread, outside the closing event."""
    from System import Action
    from System.Drawing import Size, Point, Color
    from System.Windows.Forms import (Form, Label, Button, FormBorderStyle,
                                     FormStartPosition, DialogResult)
    result = ["cancel"]
    def display():
        dialog = (form_factory or Form)()
        dialog.Text = "停止机器人并退出 WRelay？" if kind == "stop_exit" else "关闭 WRelay"
        dialog.ClientSize = Size(500, 160)
        dialog.FormBorderStyle = FormBorderStyle.FixedDialog
        dialog.StartPosition = FormStartPosition.CenterParent
        dialog.MaximizeBox = False
        dialog.MinimizeBox = False
        dialog.ShowInTaskbar = False
        message = Label()
        message.Text = ("机器人将停止运行，所有转发服务都会中断。" if kind == "stop_exit"
                        else "请选择关闭方式。退出桌面端不会停止机器人。")
        message.Location = Point(20, 20)
        message.Size = Size(460, 55)
        dialog.Controls.Add(message)
        choices = [("取消", "cancel"), ("停止并退出", "stop_exit")] if kind == "stop_exit" else [
            ("收起到托盘", "tray"), ("退出桌面端", "exit"), ("取消", "cancel")]
        for index, (label, value) in enumerate(choices):
            button = Button()
            button.Text = label
            button.Size = Size(140, 36)
            button.Location = Point(480 - 150 * (len(choices) - index), 105)
            def clicked(sender, event, choice=value):
                result[0] = choice
                dialog.Close()
            button.Click += clicked
            if value == "cancel":
                button.DialogResult = DialogResult.Cancel
                dialog.CancelButton = button
                dialog.AcceptButton = button  # Enter defaults to the safe action.
            if value == "stop_exit":
                button.ForeColor = Color.Firebrick
            dialog.Controls.Add(button)
        try:
            dialog.ShowDialog(window.native)
        finally:
            dialog.Dispose()
    window.native.Invoke(Action(display))
    return result[0]


def _panel_action(panel, action):
    if action not in ("start", "stop", "restart"):
        raise ValueError("未知机器人操作")
    request = urllib.request.Request(
        "http://%s:%d/api/bot/%s" % (panel.PANEL_HOST, panel.PANEL_PORT, action),
        data=b"", method="POST", headers={"X-WRelay-Token": panel.PANEL_TOKEN},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


class DesktopApi:
    """本地页面的外链及窗口主题桥接；窗口操作由 Windows 消息处理。"""

    def __init__(self):
        self._preferences = DesktopPreferences(PREFERENCES_PATH)
        self._maximized = False
        self._chrome = None

    def get_desktop_preferences(self):
        return self._preferences.get()

    def set_close_behavior(self, value):
        try:
            return {"ok": True, **self._preferences.set_close_behavior(value)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "msg": "无法保存关闭窗口偏好：%s" % exc}

    def set_window_theme(self, theme):
        if theme not in ("light", "dark"):
            return False
        if self._chrome is not None:
            self._chrome.SetLight(theme == "light")
        return True

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
    controls = None
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
        window_options = _window_start_options(webview)
        window = webview.create_window(
            WINDOW_TITLE,
            "http://%s:%d/?desktop=1" % (panel.PANEL_HOST, panel.PANEL_PORT),
            width=window_options["width"],
            height=window_options["height"],
            x=window_options.get("x"),
            y=window_options.get("y"),
            min_size=WINDOW_MIN_SIZE,
            hidden=autostart,
            resizable=True,
            maximized=window_options["maximized"],
            frameless=False,
            easy_drag=False,
            background_color="#1b2434",
            js_api=api,
        )
        api._maximized = window_options["maximized"]

        def attach_window_chrome():
            try:
                from windows_titlebar import attach_titlebar
                api._chrome = attach_titlebar(window.native, ICON_PATH)
            except Exception:
                # Keep the normal Windows frame if the custom caption cannot load.
                _log_error()

        window.events.before_show += attach_window_chrome
        exiting = False
        save_timer = None
        save_lock = threading.Lock()

        def save_window_state():
            with save_lock:
                maximized = _is_window_maximized(window, api._maximized)
                api._maximized = maximized
                _write_window_state(window, maximized, window_options)

        def schedule_window_state_save(*_args):
            nonlocal save_timer
            if save_timer is not None:
                save_timer.cancel()
            save_timer = threading.Timer(0.5, save_window_state)
            save_timer.daemon = True
            save_timer.start()

        def on_maximized(*_args):
            api._maximized = True
            schedule_window_state_save()

        def on_restored(*_args):
            api._maximized = _is_window_maximized(window, api._maximized)
            schedule_window_state_save()

        def show_window(page=None):
            window.show()
            _focus_existing_window()
            if page in ("overview", "settings"):
                window.evaluate_js("setActivePage(%s)" % json.dumps(page))

        def quit_desktop(icon=None, item=None):
            nonlocal exiting
            if exiting:
                return
            exiting = True
            if controls is not None:
                controls.finished.set()
            if save_timer is not None:
                save_timer.cancel()
            save_window_state()
            if tray is not None:
                tray.stop()
            window.destroy()

        def on_closing():
            save_window_state()
            if exiting:
                return True
            controls.dispatch(controls.close_window)
            return False

        window.events.closing += on_closing
        window.events.maximized += on_maximized
        window.events.restored += on_restored
        window.events.moved += schedule_window_state_save
        window.events.resized += schedule_window_state_save

        controls = DesktopControls(
            api._preferences, panel.bot_status, panel._bot_health_cached,
            lambda action: _panel_action(panel, action), show_window, window.hide,
            quit_desktop, lambda kind: _native_choice(window, kind), _message,
        )
        controls.running = bool(panel.bot_status().get("running"))
        controls.tooltip = "WRelay · 机器人运行中" if controls.running else "WRelay · 机器人已停止"
        icon_image = Image.open(ICON_PATH).convert("RGBA")
        tray = pystray.Icon("WRelay", icon_image, controls.tooltip, build_tray_menu(pystray, controls))
        controls.tray = tray
        panel.DESKTOP_RESTART_CALLBACK = quit_desktop
        tray.run_detached()
        threading.Thread(target=controls.poll, name="wrelay-tray-status", daemon=True).start()
        webview.start(gui="edgechromium", icon=ICON_PATH)
    except Exception as exc:
        _log_error()
        _message("桌面窗口启动失败：%s\n请确认已安装 WebView2 Runtime。" % exc)
    finally:
        if controls is not None:
            controls.finished.set()
        if tray is not None:
            tray.stop()
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
