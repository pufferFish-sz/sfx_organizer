"""音频文件 BWF / ID3 / Vorbis 标签读取(mutagen)。

供 classifier P3 fallback 使用:当文件名 + 关键词都失败时,
读取嵌入式标签的 Title / Description / Comment / Subject / Keywords,
拼成新的「文件名」交给 P2 重新匹配(spec 2.5)。

mutagen 缺失时直接返回空字符串(模块仍可被 import,只是不读 metadata)。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from mutagen import File as MutagenFile  # type: ignore
    _HAS_MUTAGEN = True
except ImportError:
    MutagenFile = None  # type: ignore
    _HAS_MUTAGEN = False


# 各容器中常见的「描述性」字段名,统一去重提取
_DESC_KEYS = (
    "title", "TIT2",
    "description", "TDES", "comment", "COMM", "comm",
    "subject", "TSST", "TIT3",
    "keywords", "TKEY", "TXXX:KEYWORDS",
    "artist", "TPE1",
    "album", "TALB",
    "iart", "isbj", "icmt", "ikey",  # BWF/iXML 常见标记
)


def read_descriptive_text(audio_path: str | Path) -> str:
    """读取音频文件中所有「描述性」字段,拼成一个字符串。

    Returns:
        拼好的描述文本(空格分隔)。文件不存在 / mutagen 未装 / 读取失败 → ""
    """
    if not _HAS_MUTAGEN:
        return ""

    p = Path(audio_path)
    if not p.exists():
        return ""

    try:
        mf = MutagenFile(p)
    except Exception as e:  # noqa: BLE001 — mutagen 内部异常很多变种
        logger.debug("mutagen 读取失败: %s (%s)", p, e)
        return ""

    if mf is None:
        return ""

    parts: list[str] = []
    seen: set[str] = set()

    # mutagen 把 tag 暴露为类 dict 的 .tags 或直接 file-like
    tag_source = getattr(mf, "tags", None) or mf
    try:
        items = list(tag_source.items()) if hasattr(tag_source, "items") else []
    except Exception:
        items = []

    for key, value in items:
        if str(key).lower() not in {k.lower() for k in _DESC_KEYS}:
            continue
        text = _flatten_tag_value(value)
        if text and text not in seen:
            parts.append(text)
            seen.add(text)

    return " ".join(parts)


def _flatten_tag_value(v) -> str:
    """tag value 可能是 str / list / mutagen Frame —— 尽力转字符串。"""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return " ".join(_flatten_tag_value(x) for x in v).strip()
    # mutagen ID3 frame 通常有 .text 属性
    text = getattr(v, "text", None)
    if text is not None:
        return _flatten_tag_value(text)
    return str(v).strip()
