from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from autoeditor_local.cli import main
from autoeditor_local.decisions import list_decisions, record_decision
from autoeditor_local.export import export_plan
from autoeditor_local.microcuts import apply_microcuts
from autoeditor_local.planner import create_plan, load_plan
from autoeditor_local.project import Project
from autoeditor_local.timeline import (
    rate_fraction, time_fraction, timeline_frames, timeline_from_plan, validate_timeline,
)
from autoeditor_local.util import AutoEditorError, write_json


def _feature_mode(project, mode: str, level: int = 2):
    return record_decision(project, {
        "feature": "microcuts", "target": {"kind": "feature"}, "provenance": "manual",
        "properties": {"mode": mode, "level": level},
    })


def _logical_clips(plan):
    return [clip for clip in plan["timeline"]["video_tracks"][0]["clips"] if "parent_clip_id" not in clip]


def _long_parent(plan):
    return max(_logical_clips(plan), key=lambda clip: timeline_frames(clip)[1] - timeline_frames(clip)[0])


def _target(clip):
    return {"kind": "clip", "clip_id": clip["clip_id"]}


def _rational(value: Fraction) -> dict[str, int]:
    return {"value": value.numerator, "timescale": value.denominator}


def _manual_placement(project, level: int = 2, duration: float = 10):
    base = create_plan(project, duration=duration)
    parent = _long_parent(base)
    _feature_mode(project, "manual", level)
    decision = record_decision(project, {
        "feature": "microcuts", "target": _target(parent), "provenance": "manual",
        "properties": {"placement": "auto", "level": level},
    })
    return base, parent, decision


def _children(plan, parent_id: str):
    return [
        clip for clip in plan["timeline"]["video_tracks"][0]["clips"]
        if clip.get("parent_clip_id") == parent_id
    ]


def test_off_leaves_the_timeline_identical_and_writes_nothing(catalog_project):
    plan = create_plan(catalog_project, duration=6)
    before = copy.deepcopy(plan["timeline"])
    after, warnings = apply_microcuts(catalog_project, plan["timeline"])
    assert after == before
    assert warnings == []
    assert not list_decisions(catalog_project)


def test_auto_creates_idempotent_proposals_and_deterministic_children(catalog_project):
    _feature_mode(catalog_project, "auto", 2)
    first = create_plan(catalog_project, duration=10)
    automatic = [
        row for row in list_decisions(catalog_project)
        if row["feature"] == "microcuts" and row["provenance"] == "automatic"
    ]
    assert automatic
    second = create_plan(catalog_project, duration=10)
    assert [clip["clip_id"] for clip in first["timeline"]["video_tracks"][0]["clips"]] == [
        clip["clip_id"] for clip in second["timeline"]["video_tracks"][0]["clips"]
    ]
    assert len(automatic) == len([
        row for row in list_decisions(catalog_project)
        if row["feature"] == "microcuts" and row["provenance"] == "automatic"
    ])
    _feature_mode(catalog_project, "off", 0)
    before = len(list_decisions(catalog_project))
    disabled = create_plan(catalog_project, duration=10)
    assert not any("parent_clip_id" in clip for clip in disabled["timeline"]["video_tracks"][0]["clips"])
    assert len(list_decisions(catalog_project)) == before


@pytest.mark.parametrize(("level", "expected_cuts"), [(1, 1), (2, 2), (3, 3)])
def test_manual_automatic_placement_honours_microcut_intensity(catalog_project, level, expected_cuts):
    _, parent, _ = _manual_placement(catalog_project, level)
    plan = create_plan(catalog_project, duration=10)
    children = _children(plan, parent["clip_id"])
    assert len(children) == expected_cuts + 1
    assert all(child["microcut"]["level"] == level for child in children)


def test_manual_exact_ranges_are_rational_and_preserve_exact_duration(catalog_project):
    base = create_plan(catalog_project, duration=10)
    parent = _long_parent(base)
    fps = rate_fraction(base["timeline"]["sequence"]["fps"])
    start = time_fraction(parent["source_range"]["start"])
    _feature_mode(catalog_project, "manual", 2)
    decision = record_decision(catalog_project, {
        "feature": "microcuts", "target": _target(parent), "provenance": "manual",
        "properties": {
            "level": 2,
            "remove_ranges": [
                {"start": _rational(start + Fraction(24, 1) / fps), "duration": _rational(Fraction(5, 1) / fps)},
                {"start": _rational(start + Fraction(54, 1) / fps), "duration": _rational(Fraction(5, 1) / fps)},
            ],
        },
    })
    plan = create_plan(catalog_project, duration=10)
    children = _children(plan, parent["clip_id"])
    assert len(children) == 3
    assert all(item["provenance"] == "manual" for item in children[0]["microcut"]["provenance"])
    assert decision["decision_id"] in {
        item["decision_id"] for item in children[0]["microcut"]["provenance"]
    }
    assert sum(timeline_frames(child)[1] - timeline_frames(child)[0] for child in children) == (
        timeline_frames(parent)[1] - timeline_frames(parent)[0]
    )
    assert plan["timeline"]["sequence"]["duration_frames"] == base["timeline"]["sequence"]["duration_frames"]


def test_hybrid_acceptance_uses_existing_provenance(catalog_project):
    create_plan(catalog_project, duration=10)
    _feature_mode(catalog_project, "hybrid", 2)
    proposed = create_plan(catalog_project, duration=10)
    proposal = next(
        row for row in list_decisions(catalog_project)
        if row["feature"] == "microcuts" and row["provenance"] == "automatic"
    )
    assert not any("parent_clip_id" in clip for clip in proposed["timeline"]["video_tracks"][0]["clips"])
    accepted = record_decision(catalog_project, {
        "feature": "microcuts", "target": proposal["target"], "provenance": "accepted",
        "supersedes": proposal["decision_id"],
    })
    accepted_plan = create_plan(catalog_project, duration=10)
    assert _children(accepted_plan, proposal["target"]["clip_id"])
    assert accepted["decision_id"] in {
        item["decision_id"]
        for item in _children(accepted_plan, proposal["target"]["clip_id"])[0]["microcut"]["provenance"]
    }


def test_hybrid_rejection_is_respected(catalog_project):
    create_plan(catalog_project, duration=10)
    _feature_mode(catalog_project, "hybrid", 2)
    create_plan(catalog_project, duration=10)
    proposal = next(row for row in list_decisions(catalog_project) if row["provenance"] == "automatic")
    record_decision(catalog_project, {
        "feature": "microcuts", "target": proposal["target"], "provenance": "rejected",
        "supersedes": proposal["decision_id"],
    })
    rejected = create_plan(catalog_project, duration=10)
    assert not _children(rejected, proposal["target"]["clip_id"])


def test_hybrid_modification_is_respected(catalog_project):
    create_plan(catalog_project, duration=10)
    _feature_mode(catalog_project, "hybrid", 2)
    create_plan(catalog_project, duration=10)
    proposal = next(
        row for row in reversed(list_decisions(catalog_project))
        if row["provenance"] == "automatic"
    )
    record_decision(catalog_project, {
        "feature": "microcuts", "target": proposal["target"], "provenance": "modified",
        "supersedes": proposal["decision_id"],
        "properties": {
            "level": 1, "placement": "explicit",
            "remove_ranges": proposal["properties"]["remove_ranges"][:1],
        },
    })
    modified = create_plan(catalog_project, duration=10)
    children = _children(modified, proposal["target"]["clip_id"])
    assert len(children) == 2
    assert children[0]["microcut"]["provenance"][0]["provenance"] == "modified"


def test_locks_parent_identity_source_bounds_and_persistence(catalog_project):
    _, parent, decision = _manual_placement(catalog_project, 1)
    locked = record_decision(catalog_project, {
        "feature": "microcuts", "target": _target(parent), "provenance": "manual",
        "properties": {"placement": "auto"}, "locks": ["placement"],
    })
    with pytest.raises(AutoEditorError, match="bloqueadas"):
        record_decision(catalog_project, {
            "feature": "microcuts", "target": _target(parent), "provenance": "manual",
            "properties": {"placement": "explicit"},
        })
    plan = create_plan(catalog_project, duration=10)
    children = _children(plan, parent["clip_id"])
    assert children and all(child["parent_clip_id"] == parent["clip_id"] for child in children)
    assert all(child["clip_id"].startswith(f"{parent['clip_id']}-mc-") for child in children)
    assert all(
        child["source_range"]["start"] != child["parent_source_range"]["start"]
        or child["source_range"]["duration"] != child["parent_source_range"]["duration"]
        for child in children
    )
    validate_timeline(plan["timeline"])
    fps = rate_fraction(plan["timeline"]["sequence"]["fps"])
    assert all(timeline_frames(child)[1] - timeline_frames(child)[0] >= 12 for child in children)
    source_ranges = []
    for child in children:
        start = time_fraction(child["source_range"]["start"])
        end = start + time_fraction(child["source_range"]["duration"])
        assert 0 <= start < end <= Fraction(str(child["metadata"]["duration"]))
        assert time_fraction(child["source_range"]["duration"]) * fps == (
            timeline_frames(child)[1] - timeline_frames(child)[0]
        )
        source_ranges.append((start, end))
    assert all(left[1] <= right[0] for left, right in zip(source_ranges, source_ranges[1:]))
    with Project(catalog_project.root) as reopened:
        ids = {row["decision_id"] for row in list_decisions(reopened)}
        assert {decision["decision_id"], locked["decision_id"]} <= ids


def test_music_aware_manual_placement_prefers_a_nearby_beat(catalog_project):
    base, parent, _ = _manual_placement(catalog_project, 1)
    fps = rate_fraction(base["timeline"]["sequence"]["fps"])
    timeline_start, timeline_end = timeline_frames(parent)
    beat_frame = timeline_start + (timeline_end - timeline_start) // 2
    transformed, _ = apply_microcuts(
        catalog_project, base["timeline"], music={"beats": [float(Fraction(beat_frame, 1) / fps)]},
    )
    child = _children({"timeline": transformed}, parent["clip_id"])[0]
    removed = child["microcut"]["removed_ranges"][0]
    assert time_fraction(removed["start"]) == time_fraction(parent["source_range"]["start"]) + Fraction(
        beat_frame - timeline_start, 1,
    ) / fps


def test_microcuts_keep_exact_rational_sequence_timing(catalog_project):
    base = create_plan(catalog_project, duration=10, fps="30000/1001")
    parent = _long_parent(base)
    _feature_mode(catalog_project, "manual", 1)
    record_decision(catalog_project, {
        "feature": "microcuts", "target": _target(parent), "provenance": "manual",
        "properties": {"placement": "auto", "level": 1},
    })
    plan = create_plan(catalog_project, duration=10, fps="30000/1001")
    children = _children(plan, parent["clip_id"])
    assert len(children) == 2
    assert sum(timeline_frames(child)[1] - timeline_frames(child)[0] for child in children) == (
        timeline_frames(parent)[1] - timeline_frames(parent)[0]
    )
    validate_timeline(plan["timeline"])


def test_serialization_cli_and_old_plans_without_microcuts_remain_compatible(catalog_project, tmp_path, capsys):
    base = create_plan(catalog_project, duration=8)
    parent = _long_parent(base)
    document = tmp_path / "microcuts.json"
    write_json(document, {
        "decisions": [
            {
                "feature": "microcuts", "target": {"kind": "feature"}, "provenance": "manual",
                "properties": {"mode": "manual", "level": 1},
            },
            {
                "feature": "microcuts", "target": _target(parent), "provenance": "manual",
                "properties": {"placement": "auto", "level": 1},
            },
        ],
    })
    assert main(["decisions", str(catalog_project.root), "--apply", str(document)]) == 0
    assert "microcuts" in capsys.readouterr().out
    plan = create_plan(catalog_project, duration=8)
    loaded = load_plan(catalog_project, plan["id"])
    assert loaded["timeline"] == plan["timeline"]
    assert _children(loaded, parent["clip_id"])
    assert all("parent_clip_id" not in clip for clip in base["timeline"]["video_tracks"][0]["clips"])
    legacy = copy.deepcopy(base)
    legacy.pop("timeline")
    assert all("parent_clip_id" not in clip for clip in timeline_from_plan(legacy)["video_tracks"][0]["clips"])


def test_existing_xmeml_exporter_serializes_microcut_children(catalog_project):
    _, parent, _ = _manual_placement(catalog_project, 1)
    plan = create_plan(catalog_project, duration=10)
    children = _children(plan, parent["clip_id"])
    xml, _ = export_plan(catalog_project, plan, timing="auto")
    exported = ET.parse(xml).findall("./sequence/media/video/track/clipitem")
    assert len(exported) == len(plan["timeline"]["video_tracks"][0]["clips"])
    assert len(children) == 2
