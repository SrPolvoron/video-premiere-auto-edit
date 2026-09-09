"""Small, dependency-free validation and filesystem helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any


class AutoEditorError(Exception):
    """An actionable error safe to present without a Python traceback."""


def now() -> str:
    return datetime.now(UTC).isoformat()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AutoEditorError(f"Cannot read JSON {path}: {exc}") from exc


def finite(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutoEditorError(f"{name} must be a finite number.")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise AutoEditorError(f"{name} must be between {low} and {high}.")
    return value


def fps_fraction(value: str | int | float) -> Fraction:
    aliases = {"23.976": "24000/1001", "29.97": "30000/1001",
               "59.94": "60000/1001", "119.88": "120000/1001"}
    try:
        rate = Fraction(aliases.get(str(value), str(value))).limit_denominator(100_000)
    except (ValueError, ZeroDivisionError) as exc:
        raise AutoEditorError(f"Invalid frame rate: {value}") from exc
    if not 1 <= rate <= 240:
        raise AutoEditorError(f"Unsupported frame rate: {value}; expected 1..240.")
    return rate


def seconds_to_frames(seconds: float, fps: Fraction) -> int:
    # Positive, half-up rounding; do not accumulate float-duration errors on the timeline.
    value = Fraction(str(seconds)) * fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def local_media(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AutoEditorError(f"Media path escapes the configured root: {relative}")
    if not path.is_file():
        raise AutoEditorError(f"Missing media: {path}. Use relink if the drive letter changed.")
    return path


@contextmanager
def project_lock(root: Path):
    """Single writer per project. Never silently steal a stale process lock."""
    path = root / ".autoeditor.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AutoEditorError(
            f"Project is locked: {path}. Close its worker. If it crashed, verify no worker "
            "is running before deleting this lock file."
        ) from exc
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(f"pid={os.getpid()}\ncreated={now()}\n")
        yield
    finally:
        path.unlink(missing_ok=True)
