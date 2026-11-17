# -*- mode: python ; coding: utf-8 -*-
"""Single-file Windows installer built around packaging/windows/installer/main.py."""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
PAYLOAD = ROOT / "build" / "installer-payload.zip"
if not PAYLOAD.is_file():
    raise SystemExit(f"missing {PAYLOAD}; run packaging/windows/build_setup_exe.py")

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "installer" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(PAYLOAD), ".")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GOLEM-LINK-Setup-Windows",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
