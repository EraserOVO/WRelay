# -*- coding: utf-8 -*-
"""
开机自启中转脚本（文件名纯 ASCII，避免启动项里的中文编码问题）。

由当前用户的 WRelay 开机启动注册项通过 pythonw 隐藏窗口运行本脚本，
本脚本给当前进程追加 --autostart 标志后，调用桌面入口：
  - 后台启动桌面管理进程与托盘图标（不弹窗口）
  - 管理进程启动后自动拉起机器人
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

sys.argv.append("--autostart")

import importlib

desktop = importlib.import_module("desktop_app")
desktop.main()
