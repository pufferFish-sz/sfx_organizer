"""资源路径解析 —— 兼容 dev / PyInstaller frozen 模式。

PyInstaller 把项目和 ``data/`` 都放进 ``sys._MEIPASS`` 临时解压目录(onefile)
或 ``_internal/`` 同级目录(onedir),用 ``sys._MEIPASS`` 一致访问最稳。

Dev 模式下,本文件位于 ``sfx_organizer/utils/paths.py``,``.parent.parent``
就是项目根。
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """项目根目录(含 ``data/`` 子目录)。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """``data/`` 目录(UCS / 词典 / 停用词 / 品牌 / extra_synonyms 都在里面)。"""
    return app_root() / "data"


def is_frozen() -> bool:
    """是否在 PyInstaller bundle 内运行。"""
    return getattr(sys, "frozen", False)
