"""文件移动 / 复制 + 目录创建 + 同名冲突处理。

设计分两层:
- ``plan_target_path(source, classification, root)`` — 纯函数,只算路径,不动盘
- ``execute_op(op)`` — 真正落盘(move / copy / dry_run)

GUI 的「预览」按钮调 plan_target_path,确认后调 execute_op。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .classifier import Classification, to_relative_path

logger = logging.getLogger(__name__)


class OpMode(str, Enum):
    MOVE = "move"
    COPY = "copy"
    DRY_RUN = "dry_run"   # 只规划路径,不写盘


@dataclass
class OrganizeOp:
    """一次「把 X 整理到 Y」的操作意图。"""

    source: Path
    classification: Classification
    target_root: Path
    mode: OpMode = OpMode.COPY


@dataclass
class OpResult:
    """操作结果。"""

    source: Path
    target: Path | None        # dry_run 时也填上「计划目标」
    classification: Classification
    success: bool
    error: str | None = None
    skipped_reason: str | None = None  # 如「源文件不存在」


# --------- 纯函数:计算目标路径 ---------

def plan_target_path(source: Path, classification: Classification, target_root: Path) -> Path:
    """计算文件应落到目标根下的哪里(不处理冲突)。

    保留原文件名(spec 2.7):中文、序号、品牌名都不动。
    """
    rel_dir = to_relative_path(classification)
    return Path(target_root) / rel_dir / source.name


def resolve_conflict(target: Path) -> Path:
    """若目标已存在,自动加 ``_1``, ``_2`` ... 直到不冲突。

    保留扩展名:`foo.wav` → `foo_1.wav`(而不是 `foo.wav_1`)。
    """
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    parent = target.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


# --------- 落盘 ---------

def execute_op(op: OrganizeOp) -> OpResult:
    """执行一次整理操作。"""
    src = Path(op.source)
    target_planned = plan_target_path(src, op.classification, op.target_root)

    if op.mode is OpMode.DRY_RUN:
        return OpResult(
            source=src,
            target=target_planned,
            classification=op.classification,
            success=True,
        )

    if not src.exists():
        return OpResult(
            source=src,
            target=None,
            classification=op.classification,
            success=False,
            skipped_reason="源文件不存在",
        )

    try:
        target_planned.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return OpResult(
            source=src,
            target=target_planned,
            classification=op.classification,
            success=False,
            error=f"创建目标目录失败: {e}",
        )

    final_target = resolve_conflict(target_planned)

    try:
        if op.mode is OpMode.COPY:
            shutil.copy2(src, final_target)
        else:  # MOVE
            shutil.move(str(src), str(final_target))
    except OSError as e:
        return OpResult(
            source=src,
            target=final_target,
            classification=op.classification,
            success=False,
            error=f"{op.mode.value} 失败: {e}",
        )

    return OpResult(
        source=src,
        target=final_target,
        classification=op.classification,
        success=True,
    )
