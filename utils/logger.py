"""日志配置 — 双通道:轮转文件 + 可选 GUI Text widget。

调用 ``setup_logging()`` 一次,之后任何模块 ``logging.getLogger(__name__)`` 都会自动写。
"""

from __future__ import annotations

import logging
import queue
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: str | Path | None = None,
    level: int = logging.INFO,
    file_name: str = "sfx_organizer.log",
) -> logging.Logger:
    """配置根 logger。可重复调用,每次只重置自己加的 handler。

    Args:
        log_dir: 文件日志目录;None 则不写文件
        level: 日志阈值
        file_name: 日志文件名(在 log_dir 下)

    Returns:
        根 logger
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 清掉之前可能加过的本模块 handler(避免重复打印)
    for h in list(root.handlers):
        if getattr(h, "_sfx_managed", False):
            root.removeHandler(h)

    formatter = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    # 控制台
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    sh._sfx_managed = True  # type: ignore[attr-defined]
    root.addHandler(sh)

    # 文件
    if log_dir is not None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            d / file_name, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(formatter)
        fh._sfx_managed = True  # type: ignore[attr-defined]
        root.addHandler(fh)

    return root


class QueueLogHandler(logging.Handler):
    """把 LogRecord 推到一个 ``queue.Queue``,GUI 主线程定时取出渲染。

    这样 worker 线程的日志不会直接动 Tk widget(Tk 不是线程安全的)。
    """

    def __init__(self, q: "queue.Queue[str]", level: int = logging.INFO) -> None:
        super().__init__(level)
        self._queue = q
        self.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        self._sfx_managed = True  # 标记便于 setup_logging 重置时识别

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except queue.Full:
            pass  # 丢弃总比阻塞 UI 强


def attach_queue_handler(q: "queue.Queue[str]", level: int = logging.INFO) -> QueueLogHandler:
    """给根 logger 挂上一个队列 handler。返回 handler 以便后续 detach。"""
    handler = QueueLogHandler(q, level)
    logging.getLogger().addHandler(handler)
    return handler


def detach_handler(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)
