"""Generate legal, synthetic test media. No personal footage or downloaded assets."""

from __future__ import annotations

from pathlib import Path

from .analysis import AnalysisSettings, analyze
from .export import export_plan
from .media import ingest, require_binary, run_media
from .planner import create_plan
from .project import Project, init_project
from .util import AutoEditorError, project_lock


def demo(root: Path) -> dict:
    root = root.resolve()
    if root.exists() and any(root.iterdir()):
        raise AutoEditorError("Demo destination must be empty; existing data will not be replaced.")
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        args = [
            require_binary("ffmpeg"), "-nostdin", "-v", "error", "-n",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24:duration={8 + index * 2}",
        ]
        if index != 1:
            args += ["-f", "lavfi", "-i", f"sine=frequency={330 + index * 110}:sample_rate=48000:duration={8 + index * 2}",
                     "-ac", "2", "-c:a", "aac"]
        args += ["-vf", f"hue=h={index * 60}", "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", "-shortest", str(media / f"synthetic_{index + 1:02d}.mp4")]
        run_media(args, timeout=120)
    init_project(root, media, "Synthetic demonstration - NOT an AI quality benchmark")
    with Project(root) as project, project_lock(root):
        inventory = ingest(project)
        analysis = analyze(project, AnalysisSettings(window=4, stride=3, sample_step=0.5))
        plan = create_plan(project, duration=8, fps="24", width=1280, height=720)
        xml, report = export_plan(project, plan)
        return {"inventory": inventory, "analysis": analysis, "xml": str(xml), "report": str(report),
                "note": "Technical-only demonstration; no semantic model was run."}
