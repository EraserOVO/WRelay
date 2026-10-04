"""Desktop preferences and tray actions; no forwarding configuration is changed."""
import json
import os
import threading

CLOSE_BEHAVIORS = ("tray", "exit", "ask")


class DesktopPreferences:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as stream:
                value = json.load(stream).get("close_behavior")
        except (OSError, ValueError, AttributeError):
            value = None
        self.close_behavior = value if value in CLOSE_BEHAVIORS else "tray"

    def get(self):
        with self.lock:
            return {"close_behavior": self.close_behavior}

    def set_close_behavior(self, value):
        if value not in CLOSE_BEHAVIORS:
            raise ValueError("未知的关闭窗口行为")
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + ".tmp"
            try:
                with open(temporary, "w", encoding="utf-8") as stream:
                    json.dump({"close_behavior": value}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
            self.close_behavior = value
        return self.get()


class DesktopControls:
    def __init__(self, preferences, status, health, bot_action, show, hide, quit_app, choose, error):
        self.preferences = preferences
        self.status = status
        self.health = health
        self.bot_action = bot_action
        self.show = show
        self.hide = hide
        self.quit_app = quit_app
        self.choose = choose
        self.error = error
        self.running = False
        self.tooltip = "WRelay · 机器人已停止"
        self.busy = threading.Lock()
        self.closing = threading.Lock()
        self.finished = threading.Event()
        self.tray = None

    def refresh(self):
        running = bool(self.status().get("running"))
        title = "WRelay · 机器人运行中" if running else "WRelay · 机器人已停止"
        if running:
            health = self.health()
            if not isinstance(health, dict) or health.get("qq") is False or health.get("discord") is False:
                title = "WRelay · 连接异常"
        changed = (running, title) != (self.running, self.tooltip)
        self.running, self.tooltip = running, title
        if self.tray is not None and changed:
            self.tray.title = title
            self.tray.update_menu()

    def poll(self):
        while not self.finished.is_set():
            try:
                self.refresh()
            except Exception:
                self.tooltip = "WRelay · 连接异常"
                if self.tray is not None:
                    self.tray.title = self.tooltip
            self.finished.wait(3)

    def dispatch(self, action):
        threading.Thread(target=action, daemon=True).start()

    def quit(self):
        self.finished.set()
        self.quit_app()

    def close_window(self):
        if not self.closing.acquire(False):
            return
        try:
            behavior = self.preferences.get()["close_behavior"]
            if behavior == "ask":
                behavior = self.choose("close")
            if behavior == "tray":
                self.hide()
            elif behavior == "exit":
                self.quit()
        except Exception as exc:
            self.error("无法关闭窗口：%s" % exc)
        finally:
            self.closing.release()

    def run_action(self, action):
        if self.finished.is_set() or not self.busy.acquire(False):
            return
        try:
            if self.tray is not None:
                self.tray.update_menu()
            if action == "stop_exit":
                # Always confirm, including when a stale menu says the bot is stopped.
                if self.choose("stop_exit") != "stop_exit":
                    return
                result = self.bot_action("stop")
                if not result.get("ok") or self.status().get("running"):
                    self.error(result.get("msg") or "机器人尚未停止，桌面端保持打开。")
                    return
                self.quit()
            else:
                result = self.bot_action(action)
                if not result.get("ok"):
                    self.error(result.get("msg") or "机器人操作失败")
            if not self.finished.is_set():
                self.refresh()
        except Exception as exc:
            self.error("操作失败：%s" % exc)
        finally:
            self.busy.release()
            if self.tray is not None and not self.finished.is_set():
                self.tray.update_menu()


def build_tray_menu(pystray, controls):
    item, menu = pystray.MenuItem, pystray.Menu
    def action(name):
        return lambda icon, entry: controls.dispatch(lambda: controls.run_action(name))
    def navigate(page=None):
        return lambda icon, entry: controls.dispatch(lambda: controls.show(page))
    available = lambda entry: not controls.busy.locked() and not controls.finished.is_set()
    return menu(
        item(lambda entry: "WRelay · 机器人运行中" if controls.running else "WRelay · 机器人已停止", None, enabled=False),
        menu.SEPARATOR,
        item("打开 WRelay", navigate(), default=True),
        item("总览", navigate("overview")),
        item("设置", navigate("settings")),
        menu.SEPARATOR,
        item("启动机器人", action("start"), visible=lambda entry: not controls.running, enabled=available),
        item("停止机器人", action("stop"), visible=lambda entry: controls.running, enabled=available),
        item("重启机器人", action("restart"), visible=lambda entry: controls.running, enabled=available),
        menu.SEPARATOR,
        item(lambda entry: "退出桌面端（机器人继续运行）" if controls.running else "退出桌面端",
             lambda icon, entry: controls.dispatch(controls.quit), enabled=available),
        item(lambda entry: "停止机器人并退出…" if controls.running else "退出桌面端并保持机器人停止",
             action("stop_exit"), enabled=available),
    )
