"""主窗口 — spec 第六节布局。

事件流:
- 「预览」/「开始整理」按钮 → 起 ``OrganizeWorker``(后台线程)
- worker 把事件丢 ``event_queue`` → 主窗口每 100ms ``after`` 一次拉空队列、更新 UI
- 取消按钮 → ``cancel_event.set()`` → worker 在每个文件循环开头检查
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

from core.classifier import ClassifyOptions
from core.organizer import OpMode
from core.preprocessor import load_default_config
from core.ucs_loader import (
    UCSIndex,
    default_csv_path,
    default_extra_synonyms_path,
    load_ucs,
)
from utils.logger import attach_queue_handler, detach_handler, setup_logging
from utils.paths import data_dir, is_frozen

from .password_dialog import PasswordCache, ask_password
from .preview_dialog import PreviewRow, show_preview
from .worker import Ev, JobMode, JobSettings, JobStats, OrganizeWorker

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 100


# --------- 入口 ---------

def run() -> None:
    """构造主窗口并进入 mainloop。供 ``main.py`` 调用。"""
    setup_logging(log_dir=Path.home() / ".sfx_organizer" / "logs")
    root = tk.Tk()
    root.title("音效整理工具")
    root.geometry("960x720")
    MainWindow(root)
    root.mainloop()


# --------- 主窗口 ---------

class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root

        # 资源(可换)— frozen 模式下默认显示「(内置)」避免用户误以为是外部依赖
        if is_frozen():
            self._ucs_csv_var = tk.StringVar(value="(内置)  ucs_v8_2_1.csv")
            self._zh_dict_var = tk.StringVar(value="(内置)  zh_en_dict.json")
        else:
            self._ucs_csv_var = tk.StringVar(value=str(default_csv_path()))
            self._zh_dict_var = tk.StringVar(value=str(data_dir() / "zh_en_dict.json"))
        self._source_var = tk.StringVar()
        self._target_var = tk.StringVar()

        # 模式 / 选项
        self._job_mode_var = tk.StringVar(value=JobMode.DIRECT.value)
        # 是否先解压压缩包 — 与工作模式正交,默认 False
        self._extract_var = tk.BooleanVar(value=False)
        # 处理方式必选 — 初始空字符串,_build_settings 会校验
        self._op_mode_var = tk.StringVar(value="")
        self._read_meta_var = tk.BooleanVar(value=True)
        self._zh_map_var = tk.BooleanVar(value=True)
        self._keep_log_var = tk.BooleanVar(value=True)

        # 状态
        self._ucs: Optional[UCSIndex] = None
        self._ucs_size_var = tk.StringVar(value="未加载")
        self._zh_size_var = tk.StringVar(value="未加载")
        self._worker: Optional[OrganizeWorker] = None
        self._event_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=10_000)
        self._log_queue: "queue.Queue[str]" = queue.Queue(maxsize=10_000)
        self._cancel_event = threading.Event()
        self._log_handler = None
        self._password_cache = PasswordCache()

        self._build_ui()
        self._try_autoload_ucs()
        self._attach_log_handler()
        self._schedule_poll()

    # -------- 构造 UI --------

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 3}

        # ----- 资源行 -----
        frm_res = ttk.LabelFrame(self.root, text="资源")
        frm_res.pack(fill="x", padx=8, pady=6)

        for label, var, browse, sz_var in [
            ("UCS 分类表:", self._ucs_csv_var, self._browse_ucs, self._ucs_size_var),
            ("中英文词典:", self._zh_dict_var, self._browse_zh, self._zh_size_var),
        ]:
            row = ttk.Frame(frm_res)
            row.pack(fill="x", **pad)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="选择...", command=browse).pack(side="left", padx=4)
            ttk.Label(row, textvariable=sz_var, width=18, anchor="w").pack(side="left")

        for label, var, browse in [
            ("源文件夹:", self._source_var, self._browse_source),
            ("目标文件夹:", self._target_var, self._browse_target),
        ]:
            row = ttk.Frame(frm_res)
            row.pack(fill="x", **pad)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="选择...", command=browse).pack(side="left", padx=4)

        # ----- 工作模式 -----
        frm_mode = ttk.LabelFrame(self.root, text="工作模式")
        frm_mode.pack(fill="x", padx=8, pady=6)
        for value, text in [
            (JobMode.DIRECT.value, "新建整理"),
            (JobMode.INCREMENTAL.value, "增量整理到现有库"),
        ]:
            ttk.Radiobutton(
                frm_mode, text=text, value=value, variable=self._job_mode_var,
            ).pack(anchor="w", padx=6)

        # ----- 压缩包处理 -----
        frm_arch = ttk.LabelFrame(self.root, text="压缩包处理")
        frm_arch.pack(fill="x", padx=8, pady=6)
        for value, text in [
            (False, "直接按文件名分类(不解压)"),
            (True, "先解压再整理内容"),
        ]:
            ttk.Radiobutton(
                frm_arch, text=text, value=value, variable=self._extract_var,
            ).pack(anchor="w", padx=6)

        # ----- 处理方式(必选)-----
        frm_op = ttk.LabelFrame(self.root, text="处理方式 (必选)")
        frm_op.pack(fill="x", padx=8, pady=6)
        for value, text in [
            (OpMode.COPY.value, "复制源文件到目标"),
            (OpMode.MOVE.value, "移动源文件到目标(删除源)"),
        ]:
            ttk.Radiobutton(
                frm_op, text=text, value=value, variable=self._op_mode_var,
            ).pack(anchor="w", padx=6)

        # ----- 选项 -----
        frm_opt = ttk.LabelFrame(self.root, text="选项")
        frm_opt.pack(fill="x", padx=8, pady=6)
        for var, text in [
            (self._read_meta_var, "读取音频 metadata 作为 P3 兜底"),
            (self._zh_map_var, "启用中英文映射"),
            (self._keep_log_var, "保留操作日志(用户主目录下 .sfx_organizer/logs/)"),
        ]:
            ttk.Checkbutton(frm_opt, text=text, variable=var).pack(anchor="w", padx=6)

        # ----- 按钮 -----
        frm_btn = ttk.Frame(self.root)
        frm_btn.pack(fill="x", padx=8, pady=6)
        self._btn_preview = ttk.Button(frm_btn, text="预览分类结果", command=self._on_preview)
        self._btn_preview.pack(side="left", padx=4)
        self._btn_start = ttk.Button(frm_btn, text="开始整理", command=self._on_start)
        self._btn_start.pack(side="left", padx=4)
        self._btn_cancel = ttk.Button(frm_btn, text="取消", command=self._on_cancel, state="disabled")
        self._btn_cancel.pack(side="left", padx=4)
        ttk.Button(frm_btn, text="导出日志", command=self._export_log).pack(side="right", padx=4)

        # ----- 进度 -----
        frm_prog = ttk.LabelFrame(self.root, text="进度")
        frm_prog.pack(fill="x", padx=8, pady=6)
        self._progress = ttk.Progressbar(frm_prog, mode="determinate")
        self._progress.pack(fill="x", padx=6, pady=4)
        self._progress_text_var = tk.StringVar(value="就绪")
        ttk.Label(frm_prog, textvariable=self._progress_text_var, anchor="w").pack(fill="x", padx=6)

        # ----- 日志窗口 -----
        frm_log = ttk.LabelFrame(self.root, text="日志")
        frm_log.pack(fill="both", expand=True, padx=8, pady=6)
        self._log_text = scrolledtext.ScrolledText(
            frm_log, wrap="word", height=14, state="disabled",
        )
        self._log_text.pack(fill="both", expand=True, padx=4, pady=4)

    # -------- 资源加载 --------

    def _try_autoload_ucs(self) -> None:
        try:
            self._ucs = load_ucs(default_csv_path(), default_extra_synonyms_path())
            self._ucs_size_var.set(f"已加载 {len(self._ucs)} 条")
        except Exception as e:  # noqa: BLE001
            self._ucs_size_var.set("加载失败")
            logger.error("UCS 自动加载失败: %s", e)
        try:
            cfg = load_default_config()
            self._zh_size_var.set(f"已加载 {len(cfg.zh_dict)} 条")
        except Exception as e:  # noqa: BLE001
            self._zh_size_var.set("加载失败")
            logger.error("zh_dict 加载失败: %s", e)

    def _attach_log_handler(self) -> None:
        self._log_handler = attach_queue_handler(self._log_queue, level=logging.INFO)

    # -------- 浏览按钮 --------

    def _browse_ucs(self) -> None:
        p = filedialog.askopenfilename(
            title="选择 UCS CSV",
            filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")],
        )
        if p:
            self._ucs_csv_var.set(p)
            try:
                self._ucs = load_ucs(p, default_extra_synonyms_path())
                self._ucs_size_var.set(f"已加载 {len(self._ucs)} 条")
                logger.info("UCS 重新加载: %s (%d 条)", p, len(self._ucs))
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("UCS 加载失败", str(e))
                self._ucs_size_var.set("加载失败")

    def _browse_zh(self) -> None:
        p = filedialog.askopenfilename(
            title="选择中英文词典",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if p:
            self._zh_dict_var.set(p)

    def _browse_source(self) -> None:
        p = filedialog.askdirectory(title="选择源文件夹")
        if p:
            self._source_var.set(p)

    def _browse_target(self) -> None:
        p = filedialog.askdirectory(title="选择目标文件夹")
        if p:
            self._target_var.set(p)

    # -------- 启动 worker --------

    def _on_preview(self) -> None:
        self._start_worker(preview_only=True)

    def _on_start(self) -> None:
        self._start_worker(preview_only=False)

    def _start_worker(self, *, preview_only: bool) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("忙", "已有一个任务在进行,请先取消或等待完成")
            return

        settings = self._build_settings()
        if settings is None:
            return

        self._cancel_event.clear()
        self._progress["value"] = 0
        self._progress_text_var.set("准备中...")
        self._set_busy(True)

        # 清空事件队列
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except queue.Empty:
                break

        self._worker = OrganizeWorker(
            settings=settings,
            event_queue=self._event_queue,
            cancel_event=self._cancel_event,
            preview_only=preview_only,
        )
        self._worker.start()
        logger.info("worker 启动:mode=%s preview=%s", settings.job_mode.value, preview_only)

    def _on_cancel(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_event.set()
            self._progress_text_var.set("取消请求已发出,等待当前文件完成...")

    def _build_settings(self) -> Optional[JobSettings]:
        if self._ucs is None:
            messagebox.showerror("无 UCS", "UCS 表未成功加载,请检查路径")
            return None
        src = self._source_var.get().strip()
        dst = self._target_var.get().strip()
        if not src:
            messagebox.showerror(
                "源无效",
                "请通过「源文件夹」旁的「选择...」按钮选择一个文件夹",
            )
            return None
        if not Path(src).is_dir():
            messagebox.showerror("源无效", f"源路径不是有效文件夹:\n{src}")
            return None
        if not dst:
            messagebox.showerror(
                "目标无效",
                "请通过「目标文件夹」旁的「选择...」按钮选择一个文件夹\n"
                "(目标可以是空文件夹,也可以是不存在的路径 — 会自动创建)",
            )
            return None
        try:
            Path(dst).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("目标无效", f"无法创建目标目录:\n{dst}\n\n{e}")
            return None
        # 防止用户把源选为目标的子目录或反之 — 会造成扫描时自我递归
        try:
            src_r = Path(src).resolve()
            dst_r = Path(dst).resolve()
            if src_r == dst_r:
                messagebox.showerror("源/目标重叠", "源文件夹与目标文件夹不能相同")
                return None
            if dst_r in src_r.parents:
                messagebox.showerror(
                    "源/目标重叠",
                    "目标文件夹不能是源文件夹的父级目录",
                )
                return None
        except OSError:
            pass

        op_mode_raw = self._op_mode_var.get()
        if op_mode_raw not in (OpMode.COPY.value, OpMode.MOVE.value):
            messagebox.showerror(
                "未选处理方式",
                "请在「处理方式」中选择「复制」或「移动」",
            )
            return None

        cfg = load_default_config(ucs=self._ucs)
        return JobSettings(
            source=Path(src),
            target=Path(dst),
            ucs=self._ucs,
            preproc_config=cfg,
            classify_options=ClassifyOptions(),
            job_mode=JobMode(self._job_mode_var.get()),
            extract_archives=self._extract_var.get(),
            op_mode=OpMode(op_mode_raw),
            use_metadata_fallback=self._read_meta_var.get(),
            enable_zh_mapping=self._zh_map_var.get(),
            password_provider=self._make_password_provider(),
        )

    def _make_password_provider(self):
        """生成 extractor 的密码回调:先试缓存,再弹窗。"""
        def provider(archive: Path, attempt: int) -> Optional[str]:
            # attempt 0 时先试缓存
            if attempt == 0:
                for cand in self._password_cache.candidates():
                    return cand   # 由 extractor 验证,失败会再 attempt=1 进来
            pwd, remember = ask_password(self.root, archive, attempt)
            if pwd and remember:
                self._password_cache.remember(pwd)
            return pwd
        return provider

    # -------- 事件轮询 --------

    def _schedule_poll(self) -> None:
        self.root.after(POLL_INTERVAL_MS, self._poll)

    def _poll(self) -> None:
        # 1. 日志队列
        for _ in range(200):
            try:
                line = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(line)
        # 2. 事件队列
        for _ in range(200):
            try:
                ev, args = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(ev, args)
        # 3. 继续
        self._schedule_poll()

    def _handle_event(self, ev: Ev, args: tuple) -> None:
        if ev is Ev.SCAN_START:
            self._progress_text_var.set("扫描中...")
        elif ev is Ev.SCAN_DONE:
            n, total = args
            self._progress["maximum"] = max(n, 1)
            self._progress_text_var.set(f"扫描完成:{n} 个文件,总 {total // 1024} KB")
        elif ev is Ev.SPACE_CHECK:
            est, ok = args
            self._progress_text_var.set(f"空间检查:{est}")
        elif ev is Ev.EXTRACT_START:
            n = args[0]
            self._progress_text_var.set(f"开始解压 {n} 个压缩包...")
        elif ev is Ev.EXTRACT_ONE:
            i, name, ok, err = args
            tag = "✓" if ok else "✗"
            self._progress_text_var.set(f"解压 {tag} {name}")
        elif ev is Ev.EXTRACT_DONE:
            self._progress_text_var.set("解压完毕,开始分类...")
        elif ev is Ev.PROGRESS:
            i, total, name, target = args
            self._progress["value"] = i
            self._progress["maximum"] = total
            self._progress_text_var.set(f"{i}/{total}  {name} → {target}")
        elif ev is Ev.PREVIEW_READY:
            rows: list[PreviewRow] = args[0]
            self._show_preview(rows)
        elif ev is Ev.DONE:
            stats: JobStats = args[0]
            self._progress["value"] = self._progress["maximum"]
            summary = (
                f"完成:总 {stats.total},处理 {stats.moved_or_copied},"
                f"失败 {stats.failed},_misc {stats.misc}"
            )
            self._progress_text_var.set(summary)
            logger.info(summary)
            self._set_busy(False)
        elif ev is Ev.ERROR:
            msg = args[0]
            messagebox.showerror("出错", msg)
            self._progress_text_var.set(f"出错:{msg}")
            self._set_busy(False)
        elif ev is Ev.CANCELLED:
            self._progress_text_var.set("已取消")
            self._set_busy(False)

    def _show_preview(self, rows: list[PreviewRow]) -> None:
        if not rows:
            messagebox.showinfo("预览", "没有可整理的文件")
            self._set_busy(False)
            return
        confirmed = show_preview(self.root, rows)
        if confirmed:
            logger.info("预览已确认 — 切换到正式整理")
            self._set_busy(False)
            self._start_worker(preview_only=False)
        else:
            self._progress_text_var.set("预览已取消")
            self._set_busy(False)

    # -------- 通用 UI --------

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self._btn_preview.configure(state=state)
        self._btn_start.configure(state=state)
        self._btn_cancel.configure(state="normal" if busy else "disabled")

    def _append_log(self, line: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", line + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _export_log(self) -> None:
        content = self._log_text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("导出日志", "日志为空")
            return
        p = filedialog.asksaveasfilename(
            title="导出日志",
            defaultextension=".log",
            filetypes=[("Log", "*.log"), ("All", "*.*")],
            initialfile="sfx_organizer.log",
        )
        if not p:
            return
        try:
            Path(p).write_text(content, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("导出失败", str(e))
            return
        messagebox.showinfo("已导出", p)
