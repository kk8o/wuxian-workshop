# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the onedir build of wuxian.exe; scripts\\build.ps1 runs it from the repository root:

    .venv\\Scripts\\python.exe -m PyInstaller scripts\\wuxian.spec --noconfirm --distpath dist --workpath build

Console subsystem (the MCP stdio transport needs stdin / stdout), with the console hidden early when the exe owns it (a
double-click; a terminal keeps its window); no UPX (fewer anti-virus false positives); the version resource from
scripts\\version_info.txt, regenerated here from pyproject's version. The addon folders and the ui static files (when
they exist) are copied into dist\\wuxian\\_internal, where installer.addon_source_dir() and the daemon find them.
The entry script is scripts\\wuxian_entry.py (cli/main.py uses relative imports, so it cannot be the script itself);
cli/main.py imports the sub-commands by name, so every module of the package is a hidden import.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent            # SPECPATH: the folder of this file (PyInstaller sets it)
SRC = ROOT / "src"
sys.path.insert(0, SPECPATH)
import version_info                               # scripts/version_info.py

VERSION = version_info.version()
version_info.write_resource(ROOT / "scripts" / "version_info.txt", VERSION)

datas = []
for name in ("!WuxianWorkshop", "WoWBridge"):     # installer.PRODUCT_ADDONS
    folder = ROOT / "addon" / name
    if folder.is_dir():
        datas.append((str(folder), f"addon/{name}"))
    else:
        print(f"WARNING: {folder} is missing and will not be in the build; `wuxian install` cannot work without it")
static = SRC / "wuxianworkshop" / "ui" / "static"
if static.is_dir():
    datas.append((str(static), "wuxianworkshop/ui/static"))
packs = SRC / "wuxianworkshop" / "data"                      # the content packs the program carries (content.py)
if packs.is_dir():
    datas.append((str(packs), "wuxianworkshop/data"))

a = Analysis(
    [str(ROOT / "scripts" / "wuxian_entry.py")],          # imports wuxianworkshop.cli.main (relative imports: not runnable as a script)
    pathex=[str(SRC), str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("wuxianworkshop") + ["lupa.lua51"],    # agent/lint.py imports it when it checks
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # fontTools: only the test / tool helpers fontpack.widths() and metrics() import it (core/ttf.py writes the fonts);
    # the static analysis would otherwise pull its ~200 modules into the build
    excludes=["tkinter", "fontTools"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wuxian",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    hide_console="hide-early",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "scripts" / "version_info.txt"),
    icon=str(static / "wuxian.ico"),                         # scripts\make_icons.py renders it from logo.svg
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="wuxian",
)
