"""加密压缩包密码输入弹窗 + 「会话密码缓存」。

设计:
- ``ask_password(parent, archive, attempt)``: 弹模态对话框,返回 ``(pwd|None, remember: bool)``
- ``PasswordCache``: 调用方维护;若用户勾「记住」,下次同一会话内任何加密包先用缓存的试
- 把这些组合成给 ``extractor.extract`` 的 ``password_provider`` 回调
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional


class PasswordCache:
    """会话级密码缓存。"""

    def __init__(self) -> None:
        self._remembered: list[str] = []

    def remember(self, password: str) -> None:
        if password and password not in self._remembered:
            self._remembered.append(password)

    def candidates(self) -> list[str]:
        return list(self._remembered)


def ask_password(
    parent: tk.Misc,
    archive: Path,
    attempt: int,
    max_attempts: int = 3,
) -> tuple[Optional[str], bool]:
    """模态弹窗 — 返回 ``(pwd_or_None, remember)``。

    用户点 Cancel / 关窗 → ``(None, False)``;视为取消。
    """
    dlg = _PasswordDialog(parent, archive, attempt, max_attempts)
    parent.wait_window(dlg.top)
    return dlg.result, dlg.remember


class _PasswordDialog:
    def __init__(self, parent: tk.Misc, archive: Path, attempt: int, max_attempts: int) -> None:
        top = tk.Toplevel(parent)
        top.title("需要密码")
        top.transient(parent)
        top.grab_set()
        top.resizable(False, False)

        self.top = top
        self.result: Optional[str] = None
        self.remember: bool = False

        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=f"压缩包加密:\n{archive.name}", justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        ttk.Label(body, text=f"第 {attempt + 1} / {max_attempts} 次尝试").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        ttk.Label(body, text="密码:").grid(row=2, column=0, sticky="e", padx=(0, 6))
        self._entry = ttk.Entry(body, show="•", width=28)
        self._entry.grid(row=2, column=1, sticky="we")
        self._entry.focus_set()

        self._remember_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            body, text="本次会话内记住该密码", variable=self._remember_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 8))

        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="跳过", command=self._on_cancel).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side="right")

        top.bind("<Return>", lambda _e: self._on_ok())
        top.bind("<Escape>", lambda _e: self._on_cancel())
        top.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 居中到 parent
        top.update_idletasks()
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            w = top.winfo_width()
            h = top.winfo_height()
            top.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
        except tk.TclError:
            pass

    def _on_ok(self) -> None:
        self.result = self._entry.get()
        self.remember = self._remember_var.get()
        self.top.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.remember = False
        self.top.destroy()
