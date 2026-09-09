from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET

import pytest

from autoeditor_local.analysis import AnalysisSettings, analyze
from autoeditor_local.cli import main
from autoeditor_local.demo import demo
from autoeditor_local.media import ingest, probe, require_binary, run_media
from autoeditor_local.project import Project, init_project
from autoeditor_local.util import AutoEditorError

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is not installed")]


@pytest.fixture
def real_project(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    run_media([require_binary("ffmpeg"), "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
               "testsrc2=size=320x180:rate=30:duration=9", "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", str(media / "real.mp4")])
    root = tmp_path / "project"
    init_project(root, media)
    with Project(root) as p:
        ingest(p)
        yield p


def test_full_demo(tmp_path):
    result = demo(tmp_path / "demo")
    assert result["inventory"]["added"] == 3
    assert result["analysis"]["completed_media"] == 3
    assert ET.parse(result["xml"]).getroot().tag == "xmeml"


def test_resume_without_duplicate_segments(real_project):
    settings = AnalysisSettings(window=4, stride=3)
    first = analyze(real_project, settings, max_windows=1)
    assert first["paused"]
    assert len(real_project.segments()) == 0  # Partial analysis is not silently used in a plan.
    second = analyze(real_project, settings)
    assert second["completed_media"] == 1
    segments = real_project.segments()
    assert len(segments) == 3
    assert len({s["id"] for s in segments}) == 3
    third = analyze(real_project, settings)
    assert third["cached_media"] == 1 and third["new_windows"] == 0


def test_ingest_incremental(real_project):
    assert ingest(real_project)["unchanged"] == 1
    assert ingest(real_project, verify=True)["unchanged"] == 1
    (real_project.media_root / "real.mp4").unlink()
    assert ingest(real_project)["missing"] == 1
    assert real_project.media() == []


def test_changed_original_invalidates_active_analysis(real_project):
    analyze(real_project, AnalysisSettings(window=4, stride=3))
    run_media([require_binary("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
               "testsrc2=size=320x180:rate=30:duration=5", "-c:v", "libx264", "-preset", "ultrafast",
               str(real_project.media_root / "real.mp4")])
    assert ingest(real_project)["changed"] == 1
    assert real_project.segments() == []
    assert real_project.db.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 3


class VisionDouble:
    signature = "unit-test-model-not-real-inference"
    calls = 0

    def describe(self, frames, duration):
        self.calls += 1
        assert all(path.is_file() for _, path in frames)
        return [{"start": 0.0, "end": duration / 2, "anchor": duration / 4,
                 "label": "helmet (test double)", "stage": "preparation", "shot": "medium",
                 "interest": 0.8, "confidence": 0.6, "tags": ["helmet"]},
                {"start": duration / 2, "end": duration, "anchor": 3 * duration / 4,
                 "label": "gloves (test double)", "stage": "preparation", "shot": "close",
                 "interest": 0.7, "confidence": 0.6, "tags": ["gloves"]}]


def test_multiaction_contract_not_real_semantic_quality(real_project):
    model = VisionDouble()
    result = analyze(real_project, AnalysisSettings(window=4, stride=3), model=model)
    assert result["new_windows"] == 3
    assert len(real_project.segments()) == 6
    assert model.calls == 3


def test_model_failure_persists_and_can_resume(real_project):
    class Failing(VisionDouble):
        def describe(self, frames, duration):
            raise AutoEditorError("test invalid model output")
    with pytest.raises(AutoEditorError):
        analyze(real_project, AnalysisSettings(window=4, stride=3), model=Failing())
    assert real_project.db.execute("SELECT status FROM windows").fetchone()[0] == "failed"
    result = analyze(real_project, AnalysisSettings(window=4, stride=3), model=VisionDouble())
    assert result["completed_media"] == 1


def test_cache_clear_does_not_remove_completed_index(real_project, capsys):
    analyze(real_project, AnalysisSettings(window=4, stride=3))
    assert main(["clean-cache", str(real_project.root), "--yes"]) == 0
    assert len(real_project.segments()) == 3
    assert (real_project.media_root / "real.mp4").exists()
    again = analyze(real_project, AnalysisSettings(window=4, stride=3))
    assert again["cached_media"] == 1


def test_probe_real_cfr(real_project):
    info = probe(real_project.media_root / "real.mp4")
    assert info["fps"] == "30"
    assert info["vfr_suspected"] is False
    assert info["duration"] == 9


def test_single_command_cpu_run(real_project, capsys):
    code = main(["run", str(real_project.root), "--backend", "technical", "--duration", "4", "--fps", "30"])
    assert code == 0
    assert (real_project.root / "exports" / "cut-0001.xml").exists()
