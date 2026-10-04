# -*- coding: utf-8 -*-
"""
面板重启中转脚本。

由桌面管理程序以独立进程方式拉起。新桌面进程通过单实例锁
等待旧进程退出，再沿用当前启动方式（后台自启或普通窗口）。
"""

import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE)
sys.path.insert(0, PROJECT_ROOT)

from plugins.paths import data_path, COMPILED, PROJECT_ROOT as SHARED_ROOT

if COMPILED:
    PROJECT_ROOT = os.fspath(SHARED_ROOT)
    BASE = os.path.join(PROJECT_ROOT, "panel")

AUTOSTART_FLAG = "--autostart"
RESTART_FLAG = "--restart"
AUTOSTART = AUTOSTART_FLAG in sys.argv


def _log(msg):
    try:
        log_path = data_path(os.path.join("logs", "panel.log"))
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def main():
    entry = os.path.join(
        BASE,
        "autostart.pyw" if AUTOSTART else "desktop_app.pyw",
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    try:
        if COMPILED:
            entry = os.path.join(PROJECT_ROOT, "WRelay.exe")
            args = [entry, RESTART_FLAG]
            if AUTOSTART:
                args.append(AUTOSTART_FLAG)
        else:
            args = [sys.executable, entry, RESTART_FLAG]
        subprocess.Popen(
            args,
            cwd=PROJECT_ROOT,
            creationflags=flags,
            env=env,
        )
        _log("面板重启：已拉起新进程 %s" % os.path.basename(entry))
    except Exception as exc:
        _log("面板重启拉起失败: %s" % exc)


if __name__ == "__main__":
    main()
