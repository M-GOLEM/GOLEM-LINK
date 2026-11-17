# PyInstaller build definition for the Windows onedir bundle.
# Build from the repository root:
#   python -m PyInstaller --noconfirm --clean packaging/windows/golem-link.spec
# Output: dist/GOLEM-LINK/golem-link.exe (+ dashboard bundle inside).

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent.parent  # noqa: F821 - provided by PyInstaller
WEB_DIST = ROOT / "apps" / "dashboard" / "dist"

datas = []
if (WEB_DIST / "index.html").exists():
    datas.append((str(WEB_DIST), "golem_link/web"))

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("zeroconf")
    + collect_submodules("aiohttp")
)

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "matplotlib", "numpy", "pandas", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="golem-link",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GOLEM-LINK",
)
