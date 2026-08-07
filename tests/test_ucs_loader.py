"""ucs_loader 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ucs_loader import (
    UCSEntry,
    UCSIndex,
    default_csv_path,
    default_extra_synonyms_path,
    load_ucs,
)


@pytest.fixture(scope="module")
def idx() -> UCSIndex:
    return load_ucs(default_csv_path(), default_extra_synonyms_path())


def test_loads_full_table(idx):
    # spec 1.2: 753 条目, 82 大类
    assert len(idx) == 753
    assert len(idx.categories()) >= 80


def test_entries_are_typed(idx):
    e = next(iter(idx.entries))
    assert isinstance(e, UCSEntry)
    assert e.category
    assert e.cat_id
    assert isinstance(e.synonyms, frozenset)


def test_cat_id_prefix_lookup(idx):
    # spec 2.5 P1: 文件名以 CatID 开头直接命中
    for cat_id, expect_cat, expect_sub in [
        ("AMBLake_morning_birds.wav", "AMBIENCE", "LAKESIDE"),
        ("GUNAntq_musket_fire.wav", "GUNS", "ANTIQUE"),
        ("WATRDrip_001.wav", "WATER", "DRIP"),
    ]:
        e = idx.find_cat_id_prefix(cat_id)
        assert e is not None, f"未命中 {cat_id}"
        assert e.category == expect_cat
        assert e.sub_category == expect_sub


def test_cat_id_prefix_longest_match(idx):
    # 长前缀优先 — 若有 "AMB" 和 "AMBLake",AMBLake_xxx 必须走后者
    e = idx.find_cat_id_prefix("AMBLake_xyz")
    assert e is not None and e.cat_id == "AMBLake"


def test_cat_id_prefix_no_match(idx):
    assert idx.find_cat_id_prefix("random_filename.wav") is None
    assert idx.find_cat_id_prefix("") is None


def test_synonyms_lowercased(idx):
    for e in idx.entries:
        for s in e.synonyms:
            assert s == s.lower(), f"未小写: {s} in {e.cat_id}"


def test_entries_in_category(idx):
    # 抽查几个常见大类有合理数量的子类
    for cat in ("AMBIENCE", "GUNS", "WATER", "MACHINES"):
        subs = idx.entries_in(cat)
        assert len(subs) > 0
        assert all(e.category == cat for e in subs)


def test_extra_synonyms_augment(idx):
    # SCIFI/COMPUTER 应通过 extra_synonyms 获得 'glitch'
    e = idx.by_cat_id["SCICmpt"]
    assert "glitch" in e.synonyms


def test_missing_csv_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_ucs(tmp_path / "does_not_exist.csv")


def test_extra_synonyms_ignores_unknown_keys(tmp_path: Path, capsys):
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"NONEXISTENT/SUB": ["foo"]}), encoding="utf-8")
    # 不应抛异常,仅记 warning
    idx2 = load_ucs(default_csv_path(), extra)
    assert len(idx2) == 753
