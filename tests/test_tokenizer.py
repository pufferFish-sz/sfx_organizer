"""tokenizer 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tokenizer import (
    load_zh_dict,
    map_zh_to_en,
    segment_chinese,
    split_mixed,
)

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def zh_dict() -> dict[str, list[str]]:
    return load_zh_dict(DATA / "zh_en_dict.json")


class TestSplitMixed:
    def test_basic_separators(self):
        assert split_mixed("foo-bar_baz.qux") == ["foo", "bar", "baz", "qux"]

    def test_camelcase(self):
        assert split_mixed("FooBarBaz") == ["Foo", "Bar", "Baz"]

    def test_acronym_then_word(self):
        # CSVReader → CSV, Reader
        assert split_mixed("CSVReader") == ["CSV", "Reader"]

    def test_cjk_mixed(self):
        result = split_mixed("计算机SFX故障")
        assert result == ["计算机", "SFX", "故障"]

    def test_empty(self):
        assert split_mixed("") == []
        assert split_mixed("   ") == []


class TestSegmentChinese:
    def test_longest_match(self, zh_dict):
        # 日常用品(4)+ 吹风机(3) 而不是单字
        seg = segment_chinese("日常用品吹风机", list(zh_dict.keys()), mode="longest")
        assert "日常用品" in seg
        assert "吹风机" in seg

    def test_no_dict_falls_back_to_char(self):
        # 无词典时,默认 jieba(若安装)或单字
        seg = segment_chinese("森林", [], mode="longest")
        # 至少不会爆,且 token 总长保留
        assert "".join(seg) == "森林"

    def test_empty(self, zh_dict):
        assert segment_chinese("", list(zh_dict.keys())) == []


class TestMapZhToEn:
    def test_known_word(self, zh_dict):
        result = map_zh_to_en("森林", zh_dict)
        assert "森林" in result
        assert "forest" in result

    def test_unknown_word_returns_self(self, zh_dict):
        result = map_zh_to_en("xxxyyy", zh_dict)
        assert result == ["xxxyyy"]


class TestLoadZhDict:
    def test_skips_underscore_keys(self, zh_dict):
        # 注释 key 以 _ 开头,应被忽略
        for k in zh_dict:
            assert not k.startswith("_")

    def test_returns_empty_on_missing(self, tmp_path):
        assert load_zh_dict(tmp_path / "no_such.json") == {}
