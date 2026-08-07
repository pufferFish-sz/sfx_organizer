"""disk_checker 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.disk_checker import (
    EXTRACT_BLOWUP,
    SpaceEstimate,
    WorkMode,
    check_space,
    estimate_required,
    same_disk,
)
from core.scanner import FileKind, ScannedFile


def _mk_files() -> list[ScannedFile]:
    return [
        ScannedFile(Path("a.wav"), 1_000, FileKind.AUDIO),
        ScannedFile(Path("b.mp3"), 2_000, FileKind.AUDIO),
        ScannedFile(Path("c.zip"), 10_000, FileKind.ARCHIVE),
        ScannedFile(Path("d.7z"), 5_000, FileKind.ARCHIVE),
    ]


class TestEstimate:
    def test_direct_mode_sums(self):
        assert estimate_required(_mk_files(), WorkMode.DIRECT) == 18_000

    def test_extract_mode_inflates_archives(self):
        expected = 1_000 + 2_000 + int(10_000 * EXTRACT_BLOWUP) + int(5_000 * EXTRACT_BLOWUP)
        assert estimate_required(_mk_files(), WorkMode.EXTRACT) == expected

    def test_empty_input(self):
        assert estimate_required([], WorkMode.DIRECT) == 0
        assert estimate_required([], WorkMode.EXTRACT) == 0


class TestCheckSpace:
    def test_existing_target(self, tmp_path):
        est = check_space(_mk_files(), tmp_path, WorkMode.DIRECT)
        assert est.required_bytes == 18_000
        assert est.available_bytes > 0   # tmp_path 实际可用,至少非负
        assert est.mode is WorkMode.DIRECT

    def test_target_does_not_exist_yet(self, tmp_path):
        nonexistent = tmp_path / "future_subdir" / "deeper"
        est = check_space(_mk_files(), nonexistent, WorkMode.DIRECT)
        assert est.available_bytes > 0   # 回溯到 tmp_path 取空间

    def test_ok_flag(self, tmp_path):
        est = check_space([ScannedFile(Path("a"), 1, FileKind.AUDIO)], tmp_path, WorkMode.DIRECT)
        assert est.ok is True

    def test_shortfall(self):
        # 构造 require > available 的 estimate 直接验证
        e = SpaceEstimate(required_bytes=100, available_bytes=30, mode=WorkMode.DIRECT)
        assert not e.ok
        assert e.shortfall_bytes == 70

    def test_human_summary_format(self):
        e = SpaceEstimate(required_bytes=2048, available_bytes=10_000, mode=WorkMode.DIRECT)
        s = e.human_summary()
        assert "需要" in s and "可用" in s


class TestDirectMoveSameDisk:
    """同盘 MOVE 是 rename — 几乎不占目标盘空间。这是用户 3.72T 总量 / 1.05T
    可用场景下能跑 DIRECT+MOVE 的关键。"""

    def test_direct_move_same_disk_returns_zero(self):
        files = _mk_files()
        required = estimate_required(
            files, WorkMode.DIRECT,
            move_mode=True, same_disk_as_source=True,
        )
        assert required == 0, "同盘 MOVE 应不占用目标盘空间"

    def test_direct_move_cross_disk_full_estimate(self):
        files = _mk_files()
        required = estimate_required(
            files, WorkMode.DIRECT,
            move_mode=True, same_disk_as_source=False,
        )
        # 异盘 MOVE = copy+delete,目标盘仍需要全量
        assert required == sum(f.size for f in files)

    def test_direct_copy_same_disk_unaffected(self):
        # COPY 不管同不同盘都要全量
        files = _mk_files()
        required = estimate_required(
            files, WorkMode.DIRECT,
            move_mode=False, same_disk_as_source=True,
        )
        assert required == sum(f.size for f in files)


class TestStreamingEstimate:
    """流式 EXTRACT 的空间估算 — 关键修复让 4T 盘装 3T 资源可行。"""

    def test_streaming_copy_includes_full_blowup_plus_tmp_peak(self):
        files = _mk_files()  # 2 audio (3000) + 2 archives (15000)
        max_arc = 10_000  # 文件 c.zip
        archive_total = 15_000
        audio_total = 3_000
        required = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=False, same_disk_as_source=False,
        )
        expected = int(audio_total + archive_total * EXTRACT_BLOWUP + max_arc * EXTRACT_BLOWUP)
        assert required == expected

    def test_streaming_move_same_disk_credits_source_release(self):
        """MOVE 同盘:源 zip 解一个删一个,净增量 = archive * 0.8 + max * 1.8"""
        files = _mk_files()
        max_arc = 10_000
        archive_total = 15_000
        audio_total = 3_000
        required = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=True, same_disk_as_source=True,
        )
        expected = int(
            audio_total + archive_total * (EXTRACT_BLOWUP - 1.0)
            + max_arc * EXTRACT_BLOWUP
        )
        assert required == expected

    def test_streaming_move_cross_disk_no_credit(self):
        """MOVE 但异盘:目标盘空间和源盘释放无关,仍按 COPY 估算"""
        files = _mk_files()
        a = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=True, same_disk_as_source=False,
        )
        b = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=False, same_disk_as_source=False,
        )
        assert a == b

    def test_streaming_tmp_off_target_disk_no_peak(self):
        """tmp 不在目标盘(常见:TEMP 在 C:,目标在 E:)时 tmp peak 不算进目标"""
        files = _mk_files()
        with_peak = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=True, same_disk_as_source=True,
            tmp_on_target_disk=True,
        )
        no_peak = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=True, same_disk_as_source=True,
            tmp_on_target_disk=False,
        )
        max_arc = max(f.size for f in files if f.kind is FileKind.ARCHIVE)
        assert with_peak - no_peak == int(max_arc * EXTRACT_BLOWUP)


class TestSameDisk:
    def test_same_dir_is_same_disk(self, tmp_path: Path):
        assert same_disk(tmp_path, tmp_path) is True

    def test_two_subdirs_same_disk(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir(); b.mkdir()
        assert same_disk(a, b) is True

    def test_nonexistent_path_backs_off_to_ancestor(self, tmp_path: Path):
        # Windows 上 drive 即使路径不存在也有效
        assert same_disk(tmp_path / "future", tmp_path) is True
