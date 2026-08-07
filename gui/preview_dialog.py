"""预览模式弹窗 — 列出「文件 → 目标分类」对照表。

数据由 main_window 准备(在 worker 里跑 dry_run),传入这里只做渲染。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Sequence


@dataclass(frozen=True)
class PreviewRow:
    """一行预览数据。"""

    source: Path
    size_bytes: int
    target_rel: str          # 相对目标根的路径,如 "SCIFI/WEAPON/foo.zip"
    source_label: str        # CatID 命中 / 子类匹配 / 大类根 / _misc


def show_preview(parent: tk.Misc, rows: Sequence[PreviewRow]) -> bool:
    """阻塞展示预览;用户点「开始整理」返回 True,「取消」返回 False。"""
    dlg = _PreviewDialog(parent, rows)
    parent.wait_window(dlg.top)
    return dlg.confirmed


class _PreviewDialog:
    def __init__(self, parent: tk.Misc, rows: Sequence[PreviewRow]) -> None:
        top = tk.Toplevel(parent)
        top.title(f"预览分类结果 — 共 {len(rows)} 个文件")
        top.transient(parent)
        top.grab_set()
        top.geometry("900x600")

        self.top = top
        self.confirmed = False

        # ---- 统计行 ----
        stats = ttk.Frame(top, padding=8)
        stats.pack(fill="x")
        agg = _aggregate(rows)
        summary = f"共 {len(rows)} 个文件,涉及 {len(agg)} 个大类"
        if "_misc" in agg:
            summary += f";其中 {agg['_misc']} 个无法归类(将进 _misc/)"
        ttk.Label(stats, text=summary).pack(side="left")

        # ---- Treeview ----
        cols = ("name", "size", "target", "source")
        frame = ttk.Frame(top)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("name", text="文件名")
        tree.heading("size", text="大小")
        tree.heading("target", text="目标分类路径")
        tree.heading("source", text="判定依据")
        tree.column("name", width=320, anchor="w")
        tree.column("size", width=80, anchor="e")
        tree.column("target", width=320, anchor="w")
        tree.column("source", width=120, anchor="w")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # tag 着色:_misc 给个温和的提示色
        tree.tag_configure("misc", background="#fff3cd")

        for r in rows:
            tag = ("misc",) if r.target_rel.startswith("_misc") else ()
            tree.insert(
                "", "end",
                values=(r.source.name, _format_size(r.size_bytes), r.target_rel, r.source_label),
                tags=tag,
            )

        # ---- 按钮 ----
        btns = ttk.Frame(top, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="开始整理", command=self._confirm).pack(side="right")

        top.protocol("WM_DELETE_WINDOW", self._cancel)
        top.bind("<Escape>", lambda _e: self._cancel())

    def _confirm(self) -> None:
        self.confirmed = True
        self.top.destroy()

    def _cancel(self) -> None:
        self.confirmed = False
        self.top.destroy()


# --------- helpers ---------

def _aggregate(rows: Sequence[PreviewRow]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        head = r.target_rel.split("/", 1)[0]
        out[head] = out.get(head, 0) + 1
    return out


_UNITS = ("B", "KB", "MB", "GB", "TB")


def _format_size(n: int) -> str:
    f = float(n)
    for u in _UNITS:
        if f < 1024:
            return f"{int(f)} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} PB"
