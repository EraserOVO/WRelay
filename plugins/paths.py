"""WRelay 的项目根目录和运行时数据路径。

所有运行时文件都基于本文件所在的项目目录解析，避免 Bot、面板或诊断
脚本因启动时的当前工作目录不同而把数据写到不同位置。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"


def data_path(name: str) -> str:
    """返回 data 目录下文件的绝对路径。"""
    return str(DATA_DIR / name)
