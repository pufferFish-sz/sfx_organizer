"""一键打包脚本。

用法:
  python build.py            # onedir(默认,启动快)
  python build.py --onefile  # 单文件 .exe(分发只一个文件,启动慢)
  python build.py --clean    # 透传 --clean 给 pyinstaller

构建前会:
  1. 检查 pyinstaller 是否已安装
  2. 清理上次 build 残留
  3. 跑全量测试(--skip-tests 可跳过)
  4. 调用 pyinstaller spec 文件

产物在 ``dist/sfx_organizer/`` 下。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "sfx_organizer.spec"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sfx_organizer with PyInstaller")
    parser.add_argument("--onefile", action="store_true",
                        help="产单文件 .exe(改写 spec 内 ONEFILE = True)")
    parser.add_argument("--clean", action="store_true",
                        help="透传 --clean 给 pyinstaller")
    parser.add_argument("--skip-tests", action="store_true",
                        help="打包前不跑测试")
    parser.add_argument("--icon", default=None,
                        help="窗口/exe 图标(.ico 路径,留空保持 spec 现状)")
    args = parser.parse_args()

    # 1. 检查依赖
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 pyinstaller。请先:  pip install pyinstaller", file=sys.stderr)
        return 1

    # 2. 测试
    if not args.skip_tests:
        print(">>> 运行单元测试 ...")
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"], cwd=ROOT)
        if r.returncode != 0:
            print("测试失败,中止打包。--skip-tests 可强制跳过", file=sys.stderr)
            return r.returncode

    # 3. 清理上次产物
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            print(f">>> 清理 {p}")
            shutil.rmtree(p, ignore_errors=True)

    # 4. 改写 spec 的 ONEFILE / ICON 配置(原地)
    if args.onefile:
        _patch_spec_onefile(True)
    else:
        _patch_spec_onefile(False)
    if args.icon:
        icon_path = Path(args.icon).resolve()
        if not icon_path.exists():
            print(f"图标文件不存在: {icon_path}", file=sys.stderr)
            return 1
        _patch_spec_icon(str(icon_path).replace("\\", "/"))

    # 5. 调用 pyinstaller
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC)]
    if args.clean:
        cmd.append("--clean")
    print(">>> ", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("PyInstaller 失败", file=sys.stderr)
        return r.returncode

    out_dir = ROOT / "dist" / "sfx_organizer"
    print(f"\nOK ✓ 构建完成: {out_dir}")
    return 0


def _patch_spec_onefile(onefile: bool) -> None:
    """直接改写 spec 文件里的 ``ONEFILE = ...`` 标记。"""
    txt = SPEC.read_text(encoding="utf-8")
    new_val = "True" if onefile else "False"
    import re
    txt2 = re.sub(r"^ONEFILE\s*=\s*(True|False)", f"ONEFILE = {new_val}",
                  txt, count=1, flags=re.MULTILINE)
    if txt != txt2:
        SPEC.write_text(txt2, encoding="utf-8")


def _patch_spec_icon(icon_path: str) -> None:
    """改写 spec 的 ``ICON = ...`` 行。"""
    import re
    txt = SPEC.read_text(encoding="utf-8")
    new_line = f"ICON = {icon_path!r}"
    txt2 = re.sub(r"^ICON\s*=\s*.+$", new_line, txt, count=1, flags=re.MULTILINE)
    if txt != txt2:
        SPEC.write_text(txt2, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
