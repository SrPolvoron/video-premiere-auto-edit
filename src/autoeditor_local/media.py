"""FFprobe ingestion and bounded-memory FFmpeg sampling. Originals are read-only."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .project import Project
from .util import AutoEditorError, file_hash, fps_fraction, json_text, local_media

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".m2ts"}


def require_binary(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise AutoEditorError(f"{name} is not in PATH. Install FFmpeg/FFprobe before processing media.")
    return executable


def run_media(args: list[str], timeout: float = 120) -> bytes:
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutoEditorError(f"Media process failed: {exc}") from exc
    if result.returncode:
        error = result.stderr.decode(errors="replace")[-2000:]
        raise AutoEditorError(f"Media process exited {result.returncode}: {error}")
    return result.stdout


def probe(path: Path, require_video: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise AutoEditorError(f"File does not exist: {path}")
    raw = run_media([
        require_binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams", "-of", "json", str(path.resolve()),
    ])
    data = json.loads(raw)
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if require_video and not videos:
        raise AutoEditorError(f"No video stream: {path}")
    stream = videos[0] if require_video else (audios[0] if audios else {})
    if not stream:
        raise AutoEditorError(f"No usable stream: {path}")
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise AutoEditorError(f"Unknown or invalid duration: {path}")
    rate = fps_fraction(stream.get("avg_frame_rate", "0/0")) if require_video else Fraction(30)
    nominal = stream.get("r_frame_rate", str(rate))
    try:
        suspect = require_video and abs(float(Fraction(nominal) / rate) - 1) > 0.002
    except (ValueError, ZeroDivisionError):
        suspect = True
    rotation = int(stream.get("tags", {}).get("rotate", 0))
    for side_data in stream.get("side_data_list", []):
        rotation = int(side_data.get("rotation", rotation))
    return {
        "duration": duration,
        "fps": str(rate),
        "nominal_fps": nominal,
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "rotation": rotation,
        "codec": stream.get("codec_name", "unknown"),
        "video_stream_index": stream.get("index", 0) if require_video else None,
        "audio_channels": int(audios[0].get("channels", 0)) if audios else 0,
        "audio_sample_rate": int(audios[0].get("sample_rate", 48000)) if audios else 48000,
        "audio_streams": len(audios),
        "audio_start": float(audios[0].get("start_time", 0)) if audios else 0,
        "video_start": float(stream.get("start_time", 0)),
        "creation_time": stream.get("tags", {}).get("creation_time")
            or data.get("format", {}).get("tags", {}).get("creation_time"),
        "color_transfer": stream.get("color_transfer", "unknown"),
        "color_primaries": stream.get("color_primaries", "unknown"),
        "vfr_suspected": suspect,
        "timing_check": "metadata_only_not_a_full_CFR_verification",
    }


def ingest(project: Project, verify: bool = False) -> dict[str, int]:
    root = project.media_root
    if not root.is_dir():
        raise AutoEditorError(f"Media root unavailable: {root}; use relink.")
    # No symlink traversal: never index files outside the selected local directory.
    paths = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in VIDEO_EXTENSIONS
                   and p.resolve().is_relative_to(root))
    stats = {"added": 0, "changed": 0, "unchanged": 0, "missing": 0}
    seen = set()
    # Transactional inventory: a corrupt new clip cannot mark all existing clips missing.
    with project.db:
        for path in paths:
            rel = path.relative_to(root).as_posix()
            seen.add(rel)
            stat = path.stat()
            old = project.db.execute("SELECT * FROM media WHERE relative_path=?", (rel,)).fetchone()
            if old and not verify and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns:
                project.db.execute("UPDATE media SET present=1 WHERE id=?", (old["id"],))
                stats["unchanged"] += 1
                continue
            fingerprint = file_hash(path)
            if old and old["fingerprint"] == fingerprint:
                project.db.execute("UPDATE media SET size=?,mtime_ns=?,present=1 WHERE id=?",
                                   (stat.st_size, stat.st_mtime_ns, old["id"]))
                stats["unchanged"] += 1
                continue
            metadata = probe(path)
            if old:
                project.db.execute(
                    "UPDATE media SET fingerprint=?,size=?,mtime_ns=?,metadata=?,active_key=NULL,"
                    "present=1 WHERE id=?",
                    (fingerprint, stat.st_size, stat.st_mtime_ns, json_text(metadata), old["id"]),
                )
                stats["changed"] += 1
            else:
                project.db.execute(
                    "INSERT INTO media(relative_path,fingerprint,size,mtime_ns,metadata) VALUES(?,?,?,?,?)",
                    (rel, fingerprint, stat.st_size, stat.st_mtime_ns, json_text(metadata)),
                )
                stats["added"] += 1
        for row in project.db.execute("SELECT id,relative_path,present FROM media").fetchall():
            if row["relative_path"] not in seen:
                project.db.execute("UPDATE media SET present=0 WHERE id=?", (row["id"],))
                stats["missing"] += 1
    return stats


def check_unchanged(project: Project, media: dict) -> Path:
    path = local_media(project.media_root, media["relative_path"])
    stat = path.stat()
    if stat.st_size != media["size"] or stat.st_mtime_ns != media["mtime_ns"]:
        raise AutoEditorError(f"Media changed: {media['relative_path']}. Run ingest again.")
    return path


def sample_dimensions(metadata: dict, longest: int = 384) -> tuple[int, int]:
    width, height = metadata["width"], metadata["height"]
    if abs(metadata.get("rotation", 0)) % 180 == 90:
        width, height = height, width
    scale = min(1.0, longest / max(width, height))
    return max(2, round(width * scale)), max(2, round(height * scale))


def samples(path: Path, metadata: dict, step: float = 0.5,
            longest: int = 384, timeout: float = 3600) -> Iterator[tuple[float, Image.Image]]:
    """Decode sequentially, yield RGB samples, and kill a stalled decoder on timeout.

    A full decode is still required. Only the expensive semantic stage is sparsely sampled.
    """
    width, height = sample_dimensions(metadata, longest)
    size = width * height * 3
    command = [
        require_binary("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", "file,pipe", "-i", str(path.resolve()),
        "-map", f"0:{metadata['video_stream_index']}", "-an", "-sn", "-dn",
        "-vf", f"setpts=PTS-STARTPTS,fps=fps=1/{step}:start_time=0,scale={width}:{height}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
        expired = threading.Event()
        def stop():
            expired.set()
            proc.kill()
        timer = threading.Timer(timeout, stop)
        timer.daemon = True
        timer.start()
        try:
            index = 0
            assert proc.stdout is not None
            while True:
                # Buffered pipe read normally fills n; explicitly handle short reads too.
                chunks = bytearray()
                while len(chunks) < size:
                    chunk = proc.stdout.read(size - len(chunks))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                if not chunks:
                    break
                if len(chunks) != size:
                    raise AutoEditorError("FFmpeg ended with an incomplete raw frame.")
                timestamp = index * step
                if timestamp < metadata["duration"]:
                    yield timestamp, Image.frombytes("RGB", (width, height), bytes(chunks))
                index += 1
            code = proc.wait(timeout=15)
            if expired.is_set():
                raise AutoEditorError("FFmpeg sampling timed out. Increase --decode-timeout.")
            if code:
                errors.seek(0)
                raise AutoEditorError(errors.read().decode(errors="replace")[-2000:])
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=15)
            if proc.stdout:
                proc.stdout.close()


def technical_metrics(image: Image.Image, previous: Image.Image | None) -> dict[str, Any]:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    laplacian = (4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1]
                 - gray[1:-1, :-2] - gray[1:-1, 2:])
    blur_proxy = float(laplacian.var()) if laplacian.size else 0.0
    dark = float(np.mean(gray < 12))
    bright = float(np.mean(gray > 243))
    exposure_proxy = max(0.0, 1 - dark - bright)
    sharpness_proxy = min(1.0, math.log1p(blur_proxy) / math.log(1001))
    difference = 0.0
    if previous is not None and previous.size == image.size:
        difference = float(np.mean(np.abs(gray - np.asarray(previous.convert("L"), dtype=np.float32))) / 255)
    small = np.asarray(image.convert("L").resize((9, 8)), dtype=np.uint8)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    dhash = sum(int(bit) << i for i, bit in enumerate(bits))
    # Motion is measured but NOT penalized: action footage and flowing water legitimately move.
    return {
        "laplacian_variance": round(blur_proxy, 3), "dark_fraction": round(dark, 4),
        "bright_fraction": round(bright, 4), "frame_difference": round(difference, 4),
        "quality": round(0.65 * exposure_proxy + 0.35 * sharpness_proxy, 4),
        "dhash": f"{dhash:016x}",
        "kind": "heuristic_not_calibrated_perceptual_quality",
    }
