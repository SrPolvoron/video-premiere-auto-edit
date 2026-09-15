from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from autoeditor_local.cli import parser
from autoeditor_local.export import export_plan
from autoeditor_local.planner import create_plan, load_plan
from autoeditor_local.timeline import timeline_from_plan, validate_timeline
from autoeditor_local.timing import ConformedMedia, normalized_rate, resolve_timing
from autoeditor_local.util import AutoEditorError


def _set_media_rate(project, fps: str, nominal: str, *, vfr: bool = False) -> None:
    for media in project.media():
        metadata = media["metadata"]
        metadata.update(fps=fps, nominal_fps=nominal, vfr_suspected=vfr)
        with project.db:
            project.db.execute(
                "UPDATE media SET metadata=? WHERE id=?",
                (json.dumps(metadata), media["id"]),
            )


def test_internal_timeline_is_canonical_and_progressive(catalog_project):
    plan = create_plan(catalog_project, duration=6, fps="30000/1001")
    timeline = plan["timeline"]
    validate_timeline(timeline)
    assert timeline["timeline_id"] == plan["id"]
    assert timeline["sequence"]["fps"] == {"numerator": 30000, "denominator": 1001}
    assert timeline["transitions"] == []
    assert timeline["effects"] == []
    assert timeline["enhancements"] == []
    assert timeline["color"] == {"operations": []}
    clips = timeline["video_tracks"][0]["clips"]
    assert len({clip["clip_id"] for clip in clips}) == len(clips)
    assert all(
        {
            "media_id", "source_range", "timeline_range", "source_fps", "sequence_fps",
            "audio", "retiming", "transitions", "effects", "enhancements", "color",
            "metadata",
        } <= clip.keys()
        for clip in clips
    )


def test_legacy_plan_gets_a_valid_timeline_adapter(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    plan.pop("timeline")
    timeline = timeline_from_plan(plan)
    validate_timeline(timeline)
    assert timeline["timeline_id"] == plan["id"]


def test_saved_v1_plan_without_timeline_still_loads_and_exports(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    legacy = copy.deepcopy(plan)
    legacy["schema_version"] = 1
    legacy.pop("timeline")
    with catalog_project.db:
        catalog_project.db.execute(
            "UPDATE plans SET payload=? WHERE id=?", (json.dumps(legacy), legacy["id"]),
        )
    loaded = load_plan(catalog_project, legacy["id"])
    xml, report = export_plan(catalog_project, loaded, timing="auto")
    assert ET.parse(xml).getroot().tag == "xmeml"
    assert json.loads(report.read_text(encoding="utf-8"))["timeline"]["schema_version"] == 1


def test_timeline_rejects_duplicate_ids_gaps_and_out_of_bounds(catalog_project):
    plan = create_plan(catalog_project, duration=8)
    timeline = plan["timeline"]
    clips = timeline["video_tracks"][0]["clips"]

    duplicate = copy.deepcopy(timeline)
    duplicate_clips = duplicate["video_tracks"][0]["clips"]
    duplicate_clips[1]["clip_id"] = duplicate_clips[0]["clip_id"]
    with pytest.raises(AutoEditorError, match="clip IDs"):
        validate_timeline(duplicate)

    gap = copy.deepcopy(timeline)
    gap["video_tracks"][0]["clips"][0]["timeline_range"]["start_frames"] = 1
    with pytest.raises(AutoEditorError, match="gaps"):
        validate_timeline(gap)

    outside = copy.deepcopy(timeline)
    outside["video_tracks"][0]["clips"][0]["source_range"]["start"] = {
        "value": 1000, "timescale": 1,
    }
    with pytest.raises(AutoEditorError, match="fuera del original"):
        validate_timeline(outside)
    assert clips


def test_timeline_can_represent_slow_retiming_but_xmeml_rejects_it(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    clip = plan["timeline"]["video_tracks"][0]["clips"][0]
    duration = clip["source_range"]["duration"]
    clip["source_range"]["duration"] = {
        "value": duration["value"], "timescale": duration["timescale"] * 2,
    }
    clip["retiming"] = {"mode": "speed", "speed": {"numerator": 1, "denominator": 2}}
    validate_timeline(plan["timeline"])
    with pytest.raises(AutoEditorError, match="retiming"):
        export_plan(catalog_project, plan, timing="auto")


def test_exporter_reads_the_canonical_timeline_not_the_plan_projection(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    canonical_label = plan["timeline"]["video_tracks"][0]["clips"][0]["editorial"]["label"]
    plan["clips"][0]["label"] = "PROYECCION ANTIGUA MODIFICADA"
    xml, _ = export_plan(catalog_project, plan, timing="auto")
    root = ET.parse(xml).getroot()
    assert root.findtext("./sequence/media/video/track/clipitem/name") == canonical_label


def test_auto_preserves_high_fps_source_rate(catalog_project):
    _set_media_rate(catalog_project, "60", "60")
    plan = create_plan(catalog_project, duration=4, fps="30")
    xml, report_path = export_plan(catalog_project, plan, timing="auto")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = report["export_validation"]["timing_resolutions"].values()
    assert all(record["method"] == "auto-direct" for record in records)
    assert all(record["high_fps_preserved"] for record in records)
    root = ET.parse(xml).getroot()
    assert root.findtext("./sequence/media/video/track/clipitem/rate/timebase") == "60"


@pytest.mark.parametrize(("source", "expected"), [("60", "60"), ("120", "120")])
def test_conform_rate_selection_keeps_high_fps(source, expected):
    metadata = {"fps": source, "nominal_fps": source}
    assert normalized_rate(metadata, Fraction(30)) == Fraction(expected)


def test_strict_rejects_and_interpret_resolves_unusual_rate_without_conform(catalog_project):
    _set_media_rate(catalog_project, "742343/24665", "30", vfr=True)
    plan = create_plan(catalog_project, duration=4)
    with pytest.raises(AutoEditorError, match="not exactly representable"):
        export_plan(catalog_project, plan, timing="strict")
    _, report_path = export_plan(
        catalog_project, plan, timing="interpret", source_fps_override="30",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert all(
        record["method"] == "interpret"
        for record in report["export_validation"]["timing_resolutions"].values()
    )
    assert not (catalog_project.root / "cache" / "conform").exists()


def test_cli_timing_defaults_to_auto_and_accepts_all_modes(tmp_path):
    parsed = parser().parse_args(["export", str(tmp_path)])
    assert parsed.timing == "auto"
    for mode in ("auto", "strict", "interpret", "conform"):
        assert parser().parse_args(["export", str(tmp_path), "--timing", mode]).timing == mode


def test_auto_scans_timing_for_old_metadata_without_marker(catalog_project, monkeypatch):
    media = catalog_project.media()[0]
    metadata = dict(media["metadata"])
    metadata.pop("timing_check")
    calls = []

    def inspection(project, source, fingerprint, inspected_metadata):
        calls.append((project, source, fingerprint, inspected_metadata))
        return {"status": "complete", "vfr": False}

    monkeypatch.setattr("autoeditor_local.timing.inspect_vfr", inspection)
    resolution = resolve_timing(
        catalog_project,
        catalog_project.media_root / media["relative_path"],
        media["fingerprint"], metadata, Fraction(30), "auto", include_audio=False,
    )
    assert resolution.method == "auto-direct"
    assert len(calls) == 1


def test_forced_conform_preserves_high_fps_target(catalog_project, monkeypatch):
    media = catalog_project.media()[0]
    metadata = dict(media["metadata"])
    metadata.update(fps="120", nominal_fps="120")
    chosen_rates = []

    def conform(project, source, fingerprint, inspected_metadata, rate, include_audio):
        chosen_rates.append(rate)
        converted = dict(inspected_metadata)
        converted.update(fps=str(rate), nominal_fps=str(rate), vfr_suspected=False)
        return ConformedMedia(source, converted, False, "test-conform-key")

    monkeypatch.setattr("autoeditor_local.timing.conform_media", conform)
    resolution = resolve_timing(
        catalog_project,
        catalog_project.media_root / media["relative_path"],
        media["fingerprint"], metadata, Fraction(30), "conform", include_audio=False,
    )
    assert chosen_rates == [Fraction(120)]
    assert resolution.method == "forced-conform"
    assert resolution.vfr_detected is False
