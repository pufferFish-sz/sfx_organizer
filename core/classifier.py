"""四级优先级分类逻辑 + 加权打分(spec 2.5)。

入口: ``classify(filename, ucs, preproc_config, options)``

四级优先级:
  P1. CatID 前缀直接命中 → 100% 子类
  P2. 经预处理的关键词加权匹配 → 三档判定(子类 / 大类根 / _misc)
  P3. (可选) 音频 metadata 走 P2 — 由调用方按需触发
  P4. 全部失败 → _misc

加权规则(每个 (token, entry) 取最高权命中):
  - token == Category name (lowercase exact)   → 4.0 × len(token)
  - token in SubCategory words (exact word)    → 3.0 × len(token)
  - token substring of SubCategory word (len≥4) → 2.0 × len(token)
  - token == CatShort (lowercase exact)        → 2.0 × len(token)
  - token in Synonyms                          → 1.0 × len(token)

判定:
  best_entry 与 best_category 必须一致,且子类得分 ≥ min_score 且
  在同大类内对第二名有 ≥ lead_gap 的优势 → 归子类。
  否则若大类总分 ≥ min_score 且对第二大类有 ≥ cat_lead_gap 优势 → 归大类根。
  否则 → _misc。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from .preprocessor import PreprocessConfig, PreprocessResult, preprocess
from .ucs_loader import UCSEntry, UCSIndex

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")

# 「无描述性」文件名 — 直接进 _misc
_GARBAGE_STEMS = (
    "untitled", "track", "audio", "sound", "sample", "clip", "file",
    "recording", "render", "export", "render", "out", "test", "new",
    "rec", "wav", "tmp",
)
_GARBAGE_RE = re.compile(
    r"^(?:" + "|".join(_GARBAGE_STEMS) + r")[\s_\-.]*\d*$",
    re.IGNORECASE,
)


@dataclass
class ClassifyOptions:
    """分类器可调参数。"""

    min_score: float = 4.0      # 子类得分阈值
    lead_gap: float = 1.5       # 子类对同大类内第二名的最小领先
    cat_lead_gap: float = 10.0  # 大类聚合对第二大类的最小领先(决定大类根 vs _misc)

    # 各类型命中权重 — 暴露出来便于实验调参
    w_category_exact: float = 4.0
    w_subname_word: float = 3.0
    w_subname_substring: float = 2.0
    w_cat_short: float = 2.0
    w_synonym: float = 1.0
    min_substring_len: int = 5   # 子串匹配的最小 token 长度(4 太短易把 tech 抬到 hitech)


# 判定来源 — 调试与日志用
class Hit:
    P1_CAT_ID = "p1_cat_id"
    P2_SUBCATEGORY = "p2_subcategory"
    P2_CATEGORY_ROOT = "p2_category_root"
    MISC = "misc"


@dataclass
class Classification:
    """分类结果。

    sub_category 为 None 表示落在大类根目录;category 为 None 表示落入 _misc。
    """

    category: str | None
    sub_category: str | None
    cat_id: str | None
    source: str
    score: float = 0.0
    tokens: tuple[str, ...] = ()

    @property
    def is_misc(self) -> bool:
        return self.category is None


# --------- 入口 ---------

def classify(
    filename: str,
    ucs: UCSIndex,
    preproc_config: PreprocessConfig,
    options: ClassifyOptions | None = None,
) -> Classification:
    """对单个文件名应用四级优先级。

    Args:
        filename: 原始文件名(含扩展名都行)
        ucs: 已加载的 UCS 索引
        preproc_config: preprocessor 资源(停用词 / 品牌名 / 中英词典)
        options: 阈值参数(默认按 spec)

    Returns:
        ``Classification`` 实例
    """
    opts = options or ClassifyOptions()
    pr = preprocess(filename, preproc_config)

    # 「无描述性」文件名直送 _misc(track01, untitled_001 这类默认导出名)
    if _GARBAGE_RE.match(pr.cat_id_candidate.strip()):
        return Classification(
            None, None, None,
            source=Hit.MISC,
            tokens=tuple(pr.tokens),
        )

    # P1: CatID 前缀
    entry = ucs.find_cat_id_prefix(pr.cat_id_candidate)
    if entry is not None:
        return Classification(
            category=entry.category,
            sub_category=entry.sub_category,
            cat_id=entry.cat_id,
            source=Hit.P1_CAT_ID,
            score=float("inf"),
            tokens=tuple(pr.tokens),
        )

    # P2: 关键词加权
    return classify_tokens(pr.tokens, ucs, opts)


def classify_tokens(
    tokens: list[str],
    ucs: UCSIndex,
    opts: ClassifyOptions | None = None,
) -> Classification:
    """脱离文件名,仅给定 token 列表做 P2 判定。

    metadata fallback (P3) 复用此入口。
    """
    opts = opts or ClassifyOptions()
    tok_tuple = tuple(tokens)
    if not tokens:
        return Classification(None, None, None, source=Hit.MISC, tokens=tok_tuple)

    scored = _score_all_entries(tokens, ucs.entries, opts)
    if not scored:
        return Classification(None, None, None, source=Hit.MISC, tokens=tok_tuple)

    # 先算大类聚合 — 后面用作排序次级 key
    cat_agg: dict[str, float] = {}
    for s, e in scored:
        cat_agg[e.category] = cat_agg.get(e.category, 0.0) + s

    # 按 (-score, -cat_agg, category, sub_category) 排序。
    # 关键:score 平局时优先大类聚合分高的子类 —— 防止「impact」「whoosh」这类
    # 跨多个大类共有的功能性子类名按字母序被随意分到错误大类(例: BULLETS/IMPACT)。
    scored.sort(
        key=lambda x: (-x[0], -cat_agg[x[1].category], x[1].category, x[1].sub_category)
    )

    best_score, best_entry = scored[0]

    # 同大类内第二名
    second_in_cat = 0.0
    for s, e in scored[1:]:
        if e.category == best_entry.category:
            second_in_cat = s
            break

    cats_sorted = sorted(cat_agg.items(), key=lambda x: -x[1])
    best_cat, best_cat_score = cats_sorted[0]
    second_cat_score = cats_sorted[1][1] if len(cats_sorted) > 1 else 0.0

    # 子类判定
    if (
        best_entry.category == best_cat
        and best_score >= opts.min_score
        and (best_score - second_in_cat) >= opts.lead_gap
    ):
        return Classification(
            category=best_entry.category,
            sub_category=best_entry.sub_category,
            cat_id=best_entry.cat_id,
            source=Hit.P2_SUBCATEGORY,
            score=best_score,
            tokens=tok_tuple,
        )

    # 大类根判定
    if (
        best_cat_score >= opts.min_score
        and (best_cat_score - second_cat_score) >= opts.cat_lead_gap
    ):
        return Classification(
            category=best_cat,
            sub_category=None,
            cat_id=None,
            source=Hit.P2_CATEGORY_ROOT,
            score=best_cat_score,
            tokens=tok_tuple,
        )

    return Classification(None, None, None, source=Hit.MISC, tokens=tok_tuple)


# --------- 评分核心 ---------

def _score_all_entries(
    tokens: list[str],
    entries: Iterable[UCSEntry],
    opts: ClassifyOptions,
) -> list[tuple[float, UCSEntry]]:
    """对所有 UCS 条目打分,返回 (score, entry) 列表(只含 score > 0)。"""
    out: list[tuple[float, UCSEntry]] = []
    # 预归一化 tokens(已是 lowercase 来自 preprocessor,这里再做一遍防御)
    norm_tokens = [t for t in (tk.lower() for tk in tokens) if t]
    if not norm_tokens:
        return out

    for entry in entries:
        score = _score_entry(norm_tokens, entry, opts)
        if score > 0.0:
            out.append((score, entry))
    return out


def _score_entry(tokens: list[str], entry: UCSEntry, opts: ClassifyOptions) -> float:
    cat_words = frozenset(_words_of(entry.category))
    sub_words = frozenset(_words_of(entry.sub_category))
    cat_short = entry.cat_short.lower()
    synonyms = entry.synonyms

    total = 0.0
    for tok in tokens:
        w = 0.0
        if tok in cat_words:
            w = max(w, opts.w_category_exact)
        if tok in sub_words:
            w = max(w, opts.w_subname_word)
        elif len(tok) >= opts.min_substring_len:
            for sw in sub_words:
                if len(sw) > len(tok) and tok in sw:
                    w = max(w, opts.w_subname_substring)
                    break
        if tok == cat_short:
            w = max(w, opts.w_cat_short)
        if tok in synonyms:
            w = max(w, opts.w_synonym)
        if w > 0:
            total += len(tok) * w
    return total


def _words_of(name: str) -> list[str]:
    """把分类名按非字母数字切分,返回小写词列表。

    例: 'FOOD & DRINK' → ['food', 'drink']
        'ROOM TONE' → ['room', 'tone']
        'HARD DRIVE' → ['hard', 'drive']
    """
    return _WORD_RE.findall(name.lower())


# --------- 便捷:把 Classification 转成目标相对路径 ---------

def to_relative_path(c: Classification) -> str:
    """决定文件应落在目标根目录下的哪个子路径(不含文件名)。"""
    if c.is_misc:
        return "_misc"
    if c.sub_category is None:
        return c.category or "_misc"
    return f"{c.category}/{c.sub_category}"
