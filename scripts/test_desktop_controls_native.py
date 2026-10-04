"""Hidden WinForms dialog smoke test; no WebView2, bot, service or tray is started."""
import importlib.machinery
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))
loader = importlib.machinery.SourceFileLoader("desktop_under_test", str(ROOT / "panel" / "desktop_app.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
desktop = importlib.util.module_from_spec(spec)
loader.exec_module(desktop)


def main():
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System import Action
    from System.Windows.Forms import Application, Form, Button, Timer
    from System.Drawing import Color
    owner = Form()
    owner.Text = "WRelay isolated dialog test"
    owner.Opacity = 0
    owner.ShowInTaskbar = False
    errors, results = [], []
    expected = {"label": ""}
    def hidden_form():
        form = Form()
        form.Opacity = 0
        form.ShowInTaskbar = False
        return form
    def tick(sender, event):
        for form in list(Application.OpenForms):
            if form == owner:
                continue
            try:
                assert form.Owner == owner
                buttons = [c for c in form.Controls if isinstance(c, Button)]
                cancel = next(b for b in buttons if b.Text == "取消")
                assert form.CancelButton.Equals(cancel)
                assert form.AcceptButton.Equals(cancel)
                target = next(b for b in buttons if b.Text == expected["label"])
                if expected["label"] == "停止并退出":
                    assert target.ForeColor == Color.Firebrick
                target.PerformClick()
            except Exception as exc:
                errors.append(str(exc))
                form.Close()
    timer = Timer()
    timer.Interval = 100
    timer.Tick += tick
    timeout = Timer()
    timeout.Interval = 15000
    def expired(sender, event):
        errors.append("native dialog timed out")
        Application.Exit()
    timeout.Tick += expired
    def worker():
        try:
            for kind, label, value in [("close", "收起到托盘", "tray"),
                                      ("close", "退出桌面端", "exit"),
                                      ("close", "取消", "cancel"),
                                      ("stop_exit", "取消", "cancel"),
                                      ("stop_exit", "停止并退出", "stop_exit")]:
                expected["label"] = label
                actual = desktop._native_choice(SimpleNamespace(native=owner), kind, hidden_form)
                assert actual == value, (actual, value)
                results.append(actual)
        except Exception as exc:
            errors.append(str(exc))
        finally:
            owner.BeginInvoke(Action(owner.Close))
    def shown(sender, event):
        timer.Start()
        timeout.Start()
        threading.Thread(target=worker, daemon=True).start()
    owner.Shown += shown
    Application.Run(owner)
    timer.Dispose()
    timeout.Dispose()
    assert not errors, errors
    assert len(results) == 5, results
    print("PASS: five native dialog choices, safe default, cancellation and danger semantics")


if __name__ == "__main__":
    main()
