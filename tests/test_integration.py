from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET

import pytest

from autoeditor_local.analysis import AnalysisSettings, analyze
from autoeditor_local.audio import music_info
from autoeditor_local.cli import main
from autoeditor_local.demo import demo
from autoeditor_local.export import export_plan
from autoeditor_local.media import ingest, probe, require_binary, run_media
from autoeditor_local.planner import create_plan
from autoeditor_local.project import Project, init_project
from autoeditor_local.util import AutoEditorError, file_hash

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


def test_auto_conform_is_cached_and_preserves_original(real_project):
    analyze(real_project, AnalysisSettings(window=4, stride=3))
    media = real_project.media()[0]
    metadata = media["metadata"]
    metadata.update(
        fps="742343/24665", nominal_fps="30", vfr_suspected=True,
        timing_check="forced-vfr-integration-fixture",
    )
    with real_project.db:
        real_project.db.execute(
            "UPDATE media SET metadata=? WHERE id=?", (json.dumps(metadata), media["id"]),
        )
    plan = create_plan(real_project, duration=4)
    original = real_project.media_root / media["relative_path"]
    original_hash = file_hash(original)

    xml, first_report_path = export_plan(real_project, plan)
    first = json.loads(first_report_path.read_text(encoding="utf-8"))
    first_resolution = next(iter(first["export_validation"]["timing_resolutions"].values()))
    assert first_resolution["method"] == "auto-conform"
    assert first_resolution["conform_cache_hit"] is False
    path_url = ET.parse(xml).findtext(".//file/pathurl")
    assert "/cache/conform/" in path_url.replace("%5C", "/").replace("\\", "/")

    _, second_report_path = export_plan(real_project, plan, timing="auto", overwrite=True)
    second = json.loads(second_report_path.read_text(encoding="utf-8"))
    second_resolution = next(iter(second["export_validation"]["timing_resolutions"].values()))
    assert second_resolution["conform_cache_hit"] is True
    assert file_hash(original) == original_hash
    assert list((real_project.root / "cache" / "conform").glob("*.mov"))


def test_real_multistream_source_muted_keeps_music(tmp_path):
    media_root = tmp_path / "multistream-media"
    media_root.mkdir()
    source = media_root / "two-audio-streams.mp4"
    song = tmp_path / "music.wav"
    run_media([
        require_binary("ffmpeg"), "-nostdin", "-v", "error", "-n",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=6",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ])
    run_media([
        require_binary("ffmpeg"), "-nostdin", "-v", "error", "-n",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=8",
        "-c:a", "pcm_s16le", str(song),
    ])
    project_root = tmp_path / "multistream-project"
    init_project(project_root, media_root)
    with Project(project_root) as project:
        ingest(project)
        assert project.media()[0]["metadata"]["audio_streams"] == 2
        analyze(project, AnalysisSettings(window=3, stride=3))
        music = music_info(project, song, duration=4, sync=False)
        plan = create_plan(
            project, duration=4, music=music, include_original_audio=False,
        )
        xml, _ = export_plan(project, plan, timing="auto")
        ids = [item.attrib["id"] for item in ET.parse(xml).findall(".//clipitem")]
        assert not any(clip_id.startswith("a-") for clip_id in ids)
        assert any(clip_id.startswith("music-") for clip_id in ids)
