from __future__ import annotations

import json
from collections import Counter

import pytest

from autoeditor_local.cli import parser
from autoeditor_local.planner import create_plan
from autoeditor_local.profiles import (
    load_priority_profile,
    priority_profile_names,
    resolve_policy,
    validate_policy_patch,
)
from autoeditor_local.util import AutoEditorError


EXPECTED_PROFILES = {
    "balanced", "dance", "sensual", "moto", "training", "martial-arts", "nature", "action",
}


def _lengths(plan):
    return [clip["timeline_end"] - clip["timeline_start"] for clip in plan["clips"]]


def _update_segment(project, segment_id, *, tags=None, **values):
    row = project.db.execute("SELECT prediction FROM segments WHERE id=?", (segment_id,)).fetchone()
    prediction = json.loads(row["prediction"])
    if tags is not None:
        prediction["tags"] = tags
        values["prediction"] = json.dumps(prediction, sort_keys=True)
    assignments = ",".join(f"{key}=?" for key in values)
    with project.db:
        project.db.execute(
            f"UPDATE segments SET {assignments} WHERE id=?", (*values.values(), segment_id),
        )


def test_declarative_profiles_are_complete_and_bounded():
    assert set(priority_profile_names()) == EXPECTED_PROFILES
    dance = load_priority_profile("dance")
    sensual = load_priority_profile("sensual")
    assert dance["pace"] == "dynamic" and dance["max_close_fraction"] <= 0.3
    assert {"wide", "medium"} <= set(dance["preferred_shots"])
    sensual_text = " ".join(sensual["preferred_tags"])
    for concept in ("dance", "body movement", "pose", "gesture", "gaze", "face", "medium shot", "full body"):
        assert concept in sensual_text
    assert {"wide", "medium", "close"} <= set(sensual["preferred_shots"])
    assert 0 < sensual["max_close_fraction"] < 0.5


def test_policy_merge_is_explicit_additive_and_cli_wins():
    policy, sources = resolve_policy(
        "moto",
        "sensual",
        user={"preferred_tags": ["usuario"], "max_close_fraction": 0.35},
        prompt={"preferred_tags": ["prompt-tag"], "pace": "calm", "max_close_fraction": 0.3},
        cli={"pace": "dynamic", "max_close_fraction": 0.2},
    )
    tags = {tag.casefold() for tag in policy.preferred_tags}
    assert {"motorcycle", "landscape", "dance", "usuario", "prompt-tag"} <= tags
    assert policy.pace == "dynamic" and policy.max_close_fraction == 0.2
    assert [source["source"] for source in sources] == [
        "defaults", "preset", "priority", "user", "prompt", "cli",
    ]


@pytest.mark.parametrize("payload", [
    {"pace": "hyper"},
    {"max_clips_per_media": 0},
    {"max_clips_per_media": 2.5},
    {"preferred_shots": ["selfie"]},
    {"max_close_fraction": 2},
])
def test_new_policy_fields_are_strict(payload):
    with pytest.raises(AutoEditorError):
        validate_policy_patch(payload)


def test_later_avoid_shot_resolves_an_earlier_preference():
    policy, _ = resolve_policy(
        user={"preferred_shots": ["close"]},
        prompt={"avoid_shots": ["close"]},
    )
    assert "close" not in policy.preferred_shots
    assert "close" in policy.avoid_shots


def test_same_layer_cannot_prefer_and_avoid_the_same_shot():
    with pytest.raises(AutoEditorError):
        validate_policy_patch({"preferred_shots": ["close"], "avoid_shots": ["close"]})


def test_variable_editorial_durations_not_fixed_midpoint(catalog_project):
    plan = create_plan(
        catalog_project, duration=20, intent={"min_shot": 2.5, "max_shot": 5.5},
        cli_overrides={"pace": "balanced"},
    )
    lengths = _lengths(plan)
    assert plan["actual_duration"] == 20
    assert len(set(lengths)) >= 3
    assert any(length != 120 for length in lengths)


def test_short_high_value_candidate_is_not_excluded(catalog_project):
    _update_segment(
        catalog_project, "segment-0", start=0.0, end=2.7, anchor=1.35,
        quality=1.0, interest=1.0, confidence=1.0,
    )
    catalog_project.feedback("segment-0", "prefer")
    plan = create_plan(
        catalog_project, duration=8, intent={
            "min_shot": 2.5, "max_shot": 5.5, "start_stage": "preparation",
        }, cli_overrides={"pace": "calm"},
    )
    short = next(clip for clip in plan["clips"] if clip["segment_id"] == "segment-0")
    assert short["timeline_end"] - short["timeline_start"] <= 81


def test_rebalance_hits_target_without_tiny_residual(catalog_project):
    plan = create_plan(
        catalog_project, duration=10.1,
        intent={"min_shot": 2.5, "max_shot": 3.5},
    )
    assert plan["sequence"]["duration_frames"] == 303
    assert plan["actual_duration"] == 10.1
    assert all(length >= 75 for length in _lengths(plan))


def test_pace_changes_cut_distribution(catalog_project):
    common = {"min_shot": 0.8, "max_shot": 4.0, "max_clips_per_media": 10}
    calm = create_plan(catalog_project, duration=10, intent=common, cli_overrides={"pace": "calm"})
    dynamic = create_plan(catalog_project, duration=10, intent=common, cli_overrides={"pace": "dynamic"})
    assert len(dynamic["clips"]) > len(calm["clips"])
    assert sum(_lengths(dynamic)) == sum(_lengths(calm)) == 300


def test_same_inputs_keep_editorial_result_deterministic(catalog_project):
    first = create_plan(catalog_project, duration=12, priority="action")
    second = create_plan(catalog_project, duration=12, priority="action")
    fields = lambda plan: [
        (clip["segment_id"], clip["source_in"], clip["source_out"],
         clip["timeline_start"], clip["timeline_end"])
        for clip in plan["clips"]
    ]
    assert fields(first) == fields(second)
    assert first["policy"] == second["policy"]


def test_max_clips_per_media_is_hard_when_alternatives_exist(catalog_project):
    media_id = catalog_project.db.execute(
        "SELECT id FROM media WHERE active_key='analysis-0'",
    ).fetchone()[0]
    for index in range(3):
        _update_segment(
            catalog_project, f"segment-{index}", media_id=media_id,
            analysis_key="analysis-0", start=index * 5.0, end=index * 5.0 + 4.0,
            anchor=index * 5.0 + 2.0,
        )
    plan = create_plan(
        catalog_project, duration=8,
        intent={"min_shot": 1.0, "max_shot": 2.0, "max_clips_per_media": 1},
        cli_overrides={"pace": "dynamic"},
    )
    counts = Counter(clip["media_id"] for clip in plan["clips"])
    assert max(counts.values()) == 1
    assert not any("max_clips_per_media" in warning for warning in plan["warnings"])


def test_max_clips_per_media_has_explicit_fallback(catalog_project):
    media_id = catalog_project.db.execute(
        "SELECT id FROM media WHERE active_key='analysis-0'",
    ).fetchone()[0]
    for index in range(8):
        _update_segment(
            catalog_project, f"segment-{index}", media_id=media_id,
            analysis_key="analysis-0", start=index * 2.4, end=index * 2.4 + 2.0,
            anchor=index * 2.4 + 1.0,
        )
    plan = create_plan(
        catalog_project, duration=7,
        intent={"min_shot": 1.0, "max_shot": 1.5, "max_clips_per_media": 1},
        cli_overrides={"pace": "dynamic"},
    )
    assert Counter(clip["media_id"] for clip in plan["clips"])[media_id] > 1
    assert any("max_clips_per_media" in warning for warning in plan["warnings"])


def test_shot_preferences_and_close_budget(catalog_project):
    for index in range(5):
        _update_segment(catalog_project, f"segment-{index}", shot="close")
    catalog_project.feedback("segment-0", "prefer")
    plan = create_plan(
        catalog_project, duration=12,
        intent={
            "min_shot": 1.0, "max_shot": 2.5,
            "preferred_shots": ["close"], "avoid_shots": ["pov"],
            "max_close_fraction": 0.25,
        },
    )
    close_frames = sum(
        clip["timeline_end"] - clip["timeline_start"]
        for clip in plan["clips"] if clip["shot"] == "close"
    )
    assert close_frames <= 90
    assert any(clip["shot"] == "close" for clip in plan["clips"])
    assert all(clip["shot"] != "pov" for clip in plan["clips"])


def test_sensual_profile_selects_face_and_body_without_single_shot_dominance(catalog_project):
    content = [
        (["dance", "full body"], "wide"),
        (["body movement"], "medium"),
        (["pose"], "medium"),
        (["gesture"], "close"),
        (["gaze"], "close"),
        (["face", "eye contact"], "close"),
        (["medium shot"], "medium"),
        (["plano completo"], "wide"),
    ]
    for index, (tags, shot) in enumerate(content):
        _update_segment(catalog_project, f"segment-{index}", tags=tags, shot=shot)
    catalog_project.feedback("segment-5", "prefer")
    catalog_project.feedback("segment-7", "prefer")
    plan = create_plan(catalog_project, duration=14, priority="sensual")
    selected_tags = {tag for clip in plan["clips"] for tag in clip["tags"]}
    shot_frames = Counter()
    for clip in plan["clips"]:
        shot_frames[clip["shot"]] += clip["timeline_end"] - clip["timeline_start"]
    assert "face" in selected_tags
    assert selected_tags & {"full body", "plano completo"}
    assert len(shot_frames) >= 2
    assert shot_frames["close"] <= plan["sequence"]["duration_frames"] * 0.4


def test_cli_exposes_priority_pace_and_shot_controls():
    args = parser().parse_args([
        "plan", "project", "--priority", "dance", "--pace", "dynamic",
        "--max-clips-per-media", "2", "--max-close-fraction", "0.25",
        "--preferred-shot", "wide", "--avoid-shot", "close",
    ])
    assert args.priority == "dance" and args.pace == "dynamic"
    assert args.max_clips_per_media == 2 and args.max_close_fraction == 0.25
    assert args.preferred_shot == ["wide"] and args.avoid_shot == ["close"]
