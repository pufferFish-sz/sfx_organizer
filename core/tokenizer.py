"""Token 切分 + 中文分词 + 中英映射。

供 ``preprocessor.preprocess`` 调用。本模块不做停用词过滤(交由 preprocessor)。

中文分词策略(spec 4.3):
- 优先 jieba(若已安装)
- 降级:用 zh_en_dict 的 key 列表做「最长匹配」
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# 中文字符范围(CJK 统一汉字 + 扩展A,够日常素材命名)
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]+")
# 英文/数字 token 切分:按非字母数字、按驼峰、按数字-字母边界
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9一-鿿㐀-䶿]+")
_CAMEL_RE = re.compile(
    r"""
      [A-Z]+(?=[A-Z][a-z])   # ABBRWord → ABBR, Word
    | [A-Z]?[a-z]+           # Word, word
    | [A-Z]+                 # ABBR
    | [0-9]+                 # 1234
    """,
    re.VERBOSE,
)

SegMode = Literal["jieba", "longest", "auto"]


def load_zh_dict(path: str | Path) -> dict[str, list[str]]:
    """加载中英映射词典。下划线开头的 key 是注释,忽略。

    失败时记日志并返回空 dict —— 工具仍可纯英文匹配。
    """
    p = Path(path)
    if not p.exists():
        logger.warning("zh_en_dict 不存在: %s — 使用空词典", p)
        return {}

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("zh_en_dict 解析失败: %s — 使用空词典", e)
        return {}

    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            out[k] = [s.lower() for s in v]
    return out


def split_mixed(text: str) -> list[str]:
    """第一步切分:按非字母数字、驼峰边界、CJK 块切。

    输出保持原大小写(由 preprocessor 在第 8 步统一转小写)。
    """
    if not text:
        return []

    tokens: list[str] = []
    # 先把文本切成 [非分隔符 段],再在每段内分别处理 CJK / Latin
    for chunk in _NON_ALNUM_RE.split(text):
        if not chunk:
            continue
        for piece in _split_cjk_latin(chunk):
            if _CJK_RE.fullmatch(piece):
                tokens.append(piece)
            else:
                # latin / 数字 → 进一步驼峰切分
                tokens.extend(m.group(0) for m in _CAMEL_RE.finditer(piece))
    return tokens


def _split_cjk_latin(s: str) -> list[str]:
    """同一 chunk 内,CJK 连续块与拉丁/数字连续块互不混淆。

    例: '计算机SFX故障' → ['计算机', 'SFX', '故障']
    """
    out: list[str] = []
    buf = []
    cur_is_cjk: bool | None = None
    for ch in s:
        is_cjk = bool(_CJK_RE.fullmatch(ch))
        if cur_is_cjk is None or is_cjk == cur_is_cjk:
            buf.append(ch)
        else:
            out.append("".join(buf))
            buf = [ch]
        cur_is_cjk = is_cjk
    if buf:
        out.append("".join(buf))
    return out


def segment_chinese(
    text: str,
    zh_dict_keys: list[str] | None = None,
    mode: SegMode = "auto",
) -> list[str]:
    """中文分词。

    Args:
        text: 纯中文片段(由 split_mixed 切出)
        zh_dict_keys: 降级模式所需的词典 key 列表(按长度倒序匹配)
        mode: "jieba" 强制 jieba;"longest" 强制最长匹配;"auto" 优先 jieba

    Returns:
        中文 token 列表(单字 token 也保留 —— 是否过滤由 preprocessor 决定)
    """
    if not text:
        return []

    if mode in ("jieba", "auto"):
        try:
            import jieba  # type: ignore

            # jieba 全局初始化只会发生一次
            return [t for t in jieba.lcut(text, HMM=True) if t.strip()]
        except ImportError:
            if mode == "jieba":
                logger.warning("jieba 未安装,降级为最长匹配")
        except Exception as e:
            logger.warning("jieba 分词失败: %s — 降级为最长匹配", e)

    return _longest_match_segment(text, zh_dict_keys or [])


def _longest_match_segment(text: str, dict_keys: list[str]) -> list[str]:
    """从左到右,每步尝试以最长词典词起始;无匹配则按单字切。"""
    if not text:
        return []

    # 按长度倒序排,长词优先
    keys_sorted = sorted(set(dict_keys), key=lambda k: -len(k))
    max_len = len(keys_sorted[0]) if keys_sorted else 1
    keys_set = set(keys_sorted)

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        # 从最长长度尝试
        for size in range(min(max_len, n - i), 1, -1):
            cand = text[i:i + size]
            if cand in keys_set:
                out.append(cand)
                i += size
                matched = True
                break
        if not matched:
            out.append(text[i])
            i += 1
    return out


def map_zh_to_en(token: str, zh_dict: dict[str, list[str]]) -> list[str]:
    """中文 token → 英文同义词列表。

    保留原中文 token(供日志/调试),英文映射也加入。
    例: '计算机' → ['计算机', 'computer', 'pc']
    非中文或不在词典中 → [token]
    """
    if token in zh_dict:
        return [token] + list(zh_dict[token])
    return [token]
