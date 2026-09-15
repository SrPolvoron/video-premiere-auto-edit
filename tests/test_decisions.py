from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing

import pytest

from autoeditor_local.cli import main
from autoeditor_local.decisions import (
    list_decisions,
    record_decision,
    record_decisions,
    resolve_decisions,
    set_feature_mode,
)
from autoeditor_local.planner import create_plan
from autoeditor_local.project import Project, init_project
from autoeditor_local.util import AutoEditorError, read_json, write_json


def _clip_target(plan, index: int = 0):
    clip_id = plan["timeline"]["video_tracks"][0]["clips"][index]["clip_id"]
    return {"kind": "clip", "clip_id": clip_id}


def _target_properties(resolved, feature: str, target: dict):
    return next(
        item["properties"] for item in resolved["targets"]
        if item["feature"] == feature and item["target"] == target
    )


def test_clip_ids_are_stable_across_regeneration(catalog_project):
    first = create_plan(catalog_project, duration=8)
    second = create_plan(catalog_project, duration=8)
    first_ids = {clip["segment_id"]: clip["clip_id"] for clip in first["clips"]}
    second_ids = {clip["segment_id"]: clip["clip_id"] for clip in second["clips"]}
    assert first_ids == second_ids
    assert all(re.fullmatch(r"clip-[0-9]{3,}", clip_id) for clip_id in first_ids.values())
    assert first["timeline"]["video_tracks"][0]["clips"][0]["clip_id"].startswith("clip-")


def test_feature_modes_control_automatic_and_manual_decisions(catalog_project):
    plan = create_plan(catalog_project, duration=6)
    target = _clip_target(plan)
    automatic = record_decision(catalog_project, {
        "feature": "stabilization", "target": target, "provenance": "automatic",
        "properties": {"strength": 0.25},
    })
    off = resolve_decisions(catalog_project, plan["timeline"])
    assert off["feature_modes"]["stabilization"]["value"] == "off"
    assert off["targets"] == []

    set_feature_mode(catalog_project, "stabilization", "auto")
    auto = resolve_decisions(catalog_project, plan["timeline"])
    strength = _target_properties(auto, "stabilization", target)["strength"]
    assert strength["value"] == 0.25
    assert strength["provenance"] == "automatic"
    assert strength["decision_id"] == automatic["decision_id"]

    set_feature_mode(catalog_project, "stabilization", "manual")
    record_decision(catalog_project, {
        "feature": "stabilization", "target": target, "provenance": "manual",
        "properties": {"strength": 0.6},
    })
    manual = resolve_decisions(catalog_project, plan["timeline"])
    strength = _target_properties(manual, "stabilization", target)["strength"]
    assert strength["value"] == 0.6 and strength["provenance"] == "manual"


def test_hybrid_accept_reject_and_modify_keep_provenance(catalog_project):
    plan = create_plan(catalog_project, duration=6)
    target = _clip_target(plan)
    set_feature_mode(catalog_project, "transitions", "hybrid")
    accepted_proposal = record_decision(catalog_project, {
        "feature": "transitions", "target": target, "provenance": "automatic",
        "properties": {"kind": "dissolve"},
    })
    rejected_proposal = record_decision(catalog_project, {
        "feature": "transitions", "target": target, "provenance": "automatic",
        "properties": {"curve": "ease-in"},
    })
    modified_proposal = record_decision(catalog_project, {
        "feature": "transitions", "target": target, "provenance": "automatic",
        "properties": {"duration": 0.4},
    })
    before = resolve_decisions(catalog_project, plan["timeline"])
    assert set(before["proposals"]) == {
        accepted_proposal["decision_id"], rejected_proposal["decision_id"],
        modified_proposal["decision_id"],
    }

    accepted = record_decision(catalog_project, {
        "feature": "transitions", "target": target, "provenance": "accepted",
        "supersedes": accepted_proposal["decision_id"],
    })
    rejected = record_decision(catalog_project, {
        "feature": "transitions", "target": target, "provenance": "rejected",
        "supersedes": rejected_proposal["decision_id"],
    })
    modified = record_decision(catalog_project, {
        "feature": "transitions", "target": target, "provenance": "modified",
        "properties": {"duration": 0.8}, "supersedes": modified_proposal["decision_id"],
    })
    resolved = resolve_decisions(catalog_project, plan["timeline"])
    properties = _target_properties(resolved, "transitions", target)
    assert properties["kind"]["provenance"] == "accepted"
    assert properties["kind"]["decision_id"] == accepted["decision_id"]
    assert "curve" not in properties
    assert properties["duration"]["value"] == 0.8
    assert properties["duration"]["provenance"] == "modified"
    assert properties["duration"]["decision_id"] == modified["decision_id"]
    assert rejected["decision_id"] in resolved["rejected"]
    assert resolved["proposals"] == []
    set_feature_mode(catalog_project, "transitions", "auto")
    auto_properties = _target_properties(
        resolve_decisions(catalog_project, plan["timeline"]), "transitions", target,
    )
    assert auto_properties["kind"]["provenance"] == "accepted"
    assert auto_properties["duration"]["provenance"] == "modified"


def test_locked_manual_property_wins_and_survives_regeneration(catalog_project):
    first = create_plan(catalog_project, duration=6)
    target = _clip_target(first)
    set_feature_mode(catalog_project, "night_enhance", "hybrid")
    proposal = record_decision(catalog_project, {
        "feature": "night_enhance", "target": target, "provenance": "automatic",
        "properties": {"amount": 0.2},
    })
    record_decision(catalog_project, {
        "feature": "night_enhance", "target": target, "provenance": "accepted",
        "supersedes": proposal["decision_id"],
    })
    record_decision(catalog_project, {
        "feature": "night_enhance", "target": target, "provenance": "manual",
        "properties": {"amount": 0.5},
    })
    locked = record_decision(catalog_project, {
        "feature": "night_enhance", "target": target, "provenance": "manual",
        "properties": {"amount": 0.7}, "locks": ["amount"],
    })
    record_decision(catalog_project, {
        "feature": "night_enhance", "target": target, "provenance": "automatic",
        "properties": {"amount": 1.0},
    })
    with pytest.raises(AutoEditorError, match="bloqueadas"):
        record_decision(catalog_project, {
            "feature": "night_enhance", "target": target, "provenance": "manual",
            "properties": {"amount": 0.9},
        })

    second = create_plan(catalog_project, duration=6)
    assert _clip_target(second) == target
    properties = _target_properties(
        second["timeline"]["editorial_decisions"], "night_enhance", target,
    )
    assert properties["amount"] == {
        "value": 0.7, "provenance": "manual",
        "decision_id": locked["decision_id"], "locked": True,
    }
    assert any(row["decision_id"] == locked["decision_id"] for row in list_decisions(catalog_project))
    absent = resolve_decisions(
        catalog_project, {"video_tracks": [{"track_id": "V1", "clips": []}]},
    )
    assert locked["decision_id"] in absent["unresolved"]


def test_explicit_unlock_allows_a_manual_override(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    target = _clip_target(plan)
    set_feature_mode(catalog_project, "denoise", "manual")
    record_decision(catalog_project, {
        "feature": "denoise", "target": target, "provenance": "manual",
        "properties": {"amount": 0.4}, "locks": ["amount"],
    })
    changed = record_decision(catalog_project, {
        "feature": "denoise", "target": target, "provenance": "manual",
        "properties": {"amount": 0.6}, "unlocks": ["amount"],
    })
    amount = _target_properties(
        resolve_decisions(catalog_project, plan["timeline"]), "denoise", target,
    )["amount"]
    assert amount["value"] == 0.6
    assert amount["decision_id"] == changed["decision_id"]
    assert amount["locked"] is False


def test_feature_mode_lock_requires_explicit_unlock(catalog_project):
    locked = set_feature_mode(catalog_project, "effects", "off", lock=True)
    with pytest.raises(AutoEditorError, match="bloqueadas"):
        set_feature_mode(catalog_project, "effects", "auto")
    changed = set_feature_mode(catalog_project, "effects", "manual", unlock=True)
    mode = resolve_decisions(catalog_project)["feature_modes"]["effects"]
    assert mode["value"] == "manual" and mode["locked"] is False
    assert mode["decision_id"] == changed["decision_id"]
    assert locked["decision_id"] != changed["decision_id"]


def test_automatic_decisions_are_idempotent_and_resolution_is_deterministic(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    target = _clip_target(plan)
    set_feature_mode(catalog_project, "auto_color", "auto")
    payload = {
        "feature": "auto_color", "target": target, "provenance": "automatic",
        "properties": {"exposure": 0.15, "temperature": -2},
    }
    first = record_decision(catalog_project, payload)
    second = record_decision(catalog_project, payload)
    assert first["decision_id"] == second["decision_id"]
    assert resolve_decisions(catalog_project, plan["timeline"]) == resolve_decisions(
        catalog_project, plan["timeline"],
    )
    assert sum(row["provenance"] == "automatic" for row in list_decisions(catalog_project)) == 1


def test_range_override_is_normalized_and_persists_after_reopen(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    clip_id = _clip_target(plan)["clip_id"]
    set_feature_mode(catalog_project, "microcuts", "manual")
    decision = record_decision(catalog_project, {
        "feature": "microcuts",
        "target": {
            "kind": "range", "clip_id": clip_id, "space": "source",
            "start": 0.5, "duration": "3/4",
        },
        "provenance": "manual", "properties": {"enabled": True},
    })
    with Project(catalog_project.root) as reopened:
        persisted = next(
            row for row in list_decisions(reopened)
            if row["decision_id"] == decision["decision_id"]
        )
        assert persisted["target"]["start"] == {"value": 1, "timescale": 2}
        assert persisted["target"]["duration"] == {"value": 3, "timescale": 4}


def test_decisions_cli_applies_declarative_json(catalog_project, tmp_path, capsys):
    plan = create_plan(catalog_project, duration=4)
    document = tmp_path / "decision.json"
    write_json(document, {
        "feature": "color-style", "target": _clip_target(plan),
        "provenance": "manual", "properties": {"style": "warm"},
    })
    assert main(["decisions", str(catalog_project.root), "--apply", str(document)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["applied"] == ["decision-000001"]
    assert output["decisions"][0]["feature"] == "color_style"


def test_decision_batch_is_atomic_on_invalid_override(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    with pytest.raises(AutoEditorError, match="desconocido"):
        record_decisions(catalog_project, [
            {
                "feature": "denoise", "target": _clip_target(plan),
                "provenance": "manual", "properties": {"amount": 0.4},
            },
            {
                "feature": "denoise", "target": {"kind": "clip", "clip_id": "clip-999"},
                "provenance": "manual", "properties": {"amount": 0.8},
            },
        ])
    assert list_decisions(catalog_project) == []


def test_schema_v1_migrates_without_losing_existing_plans(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    root = tmp_path / "old-project"
    init_project(root, media_root)
    with Project(root) as project:
        project.db.execute(
            "INSERT INTO plans(id,created_at,payload) VALUES(?,?,?)",
            ("cut-legacy", "2025-01-01T00:00:00+00:00", '{"legacy":true}'),
        )
        project.db.commit()
    with closing(sqlite3.connect(root / "project.db")) as database:
        database.execute("DROP TABLE editorial_decisions")
        database.execute("DROP TABLE clip_identities")
        database.execute("PRAGMA user_version = 1")
        database.commit()
    config = read_json(root / "project.json")
    config["schema_version"] = 1
    write_json(root / "project.json", config)

    with Project(root) as migrated:
        assert migrated.config["schema_version"] == 2
        assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert migrated.db.execute(
            "SELECT payload FROM plans WHERE id='cut-legacy'"
        ).fetchone()[0] == '{"legacy":true}'
        assert list_decisions(migrated) == []
    assert read_json(root / "project.json")["schema_version"] == 2


def test_failed_v1_migration_rolls_back_without_advancing_version(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    root = tmp_path / "damaged-old-project"
    init_project(root, media_root)
    with closing(sqlite3.connect(root / "project.db")) as database:
        database.execute("DROP TABLE editorial_decisions")
        database.execute("DROP TABLE clip_identities")
        database.execute("CREATE TABLE editorial_decisions(id INTEGER PRIMARY KEY)")
        database.execute("PRAGMA user_version = 1")
        database.commit()
    config = read_json(root / "project.json")
    config["schema_version"] = 1
    write_json(root / "project.json", config)

    with pytest.raises((AutoEditorError, sqlite3.Error)):
        Project(root)
    with closing(sqlite3.connect(root / "project.db")) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clip_identities'"
        ).fetchone()
    assert read_json(root / "project.json")["schema_version"] == 1
