"""Desktop preferences and tray actions; no forwarding configuration is changed."""
import json
import os
import re
import threading

CLOSE_BEHAVIORS = ("tray", "exit", "ask")


class DesktopPreferences:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        value = data.get("close_behavior")
        self.close_behavior = value if value in CLOSE_BEHAVIORS else "tray"
        bot = data.get("last_qq_bot", "")
        self.last_qq_bot = bot if self._valid_bot(bot) else ""
        theme = data.get("window_theme")
        self.window_theme = theme if theme in ("light", "dark") else None

    @staticmethod
    def _valid_bot(value):
        return isinstance(value, str) and (not value or bool(re.fullmatch(
            r"(?:[1-9][0-9]{0,19}|personal:[1-9][0-9]{4,19})", value)))

    def get(self):
        with self.lock:
            return {"close_behavior": self.close_behavior, "last_qq_bot": self.last_qq_bot}

    def _save(self, behavior, bot):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = self.path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                data = {"close_behavior": behavior, "last_qq_bot": bot}
                if self.window_theme is not None:
                    data["window_theme"] = self.window_theme
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        self.close_behavior = behavior
        self.last_qq_bot = bot

    def set_close_behavior(self, value):
        if value not in CLOSE_BEHAVIORS:
            raise ValueError("未知的关闭窗口行为")
        with self.lock:
            self._save(value, self.last_qq_bot)
        return self.get()

    def set_last_qq_bot(self, value):
        if not self._valid_bot(value):
            raise ValueError("机器人账号标识无效")
        with self.lock:
            self._save(self.close_behavior, value)
        return self.get()

    def get_window_theme(self):
        with self.lock:
            return self.window_theme

    def set_window_theme(self, value):
        if value not in ("light", "dark"):
            raise ValueError("未知的窗口主题")
        with self.lock:
            if self.window_theme == value:
                return
            previous = self.window_theme
            self.window_theme = value
            try:
                self._save(self.close_behavior, self.last_qq_bot)
            except Exception:
                self.window_theme = previous
                raise


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


class CustomTrayIcon:
    """Windows notification icon with a WRelay-styled floating action menu."""

    WIDTH = 312
    HEIGHT = 338

    def __init__(self, icon_path, controls, theme_getter):
        self.icon_path = icon_path
        self.controls = controls
        self.theme_getter = theme_getter
        self._host = None
        self._notify_icon = None
        self._icon_image = None
        self._popup = None
        self._status_label = None
        self._title_label = None
        self._buttons = {}
        self._separators = []
        self._full_popup_height = self.HEIGHT
        self._last_running = None
        self._title = controls.tooltip
        self._lock = threading.Lock()
        self._stopped = False
        self._colors = {}
        self._applied_theme = None

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        with self._lock:
            self._title = str(value or "WRelay")[:63]
        self._invoke(self._apply_shell_title)

    def initialize(self, host):
        """Create the icon on pywebview's WinForms UI thread."""
        if self._stopped or self._notify_icon is not None:
            return
        from System.Drawing import Icon
        from System.Windows.Forms import NotifyIcon

        self._host = host
        self._icon_image = Icon(self.icon_path)
        notify_icon = NotifyIcon()
        notify_icon.Icon = self._icon_image
        notify_icon.Text = self._title
        notify_icon.MouseClick += self._on_mouse_click
        notify_icon.MouseDoubleClick += self._on_mouse_double_click
        self._notify_icon = notify_icon
        notify_icon.Visible = True

    def _invoke(self, callback, wait=False):
        host = self._host
        if host is None or self._stopped:
            return
        try:
            from System import Action
            if host.IsDisposed or not host.IsHandleCreated:
                return
            if host.InvokeRequired:
                if wait:
                    host.Invoke(Action(callback))
                else:
                    host.BeginInvoke(Action(callback))
            else:
                callback()
        except Exception:
            # The main window can already be closing during desktop shutdown.
            pass

    def _apply_shell_title(self):
        if self._notify_icon is not None:
            self._notify_icon.Text = self._title

    def update_menu(self):
        self._invoke(self._refresh_popup)

    def stop(self):
        if self._stopped:
            return
        if self._host is None:
            self._stopped = True
            return
        if self._host.IsDisposed or not self._host.IsHandleCreated:
            self._dispose()
        else:
            self._invoke(self._dispose, wait=True)
        self._stopped = True

    def _dispose(self):
        self._hide_popup()
        if self._popup is not None and not self._popup.IsDisposed:
            self._popup.Dispose()
            self._popup = None
        if self._notify_icon is not None:
            self._notify_icon.Visible = False
            self._notify_icon.Dispose()
            self._notify_icon = None
        if self._icon_image is not None:
            self._icon_image.Dispose()
            self._icon_image = None

    def _on_mouse_click(self, _sender, event):
        from System.Windows.Forms import MouseButtons
        if event.Button == MouseButtons.Right:
            if self._popup is not None and self._popup.Visible:
                self._hide_popup()
            else:
                self._show_popup()

    def _on_mouse_double_click(self, _sender, event):
        from System.Windows.Forms import MouseButtons
        if event.Button == MouseButtons.Left:
            self._hide_popup()
            self.controls.dispatch(lambda: self.controls.show())

    def _show_popup(self):
        if self._stopped:
            return
        if self._popup is None or self._popup.IsDisposed:
            self._create_popup()
        self._refresh_popup()
        self._position_popup()
        self._popup.Show()
        self._popup.Activate()

    def _hide_popup(self):
        if self._popup is not None and not self._popup.IsDisposed:
            self._popup.Hide()

    def _position_popup(self):
        from System.Drawing import Point
        from System.Windows.Forms import Cursor, Screen
        cursor = Cursor.Position
        area = Screen.FromPoint(cursor).WorkingArea
        width, height = self._popup.Width, self._popup.Height
        x = max(area.Left + 4, min(cursor.X - width + 18, area.Right - width - 4))
        if cursor.Y > area.Top + area.Height // 2:
            y = cursor.Y - height - 8
        else:
            y = cursor.Y + 8
        y = max(area.Top + 4, min(y, area.Bottom - height - 4))
        self._popup.Location = Point(x, y)

    def _create_popup(self):
        from System.Drawing import ContentAlignment, Font, FontStyle, Point, Size
        from System.Windows.Forms import (
            Button, Cursors, FlatStyle, Form, FormBorderStyle,
            FormStartPosition, Label, Padding, Panel, Keys,
        )

        form = Form()
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        form.KeyPreview = True
        form.ClientSize = Size(self.WIDTH, self.HEIGHT)
        form.Padding = Padding(0)
        self._full_popup_height = form.ClientSize.Height

        self._title_label = Label()
        self._title_label.Text = "WRelay"
        self._title_label.Location = Point(14, 10)
        self._title_label.Size = Size(110, 28)
        self._title_label.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self._title_label.TextAlign = ContentAlignment.MiddleLeft
        form.Controls.Add(self._title_label)

        self._status_label = Label()
        self._status_label.Location = Point(126, 10)
        self._status_label.Size = Size(self.WIDTH - 140, 28)
        self._status_label.Font = Font("Segoe UI", 9, FontStyle.Regular)
        self._status_label.TextAlign = ContentAlignment.MiddleRight
        self._status_label.AutoEllipsis = True
        form.Controls.Add(self._status_label)

        def add_separator(y):
            line = Panel()
            line.Location = Point(10, y)
            line.Size = Size(self.WIDTH - 20, 1)
            self._separators.append(line)
            form.Controls.Add(line)

        def add_button(key, label, y, callback, style="normal"):
            button = Button()
            button.Text = label
            button.AccessibleName = label
            button.Location = Point(10, y)
            button.Size = Size(self.WIDTH - 20, 32)
            button.Region = self._rounded_region(button.Width, button.Height, 8)
            button.FlatStyle = FlatStyle.Flat
            button.FlatAppearance.BorderSize = 0
            button.TextAlign = ContentAlignment.MiddleLeft
            button.Padding = Padding(12, 0, 12, 0)
            button.UseVisualStyleBackColor = False
            button.Cursor = Cursors.Hand
            button.Tag = style
            button.Font = Font("Segoe UI", 9, FontStyle.Bold if style == "primary" else FontStyle.Regular)
            button.Click += lambda _sender, _event, action=callback: self._choose_action(action)
            self._buttons[key] = button
            form.Controls.Add(button)
            return button

        add_button("open", "打开 WRelay", 52,
                   lambda: self.controls.dispatch(lambda: self.controls.show()), "primary")
        add_button("overview", "总览", 88,
                   lambda: self.controls.dispatch(lambda: self.controls.show("overview")))
        add_button("settings", "设置", 124,
                   lambda: self.controls.dispatch(lambda: self.controls.show("settings")))
        add_separator(164)
        add_button("start", "启动机器人", 172,
                   lambda: self.controls.dispatch(lambda: self.controls.run_action("start")))
        add_button("stop", "停止机器人", 172,
                   lambda: self.controls.dispatch(lambda: self.controls.run_action("stop")))
        add_button("restart", "重启机器人", 208,
                   lambda: self.controls.dispatch(lambda: self.controls.run_action("restart")))
        add_separator(248)
        add_button("exit", "退出桌面端", 256,
                   lambda: self.controls.dispatch(self.controls.quit))
        add_button("stop_exit", "停止机器人并退出…", 292,
                   lambda: self.controls.dispatch(lambda: self.controls.run_action("stop_exit")), "danger")
        form.AcceptButton = self._buttons["open"]
        form.Deactivate += lambda _sender, _event: self._hide_popup()
        form.KeyDown += lambda _sender, event: self._hide_popup() if event.KeyCode == Keys.Escape else None
        form.Paint += self._paint_popup_border
        self._popup = form
        self._apply_popup_theme()

    def _choose_action(self, action):
        self._hide_popup()
        if not self.controls.finished.is_set():
            action()

    def _apply_popup_theme(self):
        if self._popup is None:
            return
        from System.Drawing import ColorTranslator
        theme = "light" if self.theme_getter() == "light" else "dark"
        if theme == self._applied_theme:
            return
        light = theme == "light"
        values = ({
            "background": "#ffffff", "border": "#d7deeb", "foreground": "#26334a",
            "muted": "#637087", "hover": "#f0f3fa", "pressed": "#e5eaf4",
            "primary": "#dce5ff", "primary_hover": "#cbd8ff", "primary_foreground": "#334ac5",
            "danger": "#fff1f0", "danger_hover": "#ffe2df", "danger_foreground": "#b94c4b",
            "separator": "#e2e7f0",
        } if light else {
            "background": "#1b2536", "border": "#37445a", "foreground": "#edf2ff",
            "muted": "#aab5c7", "hover": "#29364c", "pressed": "#34435d",
            "primary": "#323d82", "primary_hover": "#3e4d9d", "primary_foreground": "#eef1ff",
            "danger": "#382c39", "danger_hover": "#4a3442", "danger_foreground": "#ff9b9f",
            "separator": "#354258",
        })
        self._colors = {key: ColorTranslator.FromHtml(value) for key, value in values.items()}
        self._popup.BackColor = self._colors["background"]
        self._popup.ForeColor = self._colors["foreground"]
        self._popup.Region = self._rounded_region(
            self._popup.ClientSize.Width, self._popup.ClientSize.Height, 12)
        self._title_label.ForeColor = self._colors["foreground"]
        self._status_label.ForeColor = self._colors["muted"]
        for separator in self._separators:
            separator.BackColor = self._colors["separator"]
        for button in self._buttons.values():
            style = str(button.Tag)
            if style == "primary":
                background, foreground, hover = "primary", "primary_foreground", "primary_hover"
            elif style == "danger":
                background, foreground, hover = "danger", "danger_foreground", "danger_hover"
            else:
                background, foreground, hover = "background", "foreground", "hover"
            button.BackColor = self._colors[background]
            button.ForeColor = self._colors[foreground]
            button.FlatAppearance.MouseOverBackColor = self._colors[hover]
            button.FlatAppearance.MouseDownBackColor = self._colors["pressed"]
        self._applied_theme = theme
        self._popup.Invalidate()

    @staticmethod
    def _rounded_region(width, height, radius):
        from System.Drawing import Rectangle, Region
        from System.Drawing.Drawing2D import GraphicsPath
        path = GraphicsPath()
        diameter = radius * 2
        path.AddArc(Rectangle(0, 0, diameter, diameter), 180, 90)
        path.AddArc(Rectangle(width - diameter, 0, diameter, diameter), 270, 90)
        path.AddArc(Rectangle(width - diameter, height - diameter, diameter, diameter), 0, 90)
        path.AddArc(Rectangle(0, height - diameter, diameter, diameter), 90, 90)
        path.CloseFigure()
        region = Region(path)
        path.Dispose()
        return region

    def _paint_popup_border(self, _sender, event):
        if not self._colors:
            return
        from System.Drawing import Pen
        from System.Drawing.Drawing2D import SmoothingMode
        event.Graphics.SmoothingMode = SmoothingMode.AntiAlias
        path = self._rounded_path(self._popup.ClientSize.Width, self._popup.ClientSize.Height, 12)
        pen = Pen(self._colors["border"], 1)
        event.Graphics.DrawPath(pen, path)
        pen.Dispose()
        path.Dispose()

    @staticmethod
    def _rounded_path(width, height, radius):
        from System.Drawing import Rectangle
        from System.Drawing.Drawing2D import GraphicsPath
        path = GraphicsPath()
        diameter = radius * 2
        path.AddArc(Rectangle(0, 0, diameter, diameter), 180, 90)
        path.AddArc(Rectangle(width - diameter - 1, 0, diameter, diameter), 270, 90)
        path.AddArc(Rectangle(width - diameter - 1, height - diameter - 1, diameter, diameter), 0, 90)
        path.AddArc(Rectangle(0, height - diameter - 1, diameter, diameter), 90, 90)
        path.CloseFigure()
        return path

    def _refresh_popup(self):
        if self._popup is None or self._popup.IsDisposed:
            return
        self._apply_popup_theme()
        self._status_label.Text = self._title.replace("WRelay · ", "")
        running = bool(self.controls.running)
        self._set_popup_layout(running)
        self._buttons["start"].Visible = not running
        self._buttons["stop"].Visible = running
        self._buttons["restart"].Visible = running
        self._buttons["exit"].Text = "退出桌面端（机器人继续运行）" if running else "退出桌面端"
        self._buttons["exit"].AccessibleName = self._buttons["exit"].Text
        self._buttons["stop_exit"].Text = "停止机器人并退出…" if running else "退出桌面端并保持机器人停止"
        self._buttons["stop_exit"].AccessibleName = self._buttons["stop_exit"].Text
        available = not self.controls.busy.locked() and not self.controls.finished.is_set()
        for key in ("start", "stop", "restart", "exit", "stop_exit"):
            self._buttons[key].Enabled = available
        self._popup.Invalidate()

    def _set_popup_layout(self, running):
        if self._popup is None or running == self._last_running:
            return
        from System.Drawing import Point, Size
        scale = self._full_popup_height / self.HEIGHT
        compact = not running
        second_separator_y = int((212 if compact else 248) * scale)
        exit_y = int((220 if compact else 256) * scale)
        stop_exit_y = int((256 if compact else 292) * scale)
        self._separators[1].Location = Point(int(10 * scale), second_separator_y)
        self._buttons["exit"].Location = Point(int(10 * scale), exit_y)
        self._buttons["stop_exit"].Location = Point(int(10 * scale), stop_exit_y)
        height = self._full_popup_height - (int(36 * scale) if compact else 0)
        self._popup.ClientSize = Size(self._popup.ClientSize.Width, height)
        self._popup.Region = self._rounded_region(
            self._popup.ClientSize.Width, self._popup.ClientSize.Height, 12)
        self._last_running = running
