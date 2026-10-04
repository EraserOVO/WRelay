"""Isolated desktop preference/tray tests. Never starts or stops a real bot."""
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))
from desktop_controls import DesktopControls, DesktopPreferences, build_tray_menu


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "preferences.json")
        self.preferences = DesktopPreferences(self.path)
        self.state = {"running": True}
        self.action = Mock(side_effect=self.bot_action)
        self.show, self.hide, self.quit, self.error = Mock(), Mock(), Mock(), Mock()
        self.choose = Mock(return_value="cancel")
        self.controls = DesktopControls(self.preferences, lambda: self.state,
            lambda: {"discord": True, "qq": True}, self.action,
            self.show, self.hide, self.quit, self.choose, self.error)

    def bot_action(self, action):
        self.state["running"] = action != "stop"
        return {"ok": True}

    def test_preferences_default_persistence_and_validation(self):
        self.assertEqual(self.preferences.get()["close_behavior"], "tray")
        for value in ("exit", "ask", "tray"):
            self.preferences.set_close_behavior(value)
            self.assertEqual(DesktopPreferences(self.path).get()["close_behavior"], value)
        with self.assertRaises(ValueError):
            self.preferences.set_close_behavior("stop_bot")
        Path(self.path).write_text("invalid json", encoding="utf-8")
        self.assertEqual(DesktopPreferences(self.path).get()["close_behavior"], "tray")

    def test_failed_save_keeps_previous_value(self):
        with patch("desktop_controls.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.preferences.set_close_behavior("exit")
        self.assertEqual(self.preferences.get()["close_behavior"], "tray")
        self.assertFalse(Path(self.path + ".tmp").exists())

    def test_default_close_hides_without_bot_action(self):
        self.controls.close_window()
        self.hide.assert_called_once()
        self.action.assert_not_called()
        self.quit.assert_not_called()

    def test_exit_close_does_not_stop_bot(self):
        self.preferences.set_close_behavior("exit")
        self.controls.close_window()
        self.quit.assert_called_once()
        self.action.assert_not_called()
        self.assertTrue(self.state["running"])

    def test_ask_choices_do_not_change_preference_or_stop_bot(self):
        self.preferences.set_close_behavior("ask")
        for value in ("cancel", "tray", "exit"):
            self.choose.return_value = value
            self.controls.close_window()
        self.hide.assert_called_once()
        self.quit.assert_called_once()
        self.action.assert_not_called()
        self.assertEqual(self.preferences.get()["close_behavior"], "ask")

    def test_repeated_close_does_not_open_second_dialog(self):
        self.controls.closing.acquire()
        self.controls.close_window()
        self.controls.closing.release()
        self.choose.assert_not_called()
        self.hide.assert_not_called()

    def test_cancel_stop_exit_does_nothing(self):
        self.controls.run_action("stop_exit")
        self.choose.assert_called_once_with("stop_exit")
        self.action.assert_not_called()
        self.quit.assert_not_called()

    def test_stop_exit_requires_successful_stop(self):
        self.choose.return_value = "stop_exit"
        self.controls.run_action("stop_exit")
        self.action.assert_called_once_with("stop")
        self.assertFalse(self.state["running"])
        self.quit.assert_called_once()

    def test_failed_stop_keeps_desktop_open(self):
        self.choose.return_value = "stop_exit"
        self.action.side_effect = lambda action: {"ok": False, "msg": "stop failed"}
        self.controls.run_action("stop_exit")
        self.quit.assert_not_called()
        self.error.assert_called_once_with("stop failed")

    def test_stopped_exit_clears_running_intent(self):
        self.state["running"] = False
        self.choose.return_value = "stop_exit"
        self.controls.run_action("stop_exit")
        self.action.assert_called_once_with("stop")
        self.quit.assert_called_once()
        self.choose.assert_called_once_with("stop_exit")

    def test_bot_actions_and_tooltip(self):
        for action in ("stop", "start", "restart"):
            self.controls.run_action(action)
            self.assertEqual(self.controls.running, action != "stop")
        self.assertEqual(self.controls.tooltip, "WRelay · 机器人运行中")
        self.controls.health = lambda: None
        self.controls.refresh()
        self.assertEqual(self.controls.tooltip, "WRelay · 连接异常")
        self.state["running"] = False
        self.controls.refresh()
        self.assertEqual(self.controls.tooltip, "WRelay · 机器人已停止")

    def test_busy_actions_are_not_repeated(self):
        self.controls.busy.acquire()
        self.controls.run_action("restart")
        self.controls.busy.release()
        self.action.assert_not_called()

    def test_native_menu_model_and_navigation(self):
        import pystray
        self.controls.dispatch = lambda action: action()
        menu = build_tray_menu(pystray, self.controls)
        for running in (True, False):
            self.controls.running = running
            items = {item.text: item for item in menu.items if item.visible}
            self.assertIn("停止机器人" if running else "启动机器人", items)
            self.assertEqual("重启机器人" in items, running)
            self.assertTrue(items["打开 WRelay"].default)
            self.assertFalse(next(iter(items.values())).enabled)
        items["总览"](None)
        items["设置"](None)
        self.assertEqual(self.show.call_args_list[0].args, ("overview",))
        self.assertEqual(self.show.call_args_list[1].args, ("settings",))

    def test_autostart_desktop_restart_does_not_start_stopped_bot(self):
        # Execute only main() with all services replaced, never import config/.env.
        tree = ast.parse((ROOT / "panel" / "管理面板.pyw").read_text(encoding="utf-8-sig"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        thread = Mock()
        env = {"sys": SimpleNamespace(argv=["panel", "--autostart", "--restart", "--desktop"]),
            "AUTOSTART_FLAG": "--autostart", "RESTART_FLAG": "--restart", "DESKTOP_FLAG": "--desktop",
            "_log": Mock(), "migrate_forwarding_groups": Mock(), "read_pid": lambda: 0,
            "_ensure_bot_running": Mock(), "bot_status": lambda: {"running": False},
            "threading": SimpleNamespace(Thread=thread), "_watchdog_loop": Mock(),
            "_want_running": False}
        exec(compile(ast.Module(body=[main], type_ignores=[]), "<isolated main>", "exec"), env)
        env["main"]()
        self.assertEqual(thread.call_count, 1)
        self.assertIs(thread.call_args.kwargs["target"], env["_watchdog_loop"])
        env["_ensure_bot_running"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
