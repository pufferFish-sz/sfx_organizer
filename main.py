"""sfx_organizer 入口 — 启动 GUI 主窗口。

GUI 实现见 gui/main_window.py(第四阶段交付)。
"""

from __future__ import annotations


def main() -> None:
    from gui.main_window import run

    run()


if __name__ == "__main__":
    main()
