"""加载 UCS v8.2.1 CSV 并构建多视角索引。

CSV 列: Category, SubCategory, CatID, CatShort, Explanation, Synonyms

设计要点:
- 仅依赖标准库 `csv` —— 753 行表不需要 pandas
- 入口 ``load_ucs(path)`` 返回 ``UCSIndex``,后续 classifier / preprocessor 都从它取数
- 关键词全部预切分 + 小写化,匹配时只做集合查找
- CatID 前缀匹配的 trie 退化为「按长度倒序的 CatID 列表」 —— 753 个条目线性扫足够快
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UCSEntry:
    """单条 UCS 分类。"""

    category: str           # 大类,如 "GUNS"
    sub_category: str       # 子类,如 "ANTIQUE"
    cat_id: str             # 完整 ID,如 "GUNAntq"
    cat_short: str          # 大类缩写,如 "GUN"
    explanation: str
    synonyms: frozenset[str]  # 已小写、已去重

    @property
    def sub_category_tokens(self) -> frozenset[str]:
        """子类名拆成小写词集合(供加权打分用)。"""
        return frozenset(t for t in _split_words(self.sub_category) if t)


@dataclass
class UCSIndex:
    """UCS 表的查询索引。"""

    entries: tuple[UCSEntry, ...]
    by_cat_id: dict[str, UCSEntry] = field(default_factory=dict)
    by_category: dict[str, list[UCSEntry]] = field(default_factory=dict)
    cat_ids_by_length_desc: tuple[str, ...] = ()
    cat_shorts: frozenset[str] = field(default_factory=frozenset)
    """所有 CatShort 的小写集合。短缩写(如 ``ui``、``ir``)会被 preprocessor
    用作「受保护短词」绕过 min_ascii_len 过滤,确保它们能命中 classifier 的
    CatShort 评分。"""

    # --------- 查询接口 ---------

    def categories(self) -> list[str]:
        """所有大类,按字母排序。"""
        return sorted(self.by_category.keys())

    def entries_in(self, category: str) -> list[UCSEntry]:
        """某大类下的全部条目。"""
        return list(self.by_category.get(category.upper(), []))

    def find_cat_id_prefix(self, text: str) -> UCSEntry | None:
        """若 text 以某个已知 CatID(大小写敏感前缀)开头,返回该条目。

        按 CatID 长度倒序匹配,确保 "AMBLakeSide" 不会被 "AMB" 的某个短 ID 抢走。
        """
        for cat_id in self.cat_ids_by_length_desc:
            if text.startswith(cat_id):
                return self.by_cat_id[cat_id]
        return None

    def __len__(self) -> int:
        return len(self.entries)


def load_ucs(
    csv_path: str | Path,
    extra_synonyms_path: str | Path | None = None,
) -> UCSIndex:
    """从 CSV 加载并构建索引。

    Args:
        csv_path: UCS CSV 文件路径
        extra_synonyms_path: 可选的额外同义词补充 JSON(spec 4 节外的扩展点),
            键为 ``"CATEGORY/SUBCATEGORY"``,值为字符串列表 — 会并入该条目的
            ``synonyms``。文件不存在则忽略。

    Raises:
        FileNotFoundError: CSV 路径不存在
        ValueError: 表头缺列 / 行格式异常
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"UCS CSV 不存在: {path}")

    extra = _load_extra_synonyms(extra_synonyms_path) if extra_synonyms_path else {}

    entries: list[UCSEntry] = []
    expected_cols = {"Category", "SubCategory", "CatID", "CatShort", "Explanation", "Synonyms"}

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or not expected_cols.issubset(reader.fieldnames):
            missing = expected_cols - set(reader.fieldnames or [])
            raise ValueError(f"UCS CSV 表头缺列: {missing}")

        for line_no, row in enumerate(reader, start=2):
            try:
                entries.append(_row_to_entry(row, extra))
            except Exception as e:  # 单行失败不阻断整表
                logger.warning("UCS CSV 第 %d 行解析失败: %s — 跳过", line_no, e)

    if not entries:
        raise ValueError(f"UCS CSV 没有有效行: {path}")

    if extra:
        unknown = set(extra) - {f"{e.category}/{e.sub_category}" for e in entries}
        for k in unknown:
            logger.warning("extra_synonyms 中未知键: %s — 忽略", k)

    return _build_index(entries)


def _load_extra_synonyms(path: str | Path) -> dict[str, list[str]]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("extra_synonyms 解析失败: %s", e)
        return {}
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if k.startswith("_") or "/" not in k:
            continue
        if isinstance(v, list):
            out[k.upper()] = [str(x).strip().lower() for x in v if str(x).strip()]
    return out


def _row_to_entry(row: dict[str, str], extra: dict[str, list[str]] | None = None) -> UCSEntry:
    cat_id = (row["CatID"] or "").strip()
    if not cat_id:
        raise ValueError("CatID 为空")

    syn_raw = row.get("Synonyms") or ""
    syns = {s.strip().lower() for s in syn_raw.split(",") if s.strip()}

    cat = (row["Category"] or "").strip().upper()
    sub = (row["SubCategory"] or "").strip().upper()
    if extra:
        for s in extra.get(f"{cat}/{sub}", ()):
            if s:
                syns.add(s)

    return UCSEntry(
        category=cat,
        sub_category=sub,
        cat_id=cat_id,
        cat_short=(row["CatShort"] or "").strip(),
        explanation=(row.get("Explanation") or "").strip(),
        synonyms=frozenset(syns),
    )


def _build_index(entries: Iterable[UCSEntry]) -> UCSIndex:
    entries_t = tuple(entries)
    by_cat_id: dict[str, UCSEntry] = {}
    by_category: dict[str, list[UCSEntry]] = {}

    for e in entries_t:
        if e.cat_id in by_cat_id:
            logger.warning("CatID 重复: %s (覆盖前一条)", e.cat_id)
        by_cat_id[e.cat_id] = e
        by_category.setdefault(e.category, []).append(e)

    cat_ids_sorted = tuple(
        sorted(by_cat_id.keys(), key=lambda s: (-len(s), s))
    )

    cat_shorts = frozenset(e.cat_short.lower() for e in entries_t if e.cat_short)

    return UCSIndex(
        entries=entries_t,
        by_cat_id=by_cat_id,
        by_category=by_category,
        cat_ids_by_length_desc=cat_ids_sorted,
        cat_shorts=cat_shorts,
    )


# --------- 内部工具 ---------

def _split_words(s: str) -> list[str]:
    """子类名按空格 / & / 连字符切分,小写。"""
    out: list[str] = []
    buf = []
    for ch in s:
        if ch.isalnum():
            buf.append(ch.lower())
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


def default_csv_path() -> Path:
    """项目自带 CSV 的默认路径(兼容 PyInstaller frozen)。"""
    from utils.paths import data_dir
    return data_dir() / "ucs_v8_2_1.csv"


def default_extra_synonyms_path() -> Path:
    """项目自带 extra_synonyms 的默认路径(兼容 PyInstaller frozen)。"""
    from utils.paths import data_dir
    return data_dir() / "extra_synonyms.json"
