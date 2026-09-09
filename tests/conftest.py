from __future__ import annotations


import pytest

from autoeditor_local.project import Project, init_project
from autoeditor_local.util import file_hash, json_text, now


@pytest.fixture
def catalog_project(tmp_path):
    root = tmp_path / "project"
    media_root = tmp_path / "media with spaces"
    media_root.mkdir()
    init_project(root, media_root, "Test & project")
    with Project(root) as project:
        stages = ["preparation", "departure", "action", "action", "detail", "pause", "closing", "establishing"]
        for index, stage in enumerate(stages):
            path = media_root / f"clip {index + 1} & sample.mp4"
            path.write_bytes(f"synthetic placeholder {index}".encode())
            stat = path.stat()
            metadata = {"duration": 20.0, "fps": "30", "nominal_fps": "30", "width": 1920,
                        "height": 1080, "rotation": 0, "audio_channels": 2 if index % 2 == 0 else 0,
                        "audio_sample_rate": 48000, "audio_streams": 1 if index % 2 == 0 else 0,
                        "audio_start": 0.0, "video_start": 0.0, "vfr_suspected": False,
                        "color_transfer": "bt709", "creation_time": f"2026-01-01T10:{index:02d}:00Z"}
            fingerprint = file_hash(path)
            key = f"analysis-{index}"
            with project.db:
                cursor = project.db.execute(
                    "INSERT INTO media(relative_path,fingerprint,size,mtime_ns,metadata,active_key) VALUES(?,?,?,?,?,?)",
                    (path.name, fingerprint, stat.st_size, stat.st_mtime_ns, json_text(metadata), key))
                media_id = cursor.lastrowid
                project.db.execute("INSERT INTO analyses(key,media_id,fingerprint,config,status,created_at) VALUES(?,?,?,?,?,?)",
                                   (key, media_id, fingerprint, "{}", "complete", now()))
                project.db.execute("INSERT INTO windows(id,analysis_key,start,end,status) VALUES(?,?,?,?,?)",
                                   (f"window-{index}", key, 0, 20, "complete"))
                project.db.execute(
                    "INSERT INTO segments(id,window_id,analysis_key,media_id,start,end,anchor,label,stage,shot,quality,interest,confidence,prediction) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"segment-{index}", f"window-{index}", key, media_id, 0.0, 20.0, 10.0,
                     f"Example {stage}", stage, "pov" if index == 2 else "wide", 0.9, 0.8, 0.7,
                     json_text({"backend": "test-double", "tags": [stage], "metrics": {"dhash": f"{index * 12345:016x}"}})))
        yield project
