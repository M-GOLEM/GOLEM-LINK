"""Build dist\\GOLEM-LINK-Setup-Windows.exe (one file, no Inno Setup needed).

Steps: require the PyInstaller onedir bundle, fetch WinSW, stage the payload
with forward-slash zip names, then compile the installer onefile exe.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
BUNDLE = DIST / "GOLEM-LINK"
BUILD = ROOT / "build"
PAYLOAD_DIR = BUILD / "installer-payload"
PAYLOAD_ZIP = BUILD / "installer-payload.zip"
WINSW = BUILD / "windows" / "WinSW-x64.exe"
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
WINSW_MIN = 5 * 1024 * 1024
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)


def fetch_winsw() -> None:
    WINSW.parent.mkdir(parents=True, exist_ok=True)
    if WINSW.is_file() and WINSW.stat().st_size >= WINSW_MIN:
        return
    WINSW.unlink(missing_ok=True)
    partial = WINSW.with_suffix(".partial")
    for attempt in range(1, 4):
        partial.unlink(missing_ok=True)
        print(f"Downloading WinSW (attempt {attempt})...")
        request = urllib.request.Request(WINSW_URL, headers={"User-Agent": "golem-link-build"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as sink:
                shutil.copyfileobj(response, sink)
        except Exception as exc:
            print(f"  download failed: {exc}")
            continue
        if partial.stat().st_size >= WINSW_MIN:
            partial.replace(WINSW)
            return
        print(f"  truncated at {partial.stat().st_size} bytes")
    raise SystemExit(
        f"Could not download WinSW. Download {WINSW_URL} manually into {WINSW.parent}"
    )


def stage_payload() -> None:
    if not (BUNDLE / "golem-link.exe").is_file():
        raise SystemExit(f"missing {BUNDLE / 'golem-link.exe'}; build the PyInstaller bundle first")
    if not (BUNDLE / "_internal" / "golem_link" / "web" / "index.html").is_file():
        raise SystemExit("the bundle lacks the embedded dashboard; rebuild it")
    if not WINSW.is_file():
        raise SystemExit(f"missing {WINSW}")
    if PAYLOAD_DIR.exists():
        shutil.rmtree(PAYLOAD_DIR)
    shutil.copytree(BUNDLE, PAYLOAD_DIR)
    shutil.copy2(WINSW, PAYLOAD_DIR / "golem-link-service.exe")
    shutil.copy2(
        ROOT / "packaging" / "windows" / "golem-link-service.xml",
        PAYLOAD_DIR / "golem-link-service.xml",
    )

    PAYLOAD_ZIP.unlink(missing_ok=True)
    count = 0
    with zipfile.ZipFile(PAYLOAD_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(PAYLOAD_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(PAYLOAD_DIR).as_posix())
                count += 1
    digest = hashlib.sha256(PAYLOAD_ZIP.read_bytes()).hexdigest()
    print(f"Payload: {count} files, {PAYLOAD_ZIP.stat().st_size / 1e6:.1f} MB, sha256 {digest}")


def build_exe() -> None:
    command = [
        str(PYTHON), "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller-installer"),
        str(ROOT / "packaging" / "windows" / "golem-link-installer.spec"),
    ]
    print("Running:", " ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)
    exe = DIST / "GOLEM-LINK-Setup-Windows.exe"
    if not exe.is_file():
        raise SystemExit(f"installer was not produced: {exe}")
    print(f"Installer: {exe} ({exe.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    fetch_winsw()
    print(f"WinSW sha256: {hashlib.sha256(WINSW.read_bytes()).hexdigest()}")
    stage_payload()
    build_exe()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
