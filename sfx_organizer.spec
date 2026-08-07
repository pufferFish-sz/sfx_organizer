# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for sfx_organizer.

用法:
  pyinstaller sfx_organizer.spec        # 默认 onedir(分发文件夹,启动快)
  pyinstaller sfx_organizer.spec --clean

构建产物:
  dist/sfx_organizer/sfx_organizer.exe  # 主程序
  dist/sfx_organizer/_internal/...      # 依赖 + data/

如需单文件 .exe,改下方 ONEFILE = True(启动慢、每次解压到 TEMP,但分发只一个文件)。
"""

import os
import sys

# ---------------- 配置 ----------------
ONEFILE = True             # True = 单文件,False = 文件夹分发
APP_NAME = "sfx_organizer"
ENTRY = "main.py"
ICON = 'E:/sfx_organizer/sfx_org_cover.ico'
CONSOLE = False             # GUI 应用,无控制台

# ---------------- data 文件 ----------------
# (source, dest_in_bundle) — dest 用 ``data`` 与 dev 模式目录结构一致,
# 这样 ``utils.paths.data_dir()`` 在 frozen 模式取到的就是同样布局。
datas = [
    ("data/ucs_v8_2_1.csv", "data"),
    ("data/zh_en_dict.json", "data"),
    ("data/stopwords.json", "data"),
    ("data/brands.txt", "data"),
    ("data/extra_synonyms.json", "data"),
]

# ---------------- 可选依赖:jieba 自带 dict.txt ----------------
# jieba 装了就把 dict.txt 一并打进 bundle;未装时跳过(代码已有降级)。
try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files("jieba")
except Exception:
    pass

# ---------------- Analysis ----------------
a = Analysis(
    [ENTRY],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # PyInstaller 静态扫描可能漏掉的延迟导入
        "mutagen",
        "mutagen.mp3",
        "mutagen.id3",
        "mutagen.flac",
        "mutagen.wave",
        "mutagen.oggvorbis",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 别打不需要的大块头进 bundle
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "_pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )
