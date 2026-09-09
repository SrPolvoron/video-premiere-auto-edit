"""Project storage: versioned analyses, immutable plans, and explicit user feedback."""

from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager, closing
from pathlib import Path
from typing import Any

from .util import AutoEditorError, file_hash, now, read_json, write_json

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    metadata TEXT NOT NULL,
    active_key TEXT,
    present INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS analyses (
    key TEXT PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id),
    fingerprint TEXT NOT NULL,
    config TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS windows (
    id TEXT PRIMARY KEY,
    analysis_key TEXT NOT NULL REFERENCES analyses(key),
    start REAL NOT NULL,
    end REAL NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    CHECK(end > start AND start >= 0)
);
CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL REFERENCES windows(id),
    analysis_key TEXT NOT NULL REFERENCES analyses(key),
    media_id INTEGER NOT NULL REFERENCES media(id),
    start REAL NOT NULL,
    end REAL NOT NULL,
    anchor REAL NOT NULL,
    label TEXT NOT NULL,
    stage TEXT NOT NULL,
    shot TEXT NOT NULL,
    quality REAL NOT NULL,
    interest REAL NOT NULL,
    confidence REAL NOT NULL,
    prediction TEXT NOT NULL,
    CHECK(end > start AND start >= 0),
    CHECK(anchor >= start AND anchor <= end)
);
CREATE INDEX IF NOT EXISTS segments_analysis ON segments(analysis_key);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES segments(id),
    decision TEXT NOT NULL CHECK(decision IN ('prefer','reject','neutral')),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def init_project(root: Path, media_root: Path, name: str | None = None) -> Path:
    root, media_root = root.resolve(), media_root.resolve()
    if not media_root.is_dir():
        raise AutoEditorError(f"Media directory does not exist: {media_root}")
    if (root / "project.json").exists() or (root / "project.db").exists():
        raise AutoEditorError(f"Project already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "project.json", {
        "schema_version": SCHEMA_VERSION,
        "name": name or root.name,
        # Relative when possible, including across siblings; absolute across Windows drives.
        "media_root": _relative_root(root, media_root),
        "created_at": now(),
    })
    (root / "cache").mkdir(exist_ok=True)
    (root / "exports").mkdir(exist_ok=True)
    with Project(root):
        pass
    return root


def _relative_root(root: Path, media_root: Path) -> str:
    import os
    try:
        return os.path.relpath(media_root, root)
    except ValueError:
        return str(media_root)


class Project(AbstractContextManager):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config = read_json(self.root / "project.json")
        if self.config.get("schema_version") != SCHEMA_VERSION:
            raise AutoEditorError("Unsupported project version. Do not overwrite this database.")
        self.db = sqlite3.connect(self.root / "project.db", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        # One writer and local/external disks: DELETE avoids persistent WAL sidecars.
        self.db.execute("PRAGMA journal_mode = DELETE")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.db.close()
            raise AutoEditorError("Database schema is newer than this application.")
        self.db.executescript(SCHEMA)
        self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.db.commit()

    @property
    def media_root(self) -> Path:
        return (self.root / self.config["media_root"]).resolve()

    def __exit__(self, *args):
        self.db.close()
        return False

    def rows(self, query: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute(query, args)]

    def media(self) -> list[dict[str, Any]]:
        result = self.rows("SELECT * FROM media WHERE present=1 ORDER BY relative_path")
        for row in result:
            row["metadata"] = json.loads(row["metadata"])
        return result

    def segments(self, include_partial: bool = False) -> list[dict[str, Any]]:
        query = """
        SELECT s.*, m.relative_path, m.fingerprint, m.metadata,
               (SELECT f.decision FROM feedback f WHERE f.segment_id=s.id
                ORDER BY f.id DESC LIMIT 1) AS decision
        FROM segments s JOIN media m ON s.media_id=m.id
        JOIN analyses a ON s.analysis_key=a.key
        WHERE m.present=1 AND s.analysis_key=m.active_key
        """
        if not include_partial:
            query += " AND a.status='complete'"
        result = self.rows(query + " ORDER BY m.relative_path,s.start")
        for row in result:
            row["metadata"] = json.loads(row["metadata"])
            row["prediction"] = json.loads(row["prediction"])
        return result

    def feedback(self, segment_id: str, decision: str, note: str = "") -> None:
        if decision not in {"prefer", "reject", "neutral"}:
            raise AutoEditorError("Feedback must be prefer, reject or neutral.")
        if len(note) > 2000:
            raise AutoEditorError("Feedback note is limited to 2000 characters.")
        if not self.db.execute("SELECT 1 FROM segments WHERE id=?", (segment_id,)).fetchone():
            raise AutoEditorError(f"Unknown segment ID: {segment_id}")
        with self.db:
            self.db.execute(
                "INSERT INTO feedback(segment_id,decision,note,created_at) VALUES (?,?,?,?)",
                (segment_id, decision, note, now()),
            )

    def relink(self, new_root: Path) -> None:
        new_root = new_root.resolve()
        missing = [m["relative_path"] for m in self.media()
                   if not (new_root / m["relative_path"]).is_file()]
        if missing:
            raise AutoEditorError(f"Relink refused: missing {len(missing)} files, e.g. {missing[0]}")
        for media in self.media():
            source = (new_root / media["relative_path"]).resolve()
            if not source.is_relative_to(new_root) or file_hash(source) != media["fingerprint"]:
                raise AutoEditorError(f"Relink refused: changed original {media['relative_path']}")
        self.config["media_root"] = _relative_root(self.root, new_root)
        write_json(self.root / "project.json", self.config)

    def backup(self, path: Path) -> None:
        path = path.resolve()
        if path.exists():
            raise AutoEditorError("Backup destination already exists; refusing to overwrite.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as target:
            self.db.backup(target)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.config["name"],
            "media_root": str(self.media_root),
            "media": len(self.media()),
            "complete_segments": len(self.segments()),
            "analyses": self.rows("SELECT status,COUNT(*) AS count FROM analyses GROUP BY status"),
            "plans": self.rows("SELECT id,created_at FROM plans ORDER BY created_at"),
        }
