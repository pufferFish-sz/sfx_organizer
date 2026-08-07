"""递归扫描源文件夹。

返回 ``ScannedFile`` 流,供下游 classifier + organizer 消费。

设计要点:
- 用 ``pathlib`` 统一路径(Windows 中文友好)
- 跳过 隐藏文件 / 系统文件 / __MACOSX / .DS_Store
- 支持 ``ignore_paths`` —— 用于防「源在目标里」时无限循环
- 大文件不读取内容,仅 stat
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)


# spec 2.1: 识别的文件类型
AUDIO_EXTS = frozenset({".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff", ".m4a"})
ARCHIVE_EXTS = frozenset({".zip", ".rar", ".7z", ".exe"})

# 跨平台噪声
_SKIP_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX"})


class FileKind(str, Enum):
    AUDIO = "audio"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class ScannedFile:
    """扫描结果中的一个条目。"""

    path: Path
    size: int
    kind: FileKind

    @property
    def is_audio(self) -> bool:
        return self.kind is FileKind.AUDIO

    @property
    def is_archive(self) -> bool:
        return self.kind is FileKind.ARCHIVE


def classify_extension(path: Path) -> FileKind | None:
    """根据后缀决定文件种类;无法识别返回 None。"""
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS:
        return FileKind.AUDIO
    if ext in ARCHIVE_EXTS:
        return FileKind.ARCHIVE
    return None


def scan(
    root: str | Path,
    *,
    ignore_paths: Iterable[str | Path] = (),
    follow_symlinks: bool = False,
) -> Iterator[ScannedFile]:
    """递归扫描 ``root``,产出每个能识别后缀的文件。

    Args:
        root: 起点目录
        ignore_paths: 额外要跳过的目录(防嵌套扫描自身、目标库等)
        follow_symlinks: 是否跟随符号链接(默认否,避免环)

    Yields:
        ``ScannedFile``,按文件系统遍历顺序
    """
    root_p = Path(root).resolve()
    if not root_p.exists():
        raise FileNotFoundError(f"扫描根目录不存在: {root_p}")
    if not root_p.is_dir():
        raise NotADirectoryError(f"扫描根必须是目录: {root_p}")

    ignore_set = {Path(p).resolve() for p in ignore_paths}

    yield from _walk(root_p, ignore_set, follow_symlinks)


def scan_to_list(
    root: str | Path,
    *,
    ignore_paths: Iterable[str | Path] = (),
    follow_symlinks: bool = False,
) -> list[ScannedFile]:
    """方便测试 / GUI 一次性取全集的便捷版本。"""
    return list(scan(root, ignore_paths=ignore_paths, follow_symlinks=follow_symlinks))


# --------- 内部 ---------

def _walk(root: Path, ignore: set[Path], follow_symlinks: bool) -> Iterator[ScannedFile]:
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except PermissionError as e:
            logger.warning("无权访问目录,跳过: %s (%s)", cur, e)
            continue
        except OSError as e:
            logger.warning("读取目录失败,跳过: %s (%s)", cur, e)
            continue

        for entry in entries:
            if entry.name in _SKIP_NAMES:
                continue
            if _is_hidden(entry):
                continue

            try:
                if entry.is_symlink() and not follow_symlinks:
                    continue
                resolved = entry.resolve()
            except OSError as e:
                logger.warning("解析路径失败: %s (%s)", entry, e)
                continue

            if resolved in ignore:
                logger.debug("跳过(在 ignore 列表): %s", entry)
                continue

            if entry.is_dir():
                stack.append(entry)
                continue

            if not entry.is_file():
                continue

            kind = classify_extension(entry)
            if kind is None:
                continue

            try:
                size = entry.stat().st_size
            except OSError as e:
                logger.warning("stat 失败,跳过: %s (%s)", entry, e)
                continue

            yield ScannedFile(path=entry, size=size, kind=kind)


def _is_hidden(p: Path) -> bool:
    """跨平台「隐藏文件」判定。

    - Unix: 以 '.' 开头
    - Windows: 通过 stat 的 FILE_ATTRIBUTE_HIDDEN 位
    """
    name = p.name
    if name.startswith("."):
        return True
    try:
        attrs = p.stat().st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x2)  # FILE_ATTRIBUTE_HIDDEN
