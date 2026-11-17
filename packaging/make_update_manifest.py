"""Write dist/updates/manifest.json for the release the agents will pull.

The manifest is what a node serves to paired devices and what the update
manager validates before trusting an artifact. Build the release first, then:

    python packaging/make_update_manifest.py \
        --windows dist/GOLEM-LINK-Setup-Windows.exe \
        --linux dist/GOLEM-LINK-Debian13.run \
        --notes "Short release notes"

Artifacts are copied next to the manifest by default, because a node resolves
``<source_dir>/<file>`` when it serves or verifies the release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def artifact_entry(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"artifact not found: {path}")
    return {
        "file": path.name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def build_manifest(
    version: str,
    channel: str,
    notes: str,
    published_at: str,
    artifacts: dict[str, dict],
) -> dict:
    return {
        "version": version.lstrip("v"),
        "channel": channel,
        "notes": notes,
        "published_at": published_at,
        "artifacts": artifacts,
    }


def write_manifest(manifest: dict, out_dir: Path, sources: dict[str, Path], copy: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if copy:
        for entry in manifest["artifacts"].values():
            source = sources[entry["file"]]
            target = out_dir / entry["file"]
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
    payload = json.dumps(manifest, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix=".manifest-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    target = out_dir / "manifest.json"
    os.replace(tmp, target)
    return target


def read_notes(path: Path | None, inline: str | None) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8").strip()
    return (inline or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write an update manifest for a GOLEM LINK release")
    parser.add_argument("--version", required=True, help="release version, e.g. 1.2.0")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--notes", default="", help="release notes (plain text)")
    parser.add_argument("--notes-file", type=Path, default=None)
    parser.add_argument(
        "--published-at",
        default="",
        help="ISO-8601 timestamp; defaults to the current UTC time",
    )
    parser.add_argument("--windows", type=Path, default=DIST / "GOLEM-LINK-Setup-Windows.exe")
    parser.add_argument("--linux", type=Path, default=DIST / "GOLEM-LINK-Debian13.run")
    parser.add_argument("--out", type=Path, default=DIST / "updates")
    parser.add_argument("--no-copy", action="store_true", help="only write the manifest, do not copy artifacts")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    version = args.version.strip().lstrip("v")
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit("--version must look like 1.2.3")

    artifacts: dict[str, dict] = {}
    sources: dict[str, Path] = {}
    for platform, path in (("windows", args.windows), ("linux", args.linux)):
        if path is None:
            continue
        entry = artifact_entry(path)
        if entry["file"] in sources:
            raise SystemExit(f"two platforms share the file name {entry['file']}")
        artifacts[platform] = entry
        sources[entry["file"]] = path
    if not artifacts:
        raise SystemExit("at least one artifact is required")

    published_at = args.published_at.strip() or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    manifest = build_manifest(
        version,
        args.channel.strip()[:40] or "stable",
        read_notes(args.notes_file, args.notes)[:4000],
        published_at,
        artifacts,
    )
    target = write_manifest(manifest, args.out, sources, copy=not args.no_copy)
    if not args.quiet:
        print(f"manifest: {target}")
        for platform, entry in artifacts.items():
            print(f"{platform:8} {entry['file']}  {entry['size']} bytes  sha256 {entry['sha256']}")
        print(json.dumps({"version": manifest["version"], "channel": manifest["channel"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
