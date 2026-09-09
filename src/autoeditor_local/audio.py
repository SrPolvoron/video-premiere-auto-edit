"""Optional beat analysis. Beats are not downbeats, musical sections, or drops."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .media import probe, require_binary, run_media
from .project import Project
from .util import AutoEditorError, digest, file_hash, finite, read_json, write_json


def music_info(project: Project, path: Path, duration: float, offset: float = 0,
               sync: bool = True) -> dict:
    path = path.resolve()
    finite(offset, "music offset", 0, 86400)
    finite(duration, "music analysis duration", 0.5, 600)
    metadata = probe(path, require_video=False)
    if offset >= metadata["duration"]:
        raise AutoEditorError("Music offset is beyond the end of the song.")
    analyzed_duration = min(duration, metadata["duration"] - offset)
    fingerprint = file_hash(path)
    key = digest([fingerprint, offset, analyzed_duration, sync, "librosa-beat-v1"])
    cache = project.root / "cache" / "audio" / f"{key}.json"
    if cache.exists():
        result = read_json(cache)
    else:
        beats, tempo = [], 0.0
        if sync:
            try:
                import librosa
            except ImportError as exc:
                raise AutoEditorError('Beat sync requires: python -m pip install ".[audio]"') from exc
            raw = run_media([
                require_binary("ffmpeg"), "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-ss", str(offset), "-i", str(path), "-t", str(analyzed_duration),
                "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "22050", "-f", "f32le", "pipe:1",
            ], timeout=180)
            waveform = np.frombuffer(raw, dtype="<f4").copy()
            if waveform.size and np.max(np.abs(waveform)) > 1e-6:
                tempo_array, times = librosa.beat.beat_track(y=waveform, sr=22050, units="time")
                tempo = float(np.asarray(tempo_array).reshape(-1)[0])
                beats = [float(t) for t in times if 0 < t < analyzed_duration]
        result = {"fingerprint": fingerprint, "metadata": metadata, "offset": offset,
                  "available_duration": analyzed_duration, "beats": beats, "tempo_estimate": tempo,
                  "method": "librosa-beat-track" if sync else "disabled"}
        write_json(cache, result)
    try:
        result["path"] = os.path.relpath(path, project.root)
    except ValueError:
        result["path"] = str(path)
    return result
