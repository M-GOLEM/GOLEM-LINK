"""Built-in distributed task plugins.

Every plugin only touches paths inside the approved share roots handed to the
worker by the scheduler. The worker re-validates those roots, so a malicious
task payload cannot escape the sandbox by path tricks.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from . import register
from ..base import PluginContext, TaskPlugin


def _ensure_allowed(path: str, roots: list[str]) -> Path:
    target = Path(path).resolve()
    for root in roots:
        root_path = Path(root).resolve()
        if target == root_path or root_path in target.parents:
            return target
    raise ValueError(f"path {target} is outside the approved share roots")


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_file():
                yield p


@register
class FileHashPlugin(TaskPlugin):
    name = "file_hash"
    description = "Compute SHA-256 checksums for files under an approved share path"

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec.get("resolved_path"):
            raise ValueError("resolved_path is required")
        return spec

    def run(self, ctx: PluginContext) -> dict[str, Any]:
        roots = ctx.spec.get("allowed_roots", [])
        root = _ensure_allowed(ctx.spec["resolved_path"], roots)
        files = list(_iter_files(root))
        manifest = []
        total_bytes = 0
        for index, path in enumerate(files):
            if ctx.cancelled:
                raise RuntimeError("cancelled")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            total_bytes += size
            manifest.append(
                {
                    "path": str(path.relative_to(root.parent if root.is_file() else root)),
                    "size": size,
                    "sha256": digest.hexdigest(),
                }
            )
            ctx.progress((index + 1) / max(len(files), 1), f"hashed {path.name}")
        return {
            "files": len(manifest),
            "total_bytes": total_bytes,
            "manifest": manifest[:5000],
        }


@register
class CompressPlugin(TaskPlugin):
    name = "compress"
    description = "Create a zip or tar.gz archive inside an approved share"

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        for key in ("resolved_path", "destination"):
            if not spec.get(key):
                raise ValueError(f"{key} is required")
        if spec.get("format", "zip") not in ("zip", "tar.gz", "tar"):
            raise ValueError("format must be zip, tar.gz or tar")
        return spec

    def run(self, ctx: PluginContext) -> dict[str, Any]:
        roots = ctx.spec.get("allowed_roots", [])
        source = _ensure_allowed(ctx.spec["resolved_path"], roots)
        destination = _ensure_allowed(ctx.spec["destination"], roots)
        fmt = ctx.spec.get("format", "zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        files = list(_iter_files(source))
        total = max(len(files), 1)
        started = time.time()
        if fmt == "zip":
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for index, path in enumerate(files):
                    if ctx.cancelled:
                        raise RuntimeError("cancelled")
                    arcname = path.name if source.is_file() else str(path.relative_to(source))
                    zf.write(path, arcname)
                    ctx.progress((index + 1) / total, f"compressed {path.name}")
        else:
            mode = "w:gz" if fmt == "tar.gz" else "w"
            with tarfile.open(destination, mode) as tf:
                for index, path in enumerate(files):
                    if ctx.cancelled:
                        raise RuntimeError("cancelled")
                    arcname = path.name if source.is_file() else str(path.relative_to(source))
                    tf.add(path, arcname=arcname)
                    ctx.progress((index + 1) / total, f"archived {path.name}")
        size = destination.stat().st_size
        return {
            "archive": str(destination),
            "size": size,
            "files": len(files),
            "elapsed_s": round(time.time() - started, 2),
        }


@register
class ImageResizePlugin(TaskPlugin):
    name = "image_resize"
    description = "Batch-resize images with Pillow (requires Pillow on the executor)"
    requires = ["Pillow"]

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        for key in ("resolved_path", "output_dir"):
            if not spec.get(key):
                raise ValueError(f"{key} is required")
        if int(spec.get("max_width", 1920)) <= 0 or int(spec.get("max_height", 1080)) <= 0:
            raise ValueError("max_width/max_height must be positive")
        return spec

    def run(self, ctx: PluginContext) -> dict[str, Any]:
        from PIL import Image  # imported lazily so other plugins work without it

        roots = ctx.spec.get("allowed_roots", [])
        source = _ensure_allowed(ctx.spec["resolved_path"], roots)
        out_dir = _ensure_allowed(ctx.spec["output_dir"], roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        max_w = int(ctx.spec.get("max_width", 1920))
        max_h = int(ctx.spec.get("max_height", 1080))
        quality = int(ctx.spec.get("quality", 85))
        target_format = ctx.spec.get("format", "same")
        files = [
            p for p in _iter_files(source)
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp")
        ]
        processed = []
        for index, path in enumerate(files):
            if ctx.cancelled:
                raise RuntimeError("cancelled")
            with Image.open(path) as img:
                img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
                img.thumbnail((max_w, max_h))
                fmt = target_format if target_format != "same" else (path.suffix.lstrip(".").lower() or "jpeg")
                fmt = "jpeg" if fmt == "jpg" else fmt
                suffix = ".jpg" if fmt == "jpeg" else f".{fmt}"
                dest = out_dir / f"{path.stem}{suffix}"
                save_kwargs = {"quality": quality} if fmt == "jpeg" else {}
                img.save(dest, fmt.upper(), **save_kwargs)
                processed.append({"source": str(path), "output": str(dest), "bytes": dest.stat().st_size})
            ctx.progress((index + 1) / max(len(files), 1), f"resized {path.name}")
        return {"images": len(processed), "outputs": processed[:2000], "output_dir": str(out_dir)}


@register
class TextStatsPlugin(TaskPlugin):
    name = "text_stats"
    description = "Count lines/words/characters for text files under an approved share"

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec.get("resolved_path"):
            raise ValueError("resolved_path is required")
        return spec

    def run(self, ctx: PluginContext) -> dict[str, Any]:
        roots = ctx.spec.get("allowed_roots", [])
        root = _ensure_allowed(ctx.spec["resolved_path"], roots)
        files = [p for p in _iter_files(root) if p.suffix.lower() in (".txt", ".md", ".csv", ".log", ".json")]
        results = []
        for index, path in enumerate(files):
            if ctx.cancelled:
                raise RuntimeError("cancelled")
            text = path.read_text(encoding="utf-8", errors="replace")
            results.append(
                {
                    "path": path.name,
                    "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                    "words": len(text.split()),
                    "chars": len(text),
                }
            )
            ctx.progress((index + 1) / max(len(files), 1), f"counted {path.name}")
        return {"files": results}


@register
class PythonTaskPlugin(TaskPlugin):
    name = "python"
    description = "Run a user-supplied Python script in an isolated subprocess"
    runs_user_code = True
    default_enabled = False

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        script = spec.get("script")
        if not isinstance(script, str) or not script.strip():
            raise ValueError("script is required")
        if len(script) > 512 * 1024:
            raise ValueError("script too large (max 512 KiB)")
        work_dir = spec.get("work_dir")
        if work_dir and not spec.get("allowed_roots"):
            raise ValueError("work_dir requires allowed_roots")
        return spec

    def run(self, ctx: PluginContext) -> dict[str, Any]:
        work_dir = ctx.spec.get("work_dir")
        if work_dir:
            work_dir = str(_ensure_allowed(work_dir, ctx.spec.get("allowed_roots", [])))
        else:
            work_dir = str(ctx.output_dir)
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        script_path = ctx.output_dir / "task_script.py"
        script_path.write_text(ctx.spec["script"], encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "GOLEM_TASK_ID": ctx.task_id,
        }
        proc = subprocess.run(
            [sys.executable, str(script_path), *map(str, ctx.spec.get("args", []))],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=ctx.spec.get("script_timeout_s", 300),
            env=env,
        )
        tail = (proc.stdout or "")[-20000:]
        err_tail = (proc.stderr or "")[-8000:]
        if proc.returncode != 0:
            raise RuntimeError(f"script exited with {proc.returncode}: {err_tail[-2000:]}")
        return {"exit_code": 0, "stdout": tail, "stderr": err_tail[-2000:]}


@register
class DiskBenchmarkPlugin(TaskPlugin):
    name = "disk_benchmark"
    description = "Write/read a temporary file to measure real disk throughput"

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        size_mb = int(spec.get("size_mb", 64))
        if not (1 <= size_mb <= 1024):
            raise ValueError("size_mb must be between 1 and 1024")
        spec["size_mb"] = size_mb
        return spec

    def run(self, ctx: PluginContext) -> dict[str, Any]:
        size = ctx.spec["size_mb"] * 1024 * 1024
        block = os.urandom(1024 * 1024)
        tmp = ctx.output_dir / "bench.bin"
        start = time.perf_counter()
        with tmp.open("wb") as fh:
            written = 0
            while written < size:
                fh.write(block)
                written += len(block)
                if ctx.cancelled:
                    raise RuntimeError("cancelled")
                ctx.progress(min(written / size, 0.95), f"wrote {written // (1024 * 1024)} MiB")
            fh.flush()
            os.fsync(fh.fileno())
        write_s = time.perf_counter() - start
        start = time.perf_counter()
        digest = hashlib.sha256()
        with tmp.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                digest.update(chunk)
        read_s = time.perf_counter() - start
        tmp.unlink(missing_ok=True)
        return {
            "size_bytes": size,
            "write_mbps": round(size / write_s / (1024 * 1024), 1),
            "read_mbps": round(size / read_s / (1024 * 1024), 1),
            "sha256": digest.hexdigest(),
        }
