"""Refuse accidental publication of media, models, databases, binaries and obvious secrets.

Checks Git-tracked files when available. In a source archive, checks the source tree while
ignoring known local/build directories. This is not a complete secret or vulnerability scanner.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {"work", "projects", "models", "tools", "cache", "exports", "logs", ".venv", "venv"}
SKIP_DIRS = PRIVATE_DIRS | {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", "htmlcov"}
PRIVATE_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".mp3", ".wav",
                    ".flac", ".aac", ".m4a", ".jpg", ".jpeg", ".png", ".webp", ".db", ".sqlite",
                    ".sqlite3", ".gguf", ".safetensors", ".onnx", ".pt", ".pth", ".bin", ".exe",
                    ".dll", ".so", ".dylib", ".pem", ".key", ".log"}
SECRETS = [re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
           re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
           re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
           re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{35,}\b")]


def source_files() -> list[Path]:
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                              capture_output=True, timeout=10, check=False)
        if proc.returncode == 0 and proc.stdout:
            return [ROOT / name.decode() for name in proc.stdout.split(b"\0") if name]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return [p for p in ROOT.rglob("*") if p.is_file()
            and not any(part in SKIP_DIRS or part.endswith(".egg-info") for part in p.relative_to(ROOT).parts)
            and not p.name.startswith(".coverage")]


def main() -> int:
    issues = []
    files = source_files()
    for path in files:
        rel = path.relative_to(ROOT)
        name = path.name.lower()
        if (any(part in PRIVATE_DIRS for part in rel.parts) or path.suffix.lower() in PRIVATE_SUFFIXES
                or name in {"project.json", "analysis.json", ".env"}
                or name.startswith(".env.") and name != ".env.example"
                or re.search(r"\.(db|sqlite|sqlite3)-(wal|shm|journal)$", name)
                or name.endswith(".local.toml") or name.startswith("cut-") and path.suffix in {".xml", ".json"}):
            issues.append(f"Private/generated artifact: {rel.as_posix()}")
        if path.is_symlink():
            issues.append(f"Symlink needs manual review: {rel.as_posix()}")
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            issues.append(f"Large source artifact (>2 MiB): {rel.as_posix()}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            issues.append(f"Non-text artifact needs manual review: {rel.as_posix()}")
            continue
        if any(pattern.search(text) for pattern in SECRETS):
            issues.append(f"Possible secret: {rel.as_posix()}")
    print(json.dumps({"checked_files": len(files), "issues": issues}, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
