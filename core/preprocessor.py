"""文件名预处理管线 — spec 2.3 节九步清洗。

入口: ``preprocess(filename, config) -> PreprocessResult``

输出的 ``tokens`` 是给 classifier 用的英文(和必要中文)小写词集。
``cat_id_candidate`` 则是给 CatID 前缀匹配用的「剥离序号后、未小写、未切分」的字符串。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .tokenizer import (
    SegMode,
    load_zh_dict,
    map_zh_to_en,
    segment_chinese,
    split_mixed,
)

logger = logging.getLogger(__name__)

# 步骤 2: 序号前缀。覆盖 "04 - ", "01_", "12. ", "07 "
_LEADING_NUM_RE = re.compile(r"^\s*\d+\s*[-_.·\s]+")
# 步骤 3: 中文括号【】《》 — 内容保留;英文 [括号](常是格式标识) — 内容丢弃
_CN_BRACKETS_RE = re.compile(r"[【《](.+?)[】》]")
_EN_BRACKETS_RE = re.compile(r"\[[^\]]*\]")
# 圆括号统一保留内容(中英常表描述)
_PARENS_RE = re.compile(r"[(（](.+?)[)）]")

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


@dataclass
class PreprocessConfig:
    """preprocessor 的可调参数。"""

    stopwords: frozenset[str] = frozenset()
    brands: frozenset[str] = frozenset()
    zh_dict: dict[str, list[str]] = field(default_factory=dict)
    min_ascii_len: int = 3       # ASCII 纯字母 token 至少长度,默认 3
    # 受保护的短词(小写)— 即使 < min_ascii_len 也保留。
    # 由 ``load_default_config(ucs=...)`` 自动注入 UCS 表里所有 CatShort,
    # 防止 "UI"、"IR" 这类 2 字符大类缩写被短词过滤丢掉。
    protected_short_tokens: frozenset[str] = frozenset()
    seg_mode: SegMode = "auto"


@dataclass
class PreprocessResult:
    """预处理输出。"""

    cat_id_candidate: str        # 剥离序号后的原文(用于 CatID 前缀匹配)
    tokens: list[str]            # 小写 / 去重 / 过滤后的最终 token 集


def preprocess(filename: str, config: PreprocessConfig) -> PreprocessResult:
    """对单个文件名执行九步清洗。

    Args:
        filename: 文件名(含或不含扩展名都行)
        config: 停用词 / 品牌名 / 中英词典等配置

    Returns:
        ``PreprocessResult``: 包含 CatID 候选串与最终 token 列表
    """
    # 1. 去扩展名
    stem = _strip_extension(filename)

    # 2. 剥离开头序号
    stripped = _LEADING_NUM_RE.sub("", stem).strip()
    cat_id_candidate = stripped  # 保留原大小写,供 CatID 前缀匹配

    # 3. 括号处理:中文括号内容拼回,英文方括号丢弃
    body = _process_brackets(stripped)

    # 4. 切分(英文驼峰 / 分隔符 / CJK 分块)
    raw_tokens = split_mixed(body)

    # 5. 中文 token 再切分
    expanded: list[str] = []
    zh_keys = list(config.zh_dict.keys())
    for tok in raw_tokens:
        if _is_cjk(tok):
            expanded.extend(segment_chinese(tok, zh_keys, mode=config.seg_mode))
        else:
            expanded.append(tok)

    # 6. 过滤停用词 / 品牌名 / 短词
    kept: list[str] = []
    for tok in expanded:
        if _should_drop(tok, config):
            continue
        kept.append(tok)

    # 7. 中文 token 映射到英文(原中文也保留)
    mapped: list[str] = []
    for tok in kept:
        if _is_cjk(tok) and tok in config.zh_dict:
            mapped.extend(map_zh_to_en(tok, config.zh_dict))
        else:
            mapped.append(tok)

    # 8. 全部小写、去重(保留首次出现顺序便于调试)
    seen: set[str] = set()
    final: list[str] = []
    for tok in mapped:
        lo = tok.lower()
        # 映射后再次过滤:英文同义词可能命中停用词/品牌/短词
        if lo in config.stopwords or lo in config.brands:
            continue
        if (
            lo.isascii() and lo.isalpha()
            and len(lo) < config.min_ascii_len
            and lo not in config.protected_short_tokens
        ):
            continue
        if lo in seen:
            continue
        seen.add(lo)
        final.append(lo)

    return PreprocessResult(cat_id_candidate=cat_id_candidate, tokens=final)


# --------- 辅助 ---------

def _strip_extension(filename: str) -> str:
    """剥离常见音频 / 压缩包扩展名;其余扩展名也一并剥离最后一段。"""
    p = Path(filename)
    return p.stem if p.suffix else filename


def _process_brackets(text: str) -> str:
    """中文【】《》保留内容;英文 [...] 丢弃;圆括号()保留内容。"""
    # 英文方括号先去
    text = _EN_BRACKETS_RE.sub(" ", text)
    # 中文括号:把内容剥出来嵌回原位
    text = _CN_BRACKETS_RE.sub(lambda m: " " + m.group(1) + " ", text)
    text = _PARENS_RE.sub(lambda m: " " + m.group(1) + " ", text)
    return text


def _is_cjk(token: str) -> bool:
    """是否含 CJK 字符。"""
    return bool(_CJK_RE.search(token))


def _should_drop(token: str, config: PreprocessConfig) -> bool:
    """步骤 6 的过滤规则。

    - 停用词 / 品牌名(大小写不敏感) → 丢
    - 纯数字 → 丢(序号尾巴、版本号等)
    - 纯 ASCII 字母 且 长度 < min_ascii_len → 丢(但 ``protected_short_tokens``
      里的短词放行 — 例如 UCS CatShort "UI" / "IR")
    - CJK 单字符 → 丢(通常是最长匹配未命中的残片,信噪比低)
    - 其它(混合数字字母、CJK ≥2 字符)→ 保留
    """
    if not token:
        return True
    lo = token.lower()
    if lo in config.stopwords or lo in config.brands:
        return True
    if token.isdigit():
        return True
    if _is_cjk(token):
        # 仅当全是 CJK 且只 1 字符时丢
        if len(token) <= 1:
            return True
        return False
    if token.isalpha() and len(token) < config.min_ascii_len:
        if lo in config.protected_short_tokens:
            return False
        return True
    return False


# --------- 资源加载便捷函数 ---------

def load_stopwords(path: str | Path) -> frozenset[str]:
    """从 ``stopwords.json`` 加载行业噪声词集合(小写)。"""
    p = Path(path)
    if not p.exists():
        logger.warning("stopwords 不存在: %s", p)
        return frozenset()
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("stopwords 解析失败: %s", e)
        return frozenset()

    out: set[str] = set()
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list):
            out.update(str(x).strip().lower() for x in v if str(x).strip())
    return frozenset(out)


def load_brands(path: str | Path) -> frozenset[str]:
    """从 ``brands.txt`` 加载品牌名集合(小写,# 开头行忽略)。"""
    p = Path(path)
    if not p.exists():
        logger.warning("brands.txt 不存在: %s", p)
        return frozenset()
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s.lower())
    return frozenset(out)


def load_default_config(
    data_dir: str | Path | None = None,
    ucs=None,
) -> PreprocessConfig:
    """加载项目自带数据目录下的全套资源。

    Args:
        data_dir: 数据目录;None 时用项目自带 ``data/``
        ucs: 可选 ``UCSIndex`` — 给了就把其全部 CatShort 注入 ``protected_short_tokens``,
            保证 2 字符大类缩写(如 "UI")不被短词过滤丢掉。
            未给时退而求其次:硬编码一个最小集合 ``{"ui"}``。
    """
    if data_dir is None:
        from utils.paths import data_dir as _resolve_data_dir
        data_dir = _resolve_data_dir()
    d = Path(data_dir)

    if ucs is not None:
        protected = ucs.cat_shorts
    else:
        # 最小默认:至少保住 USER INTERFACE 的「UI」
        protected = frozenset({"ui"})

    return PreprocessConfig(
        stopwords=load_stopwords(d / "stopwords.json"),
        brands=load_brands(d / "brands.txt"),
        zh_dict=load_zh_dict(d / "zh_en_dict.json"),
        protected_short_tokens=protected,
    )
