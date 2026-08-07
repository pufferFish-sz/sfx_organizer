"""classifier 集成测试 — 必须覆盖 real_filenames.txt 全部样本。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.classifier import Classification, Hit, classify, to_relative_path
from core.preprocessor import PreprocessConfig, load_default_config
from core.ucs_loader import (
    UCSIndex,
    default_csv_path,
    default_extra_synonyms_path,
    load_ucs,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "real_filenames.txt"

# 测试 fixture 中的子类别名 → UCS 真实子类
_SUB_ALIASES = {
    "LAKE": "LAKESIDE",
    "DROPS": "DRIP",
}


# --------- shared resources ---------

@pytest.fixture(scope="module")
def ucs() -> UCSIndex:
    return load_ucs(default_csv_path(), default_extra_synonyms_path())


@pytest.fixture(scope="module")
def cfg() -> PreprocessConfig:
    return load_default_config()


# --------- fixture 解析 ---------

def _parse_fixture(p: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split("|")]
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


REAL_SAMPLES = _parse_fixture(FIXTURE)


# --------- 核心断言 ---------

def _assert_matches(c: Classification, exp_cat: str, exp_sub: str, fname: str) -> None:
    """根据 fixture 约定灵活判定分类是否正确。

    约定:
    - exp 为 ('misc', 'misc'): 必须落入 _misc
    - exp_sub = '_ROOT': 大类对即可(可以是根目录,也可以是该大类下任一子类)
    - exp_sub = 'ANY': 同上,大类对即可
    - 其余:子类必须严格匹配(按别名表后)
    - 源为 P1(CatID 命中):只要大类对即视为权威
    """
    if exp_cat == "misc" and exp_sub == "misc":
        assert c.is_misc, f"{fname}: 期望 _misc,实得 {c.category}/{c.sub_category}"
        return

    assert not c.is_misc, f"{fname}: 不应落入 _misc"
    assert c.category == exp_cat, (
        f"{fname}: 大类不符 期望={exp_cat} 实得={c.category}"
    )

    if c.source == Hit.P1_CAT_ID:
        return  # CatID 命中是 100% 权威
    if exp_sub in ("_ROOT", "ANY"):
        return
    target = _SUB_ALIASES.get(exp_sub, exp_sub)
    assert c.sub_category == target, (
        f"{fname}: 子类不符 期望={exp_sub}(→{target}) 实得={c.sub_category}"
    )


# --------- 真实样本逐条 ---------

@pytest.mark.parametrize("fname,exp_cat,exp_sub", REAL_SAMPLES)
def test_real_filename(ucs, cfg, fname, exp_cat, exp_sub):
    c = classify(fname, ucs, cfg)
    _assert_matches(c, exp_cat, exp_sub, fname)


# --------- 行为性断言 ---------

class TestPriorityOrder:
    def test_p1_cat_id_short_circuits(self, ucs, cfg):
        """文件名以 CatID 开头时,P1 立即返回,score=inf。"""
        c = classify("AMBLake_morning_birds.wav", ucs, cfg)
        assert c.source == Hit.P1_CAT_ID
        assert c.cat_id == "AMBLake"
        assert c.category == "AMBIENCE"

    def test_p2_subcategory_when_confident(self, ucs, cfg):
        c = classify("04 - Async Audio Sci-Fi Blaster.zip", ucs, cfg)
        assert c.source == Hit.P2_SUBCATEGORY
        assert c.category == "SCIFI"
        assert c.sub_category == "WEAPON"

    def test_p2_category_root_when_subs_close(self, ucs, cfg):
        c = classify("CS_Designed Cartoon SFX_01.wav", ucs, cfg)
        assert c.source == Hit.P2_CATEGORY_ROOT
        assert c.category == "CARTOON"
        assert c.sub_category is None

    def test_misc_for_garbage_filenames(self, ucs, cfg):
        for fname in ("untitled_001.wav", "track01.wav", "audio_002.wav", "untitled.wav"):
            c = classify(fname, ucs, cfg)
            assert c.is_misc, f"{fname}: 应落 _misc"

    def test_misc_for_no_signal(self, ucs, cfg):
        c = classify("xyzqqq.wav", ucs, cfg)
        assert c.is_misc


class TestRelativePath:
    def test_subcategory_path(self, ucs, cfg):
        c = classify("04 - Async Audio Sci-Fi Blaster.zip", ucs, cfg)
        assert to_relative_path(c) == "SCIFI/WEAPON"

    def test_root_path(self, ucs, cfg):
        c = classify("CS_Designed Cartoon SFX_01.wav", ucs, cfg)
        assert to_relative_path(c) == "CARTOON"

    def test_misc_path(self, ucs, cfg):
        c = classify("untitled_001.wav", ucs, cfg)
        assert to_relative_path(c) == "_misc"


class TestEdgeCases:
    def test_empty_filename(self, ucs, cfg):
        c = classify("", ucs, cfg)
        assert c.is_misc

    def test_extension_only(self, ucs, cfg):
        c = classify(".wav", ucs, cfg)
        assert c.is_misc

    def test_pure_chinese(self, ucs, cfg):
        c = classify("枪声_机枪连发.wav", ucs, cfg)
        assert c.category == "GUNS"

    def test_pure_english_concrete(self, ucs, cfg):
        c = classify("rain_thunder_forest.wav", ucs, cfg)
        assert c.category == "RAIN"
