"""Build dist\\GOLEM-LINK-Debian13.run — a single self-extracting Linux installer.

The payload holds a portable CPython runtime (python-build-standalone),
offline wheels for CPython 3.13 / manylinux x86_64, the systemd unit and the
dashboard bundle. The shell header installs the agent without any system
Python, pip, apt or network access, completes the first-run setup, opens the
LAN firewall and finishes pairing.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
WHEELS = BUILD / "linux-wheels"
PAYLOAD = BUILD / "linux-payload"
RUNTIME_DIR = BUILD / "linux-runtime"
RUNTIME_CACHE = RUNTIME_DIR / "runtime.tar.gz"
RUNTIME_TGZ = RUNTIME_DIR / "python-runtime.tar.gz"
WEB = ROOT / "apps" / "dashboard" / "dist"
HEADER = ROOT / "packaging" / "linux" / "golem-link-installer.sh"
OUT = DIST / "GOLEM-LINK-Debian13.run"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

DEPENDENCIES = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "psutil>=5.9",
    "zeroconf>=0.131",
    "pydantic>=2.6",
    "cryptography>=42.0",
    "aiohttp>=3.9",
]
PLATFORMS = [
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
    "manylinux_2_34_x86_64",
]
RUNTIME_RELEASE = "20261110"
RUNTIME_NAME = (
    "cpython-3.13.18+20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
)
RUNTIME_SHA256 = "f2d2199eb25880e07863b391cf7124e7ffa1338b0d65600dfcec117c4223a7fb"
RUNTIME_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{RUNTIME_RELEASE}/{quote(RUNTIME_NAME)}"
)
# Not needed by the agent: test suites, IDLE, Tk, pip/ensurepip, headers.
PRUNE_PREFIXES = (
    "python/lib/python3.13/test",
    "python/lib/python3.13/idlelib",
    "python/lib/python3.13/tkinter",
    "python/lib/python3.13/turtledemo",
    "python/lib/python3.13/ensurepip",
    "python/lib/python3.13/site-packages/pip",
    "python/lib/python3.13/config-",
    "python/lib/tcl9",
    "python/lib/tk9",
    "python/lib/libtcl",
    "python/lib/libtk",
    "python/lib/itcl",
    "python/lib/thread3",
    "python/lib/tdbc",
    "python/lib/pkgconfig",
    "python/include",
    "python/share/man",
    "python/share/terminfo",
    "python/bin/idle3.13",
    "python/bin/pip",
    "python/bin/python3.13-config",
    "python/lib/python3.13/lib-dynload/_tkinter",
)


def version() -> str:
    for line in (ROOT / "golem_link" / "version.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("could not read the version")


def run(command: list[str], **kwargs) -> None:
    print("Running:", " ".join(command))
    subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def build_wheels() -> None:
    if not (WEB / "index.html").is_file():
        raise SystemExit(f"missing {WEB / 'index.html'}; build the dashboard first")
    WHEELS.mkdir(parents=True, exist_ok=True)
    # Always rebuild the app wheel: a cached one from an earlier version
    # silently shipped old Python code inside the .run.
    for stale in WHEELS.glob("golem_link-*.whl"):
        stale.unlink()
    run([str(PYTHON), "-m", "pip", "wheel", "--no-deps", "--no-cache-dir", "-w", str(WHEELS), "."])
    tags: list[str] = []
    for platform in PLATFORMS:
        tags += ["--platform", platform]
    run(
        [
            str(PYTHON), "-m", "pip", "download", "-d", str(WHEELS),
            "--only-binary=:all:", *tags,
            "--python-version", "313", "--implementation", "cp", "--abi", "cp313", "--abi", "abi3",
            *DEPENDENCIES,
        ]
    )


def fetch_runtime() -> None:
    """Download the portable CPython once and keep it in build/linux-runtime."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if RUNTIME_CACHE.is_file():
        digest = hashlib.sha256(RUNTIME_CACHE.read_bytes()).hexdigest()
        if digest == RUNTIME_SHA256:
            return
        print(f"Runtime checksum mismatch ({digest}); downloading again.")
        RUNTIME_CACHE.unlink()
    partial = RUNTIME_DIR / "runtime.partial"
    for attempt in range(1, 4):
        partial.unlink(missing_ok=True)
        print(f"Downloading the portable Python runtime (attempt {attempt})...")
        request = urllib.request.Request(RUNTIME_URL, headers={"User-Agent": "golem-link-build"})
        try:
            with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as sink:
                shutil.copyfileobj(response, sink)
        except Exception as exc:
            print(f"  download failed: {exc}")
            continue
        digest = hashlib.sha256(partial.read_bytes()).hexdigest()
        if digest == RUNTIME_SHA256:
            partial.replace(RUNTIME_CACHE)
            return
        print(f"  checksum mismatch: {digest}")
    raise SystemExit(
        f"Could not download the portable Python runtime. Fetch {RUNTIME_URL} "
        f"into {RUNTIME_DIR} and verify sha256 {RUNTIME_SHA256}."
    )


def repack_runtime() -> None:
    """Drop unused stdlib parts and keep executable bits for the Linux tarball."""
    RUNTIME_TGZ.unlink(missing_ok=True)
    kept = 0
    with tarfile.open(RUNTIME_CACHE, "r:gz") as source, tarfile.open(
        RUNTIME_TGZ, "w:gz", compresslevel=9
    ) as target:
        for member in source:
            name = member.name
            if any(name == prefix or name.startswith(prefix) for prefix in PRUNE_PREFIXES):
                continue
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            if member.isdir():
                member.mode = 0o755
                target.addfile(member)
            elif member.isfile():
                member.mode = 0o755 if member.mode & 0o111 else 0o644
                target.addfile(member, source.extractfile(member))
            kept += 1
    print(f"Runtime: {kept} entries, {RUNTIME_TGZ.stat().st_size / 1e6:.1f} MB")


def verify_runtime() -> None:
    with tarfile.open(RUNTIME_TGZ, "r:gz") as archive:
        names = archive.getnames()
        python = next((m for m in archive.getmembers() if m.name == "python/bin/python3.13"), None)
        if python is None or not python.mode & 0o111:
            raise SystemExit("the runtime tarball lacks an executable python/bin/python3.13")
        binary = archive.extractfile(python).read()
    for module in (b"_ssl", b"_sqlite3"):
        if module not in binary:
            raise SystemExit(f"the portable Python lacks {module.decode()}")
    if "python/lib/python3.13/encodings/__init__.py" not in names:
        raise SystemExit("the portable Python lacks its standard library")
    for banned in (
        "python/lib/python3.13/test/",
        "python/lib/tcl9",
        "python/lib/python3.13/site-packages/pip/",
        "python/bin/pip",
    ):
        if any(name.startswith(banned) for name in names):
            raise SystemExit(f"runtime pruning missed {banned}")


def stage() -> None:
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    (PAYLOAD / "wheels").mkdir(parents=True)
    for wheel in sorted(WHEELS.glob("*.whl")):
        shutil.copy2(wheel, PAYLOAD / "wheels" / wheel.name)
    shutil.copy2(RUNTIME_TGZ, PAYLOAD / "python-runtime.tar.gz")
    shutil.copy2(ROOT / "packaging" / "linux" / "golem-link.service", PAYLOAD / "golem-link.service")
    shutil.copy2(
        ROOT / "packaging" / "linux" / "golem-link-apply-update",
        PAYLOAD / "golem-link-apply-update",
    )
    shutil.copy2(
        ROOT / "packaging" / "linux" / "golem-link-update.service",
        PAYLOAD / "golem-link-update.service",
    )
    shutil.copy2(
        ROOT / "packaging" / "linux" / "golem-link-update.path",
        PAYLOAD / "golem-link-update.path",
    )
    shutil.copytree(WEB, PAYLOAD / "web")

    entries = [
        path.relative_to(PAYLOAD).as_posix()
        for path in sorted(PAYLOAD.rglob("*"))
        if path.is_file()
    ]
    lines = [
        f"{hashlib.sha256((PAYLOAD / name).read_bytes()).hexdigest()}  {name}"
        for name in entries
    ]
    (PAYLOAD / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")

    stamp = BUILD / "linux-payload.tar.gz"
    stamp.unlink(missing_ok=True)
    with tarfile.open(stamp, "w:gz", compresslevel=9) as archive:
        for path in sorted(PAYLOAD.rglob("*")):
            arcname = path.relative_to(PAYLOAD).as_posix()
            info = archive.gettarinfo(str(path), arcname=arcname)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if path.is_dir():
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.mode = 0o644
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
    print(f"Payload: {len(entries)} files, {stamp.stat().st_size / 1e6:.1f} MB")


def assemble() -> None:
    header = HEADER.read_text(encoding="utf-8").replace("@VERSION@", version())
    payload = (BUILD / "linux-payload.tar.gz").read_bytes()
    DIST.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header.encode("utf-8") + payload)
    OUT.chmod(0o755)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"Installer: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256: {digest}")


def verify_app_wheel() -> None:
    expected = f"golem_link-{version()}-py3-none-any.whl"
    wheels = list(WHEELS.glob("golem_link-*.whl"))
    if len(wheels) != 1 or wheels[0].name != expected:
        raise SystemExit(f"expected exactly {expected}, found {[w.name for w in wheels]}")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        identity = archive.read("golem_link/version.py").decode("utf-8")
    if f'VERSION = "{version()}"' not in identity:
        raise SystemExit(f"{wheel.name} does not carry version {version()}")
    for required in ("golem_link/link.py", "golem_link/cli.py", "golem_link/app.py"):
        if required not in names:
            raise SystemExit(f"{wheel.name} is missing {required}")


def main() -> int:
    build_wheels()
    verify_app_wheel()
    fetch_runtime()
    repack_runtime()
    verify_runtime()
    stage()
    assemble()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
