"""Windows-only smoke check for the custom caption; never starts the bot/panel."""
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))


def main(snapshot_path=None, interactive=False):
    import webview
    from windows_titlebar import attach_titlebar

    failures = []
    adapters = []
    loaded_event = threading.Event()
    maximized_event = threading.Event()
    restored_event = threading.Event()
    closing_event = threading.Event()
    allow_close = threading.Event()
    preview = """<html><body style="margin:0;padding:32px;background:#1e2229;color:#e6e8eb;font:18px 'Segoe UI',sans-serif">
    <h2>WRelay 原生窗口行为验证</h2><p>独立测试窗口，不连接机器人或管理 API。</p>
    <p id="metrics"></p><p>可检查：拖动、双击标题栏、四边四角缩放、Snap 与窗口快捷键。</p>
    <script>function update(){document.getElementById('metrics').textContent=
    '内容视口 '+innerWidth+' × '+innerHeight+' · DPI '+Math.round(devicePixelRatio*100)+'%';}
    addEventListener('resize',update);update();</script></body></html>"""
    window = webview.create_window("WRelay caption smoke", html=preview if interactive else "<p>caption smoke</p>",
                                   focus=interactive, width=1000, height=700,
                                   min_size=(960, 640), resizable=True, frameless=False)

    def attach():
        try:
            import ctypes
            from System.Drawing import Point
            form = window.native
            # WebView2 needs a shown parent to initialize. Keep this disposable test
            # window transparent, non-focusable and absent from the taskbar.
            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            handle = form.Handle.ToInt64()
            if not interactive:
                form.Opacity = 0
                style = user32.GetWindowLongPtrW(handle, -20)
                user32.SetWindowLongPtrW(handle, -20, (style & ~0x40000) | 0x80 | 0x8000000)
            chrome = attach_titlebar(form, str(ROOT / "panel" / "wrelay_panel.ico"))
            adapters.append(chrome)
            form.PerformLayout()
            user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            assert chrome.CaptionHeight == round(user32.GetDpiForWindow(handle) * 38 / 96), "DPI caption size mismatch"
            for point, expected in ((Point(100, 20), 2), (Point(1, 1), 13), (Point(100, 100), 1)):
                assert chrome.HitTest(form.PointToScreen(point)) == expected, "Caption/resize/content hit mismatch"
            width = form.ClientSize.Width
            height = form.ClientSize.Height
            edges = ((1, height // 2, 10), (width - 1, height // 2, 11),
                     (width // 2, 1, 12), (width // 2, height - 1, 15),
                     (1, 1, 13), (width - 1, 1, 14),
                     (1, height - 1, 16), (width - 1, height - 1, 17))
            for x, y, expected in edges:
                assert chrome.HitTest(form.PointToScreen(Point(x, y))) == expected, "Eight-way resize hit mismatch"
            scale = chrome.CaptionHeight / 38
            for distance, expected in ((25, 20), (70, 9), (115, 8)):
                assert chrome.HitTest(form.PointToScreen(Point(width - round(distance * scale), round(20 * scale)))) == expected, "Button hit mismatch"
            assert form.webview.Top >= chrome.CaptionHeight, "WebView overlaps caption"
            chrome.SetLight(True)
            chrome.SetLight(False)
        except Exception as exc:
            failures.append(traceback.format_exc())

    def loaded():
        from System import Action
        loaded_event.set()
        if interactive:
            print("READY: interactive caption test window", flush=True)
            return
        try:
            form = window.native
            chrome = adapters[0]
            caption = next(c for c in form.Controls if c.GetType().Name == "CaptionSurface")
            original_size = form.Size
            form.Invoke(Action(lambda: caption.Controls[1].PerformClick()))
            assert maximized_event.wait(3), "Maximize command did not reach Windows"
            def check_maximized():
                assert chrome.Maximized, "Windows did not maximize"
                assert caption.Controls[1].AccessibleName == "还原", "Restore icon/name not updated"
            form.Invoke(Action(check_maximized))
            form.Invoke(Action(lambda: caption.Controls[1].PerformClick()))
            assert restored_event.wait(3), "Restore command did not reach Windows"
            def check_restored():
                assert not chrome.Maximized, "Windows did not restore"
                assert caption.Controls[1].AccessibleName == "最大化", "Maximize icon/name not updated"
                assert form.Size == original_size, "Restore size drift: %s -> %s" % (original_size, form.Size)
            form.Invoke(Action(check_restored))
            # Repeated transitions must not add the removed native frame again.
            from System.Windows.Forms import FormWindowState
            def repeated_transitions():
                for state in (FormWindowState.Maximized, FormWindowState.Minimized,
                              FormWindowState.Maximized, FormWindowState.Maximized):
                    form.WindowState = state
                    form.WindowState = FormWindowState.Normal
                    check_restored()
                from System.Drawing import Size
                resized = Size(original_size.Width + 40, original_size.Height + 20)
                form.Size = resized
                assert form.Size == resized, "Normal resizing was constrained by restore guard"
                form.Size = original_size
            form.Invoke(Action(repeated_transitions))
            # Verify the real parent WndProc delegates caption double-click to
            # Windows, rather than only checking our custom button's command.
            import ctypes
            user32 = ctypes.windll.user32
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user32.SendMessageW.restype = ctypes.c_ssize_t
            maximized_event.clear()
            user32.SendMessageW(form.Handle.ToInt64(), 0xA3, 2, 0)
            assert maximized_event.wait(3), "Caption double-click did not maximize"
            form.Invoke(Action(check_maximized))
            restored_event.clear()
            user32.SendMessageW(form.Handle.ToInt64(), 0xA3, 2, 0)
            assert restored_event.wait(3), "Caption double-click did not restore"
            form.Invoke(Action(check_restored))
            if snapshot_path:
                def render_caption():
                    from System.Drawing import Bitmap, Rectangle
                    from System.Drawing.Imaging import ImageFormat
                    caption.SetHover(20)
                    bitmap = Bitmap(caption.Width, caption.Height)
                    try:
                        caption.DrawToBitmap(bitmap, Rectangle(0, 0, bitmap.Width, bitmap.Height))
                        bitmap.Save(str(snapshot_path), ImageFormat.Png)
                    finally:
                        bitmap.Dispose()
                form.Invoke(Action(render_caption))
            form.Invoke(Action(lambda: caption.Controls[2].PerformClick()))
            assert closing_event.wait(3), "Close command skipped existing closing handler"
            assert not form.IsDisposed, "Cancelled close must keep the window alive"
        except Exception as exc:
            failures.append(traceback.format_exc())
        finally:
            allow_close.set()
            window.destroy()

    def closing():
        closing_event.set()
        return interactive or allow_close.is_set()

    window.events.before_show += attach
    window.events.loaded += loaded
    window.events.maximized += maximized_event.set
    window.events.restored += restored_event.set
    window.events.closing += closing

    def timeout():
        allow_close.set()
        window.destroy()
    watchdog = threading.Timer(900 if interactive else 25, timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        webview.start(gui="edgechromium")
    finally:
        watchdog.cancel()
    assert adapters, "Caption was not attached"
    assert loaded_event.is_set(), "WebView2 did not initialize"
    assert not failures, failures
    if not interactive:
        print("PASS: WebView2 caption integration, eight-way resize hit tests, caption double-click messages, repeated maximize/minimize/restore without size drift, normal resizing, content inset, themes, maximize/restore icons and cancellable close")


if __name__ == "__main__":
    main(interactive="--interactive" in sys.argv)
