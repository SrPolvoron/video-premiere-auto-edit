"""Resumable temporal-window analysis; sampling is not scene-cut detection."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .media import check_unchanged, samples, technical_metrics
from .project import Project
from .util import AutoEditorError, digest, finite, json_text, now, read_json, write_json

LOGGER = logging.getLogger(__name__)
ANALYZER_VERSION = 1


@dataclass(frozen=True)
class AnalysisSettings:
    window: float = 8
    stride: float = 6
    sample_step: float = 0.5
    frames_per_window: int = 8
    longest: int = 384
    cache_mb: int = 2048
    decode_timeout: int = 3600

    def validate(self):
        finite(self.window, "window", 1, 60)
        finite(self.stride, "stride", 0.25, self.window)
        finite(self.sample_step, "sample_step", 0.1, self.window / 2)
        finite(self.frames_per_window, "frames_per_window", 2, 16)
        finite(self.longest, "longest", 128, 1024)
        finite(self.cache_mb, "cache_mb", 1, 102400)
        finite(self.decode_timeout, "decode_timeout", 10, 86400)


def windows(duration: float, width: float, stride: float):
    start = 0.0
    while start < duration - 0.15:
        end = min(duration, start + width)
        yield round(start, 6), round(end, 6)
        if end == duration:
            break
        start += stride


def cache_samples(project: Project, media: dict, settings: AnalysisSettings) -> list[dict]:
    path = check_unchanged(project, media)
    key = digest({"file": media["fingerprint"], "step": settings.sample_step,
                  "longest": settings.longest, "version": ANALYZER_VERSION})
    directory = project.root / "cache" / key
    index = directory / "samples.json"
    if index.exists():
        cached = read_json(index)
        if cached and all((directory / row["image"]).is_file() for row in cached):
            for row in cached:
                row["path"] = directory / row["image"]
            return cached
    directory.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in (project.root / "cache").rglob("*") if p.is_file())
    limit = settings.cache_mb * 1024 * 1024
    rows, previous = [], None
    for i, (timestamp, image) in enumerate(samples(path, media["metadata"], settings.sample_step,
                                                settings.longest, settings.decode_timeout)):
        target = directory / f"{i:07d}.jpg"
        old_size = target.stat().st_size if target.exists() else 0
        image.save(target, format="JPEG", quality=80)
        used += target.stat().st_size - old_size
        if used > limit:
            target.unlink()
            raise AutoEditorError("Thumbnail cache limit reached. Increase --cache-mb or clear old cache.")
        rows.append({"time": timestamp, "image": target.name,
                     "metrics": technical_metrics(image, previous)})
        previous = image
    if not rows:
        raise AutoEditorError(f"No frames decoded from {media['relative_path']}")
    write_json(index, rows)
    for row in rows:
        row["path"] = directory / row["image"]
    return rows


def _select_frames(rows: list[dict], start: float, end: float, count: int) -> list[dict]:
    subset = [r for r in rows if start <= r["time"] < end]
    if not subset:
        subset = [min(rows, key=lambda r: abs(r["time"] - (start + end) / 2))]
    indices = sorted(set(np.linspace(0, len(subset) - 1, min(count, len(subset)), dtype=int)))
    return [subset[i] for i in indices]


def analyze(project: Project, settings: AnalysisSettings, model=None,
            max_windows: int | None = None, continue_on_error: bool = False) -> dict[str, Any]:
    settings.validate()
    if max_windows is not None and max_windows < 1:
        raise AutoEditorError("max_windows must be positive.")
    backend = "llama" if model is not None else "technical"
    config = {"analyzer_version": ANALYZER_VERSION, "settings": asdict(settings),
              "backend": backend, "model_signature": model.signature if model else None}
    # Cache budget/timeout do not change predictions and must not invalidate completed windows.
    config["settings"].pop("cache_mb")
    config["settings"].pop("decode_timeout")
    stats = {"backend": backend, "completed_media": 0, "cached_media": 0,
             "new_windows": 0, "failed_windows": 0, "paused": False}
    inventory = project.media()
    if not inventory:
        raise AutoEditorError("No videos indexed. Run ingest first.")
    for media in inventory:
        check_unchanged(project, media)
        key = digest({"media_id": media["id"], "file": media["fingerprint"], "config": config})
        existing = project.db.execute("SELECT status FROM analyses WHERE key=?", (key,)).fetchone()
        if existing and existing["status"] == "complete":
            with project.db:
                project.db.execute("UPDATE media SET active_key=? WHERE id=?", (key, media["id"]))
            stats["cached_media"] += 1
            continue
        if max_windows is not None and stats["new_windows"] >= max_windows:
            stats["paused"] = True
            break
        with project.db:
            project.db.execute(
                "INSERT INTO analyses(key,media_id,fingerprint,config,status,created_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET status='running'",
                (key, media["id"], media["fingerprint"], json_text(config), "running", now()),
            )
        LOGGER.info("Sampling %s (%0.1f s)", media["relative_path"], media["metadata"]["duration"])
        rows = cache_samples(project, media, settings)
        failed, paused = False, False
        for start, end in windows(media["metadata"]["duration"], settings.window, settings.stride):
            window_id = digest([key, start, end])[:24]
            done = project.db.execute("SELECT status FROM windows WHERE id=?", (window_id,)).fetchone()
            if done and done["status"] == "complete":
                continue
            if max_windows is not None and stats["new_windows"] >= max_windows:
                paused = True
                break
            selected = _select_frames(rows, start, end, settings.frames_per_window)
            with project.db:
                project.db.execute(
                    "INSERT INTO windows(id,analysis_key,start,end,status) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET status='running',error=NULL",
                    (window_id, key, start, end, "running"),
                )
            try:
                if model:
                    actions = model.describe([(max(0, r["time"] - start), r["path"]) for r in selected], end - start)
                else:
                    best = max(selected, key=lambda row: row["metrics"]["quality"])
                    actions = [{"start": 0, "end": end - start,
                                "anchor": max(0, min(end - start, best["time"] - start)),
                                "label": "Unclassified technical candidate", "stage": "unknown",
                                "shot": "unknown", "tags": [], "interest": 0.5, "confidence": 0.0}]
                # An empty semantic result is not fabricated into an invented action.
                with project.db:
                    for action in actions:
                        absolute_start, absolute_end = start + action["start"], start + action["end"]
                        anchor = start + action["anchor"]
                        center = min(selected, key=lambda row: abs(row["time"] - anchor))
                        metrics = center["metrics"]
                        segment_id = digest([window_id, action["start"], action["end"], action["label"]])[:24]
                        prediction = {"backend": backend, "model_signature": config["model_signature"],
                                      "tags": action["tags"], "metrics": metrics,
                                      "thumbnail": center["path"].relative_to(project.root).as_posix(),
                                      "boundary_precision": "sampled_estimate", "sample_step": settings.sample_step,
                                      "analyzed_frame_times": [r["time"] for r in selected]}
                        project.db.execute(
                            "INSERT INTO segments(id,window_id,analysis_key,media_id,start,end,anchor,label,"
                            "stage,shot,quality,interest,confidence,prediction) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (segment_id, window_id, key, media["id"], absolute_start, absolute_end, anchor,
                             action["label"], action["stage"], action["shot"], metrics["quality"],
                             action["interest"], action["confidence"], json_text(prediction)),
                        )
                    project.db.execute("UPDATE windows SET status='complete',error=NULL WHERE id=?", (window_id,))
                stats["new_windows"] += 1
                LOGGER.info("%s [%0.1f..%0.1f]: %d candidate(s)", media["relative_path"], start, end, len(actions))
            except AutoEditorError as exc:
                with project.db:
                    project.db.execute("UPDATE windows SET status='failed',error=? WHERE id=?", (str(exc)[:2000], window_id))
                    project.db.execute("UPDATE analyses SET status='failed' WHERE key=?", (key,))
                stats["failed_windows"] += 1
                failed = True
                if not continue_on_error:
                    raise
                LOGGER.warning("Window failed: %s", exc)
        with project.db:
            status = "paused" if paused else ("failed" if failed else "complete")
            project.db.execute("UPDATE analyses SET status=?,completed_at=? WHERE key=?",
                               (status, now() if status == "complete" else None, key))
            if status == "complete":
                project.db.execute("UPDATE media SET active_key=? WHERE id=?", (key, media["id"]))
                stats["completed_media"] += 1
        if paused:
            stats["paused"] = True
            break
    return stats
