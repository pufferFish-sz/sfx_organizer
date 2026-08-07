"""scanner 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.scanner import (
    ARCHIVE_EXTS,
    AUDIO_EXTS,
    FileKind,
    classify_extension,
    scan_to_list,
)


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """构造一棵典型素材树。"""
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested").mkdir()
    (tmp_path / "ignored_dir").mkdir()
    files = [
        "root_audio.wav",
        "root_pack.zip",
        "readme.txt",            # 非识别后缀
        "subdir/sub.mp3",
        "subdir/sub.7z",
        "subdir/nested/deep.flac",
        "subdir/.hidden.wav",    # 隐藏
        "Thumbs.db",             # 系统噪声
        ".DS_Store",
        "ignored_dir/ignore_me.wav",
    ]
    for f in files:
        (tmp_path / f).write_bytes(b"x")
    return tmp_path


class TestExtensionClassify:
    def test_audio_extensions(self):
        for ext in AUDIO_EXTS:
            assert classify_extension(Path(f"foo{ext}")) is FileKind.AUDIO
            assert classify_extension(Path(f"foo{ext.upper()}")) is FileKind.AUDIO

    def test_archive_extensions(self):
        for ext in ARCHIVE_EXTS:
            assert classify_extension(Path(f"foo{ext}")) is FileKind.ARCHIVE

    def test_unknown_returns_none(self):
        assert classify_extension(Path("foo.txt")) is None
        assert classify_extension(Path("foo")) is None


class TestScan:
    def test_finds_audio_and_archive(self, sample_tree):
        results = scan_to_list(sample_tree)
        names = {f.path.name for f in results}
        assert "root_audio.wav" in names
        assert "root_pack.zip" in names
        assert "sub.mp3" in names
        assert "sub.7z" in names
        assert "deep.flac" in names

    def test_skips_unknown_extensions(self, sample_tree):
        results = scan_to_list(sample_tree)
        names = {f.path.name for f in results}
        assert "readme.txt" not in names

    def test_skips_hidden_files(self, sample_tree):
        results = scan_to_list(sample_tree)
        names = {f.path.name for f in results}
        assert ".hidden.wav" not in names

    def test_skips_system_files(self, sample_tree):
        results = scan_to_list(sample_tree)
        names = {f.path.name for f in results}
        assert "Thumbs.db" not in names
        assert ".DS_Store" not in names

    def test_ignore_paths(self, sample_tree):
        ignored = sample_tree / "ignored_dir"
        results = scan_to_list(sample_tree, ignore_paths=[ignored])
        names = {f.path.name for f in results}
        assert "ignore_me.wav" not in names
        # 没被忽略的依然能找到
        assert "root_audio.wav" in names

    def test_correct_kind_attribution(self, sample_tree):
        results = scan_to_list(sample_tree)
        kinds = {f.path.name: f.kind for f in results}
        assert kinds["root_audio.wav"] is FileKind.AUDIO
        assert kinds["root_pack.zip"] is FileKind.ARCHIVE
        assert kinds["deep.flac"] is FileKind.AUDIO

    def test_sizes_populated(self, sample_tree):
        results = scan_to_list(sample_tree)
        for f in results:
            assert f.size >= 0   # 至少有 stat 成功

    def test_chinese_filename(self, tmp_path: Path):
        (tmp_path / "森林_鸟鸣.wav").write_bytes(b"x")
        results = scan_to_list(tmp_path)
        assert any(f.path.name == "森林_鸟鸣.wav" for f in results)

    def test_missing_root_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            scan_to_list(tmp_path / "no_such_dir")

    def test_root_must_be_dir(self, tmp_path: Path):
        f = tmp_path / "a_file.wav"
        f.write_bytes(b"x")
        with pytest.raises(NotADirectoryError):
            scan_to_list(f)
