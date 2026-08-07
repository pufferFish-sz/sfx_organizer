"""后台 worker —— 扫描 / 解压 / 分类 / 整理在线程内串行执行。

UI 线程通过 ``event_queue`` 拉事件做进度刷新;通过 ``cancel_event`` 请求取消。
"""

from __future__ import annotations

import logging
import queue
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from core.classifier import Classification, ClassifyOptions, classify
from core.disk_checker import WorkMode, check_space, same_disk
from core.extractor import extract
from core.organizer import OpMode, OrganizeOp, execute_op, plan_target_path
from core.preprocessor import PreprocessConfig
from core.scanner import FileKind, ScannedFile, classify_extension, scan_to_list
from core.ucs_loader import UCSIndex

from .preview_dialog import PreviewRow

logger = logging.getLogger(__name__)


class JobMode(str, Enum):
    """工作流位置 — 与「是否解压」正交。"""
    DIRECT = "direct"            # 模式 A:新建整理(目标可为空)
    INCREMENTAL = "incremental"  # 模式 C:合并到现有素材库


@dataclass
class JobSettings:
    """一次「开始整理」所需的全部参数。"""

    source: Path
    target: Path
    ucs: UCSIndex
    preproc_config: PreprocessConfig
    classify_options: ClassifyOptions = field(default_factory=ClassifyOptions)
    job_mode: JobMode = JobMode.DIRECT
    # 压缩包处理:False 按文件名分类整包,True 先解压再分类内容(spec 模式 B 行为)。
    # 与 job_mode 正交 — 增量模式也可以选先解压。
    extract_archives: bool = False
    op_mode: OpMode = OpMode.COPY
    use_metadata_fallback: bool = True
    enable_zh_mapping: bool = True   # GUI 选项;关闭时清空 zh_dict
    password_provider: Optional[Callable] = None   # extractor PasswordProvider


# --------- 事件类型 ---------

class Ev(str, Enum):
    SCAN_START = "scan_start"          # () — 扫描开始
    SCAN_DONE = "scan_done"            # (n_files: int, total_bytes: int)
    SPACE_CHECK = "space_check"        # (estimate_str: str, ok: bool)
    EXTRACT_START = "extract_start"    # (n_archives: int)
    EXTRACT_ONE = "extract_one"        # (i: int, name: str, ok: bool, err: str|None)
    EXTRACT_DONE = "extract_done"      # ()
    PROGRESS = "progress"              # (i: int, total: int, source_name: str, target_rel: str)
    DONE = "done"                      # (stats: dict)
    ERROR = "error"                    # (msg: str)
    CANCELLED = "cancelled"            # ()
    PREVIEW_READY = "preview_ready"    # (rows: list[PreviewRow])


@dataclass
class JobStats:
    total: int = 0
    moved_or_copied: int = 0
    failed: int = 0
    misc: int = 0
    by_category: dict[str, int] = field(default_factory=dict)


# --------- Worker ---------

class OrganizeWorker(threading.Thread):
    """整理流程后台线程。事件全部走 event_queue,UI 线程定时 poll。"""

    def __init__(
        self,
        settings: JobSettings,
        event_queue: "queue.Queue[tuple]",
        cancel_event: threading.Event,
        *,
        preview_only: bool = False,
    ) -> None:
        super().__init__(daemon=True, name="sfx-worker")
        self.settings = settings
        self.events = event_queue
        self.cancel_event = cancel_event
        self.preview_only = preview_only

    # -------- main --------

    def run(self) -> None:
        try:
            self._run_inner()
        except Exception as e:  # noqa: BLE001 — 兜底
            logger.exception("worker 顶层异常")
            self._emit(Ev.ERROR, str(e))

    def _run_inner(self) -> None:
        s = self.settings
        tmp_root: Optional[Path] = None

        # 1. 扫描源 — 始终把目标加入 ignore,防止源/目标嵌套时无限递归
        self._emit(Ev.SCAN_START)
        files = scan_to_list(s.source, ignore_paths=[s.target])
        total_bytes = sum(f.size for f in files)
        self._emit(Ev.SCAN_DONE, len(files), total_bytes)

        if not files:
            self._emit(Ev.DONE, JobStats())
            return
        if self._cancelled():
            return

        # 2. 空间校验 — 关键优化:
        #    - DIRECT + MOVE + 同盘:rename,几乎零占用
        #    - EXTRACT_STREAMING + MOVE + 同盘:源 zip 解一个删一个,净增量小
        #    - 系统 TEMP 不在目标盘时,EXTRACT 的 tmp peak 不算进目标
        sd = same_disk(s.source, s.target)
        tmp_same = same_disk(tempfile.gettempdir(), s.target)
        move = s.op_mode is OpMode.MOVE
        if s.extract_archives:
            est = check_space(
                files, s.target, WorkMode.EXTRACT_STREAMING,
                move_mode=move, same_disk_as_source=sd,
                tmp_on_target_disk=tmp_same,
            )
        else:
            est = check_space(
                files, s.target, WorkMode.DIRECT,
                move_mode=move, same_disk_as_source=sd,
            )
        self._emit(Ev.SPACE_CHECK, est.human_summary(), est.ok)
        if not est.ok:
            self._emit(Ev.ERROR, f"目标盘空间不足:{est.human_summary()}")
            return

        # 3. 分流:audios 直接整理;archives 若 extract_archives=True 则流式
        audios = [f for f in files if f.kind is FileKind.AUDIO]
        archives = [f for f in files if f.kind is FileKind.ARCHIVE]
        direct_items: list[ScannedFile] = list(audios)
        if not s.extract_archives:
            direct_items.extend(archives)

        if s.extract_archives and archives:
            tmp_root = Path(tempfile.mkdtemp(prefix="sfx_extract_"))

        # 进度估算:audio + archive 各 1 单位;每解开一个 archive 时再按内含
        # 文件数动态修正(只增不减),始终单调
        estimated_total = len(audios) + len(archives)
        processed = 0
        final_stats = JobStats()
        preview_rows: list[PreviewRow] = []

        # 4a. 整理散落音频 + (extract=False 时)整包 archive
        if direct_items:
            cls = self._classify_all(direct_items)
            if self._cancelled():
                self._cleanup_tmp(tmp_root); return
            sub_stats, _ = self._process_batch(
                direct_items, cls, preview_rows,
                idx_offset=processed, total_ref=lambda: estimated_total,
            )
            processed += len(direct_items)
            _merge_stats_into(final_stats, sub_stats)

        # 4b. 流式处理 archive(extract=True)
        if s.extract_archives and archives and not self._cancelled():
            self._emit(Ev.EXTRACT_START, len(archives))
            for i, arc in enumerate(archives):
                if self._cancelled(): break
                sub = tmp_root / f"{i:04d}_{arc.path.stem}"
                try:
                    result = extract(
                        arc.path, sub,
                        password_provider=s.password_provider,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("解压异常: %s", arc.path)
                    self._emit(Ev.EXTRACT_ONE, i, arc.path.name, False, str(e))
                    processed += 1
                    continue
                self._emit(Ev.EXTRACT_ONE, i, arc.path.name, result.success, result.error)
                if not result.success:
                    logger.warning("解压失败: %s — %s", arc.path.name, result.error)
                    processed += 1
                    continue

                # 扫子 tmp 的整理对象
                inside = scan_to_list(sub)
                if not inside:
                    # zip 内全是 scanner 不识别的文件或空 — 无需整理
                    # MOVE 模式仍可删源(没东西要整,源 zip 当作"成功处理"清掉)
                    if not self.preview_only and s.op_mode is OpMode.MOVE:
                        self._unlink_archive(arc.path)
                    shutil.rmtree(sub, ignore_errors=True)
                    processed += 1
                    continue

                # 修正进度:这 archive 内有 len(inside) 个文件而不是 1 个
                estimated_total += len(inside) - 1

                inside_cls = self._classify_all(inside)
                if self._cancelled():
                    shutil.rmtree(sub, ignore_errors=True)
                    break

                sub_stats, sub_success = self._process_batch(
                    inside, inside_cls, preview_rows,
                    idx_offset=processed, total_ref=lambda: estimated_total,
                )
                processed += len(inside)
                _merge_stats_into(final_stats, sub_stats)

                # 删源 zip:仅当 (a) 实际执行(非预览) (b) MOVE 模式
                # (c) 内容文件全部成功落盘
                if not self.preview_only and s.op_mode is OpMode.MOVE:
                    trackable = self._trackable_extracted(result.extracted)
                    unfinished = [p for p in trackable if p not in sub_success]
                    if not unfinished:
                        self._unlink_archive(arc.path)
                    else:
                        logger.warning(
                            "保留源压缩包 %s — 内有 %d 个文件未成功整理",
                            arc.path.name, len(unfinished),
                        )

                # 立即清理子 tmp 释放空间 — 流式的精髓
                shutil.rmtree(sub, ignore_errors=True)

            self._emit(Ev.EXTRACT_DONE)

        # 5. 收尾
        if self.preview_only:
            self._emit(Ev.PREVIEW_READY, preview_rows)
        self._cleanup_tmp(tmp_root)
        self._emit(Ev.DONE, final_stats)

    # -------- 步骤 --------

    def _process_batch(
        self,
        files: list[ScannedFile],
        classifications: list[Classification],
        preview_rows: list[PreviewRow],
        *,
        idx_offset: int,
        total_ref,
    ) -> tuple[JobStats, set[Path]]:
        """统一处理一批文件 — 预览或实际落盘。

        Args:
            total_ref: 取当前 total 的 callable,流式过程中 total 会动态增加
        """
        stats = JobStats(total=len(files))
        success: set[Path] = set()
        s = self.settings
        for i, (f, c) in enumerate(zip(files, classifications)):
            if self._cancelled():
                break
            target_path = plan_target_path(f.path, c, s.target)
            target_rel = str(target_path.relative_to(s.target)).replace("\\", "/")
            idx = idx_offset + i + 1

            if self.preview_only:
                preview_rows.append(PreviewRow(
                    source=f.path, size_bytes=f.size,
                    target_rel=target_rel, source_label=_label(c),
                ))
                self._emit(Ev.PROGRESS, idx, total_ref(), f.path.name, target_rel)
                success.add(_safe_resolve(f.path))
                stats.moved_or_copied += 1
                continue

            op = OrganizeOp(
                source=f.path, classification=c,
                target_root=s.target, mode=s.op_mode,
            )
            result = execute_op(op)
            actual_rel = (
                str(result.target.relative_to(s.target)).replace("\\", "/")
                if result.target else "?"
            )
            self._emit(Ev.PROGRESS, idx, total_ref(), f.path.name, actual_rel)
            if result.success:
                stats.moved_or_copied += 1
                success.add(_safe_resolve(f.path))
                if c.is_misc:
                    stats.misc += 1
                cat = c.category or "_misc"
                stats.by_category[cat] = stats.by_category.get(cat, 0) + 1
            else:
                stats.failed += 1
                logger.error("整理失败: %s — %s", f.path,
                             result.error or result.skipped_reason)
        return stats, success

    def _trackable_extracted(self, extracted: list[Path]) -> list[Path]:
        """过滤 ``result.extracted`` 为 scanner 能扫的文件 — 同盘下与
        ``self._process_batch`` 中的 success 集合可比对。"""
        out: list[Path] = []
        for p_raw in extracted:
            p = Path(p_raw)
            if not p.is_file():
                continue
            if classify_extension(p) is None:
                continue
            out.append(_safe_resolve(p))
        return out

    def _unlink_archive(self, path: Path) -> None:
        try:
            path.unlink()
            logger.info("EXTRACT+MOVE — 已删除源压缩包: %s", path.name)
        except OSError as e:
            logger.warning("删除源压缩包失败: %s — %s", path, e)

    def _classify_all(self, files: list[ScannedFile]) -> list[Classification]:
        out: list[Classification] = []
        s = self.settings
        cfg = s.preproc_config
        if not s.enable_zh_mapping:
            # 临时关闭中英映射 — 拷贝一份 cfg 把 zh_dict 清空
            from dataclasses import replace
            cfg = replace(cfg, zh_dict={})

        for f in files:
            c = classify(f.path.name, s.ucs, cfg, s.classify_options)
            # P3 fallback: 文件名失败 且 启用 metadata 且 是音频
            if c.is_misc and s.use_metadata_fallback and f.kind is FileKind.AUDIO:
                c = self._try_metadata_fallback(f.path, cfg) or c
            out.append(c)
        return out

    def _try_metadata_fallback(
        self, audio_path: Path, cfg: PreprocessConfig
    ) -> Optional[Classification]:
        try:
            from utils.metadata_reader import read_descriptive_text
        except ImportError:
            return None
        text = read_descriptive_text(audio_path)
        if not text.strip():
            return None
        # 把 metadata 文本当作「文件名」重跑 P2
        s = self.settings
        c = classify(text, s.ucs, cfg, s.classify_options)
        return c if not c.is_misc else None

    # -------- 工具 --------

    def _emit(self, ev: Ev, *args) -> None:
        try:
            self.events.put_nowait((ev, args))
        except queue.Full:
            logger.warning("事件队列满,丢弃 %s", ev)

    def _cancelled(self) -> bool:
        if self.cancel_event.is_set():
            self._emit(Ev.CANCELLED)
            return True
        return False

    def _cleanup_tmp(self, tmp: Optional[Path]) -> None:
        if tmp is None or not tmp.exists():
            return
        try:
            shutil.rmtree(tmp)
        except OSError as e:
            logger.warning("清理临时目录失败: %s (%s)", tmp, e)


def _merge_stats_into(target: JobStats, src: JobStats) -> None:
    """流式过程中把分批 stats 累加到全局 stats。"""
    target.total += src.total
    target.moved_or_copied += src.moved_or_copied
    target.failed += src.failed
    target.misc += src.misc
    for k, v in src.by_category.items():
        target.by_category[k] = target.by_category.get(k, 0) + v


def _safe_resolve(p: Path) -> Path:
    """``Path.resolve()`` 在 Windows 上把 8.3 短路径展开成长路径。

    若文件已被 MOVE 走,源路径不存在 → ``strict=False`` 仍展开符号链接/短路径。
    """
    try:
        return p.resolve(strict=False)
    except OSError:
        return p


def _label(c: Classification) -> str:
    from core.classifier import Hit
    return {
        Hit.P1_CAT_ID: "CatID",
        Hit.P2_SUBCATEGORY: "子类匹配",
        Hit.P2_CATEGORY_ROOT: "大类根",
        Hit.MISC: "_misc",
    }.get(c.source, c.source)
