"""organizer 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.classifier import Classification, Hit
from core.organizer import (
    OpMode,
    OrganizeOp,
    execute_op,
    plan_target_path,
    resolve_conflict,
)


# --------- helpers ---------

def _mk_classification(category, sub=None, source=Hit.P2_SUBCATEGORY) -> Classification:
    return Classification(
        category=category,
        sub_category=sub,
        cat_id=None,
        source=source,
        score=10.0,
    )


# --------- plan_target_path ---------

class TestPlanTargetPath:
    def test_subcategory_path(self, tmp_path):
        src = Path("foo.wav")
        c = _mk_classification("SCIFI", "WEAPON")
        target = plan_target_path(src, c, tmp_path)
        assert target == tmp_path / "SCIFI" / "WEAPON" / "foo.wav"

    def test_root_path_no_subcategory(self, tmp_path):
        src = Path("bar.zip")
        c = _mk_classification("CARTOON", None, source=Hit.P2_CATEGORY_ROOT)
        target = plan_target_path(src, c, tmp_path)
        assert target == tmp_path / "CARTOON" / "bar.zip"

    def test_misc_path(self, tmp_path):
        src = Path("junk.wav")
        c = Classification(None, None, None, source=Hit.MISC)
        target = plan_target_path(src, c, tmp_path)
        assert target == tmp_path / "_misc" / "junk.wav"

    def test_preserves_chinese_filename(self, tmp_path):
        src = Path("森林_鸟鸣.wav")
        c = _mk_classification("AMBIENCE", "FOREST")
        target = plan_target_path(src, c, tmp_path)
        assert target.name == "森林_鸟鸣.wav"


# --------- resolve_conflict ---------

class TestResolveConflict:
    def test_no_conflict_unchanged(self, tmp_path):
        target = tmp_path / "free.wav"
        assert resolve_conflict(target) == target

    def test_single_conflict_appends_1(self, tmp_path):
        existing = tmp_path / "taken.wav"
        existing.write_bytes(b"x")
        resolved = resolve_conflict(existing)
        assert resolved.name == "taken_1.wav"
        assert not resolved.exists()

    def test_multiple_conflicts(self, tmp_path):
        for n in ("taken.wav", "taken_1.wav", "taken_2.wav"):
            (tmp_path / n).write_bytes(b"x")
        resolved = resolve_conflict(tmp_path / "taken.wav")
        assert resolved.name == "taken_3.wav"

    def test_keeps_extension(self, tmp_path):
        (tmp_path / "song.mp3").write_bytes(b"x")
        resolved = resolve_conflict(tmp_path / "song.mp3")
        assert resolved.suffix == ".mp3"
        assert resolved.stem == "song_1"


# --------- execute_op ---------

@pytest.fixture
def src_file(tmp_path: Path) -> Path:
    p = tmp_path / "src" / "input.wav"
    p.parent.mkdir()
    p.write_bytes(b"hello world")
    return p


@pytest.fixture
def target_root(tmp_path: Path) -> Path:
    p = tmp_path / "target"
    p.mkdir()
    return p


class TestExecuteOp:
    def test_copy_creates_target_keeps_source(self, src_file, target_root):
        c = _mk_classification("SCIFI", "WEAPON")
        result = execute_op(OrganizeOp(src_file, c, target_root, OpMode.COPY))
        assert result.success
        assert src_file.exists(), "复制后源应保留"
        assert result.target == target_root / "SCIFI" / "WEAPON" / "input.wav"
        assert result.target.read_bytes() == b"hello world"

    def test_move_removes_source(self, src_file, target_root):
        c = _mk_classification("SCIFI", "WEAPON")
        result = execute_op(OrganizeOp(src_file, c, target_root, OpMode.MOVE))
        assert result.success
        assert not src_file.exists(), "移动后源应消失"
        assert result.target.exists()

    def test_dry_run_makes_no_changes(self, src_file, target_root):
        c = _mk_classification("SCIFI", "WEAPON")
        result = execute_op(OrganizeOp(src_file, c, target_root, OpMode.DRY_RUN))
        assert result.success
        assert src_file.exists()
        # 目标不会被创建
        assert not (target_root / "SCIFI" / "WEAPON").exists()
        # 但 result.target 给出了计划路径
        assert result.target == target_root / "SCIFI" / "WEAPON" / "input.wav"

    def test_creates_nested_directories(self, src_file, target_root):
        c = _mk_classification("FOOD & DRINK", "KITCHENWARE")
        result = execute_op(OrganizeOp(src_file, c, target_root, OpMode.COPY))
        assert result.success
        assert (target_root / "FOOD & DRINK" / "KITCHENWARE").is_dir()

    def test_conflict_auto_suffixes(self, src_file, target_root):
        c = _mk_classification("SCIFI", "WEAPON")
        # 先放一个同名文件占位
        dest_dir = target_root / "SCIFI" / "WEAPON"
        dest_dir.mkdir(parents=True)
        (dest_dir / "input.wav").write_bytes(b"old")

        result = execute_op(OrganizeOp(src_file, c, target_root, OpMode.COPY))
        assert result.success
        assert result.target.name == "input_1.wav"
        assert (dest_dir / "input.wav").read_bytes() == b"old"
        assert result.target.read_bytes() == b"hello world"

    def test_missing_source_reports_skipped(self, target_root):
        c = _mk_classification("SCIFI", "WEAPON")
        op = OrganizeOp(Path("/no/such/file.wav"), c, target_root, OpMode.COPY)
        result = execute_op(op)
        assert not result.success
        assert result.skipped_reason

    def test_misc_goes_to_misc_dir(self, src_file, target_root):
        c = Classification(None, None, None, source=Hit.MISC)
        result = execute_op(OrganizeOp(src_file, c, target_root, OpMode.COPY))
        assert result.success
        assert result.target.parent.name == "_misc"
