// Custom caption on a normal, sizable WinForms window. Windows still owns moves,
// sizing, system commands and Snap; the WebView never emulates window dragging.
using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace WRelay.Desktop
{
    public sealed class WindowChrome : NativeWindow
    {
        const int WM_NCCALCSIZE = 0x83, WM_NCHITTEST = 0x84;
        const int WM_NCMOUSEMOVE = 0xA0, WM_NCMOUSELEAVE = 0x2A2;
        const int WM_NCLBUTTONDOWN = 0xA1, WM_LBUTTONUP = 0x202;
        const int WM_MOUSEMOVE = 0x200, WM_CAPTURECHANGED = 0x215;
        const int WM_WINDOWPOSCHANGING = 0x46, WM_WINDOWPOSCHANGED = 0x47;
        const int HTCLIENT = 1, HTCAPTION = 2, HTMINBUTTON = 8, HTMAXBUTTON = 9, HTCLOSE = 20;
        readonly Form form;
        readonly CaptionSurface caption;
        int pressed;
        bool disposed;
        bool light;
        Size? restoringSize;
        FormWindowState nativeState;

        [StructLayout(LayoutKind.Sequential)]
        struct Rect { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)]
        struct WindowPosition { public IntPtr Window, After; public int X, Y, Width, Height; public uint Flags; }
        [StructLayout(LayoutKind.Sequential)]
        struct MouseTracking { public int Size, Flags; public IntPtr Window; public int HoverTime; }
        [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
        [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] static extern bool TrackMouseEvent(ref MouseTracking tracking);
        [DllImport("user32.dll")] static extern IntPtr SetCapture(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd, int msg, IntPtr wparam, IntPtr lparam);

        public WindowChrome(Form target, string iconPath)
        {
            form = target;
            caption = new CaptionSurface(this, iconPath);
            form.Controls.Add(caption);
            AssignHandle(form.Handle);
            nativeState = form.WindowState;
            form.SizeChanged += LayoutCaption;
            form.Activated += Repaint;
            form.Deactivate += Repaint;
            form.FormClosed += Closed;
            LayoutCaption(null, EventArgs.Empty);
            // Recalculate the client area without changing position, size or focus.
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0, 0x37);
        }

        internal int Px(int value) { return (int)Math.Round(value * GetDpiForWindow(form.Handle) / 96.0); }
        public bool Maximized { get { return IsZoomed(form.Handle); } }
        public int CaptionHeight { get { return Px(38); } }
        public void SetLight(bool value)
        {
            if (disposed || form.IsDisposed) return;
            if (form.InvokeRequired) { form.BeginInvoke((Action)(() => SetLight(value))); return; }
            light = value;
            caption.BackColor = light ? Color.FromArgb(238, 241, 247) : Color.FromArgb(30, 34, 41);
            caption.ForeColor = light ? Color.FromArgb(35, 39, 46) : Color.FromArgb(230, 232, 235);
            form.BackColor = caption.BackColor;
            caption.Refresh();
        }

        void LayoutCaption(object sender, EventArgs e)
        {
            if (disposed || form.WindowState == FormWindowState.Minimized) return;
            int border = Maximized ? 0 : Px(6);
            form.Padding = new Padding(border, CaptionHeight, border, border);
            caption.Bounds = new Rectangle(border, 0, Math.Max(0, form.ClientSize.Width - 2 * border), CaptionHeight);
            caption.BringToFront();
            caption.LayoutButtons();
            SetLight(light);
        }
        void Repaint(object sender, EventArgs e) { caption.Invalidate(true); }
        void Closed(object sender, FormClosedEventArgs e)
        {
            disposed = true;
            form.SizeChanged -= LayoutCaption;
            form.Activated -= Repaint;
            form.Deactivate -= Repaint;
            form.FormClosed -= Closed;
            ReleaseHandle();
            caption.Dispose();
        }

        // Uses client coordinates so maximized work-area insets and negative monitor
        // coordinates are handled by Windows rather than JS/CSS scale arithmetic.
        public int HitTest(Point screenPoint)
        {
            Point p = form.PointToClient(screenPoint);
            int w = form.ClientSize.Width, h = form.ClientSize.Height;
            int b = Maximized ? 0 : Px(6);
            bool left = p.X < b, right = p.X >= w - b, top = p.Y < b, bottom = p.Y >= h - b;
            if (b > 0) {
                if (top) return left ? 13 : right ? 14 : 12;
                if (bottom) return left ? 16 : right ? 17 : 15;
                if (left) return 10;
                if (right) return 11;
            }
            if (!caption.Bounds.Contains(p)) return HTCLIENT;
            int x = p.X - caption.Left;
            if (x >= caption.Width - Px(46)) return HTCLOSE;
            if (x >= caption.Width - Px(92)) return HTMAXBUTTON;
            if (x >= caption.Width - Px(138)) return HTMINBUTTON;
            return HTCAPTION;
        }

        internal void Command(int hit)
        {
            int command = hit == HTCLOSE ? 0xF060 : hit == HTMINBUTTON ? 0xF020 : Maximized ? 0xF120 : 0xF030;
            // WM_SYSCOMMAND preserves the existing close-to-tray handler.
            PostMessage(form.Handle, 0x112, new IntPtr(command), IntPtr.Zero);
        }
        static bool IsButton(int hit) { return hit == HTMINBUTTON || hit == HTMAXBUTTON || hit == HTCLOSE; }
        protected override void WndProc(ref Message m)
        {
            // WinForms converts its saved client size back through the standard
            // frame on restore. Our client already includes that frame. Preserve
            // Windows' chosen restore size during that synchronous conversion;
            // leave position, Snap and subsequent user sizing to Windows.
            bool restoring = false;
            if (m.Msg == WM_WINDOWPOSCHANGED) {
                FormWindowState state = IsIconic(Handle) ? FormWindowState.Minimized : Maximized ? FormWindowState.Maximized : FormWindowState.Normal;
                restoring = nativeState != FormWindowState.Normal && state == FormWindowState.Normal;
                nativeState = state;
            }
            if (restoring) {
                Rect bounds;
                GetWindowRect(Handle, out bounds);
                Size? previous = restoringSize;
                restoringSize = new Size(bounds.Right - bounds.Left, bounds.Bottom - bounds.Top);
                try { base.WndProc(ref m); }
                finally { restoringSize = previous; }
                return;
            }
            if (m.Msg == WM_WINDOWPOSCHANGING && restoringSize.HasValue) {
                WindowPosition pos = (WindowPosition)Marshal.PtrToStructure(m.LParam, typeof(WindowPosition));
                if ((pos.Flags & 1) == 0) {
                    pos.Width = restoringSize.Value.Width;
                    pos.Height = restoringSize.Value.Height;
                    Marshal.StructureToPtr(pos, m.LParam, false);
                }
            }
            if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero) {
                if (Maximized) {
                    Rect r = (Rect)Marshal.PtrToStructure(m.LParam, typeof(Rect));
                    Rectangle work = Screen.FromHandle(Handle).WorkingArea;
                    r.Left = Math.Max(r.Left, work.Left); r.Top = Math.Max(r.Top, work.Top);
                    r.Right = Math.Min(r.Right, work.Right); r.Bottom = Math.Min(r.Bottom, work.Bottom);
                    Marshal.StructureToPtr(r, m.LParam, false);
                }
                m.Result = IntPtr.Zero; return;
            }
            if (m.Msg == WM_NCHITTEST) {
                long packed = m.LParam.ToInt64();
                m.Result = new IntPtr(HitTest(new Point((short)(packed & 0xffff), (short)((packed >> 16) & 0xffff))));
                return;
            }
            if (m.Msg == WM_NCMOUSEMOVE || (m.Msg == WM_MOUSEMOVE && pressed != 0)) {
                int hit = HitTest(Cursor.Position);
                caption.SetHover(IsButton(hit) ? hit : 0);
                MouseTracking t = new MouseTracking { Size = Marshal.SizeOf(typeof(MouseTracking)), Flags = 0x12, Window = Handle };
                TrackMouseEvent(ref t);
                // Forward non-client hover so Windows 11 can show its Snap flyout.
            }
            if (m.Msg == WM_NCMOUSELEAVE) caption.SetHover(0);
            if (m.Msg == WM_NCLBUTTONDOWN && IsButton(m.WParam.ToInt32())) {
                pressed = m.WParam.ToInt32(); SetCapture(Handle); m.Result = IntPtr.Zero; return;
            }
            if (m.Msg == WM_LBUTTONUP && pressed != 0) {
                int hit = pressed; pressed = 0; ReleaseCapture();
                if (HitTest(Cursor.Position) == hit) Command(hit);
                m.Result = IntPtr.Zero; return;
            }
            if (m.Msg == WM_CAPTURECHANGED) { pressed = 0; caption.SetHover(0); }
            base.WndProc(ref m);
            if (m.Msg == 0x2E0) LayoutCaption(null, EventArgs.Empty); // WM_DPICHANGED
        }

        sealed class CaptionSurface : Panel
        {
            readonly WindowChrome owner;
            readonly Icon logo;
            readonly CaptionButton[] buttons;
            public CaptionSurface(WindowChrome owner, string iconPath)
            {
                this.owner = owner;
                DoubleBuffered = true; TabStop = false;
                logo = new Icon(iconPath, 48, 48);
                buttons = new[] { new CaptionButton(owner, 8), new CaptionButton(owner, 9), new CaptionButton(owner, 20) };
                Controls.AddRange(buttons);
            }
            public void LayoutButtons()
            {
                int width = owner.Px(46);
                for (int i = 0; i < 3; i++) {
                    buttons[i].Bounds = new Rectangle(Width - (3 - i) * width, 0, width, Height);
                    buttons[i].AccessibleName = i == 0 ? "最小化" : i == 2 ? "关闭" : owner.Maximized ? "还原" : "最大化";
                }
            }
            public void SetHover(int hit) { foreach (CaptionButton b in buttons) b.SetHover(hit); }
            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                int iconSize = owner.Px(24), left = owner.Px(14);
                e.Graphics.DrawIcon(logo, new Rectangle(left, (Height - iconSize) / 2, iconSize, iconSize));
                using (Font font = new Font("Segoe UI", 11, FontStyle.Bold))
                    TextRenderer.DrawText(e.Graphics, "WRelay", font,
                        new Rectangle(left + iconSize + owner.Px(9), 0, Math.Max(0, Width - owner.Px(200)), Height),
                        ForeColor, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
            }
            protected override void WndProc(ref Message m)
            {
                if (m.Msg == WM_NCHITTEST) { m.Result = new IntPtr(-1); return; } // HTTRANSPARENT -> parent native hit-test
                base.WndProc(ref m);
            }
            protected override void Dispose(bool disposing) { if (disposing) logo.Dispose(); base.Dispose(disposing); }
        }
        sealed class CaptionButton : Button
        {
            readonly WindowChrome owner;
            readonly int hit;
            bool hover;
            public CaptionButton(WindowChrome owner, int hit)
            {
                this.owner = owner; this.hit = hit;
                TabStop = false; AccessibleRole = AccessibleRole.PushButton;
                SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
                Click += delegate { owner.Command(hit); };
            }
            public void SetHover(int value) { bool next = value == hit; if (next != hover) { hover = next; Invalidate(); } }
            protected override void OnPaint(PaintEventArgs e)
            {
                Color bg = hover ? (hit == HTCLOSE ? Color.FromArgb(196, 43, 28) : owner.light ? Color.FromArgb(220, 225, 234) : Color.FromArgb(49, 55, 65)) : Parent.BackColor;
                e.Graphics.Clear(bg);
                Color fg = hover && hit == HTCLOSE ? Color.White : Parent.ForeColor;
                int size = owner.Px(10), x = (Width - size) / 2, y = (Height - size) / 2;
                using (Pen pen = new Pen(fg, Math.Max(1f, owner.Px(1)))) {
                    if (hit == HTMINBUTTON) e.Graphics.DrawLine(pen, x, Height / 2, x + size, Height / 2);
                    else if (hit == HTCLOSE) {
                        e.Graphics.DrawLine(pen, x, y, x + size, y + size);
                        e.Graphics.DrawLine(pen, x + size, y, x, y + size);
                    } else if (owner.Maximized) {
                        int d = owner.Px(3);
                        e.Graphics.DrawRectangle(pen, x + d, y, size - d, size - d);
                        using (SolidBrush fill = new SolidBrush(bg)) e.Graphics.FillRectangle(fill, x, y + d, size - d, size - d);
                        e.Graphics.DrawRectangle(pen, x, y + d, size - d, size - d);
                    } else e.Graphics.DrawRectangle(pen, x, y, size, size);
                }
            }
            protected override void WndProc(ref Message m)
            {
                if (m.Msg == WM_NCHITTEST) { m.Result = new IntPtr(-1); return; }
                base.WndProc(ref m);
            }
        }
    }
}
