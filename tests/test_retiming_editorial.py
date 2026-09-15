from __future__ import annotations

import copy
import json
from fractions import Fraction

import pytest

from autoeditor_local.decisions import list_decisions, record_decision
from autoeditor_local.editorial_features import apply_effects, apply_transitions
from autoeditor_local.export import export_plan
from autoeditor_local.planner import create_plan, load_plan
from autoeditor_local.retiming import apply_slow_motion
from autoeditor_local.timeline import time_fraction, validate_timeline
from autoeditor_local.util import AutoEditorError


def _set_fps(project, fps: str, *, first_only: bool = False) -> None:
    for index, media in enumerate(project.media()):
        if first_only and index:
            continue
        metadata = media["metadata"]
        metadata.update(fps=fps, nominal_fps=fps, vfr_suspected=False)
        with project.db:
            project.db.execute(
                "UPDATE media SET metadata=? WHERE id=?",
                (json.dumps(metadata), media["id"]),
            )


def _mode(project, feature: str, mode: str, level: int | None = None):
    properties = {"mode": mode}
    if level is not None:
        properties["level"] = level
    return record_decision(project, {
        "feature": feature,
        "target": {"kind": "feature"},
        "provenance": "manual",
        "properties": properties,
    })


def _clips(plan):
    return plan["timeline"]["video_tracks"][0]["clips"]


def _target(clip):
    return {"kind": "clip", "clip_id": clip["clip_id"]}


def _retimed(plan):
    return [clip for clip in _clips(plan) if clip["retiming"]["mode"] != "none"]


def _slow_speeds(clip):
    return [
        Fraction(region["speed"]["numerator"], region["speed"]["denominator"])
        for region in clip["retiming"].get("regions", [])
        if region["speed"] != {"numerator": 1, "denominator": 1}
    ]


def _fake_music(duration: int = 12):
    return {
        "path": "music.wav",
        "fingerprint": "music-test",
        "offset": 0.0,
        "available_duration": float(duration),
        "method": "synthetic-test-events",
        "beats": [float(Fraction(index, 2)) for index in range(1, duration * 2)],
        "metadata": {
            "duration": float(duration), "audio_channels": 2, "audio_sample_rate": 48000,
        },
    }


def test_slow_motion_off_and_speed_one_leave_timing_unchanged(catalog_project):
    _set_fps(catalog_project, "60")
    base = create_plan(catalog_project, duration=8, fps="30")
    before = copy.deepcopy(base["timeline"])
    after, warnings = apply_slow_motion(catalog_project, base["timeline"])
    assert after == before and warnings == []

    clip = next(
        item for item in _clips(base)
        if item["timeline_range"]["duration_frames"] >= 24
        and item["timeline_range"]["duration_frames"] % 2 == 0
        and item["audio"]["enabled"]
    )
    _mode(catalog_project, "slow_motion", "manual", 2)
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": _target(clip), "provenance": "manual",
        "properties": {
            "placement": "constant", "speed": {"numerator": 1, "denominator": 1},
        },
    })
    unchanged, _ = apply_slow_motion(catalog_project, base["timeline"])
    assert next(
        item for item in unchanged["video_tracks"][0]["clips"]
        if item["clip_id"] == clip["clip_id"]
    )[
        "retiming"
    ]["mode"] == "none"
    assert [item["timeline_range"] for item in unchanged["video_tracks"][0]["clips"]] == [
        item["timeline_range"] for item in _clips(base)
    ]


def test_constant_speed_is_rational_exact_deterministic_and_preserves_duration(catalog_project):
    _set_fps(catalog_project, "60")
    base = create_plan(catalog_project, duration=8, fps="30")
    clip = next(
        item for item in _clips(base)
        if item["timeline_range"]["duration_frames"] >= 24
        and item["timeline_range"]["duration_frames"] % 2 == 0
        and item["audio"]["enabled"]
    )
    original_duration = time_fraction(clip["source_range"]["duration"])
    _mode(catalog_project, "slow_motion", "manual", 2)
    decision = record_decision(catalog_project, {
        "feature": "slow_motion", "target": _target(clip), "provenance": "manual",
        "properties": {
            "placement": "constant", "speed": {"numerator": 1, "denominator": 2},
        },
    })
    first = create_plan(catalog_project, duration=8, fps="30")
    second = create_plan(catalog_project, duration=8, fps="30")
    changed = next(item for item in _retimed(first) if item["clip_id"] == clip["clip_id"])
    assert changed["retiming"] == {
        "mode": "constant", "speed": {"numerator": 1, "denominator": 2}, "regions": [],
        "provenance": [{
            "decision_id": decision["decision_id"], "provenance": "manual",
            "target_clip_id": clip["clip_id"],
        }],
    }
    assert time_fraction(changed["source_range"]["duration"]) == original_duration / 2
    assert changed["audio"]["retiming"] == {"mode": "none"}
    assert changed["audio"]["timeline_range"] == changed["timeline_range"]
    assert first["sequence"]["duration_frames"] == first["timeline"]["sequence"]["duration_frames"]
    first_timeline, second_timeline = copy.deepcopy(first["timeline"]), copy.deepcopy(second["timeline"])
    first_timeline["timeline_id"] = second_timeline["timeline_id"] = "deterministic"
    first_timeline["metadata"]["plan_id"] = second_timeline["metadata"]["plan_id"] = "deterministic"
    assert first_timeline == second_timeline
    validate_timeline(first["timeline"])


@pytest.mark.parametrize(
    ("level", "expected_speed"),
    [(1, Fraction(4, 5)), (2, Fraction(1, 2)), (3, Fraction(2, 5))],
)
def test_auto_levels_use_clean_high_fps_speeds(catalog_project, level, expected_speed):
    _set_fps(catalog_project, "120")
    _mode(catalog_project, "slow_motion", "auto", level)
    plan = create_plan(catalog_project, duration=12, fps="30")
    retimed = _retimed(plan)
    assert retimed
    speeds = [speed for clip in retimed for speed in _slow_speeds(clip)]
    assert expected_speed in speeds, speeds
    assert len(retimed) <= (2 if level == 3 else 1)
    assert plan["timeline"]["sequence"]["duration_frames"] == 360


def test_auto_prefers_high_fps_and_skips_unsafe_low_fps(catalog_project):
    _set_fps(catalog_project, "120", first_only=True)
    _mode(catalog_project, "slow_motion", "auto", 3)
    mixed = create_plan(catalog_project, duration=20, fps="30")
    assert all(clip["source_fps"] == {"numerator": 120, "denominator": 1} for clip in _retimed(mixed))

    # Un proyecto nuevo del mismo fixture no existe aquí; volver a 30 hace que cualquier propuesta
    # ya persistida se descarte por seguridad temporal sin inventar frames.
    _set_fps(catalog_project, "30")
    safe = create_plan(catalog_project, duration=20, fps="30")
    assert _retimed(safe) == []


def test_manual_clip_auto_and_exact_source_range_keep_provenance(catalog_project):
    _set_fps(catalog_project, "120")
    base = create_plan(catalog_project, duration=10, fps="30")
    clip = max(_clips(base), key=lambda item: item["timeline_range"]["duration_frames"])
    _mode(catalog_project, "slow_motion", "manual", 3)
    auto_decision = record_decision(catalog_project, {
        "feature": "slow_motion", "target": _target(clip), "provenance": "manual",
        "properties": {"placement": "auto", "level": 3},
    })
    automatic_range = create_plan(catalog_project, duration=10, fps="30")
    changed = next(item for item in _retimed(automatic_range) if item["clip_id"] == clip["clip_id"])
    assert changed["retiming"]["mode"] == "regions"
    assert changed["retiming"]["provenance"][0]["decision_id"] == auto_decision["decision_id"]

    # Otro clip evita que el override manual anterior gane sobre el rango exacto.
    exact_clip = next(item for item in _clips(base) if item["clip_id"] != clip["clip_id"])
    start = time_fraction(exact_clip["source_range"]["start"]) + Fraction(1, 2)
    exact = record_decision(catalog_project, {
        "feature": "slow_motion",
        "target": {
            "kind": "range", "clip_id": exact_clip["clip_id"], "space": "source",
            "start": str(start), "duration": "2/5",
        },
        "provenance": "manual",
        "properties": {"speed": {"numerator": 1, "denominator": 2}},
    })
    exact_plan = create_plan(catalog_project, duration=10, fps="30")
    exact_result = next(item for item in _retimed(exact_plan) if item["clip_id"] == exact_clip["clip_id"])
    assert exact_result["retiming"]["mode"] == "regions"
    assert exact_result["retiming"]["provenance"][0]["decision_id"] == exact["decision_id"]


def test_manual_exact_timeline_range_maps_to_source_without_float_drift(catalog_project):
    _set_fps(catalog_project, "120")
    base = create_plan(catalog_project, duration=10, fps="30")
    clip = next(item for item in _clips(base) if item["timeline_range"]["duration_frames"] >= 36)
    timeline_start = Fraction(clip["timeline_range"]["start_frames"], 30) + Fraction(1, 5)
    _mode(catalog_project, "slow_motion", "manual", 2)
    record_decision(catalog_project, {
        "feature": "slow_motion",
        "target": {
            "kind": "range", "clip_id": clip["clip_id"], "space": "timeline",
            "start": str(timeline_start), "duration": "2/5",
        },
        "provenance": "manual",
        "properties": {"speed": {"numerator": 1, "denominator": 2}},
    })
    plan = create_plan(catalog_project, duration=10, fps="30")
    changed = next(item for item in _retimed(plan) if item["clip_id"] == clip["clip_id"])
    assert changed["retiming"]["mode"] == "regions"
    validate_timeline(plan["timeline"])


def test_hybrid_accept_reject_modify_locks_and_music_preservation(catalog_project):
    _set_fps(catalog_project, "120")
    music = _fake_music()
    _mode(catalog_project, "slow_motion", "hybrid", 2)
    proposed = create_plan(catalog_project, duration=10, fps="30", music=music)
    proposal = next(
        row for row in list_decisions(catalog_project)
        if row["feature"] == "slow_motion" and row["provenance"] == "automatic"
    )
    assert _retimed(proposed) == []
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": proposal["target"], "provenance": "modified",
        "properties": {"speed": {"numerator": 2, "denominator": 5}},
        "supersedes": proposal["decision_id"],
    })
    modified = create_plan(catalog_project, duration=10, fps="30", music=music)
    assert _retimed(modified)
    assert any(
        item["provenance"] == "modified"
        for clip in _retimed(modified) for item in clip["retiming"]["provenance"]
    )
    assert modified["timeline"]["music_tracks"] == proposed["timeline"]["music_tracks"]
    assert modified["timeline"]["sequence"]["duration_frames"] == proposed["timeline"]["sequence"]["duration_frames"]

    target = proposal["target"]
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": target, "provenance": "manual",
        "properties": {"enabled": False}, "locks": ["enabled"],
    })
    locked = create_plan(catalog_project, duration=10, fps="30", music=music)
    assert not any(clip["clip_id"] == target["clip_id"] for clip in _retimed(locked))
    with pytest.raises(AutoEditorError, match="bloqueadas"):
        record_decision(catalog_project, {
            "feature": "slow_motion", "target": target, "provenance": "manual",
            "properties": {"enabled": True},
        })


def test_manual_auto_placement_can_align_a_valid_region_to_music(catalog_project):
    _set_fps(catalog_project, "120")
    base = create_plan(catalog_project, duration=20, fps="30")
    clip = max(_clips(base), key=lambda item: item["timeline_range"]["duration_frames"])
    start, end = clip["timeline_range"]["start_frames"], sum(
        (clip["timeline_range"]["start_frames"], clip["timeline_range"]["duration_frames"]),
    )
    beat_frame = (start + end) // 2
    _mode(catalog_project, "slow_motion", "manual", 1)
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": _target(clip), "provenance": "manual",
        "properties": {"placement": "auto", "level": 1},
    })
    timeline, _ = apply_slow_motion(
        catalog_project, base["timeline"],
        music={"beats": [float(Fraction(beat_frame, 30))]},
    )
    changed = next(
        item for item in timeline["video_tracks"][0]["clips"]
        if item["clip_id"] == clip["clip_id"]
    )
    slow_region = next(
        region for region in changed["retiming"]["regions"]
        if region["speed"] != {"numerator": 1, "denominator": 1}
    )
    assert slow_region["timeline_range"]["start_frames"] == beat_frame


def test_hybrid_rejection_prevents_slow_motion(catalog_project):
    _set_fps(catalog_project, "120")
    _mode(catalog_project, "slow_motion", "hybrid", 2)
    create_plan(catalog_project, duration=10, fps="30")
    proposal = next(
        row for row in list_decisions(catalog_project)
        if row["feature"] == "slow_motion" and row["provenance"] == "automatic"
    )
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": proposal["target"], "provenance": "rejected",
        "supersedes": proposal["decision_id"],
    })
    assert _retimed(create_plan(catalog_project, duration=10, fps="30")) == []


def test_hybrid_accept_applies_slow_motion_with_accepted_provenance(catalog_project):
    _set_fps(catalog_project, "120")
    _mode(catalog_project, "slow_motion", "hybrid", 2)
    create_plan(catalog_project, duration=10, fps="30")
    proposal = next(
        row for row in list_decisions(catalog_project)
        if row["feature"] == "slow_motion" and row["provenance"] == "automatic"
    )
    accepted = record_decision(catalog_project, {
        "feature": "slow_motion", "target": proposal["target"], "provenance": "accepted",
        "supersedes": proposal["decision_id"],
    })
    plan = create_plan(catalog_project, duration=10, fps="30")
    assert _retimed(plan)
    assert _retimed(plan)[0]["retiming"]["provenance"][0] == {
        "decision_id": accepted["decision_id"],
        "provenance": "accepted",
        "target_clip_id": proposal["target"]["clip_id"],
    }


def test_microcut_parent_and_child_targets_remain_stable_on_regeneration(catalog_project):
    _set_fps(catalog_project, "120")
    base = create_plan(catalog_project, duration=10, fps="30")
    parent = max(_clips(base), key=lambda item: item["timeline_range"]["duration_frames"])
    _mode(catalog_project, "microcuts", "manual", 1)
    record_decision(catalog_project, {
        "feature": "microcuts", "target": _target(parent), "provenance": "manual",
        "properties": {"placement": "auto", "level": 1},
    })
    split = create_plan(catalog_project, duration=10, fps="30")
    children = [clip for clip in _clips(split) if clip.get("parent_clip_id") == parent["clip_id"]]
    assert len(children) == 2
    _mode(catalog_project, "slow_motion", "manual", 2)
    child = max(children, key=lambda item: item["timeline_range"]["duration_frames"])
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": _target(child), "provenance": "manual",
        "properties": {
            "placement": "constant", "speed": {"numerator": 1, "denominator": 2},
        },
    })
    first = create_plan(catalog_project, duration=10, fps="30")
    second = create_plan(catalog_project, duration=10, fps="30")
    assert [clip["clip_id"] for clip in _clips(first)] == [clip["clip_id"] for clip in _clips(second)]
    assert [clip["clip_id"] for clip in _retimed(first)] == [child["clip_id"]]

    # Un target al parent sigue resolviéndose después del split y el target directo al child gana.
    record_decision(catalog_project, {
        "feature": "slow_motion", "target": _target(parent), "provenance": "manual",
        "properties": {"placement": "auto", "level": 1},
    })
    parent_plan = create_plan(catalog_project, duration=10, fps="30")
    assert _retimed(parent_plan)


def test_transitions_off_manual_cut_auto_and_hybrid(catalog_project):
    base = create_plan(catalog_project, duration=16)
    before = copy.deepcopy(base["timeline"])
    off, _ = apply_transitions(catalog_project, base["timeline"])
    assert off == before
    left, right = _clips(base)[:2]
    _mode(catalog_project, "transitions", "manual")
    manual = record_decision(catalog_project, {
        "feature": "transitions", "target": _target(left), "provenance": "manual",
        "properties": {"to_clip_id": right["clip_id"], "type": "cross_dissolve", "duration": 0.2},
    })
    with_transition = create_plan(catalog_project, duration=16)
    assert len(with_transition["timeline"]["transitions"]) == 1
    assert with_transition["timeline"]["transitions"][0]["decision_id"] == manual["decision_id"]
    assert with_transition["timeline"]["sequence"]["duration_frames"] == 480

    record_decision(catalog_project, {
        "feature": "transitions", "target": _target(left), "provenance": "manual",
        "properties": {"to_clip_id": right["clip_id"], "type": "cut"},
    })
    cut = create_plan(catalog_project, duration=16)
    assert cut["timeline"]["transitions"] == []


def test_transition_auto_is_sparse_and_hybrid_requires_acceptance(catalog_project):
    _mode(catalog_project, "transitions", "auto")
    auto = create_plan(catalog_project, duration=30, cli_overrides={"pace": "calm"})
    assert len(auto["timeline"]["transitions"]) == 1
    assert sum(
        row["feature"] == "transitions" and row["provenance"] == "automatic"
        for row in list_decisions(catalog_project)
    ) == 1


def test_transition_hybrid_waits_for_acceptance(catalog_project):
    base = create_plan(catalog_project, duration=16)
    left, right = _clips(base)[:2]
    _mode(catalog_project, "transitions", "hybrid")
    proposal = record_decision(catalog_project, {
        "feature": "transitions", "target": _target(left), "provenance": "automatic",
        "properties": {"to_clip_id": right["clip_id"], "type": "dip_to_black", "duration": 0.2},
    })
    waiting = create_plan(catalog_project, duration=16)
    assert waiting["timeline"]["transitions"] == []
    accepted = record_decision(catalog_project, {
        "feature": "transitions", "target": proposal["target"], "provenance": "accepted",
        "supersedes": proposal["decision_id"],
    })
    applied = create_plan(catalog_project, duration=16)
    assert applied["timeline"]["transitions"][0]["decision_id"] == accepted["decision_id"]
    assert applied["timeline"]["transitions"][0]["type"] == "dip_to_black"


def test_effects_off_manual_auto_and_hybrid(catalog_project):
    base = create_plan(catalog_project, duration=12)
    off, _ = apply_effects(catalog_project, base["timeline"])
    assert off == base["timeline"]
    clip = _clips(base)[0]
    _mode(catalog_project, "effects", "manual", 2)
    manual = record_decision(catalog_project, {
        "feature": "effects", "target": _target(clip), "provenance": "manual",
        "properties": {"type": "soft_zoom_in", "level": 2},
    })
    changed = create_plan(catalog_project, duration=12)
    assert changed["timeline"]["effects"][0]["decision_id"] == manual["decision_id"]
    assert changed["timeline"]["effects"][0]["type"] == "soft_zoom_in"
    assert changed["timeline"]["sequence"]["duration_frames"] == 360


def test_effect_auto_is_sparse_and_hybrid_proposal_can_be_accepted(catalog_project):
    _mode(catalog_project, "effects", "hybrid", 3)
    proposed = create_plan(catalog_project, duration=20)
    assert proposed["timeline"]["effects"] == []
    proposal = next(
        row for row in list_decisions(catalog_project)
        if row["feature"] == "effects" and row["provenance"] == "automatic"
    )
    accepted = record_decision(catalog_project, {
        "feature": "effects", "target": proposal["target"], "provenance": "accepted",
        "supersedes": proposal["decision_id"],
    })
    applied = create_plan(catalog_project, duration=20)
    assert len(applied["timeline"]["effects"]) == 1
    assert applied["timeline"]["effects"][0]["decision_id"] == accepted["decision_id"]


def test_effect_auto_applies_only_a_sparse_subset(catalog_project):
    _mode(catalog_project, "effects", "auto", 3)
    plan = create_plan(catalog_project, duration=20)
    assert 1 <= len(plan["timeline"]["effects"]) <= 2
    assert len(plan["timeline"]["effects"]) < len(_clips(plan))


def test_serialization_backwards_compatibility_and_exporter_fail_loudly(catalog_project):
    basic = create_plan(catalog_project, duration=8)
    loaded = load_plan(catalog_project, basic["id"])
    validate_timeline(loaded["timeline"])
    export_plan(catalog_project, loaded, timing="auto")

    clip = _clips(basic)[0]
    _mode(catalog_project, "effects", "manual", 1)
    record_decision(catalog_project, {
        "feature": "effects", "target": _target(clip), "provenance": "manual",
        "properties": {"type": "subtle_push_in", "level": 1},
    })
    enhanced = create_plan(catalog_project, duration=8)
    with pytest.raises(AutoEditorError, match="effects"):
        export_plan(catalog_project, enhanced, timing="auto")

    legacy = copy.deepcopy(basic)
    legacy.pop("timeline")
    from autoeditor_local.timeline import timeline_from_plan

    validate_timeline(timeline_from_plan(legacy))
