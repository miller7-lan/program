"""
core/app_paths.py
统一处理源码运行与 PyInstaller 打包后的资源路径、数据路径。

数据文件（账本、锁文件等）默认放在 exe 所在文件夹，整个文件夹复制走即可换电脑。
在压缩包里直接双击打开时，解压软件会把 exe 放进系统临时文件夹，随时可能被清理，
这时改用固定的 %LOCALAPPDATA%\\ProfitHelper，保证“点开即用”也不会丢数据。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def running_from_temp() -> bool:
    """打包后的 exe 是否位于系统临时文件夹里（通常是在压缩包里直接双击打开的）。"""
    if not getattr(sys, "frozen", False):
        return False
    try:
        temp = Path(tempfile.gettempdir()).resolve()
        exe_dir = executable_dir()
    except OSError:
        return False
    return exe_dir == temp or temp in exe_dir.parents


def data_dir() -> Path:
    if running_from_temp():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        folder = Path(base) / "ProfitHelper"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    return executable_dir()


def data_file_path(filename: str) -> Path:
    return data_dir() / filename
