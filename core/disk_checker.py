"""磁盘空间预估 + 校验(spec 5.1)。

直接模式: 累加源文件大小。
解压模式: 累加非压缩包大小 + 压缩包大小 × 1.8(经验系数)。
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .scanner import FileKind, ScannedFile

logger = logging.getLogger(__name__)


# spec 5.1: 解压后体积按 1.8 倍估算
EXTRACT_BLOWUP = 1.8


class WorkMode(str, Enum):
    DIRECT = "direct"                        # 模式 A:不解压
    EXTRACT = "extract"                      # 旧:一次性全解压(保留兼容)
    EXTRACT_STREAMING = "extract_streaming"  # 新:逐 archive 流式解压


@dataclass(frozen=True)
class SpaceEstimate:
    """空间估算 + 校验结果。"""

    required_bytes: int
    available_bytes: int
    mode: WorkMode

    @property
    def ok(self) -> bool:
        return self.available_bytes >= self.required_bytes

    @property
    def shortfall_bytes(self) -> int:
        return max(0, self.required_bytes - self.available_bytes)

    def human_summary(self) -> str:
        """供 GUI 弹窗使用的友好字符串。"""
        req = _format_bytes(self.required_bytes)
        avail = _format_bytes(self.available_bytes)
        verdict = "充足" if self.ok else f"不足(还差 {_format_bytes(self.shortfall_bytes)})"
        return f"需要 {req},可用 {avail} — {verdict}"


def estimate_required(
    files: Iterable[ScannedFile],
    mode: WorkMode,
    *,
    move_mode: bool = False,
    same_disk_as_source: bool = False,
    tmp_on_target_disk: bool = True,
) -> int:
    """根据扫描结果与工作模式,估算**目标盘**所需新增字节数。

    Args:
        files: 扫描结果
        mode: 工作模式
        move_mode: 是否 MOVE — 与 ``same_disk_as_source`` 联动:
            - DIRECT + MOVE + 同盘:``os.rename`` 几乎不占空间,返回 0
            - EXTRACT_STREAMING + MOVE + 同盘:源 zip 解一个删一个,净增量小
        same_disk_as_source: 目标盘是否与源盘同一物理/逻辑磁盘
        tmp_on_target_disk: 系统 TEMP 是否与目标在同盘 —— 否则 tmp peak 占的是
            别的盘,不应算进目标 require
    """
    files_list = list(files)

    if mode is WorkMode.DIRECT:
        if move_mode and same_disk_as_source:
            # 同盘 MOVE 就是 ``os.rename`` — 数据不动,仅改 inode,几乎零空间
            return 0
        return sum(f.size for f in files_list)

    if mode is WorkMode.EXTRACT:
        # 旧模式:压缩包按 1.8x,音频按原样 — 假设全部解压同时存在
        total = 0.0
        for f in files_list:
            if f.kind is FileKind.ARCHIVE:
                total += f.size * EXTRACT_BLOWUP
            else:
                total += f.size
        return int(total)

    if mode is WorkMode.EXTRACT_STREAMING:
        audio_total = sum(f.size for f in files_list if f.kind is FileKind.AUDIO)
        archives = [f.size for f in files_list if f.kind is FileKind.ARCHIVE]
        archive_total = sum(archives)
        max_archive = max(archives, default=0)
        tmp_peak = int(max_archive * EXTRACT_BLOWUP) if tmp_on_target_disk else 0

        if move_mode and same_disk_as_source:
            # MOVE + 同盘:源 zip 解一个删一个,目标净增量 = (解压后 - 原 zip)
            #   = archive_total * (BLOWUP - 1)
            net_archive = archive_total * (EXTRACT_BLOWUP - 1.0)
            return int(audio_total + net_archive + tmp_peak)

        # COPY 或 异盘:目标需容纳全部解压后内容 (+ tmp peak 当 tmp 也在目标盘)
        return int(audio_total + archive_total * EXTRACT_BLOWUP + tmp_peak)

    return sum(f.size for f in files_list)


def check_space(
    files: Iterable[ScannedFile],
    target_path: str | Path,
    mode: WorkMode = WorkMode.DIRECT,
    *,
    move_mode: bool = False,
    same_disk_as_source: bool = False,
    tmp_on_target_disk: bool = True,
) -> SpaceEstimate:
    """估算并校验目标盘空间。

    Args:
        files: 待处理文件列表(可二次迭代或一次性 list)
        target_path: 目标根目录(目录可不存在,会回溯到最近存在的祖先)
        mode: 工作模式
        move_mode: 透传给 ``estimate_required``
        same_disk_as_source: 同上
        tmp_on_target_disk: 同上

    Returns:
        ``SpaceEstimate``,GUI 可读 ``.ok`` 与 ``.human_summary()``
    """
    files_list = list(files)
    required = estimate_required(
        files_list, mode,
        move_mode=move_mode,
        same_disk_as_source=same_disk_as_source,
        tmp_on_target_disk=tmp_on_target_disk,
    )
    available = _available_at(Path(target_path))
    return SpaceEstimate(required, available, mode)


def same_disk(p1: str | Path, p2: str | Path) -> bool:
    """两条路径是否在同一物理/逻辑磁盘。

    - Windows:比盘符(``Path.drive``)
    - Unix-like:比 ``st_dev``;路径不存在则回溯到最近祖先

    返回 ``False`` 在无法判定时(保守 — 让上层走非同盘的悲观估算)。
    """
    a = Path(p1)
    b = Path(p2)
    if sys.platform == "win32":
        try:
            return a.resolve().drive.lower() == b.resolve().drive.lower()
        except OSError:
            return False

    def _dev(p: Path) -> int | None:
        cur = p
        while not cur.exists():
            if cur.parent == cur:
                return None
            cur = cur.parent
        try:
            return cur.stat().st_dev
        except OSError:
            return None

    d1 = _dev(a)
    d2 = _dev(b)
    return d1 is not None and d1 == d2


# --------- 内部 ---------

def _available_at(path: Path) -> int:
    """``shutil.disk_usage`` 要求路径存在 — 不存在时回溯到最近祖先。"""
    p = path.resolve() if path.exists() else path
    while not p.exists():
        if p.parent == p:  # 根了
            raise FileNotFoundError(f"找不到任何可估算的祖先路径: {path}")
        p = p.parent
    return shutil.disk_usage(p).free


_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def _format_bytes(n: int) -> str:
    if n < 0:
        return f"-{_format_bytes(-n)}"
    f = float(n)
    for u in _UNITS:
        if f < 1024:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} EB"
