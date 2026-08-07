"""preprocessor 单元测试 — spec 2.3 节九步管线。"""

from __future__ import annotations

import pytest

from core.preprocessor import (
    PreprocessConfig,
    load_default_config,
    preprocess,
)


@pytest.fixture(scope="module")
def cfg() -> PreprocessConfig:
    return load_default_config()


# --------- 步骤性测试 ---------

class TestSteps:
    def test_strip_extension(self, cfg):
        r = preprocess("foo.wav", cfg)
        assert "foo" in r.cat_id_candidate

    def test_strip_leading_number(self, cfg):
        for fname in ("04 - Foo Bar.wav", "01_Foo.zip", "12. Foo.wav"):
            r = preprocess(fname, cfg)
            assert not r.cat_id_candidate.startswith(("04", "01", "12"))
            assert "Foo" in r.cat_id_candidate

    def test_cn_brackets_kept(self, cfg):
        r = preprocess("Foo【森林】Bar.wav", cfg)
        assert "森林" in r.tokens or "forest" in r.tokens

    def test_en_brackets_dropped(self, cfg):
        r = preprocess("Foo [WAV] Bar.wav", cfg)
        assert "wav" not in r.tokens  # 同时被停用词过滤

    def test_camelcase_split(self, cfg):
        r = preprocess("AsyncAudioBlaster.wav", cfg)
        assert "blaster" in r.tokens

    def test_cjk_latin_split(self, cfg):
        # 同一片段中的中英文应被分开
        r = preprocess("Computer计算机SFX.wav", cfg)
        assert "computer" in r.tokens
        assert "故障" not in r.tokens  # 不出现没在原文的词
        assert any("comput" in t or t == "computer" for t in r.tokens)

    def test_stopwords_filtered(self, cfg):
        r = preprocess("Audio Sample Pack Library SFX.wav", cfg)
        assert "audio" not in r.tokens
        assert "sample" not in r.tokens
        assert "pack" not in r.tokens
        assert "sfx" not in r.tokens

    def test_brand_names_filtered(self, cfg):
        r = preprocess("Avosound Beatsburg Forest.wav", cfg)
        assert "avosound" not in r.tokens
        assert "beatsburg" not in r.tokens
        assert "forest" in r.tokens

    def test_short_ascii_dropped(self, cfg):
        # PC 是 2 字符,应被过滤
        r = preprocess("Computer PC Tech.wav", cfg)
        assert "pc" not in r.tokens
        assert "computer" in r.tokens

    def test_protected_short_token_kept(self, cfg):
        # UI 是 USER INTERFACE 的 CatShort,虽 2 字符也必须保留
        # load_default_config() 默认会注入 {"ui"}
        r = preprocess("UI Deny.wav", cfg)
        assert "ui" in r.tokens, f"'ui' 必须保留;实得 {r.tokens}"
        assert "deny" in r.tokens

    def test_protected_short_token_full_set_from_ucs(self):
        # 传入 UCS 时,所有 CatShort 都应被保护
        from core.preprocessor import load_default_config
        from core.ucs_loader import (
            default_csv_path,
            default_extra_synonyms_path,
            load_ucs,
        )
        ucs = load_ucs(default_csv_path(), default_extra_synonyms_path())
        cfg2 = load_default_config(ucs=ucs)
        # IR 也是 2 字符 CatShort,应保留
        assert "ui" in cfg2.protected_short_tokens
        assert "ir" in cfg2.protected_short_tokens
        r = preprocess("IR Reverb.wav", cfg2)
        assert "ir" in r.tokens

    def test_chinese_to_english_mapping(self, cfg):
        r = preprocess("森林雨声.wav", cfg)
        assert "forest" in r.tokens or "woods" in r.tokens
        assert "rain" in r.tokens or "rainfall" in r.tokens

    def test_dedupe_and_lowercase(self, cfg):
        r = preprocess("Forest forest FOREST.wav", cfg)
        assert r.tokens.count("forest") == 1


# --------- 端到端真实样本 ---------

class TestRealSamples:
    @pytest.mark.parametrize("fname,expect_any", [
        ("04 - Async Audio Sci-Fi Blaster.zip", {"blaster"}),
        ("40 - Future Samples Computer SFX [WAV] 计算机科技故障音效FX采样.zip",
         {"computer", "glitch"}),
        ("01 - Beatsburg Machinery 日常用品吹风机等音效.zip",
         {"machinery", "dryer", "blower"}),
        ("CS_Designed Cartoon SFX_01.wav", {"cartoon"}),
        ("雨声_暴风雨_森林.wav", {"rain", "storm", "forest"}),
        ("脚步声_木地板.wav", {"footstep", "wood"}),
    ])
    def test_essential_tokens_present(self, cfg, fname, expect_any):
        r = preprocess(fname, cfg)
        assert expect_any.issubset(set(r.tokens)), (
            f"{fname}\n  期望: {expect_any}\n  实得: {r.tokens}"
        )
