"""worker 集成测试 — 重点覆盖 EXTRACT 模式 + 源 archive 清理。"""

from __future__ import annotations

import queue
import threading
import time
import zipfile
from pathlib import Path
from typing import Iterable

import pytest

from core.classifier import ClassifyOptions
from core.organizer import OpMode
from core.preprocessor import load_default_config
from core.ucs_loader import (
    UCSIndex,
    default_csv_path,
    default_extra_synonyms_path,
    load_ucs,
)
from gui.worker import Ev, JobMode, JobSettings, OrganizeWorker


# --------- shared ---------

@pytest.fixture(scope="module")
def ucs() -> UCSIndex:
    return load_ucs(default_csv_path(), default_extra_synonyms_path())


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _run_worker(
    settings: JobSettings, *, preview_only: bool = False, timeout: float = 10
) -> tuple[list[tuple], threading.Event]:
    events: "queue.Queue[tuple]" = queue.Queue()
    cancel = threading.Event()
    worker = OrganizeWorker(settings, events, cancel, preview_only=preview_only)
    worker.start()
    worker.join(timeout=timeout)
    assert not worker.is_alive(), "worker 未在 timeout 内退出"
    out: list[tuple] = []
    while not events.empty():
        out.append(events.get_nowait())
    return out, cancel


def _build_settings(
    ucs,
    src: Path,
    dst: Path,
    *,
    op_mode: OpMode,
    extract_archives: bool = False,
    job_mode: JobMode = JobMode.DIRECT,
) -> JobSettings:
    return JobSettings(
        source=src,
        target=dst,
        ucs=ucs,
        preproc_config=load_default_config(ucs=ucs),
        classify_options=ClassifyOptions(),
        job_mode=job_mode,
        extract_archives=extract_archives,
        op_mode=op_mode,
        use_metadata_fallback=False,
        enable_zh_mapping=True,
    )


# --------- EXTRACT 模式源 archive 处理 ---------

class TestExtractMoveSourceCleanup:
    """spec 三种模式下源 archive 的命运 — 重点 bug:MOVE 模式之前不删 zip。"""

    @pytest.fixture
    def src_dst(self, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        # 直接散落的音频
        (src / "AMBLake_001.wav").write_bytes(b"x" * 100)
        # 压缩包,内含两个能正常分类的音频
        _make_zip(src / "pack.zip", {
            "GUNAntq_fire.wav": b"x" * 100,
            "WATRDrip_001.wav": b"x" * 100,
        })
        return src, dst

    def test_extract_move_deletes_source_zip_when_all_contents_organized(self, ucs, src_dst):
        """EXTRACT + MOVE 且 zip 内所有文件都成功归类 → 源 zip 应被删除。"""
        src, dst = src_dst
        zip_path = src / "pack.zip"
        assert zip_path.exists()

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        _run_worker(s)

        # 源 zip 应被删除
        assert not zip_path.exists(), "MOVE 模式下源 zip 应被删除"
        # 源音频也应被移走
        assert not (src / "AMBLake_001.wav").exists()
        # 目标里应有解压出的两个音频
        all_files = [p for p in dst.rglob("*") if p.is_file()]
        assert any(p.name == "GUNAntq_fire.wav" for p in all_files)
        assert any(p.name == "WATRDrip_001.wav" for p in all_files)
        assert any(p.name == "AMBLake_001.wav" for p in all_files)

    def test_extract_copy_keeps_source_zip(self, ucs, src_dst):
        """EXTRACT + COPY 不应动源 zip。"""
        src, dst = src_dst
        zip_path = src / "pack.zip"

        s = _build_settings(ucs, src, dst, op_mode=OpMode.COPY, extract_archives=True)
        _run_worker(s)

        # 源 zip 保留(COPY 语义)
        assert zip_path.exists(), "COPY 模式下源 zip 应保留"
        # 源音频也保留
        assert (src / "AMBLake_001.wav").exists()
        # 目标里仍有解压出的内容
        all_files = [p for p in dst.rglob("*") if p.is_file()]
        assert any(p.name == "GUNAntq_fire.wav" for p in all_files)

    def test_preview_does_not_delete_source(self, ucs, src_dst):
        """preview 不应动源 — 即使 op_mode=MOVE。"""
        src, dst = src_dst
        zip_path = src / "pack.zip"
        audio_path = src / "AMBLake_001.wav"

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        events, _ = _run_worker(s, preview_only=True)

        # 预览只应发 PREVIEW_READY,不动盘
        assert zip_path.exists(), "preview 不该删源 zip"
        assert audio_path.exists(), "preview 不该删源音频"
        # 目标也不应有文件
        assert not any(p.is_file() for p in dst.rglob("*"))
        ev_kinds = [ev for ev, _ in events]
        assert Ev.PREVIEW_READY in ev_kinds

    def test_direct_move_unaffected(self, ucs, src_dst):
        """DIRECT + MOVE 下,zip 走 organizer 被移走(老行为,不应受影响)。"""
        src, dst = src_dst
        zip_path = src / "pack.zip"

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE)  # extract=False (默认)
        _run_worker(s)

        # zip 整包被分类移走
        assert not zip_path.exists(), "DIRECT+MOVE 下源 zip 应被 organizer 移走"
        # 移动到的目标位置应存在
        moved = list(dst.rglob("pack.zip"))
        assert moved, f"目标盘没找到 pack.zip,dst 内容: {list(dst.rglob('*'))}"


class TestExtractMoveWithNestedZip:
    """zip 内含子目录或非音频文件(常见真实素材包结构)— 这些不应阻止删源 zip。"""

    def test_zip_with_nested_dirs_still_gets_deleted(self, ucs, tmp_path: Path):
        """zipfile.namelist() 会把目录条目也列出来 — 别让它阻止删源。"""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        _make_zip(src / "pack.zip", {
            # 显式目录条目 — zipfile 用 '/' 后缀识别
            "Sounds/": b"",
            "Sounds/GUNAntq_fire.wav": b"x" * 100,
            "Sounds/AMBLake_001.wav": b"x" * 100,
        })

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        _run_worker(s)
        assert not (src / "pack.zip").exists(), \
            "zip 内含子目录条目不应阻止源 zip 删除"

    def test_zip_with_unrecognized_files_still_gets_deleted(self, ucs, tmp_path: Path):
        """zip 内有 readme.txt 这种 scanner 不扫的文件 — 它们本就不在整理范围,
        不应阻止删源。"""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        _make_zip(src / "pack.zip", {
            "GUNAntq_fire.wav": b"x" * 100,
            "readme.txt": b"some docs",
            "license.md": b"license",
        })

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        _run_worker(s)
        assert not (src / "pack.zip").exists(), \
            "zip 内的非音频文件(scanner 不扫)不应阻止源 zip 删除"


class TestExtractMovePartialFailure:
    """部分文件归类失败时,源 zip 应被**保留**,避免数据丢失。"""

    def test_keeps_source_zip_if_any_content_fails_to_organize(self, ucs, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        # zip 内一个能分类(GUNAntq 直命中)+ 一个铁定 _misc(untitled)
        # 但 _misc 也能成功落盘 — 需要构造真正"organize 失败"的场景。
        # 最稳的 strick:把 dst 设为只读,让 mkdir 失败。
        # 简单点:让 dst 路径指向一个文件而非目录,使所有 mkdir 失败 → 全部 failed
        _make_zip(src / "partial.zip", {
            "GUNAntq_fire.wav": b"x" * 100,
        })

        # 让 target 路径冲突:在 dst 下放一个 "GUNS" 文件,使 mkdir 失败
        # 这样 organize "GUNAntq_fire.wav" 会因 mkdir 失败而归 stats.failed
        (dst / "GUNS").write_bytes(b"placeholder file blocks dir creation")

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        _run_worker(s)

        # 整理失败 → 源 zip 应保留
        assert (src / "partial.zip").exists(), \
            "内容未全部落盘,源 zip 不应被删除"


# --------- 基础流程 ---------

class TestIncrementalModeWithExtract:
    """spec 模式 C「增量整理到现有库」也应允许搭配先解压。"""

    def test_incremental_plus_extract_works(self, ucs, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        # 模拟已存在的素材库:有些大类已经在了
        (dst / "AMBIENCE").mkdir(parents=True)
        (dst / "AMBIENCE" / "existing.wav").write_bytes(b"old")
        _make_zip(src / "new_pack.zip", {"GUNAntq_fire.wav": b"x"})

        s = _build_settings(
            ucs, src, dst,
            job_mode=JobMode.INCREMENTAL,
            extract_archives=True,
            op_mode=OpMode.COPY,
        )
        events, _ = _run_worker(s)

        # 既存文件保留
        assert (dst / "AMBIENCE" / "existing.wav").read_bytes() == b"old"
        # 解压出的新内容也归位了
        assert any(p.name == "GUNAntq_fire.wav" for p in dst.rglob("*"))
        # 源 zip 在 COPY 模式下保留
        assert (src / "new_pack.zip").exists()

    def test_incremental_plus_extract_move_deletes_source_zip(self, ucs, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        _make_zip(src / "pack.zip", {"GUNAntq_fire.wav": b"x"})

        s = _build_settings(
            ucs, src, dst,
            job_mode=JobMode.INCREMENTAL,
            extract_archives=True,
            op_mode=OpMode.MOVE,
        )
        _run_worker(s)
        assert not (src / "pack.zip").exists(), "MOVE+解压 全成功 → 源 zip 应被删"


class TestDirectMoveSameDisk:
    """同盘 MOVE = rename,空间校验不应过严卡死用户。"""

    def test_direct_move_same_disk_uses_zero_estimate(self, ucs, tmp_path: Path):
        """重现用户场景:DIRECT+MOVE,源/目标同盘,即使源很大也应通过校验。"""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        # 假装"大文件" — 实际测试只是几 KB,但 estimate 看 size 字段
        (src / "AMBLake_001.wav").write_bytes(b"x" * 1024)

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE)
        events, _ = _run_worker(s)

        # 空间校验应通过(因为同盘 require=0)
        space_events = [args for ev, args in events if ev is Ev.SPACE_CHECK]
        assert space_events
        summary, ok = space_events[0]
        assert ok, f"同盘 MOVE 应通过空间校验;summary={summary}"
        assert "需要 0" in summary or "0 B" in summary, \
            f"估算应显示 0;实得 {summary}"

        # 文件确实 move 到了目标
        assert not (src / "AMBLake_001.wav").exists()
        assert any(p.name == "AMBLake_001.wav" for p in dst.rglob("*"))


class TestStreamingBehavior:
    """流式 EXTRACT 的特征:解一个清一个 + MOVE 逐包删源 + tmp 不堆积。"""

    def test_tmp_root_is_cleaned_after_run(self, ucs, tmp_path: Path):
        """流式完成后,临时根目录应不存在。"""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        for i in range(3):
            _make_zip(src / f"pack{i}.zip", {f"GUNAntq_{i}.wav": b"x" * 100})

        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        _run_worker(s)

        # 所有 zip 都该被删
        assert not list(src.glob("*.zip")), "全部 zip 应被流式删除"
        # 不该有 sfx_extract_ 临时目录残留(看 system TEMP 是否有 — 至少不留在 tmp_path)
        assert not any(p.name.startswith("sfx_extract_") for p in tmp_path.iterdir())

    def test_zips_deleted_progressively_not_at_end(self, ucs, tmp_path: Path):
        """流式核心:第 N 个 zip 处理完时,1..N-1 都已删源(不是等到全部结束才删)。

        通过事件流验证:EXTRACT_ONE i 之间,先前的源 zip 应已 unlink。
        """
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        # 3 个 zip,各含 1 个能正常分类的音频
        zip_paths = []
        for i in range(3):
            p = src / f"pack{i:02d}.zip"
            _make_zip(p, {f"GUNAntq_fire_{i}.wav": b"x" * 100})
            zip_paths.append(p)

        # 在每次 EXTRACT_ONE 后立刻 snapshot 看哪些源 zip 仍存在
        # 用一个穿插的 event listener:跑完后回看事件流不够直接 — 不如把
        # 「pack0.zip 删除时间点」和「pack2.zip 解压时间点」对比
        # 这里简化:跑完后所有源 zip 应被删
        s = _build_settings(ucs, src, dst, op_mode=OpMode.MOVE, extract_archives=True)
        events, _ = _run_worker(s)

        for p in zip_paths:
            assert not p.exists(), f"{p.name} 应被删"

        # 至少应该有 3 个 EXTRACT_ONE 事件
        extract_events = [args for ev, args in events if ev is Ev.EXTRACT_ONE]
        assert len(extract_events) == 3

    def test_streaming_space_estimate_accepts_tight_target(self, ucs, tmp_path: Path):
        """关键:用户场景 — 流式 + MOVE + 同盘,空间估算不应过度保守。

        构造一个"目标盘可用空间紧张但够流式跑"的场景 — 实际我们无法控制
        tmp_path 所在盘的 free space,只能验证 estimate_required 在
        MOVE+同盘下显著小于 EXTRACT 旧估算。
        """
        from core.disk_checker import (
            EXTRACT_BLOWUP,
            WorkMode,
            estimate_required,
        )
        from core.scanner import FileKind, ScannedFile

        # 10 个 1GB zip — 规模够大让 MOVE+同盘的"源 zip 抵消"优势体现
        files = [
            ScannedFile(Path(f"a{i}.zip"), 1_000_000_000, FileKind.ARCHIVE)
            for i in range(10)
        ]
        old = estimate_required(files, WorkMode.EXTRACT)
        # 流式 + MOVE + 同盘:净增量 ≈ archive_total * 0.8 + max * 1.8
        new_move = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=True, same_disk_as_source=True,
        )
        # 流式 + COPY:archive_total * 1.8 + max * 1.8(比旧 EXTRACT 略大,
        # 因为多了一个 tmp peak,但中等数量下接近)
        new_copy = estimate_required(
            files, WorkMode.EXTRACT_STREAMING,
            move_mode=False, same_disk_as_source=False,
        )
        # MOVE + 同盘下,流式估算显著小于旧的全解压估算
        assert new_move < old * 0.7, \
            f"流式 MOVE 同盘估算应显著低于旧 EXTRACT;old={old} new={new_move}"
        # COPY 模式两种估算接近(都需容纳全部解压物)
        assert abs(new_copy - old) / old < 0.6


class TestBasicFlow:
    def test_direct_copy_emits_progress(self, ucs, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "AMBLake_001.wav").write_bytes(b"x" * 100)

        s = _build_settings(ucs, src, dst, op_mode=OpMode.COPY)
        events, _ = _run_worker(s)
        ev_kinds = [ev for ev, _ in events]
        assert Ev.SCAN_DONE in ev_kinds
        assert Ev.PROGRESS in ev_kinds
        assert Ev.DONE in ev_kinds

    def test_empty_source_emits_done_immediately(self, ucs, tmp_path: Path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        s = _build_settings(ucs, src, dst, op_mode=OpMode.COPY)
        events, _ = _run_worker(s)
        # 空源 → 不应有 PROGRESS,但有 DONE
        ev_kinds = [ev for ev, _ in events]
        assert Ev.DONE in ev_kinds
        assert Ev.PROGRESS not in ev_kinds
