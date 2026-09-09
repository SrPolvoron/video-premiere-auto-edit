from __future__ import annotations

import math
from fractions import Fraction

import pytest
from PIL import Image

from autoeditor_local.analysis import AnalysisSettings, windows
from autoeditor_local.media import sample_dimensions, technical_metrics
from autoeditor_local.project import init_project
from autoeditor_local.util import AutoEditorError, finite, fps_fraction, local_media, project_lock, seconds_to_frames
from autoeditor_local.vision import validate_actions, validate_endpoint, validate_intent


@pytest.mark.parametrize("value,expected", [("30", Fraction(30)), ("29.97", Fraction(30000, 1001)),
                                           ("59.94", Fraction(60000, 1001)), ("24", Fraction(24))])
def test_fps(value, expected):
    assert fps_fraction(value) == expected


@pytest.mark.parametrize("value", ["0", "0/0", "abc", "999", "-1"])
def test_invalid_fps(value):
    with pytest.raises(AutoEditorError):
        fps_fraction(value)


@pytest.mark.parametrize("value", [math.nan, math.inf, -1, 2, True, "0.5", None])
def test_finite_rejects_invalid(value):
    with pytest.raises(AutoEditorError):
        finite(value, "score", 0, 1)


def test_frame_rounding():
    assert seconds_to_frames(0.05, Fraction(30)) == 2
    assert seconds_to_frames(1, Fraction(30000, 1001)) == 30


@pytest.mark.parametrize("duration,width,stride", [(45, 8, 6), (1, 8, 6), (601.5, 8, 6), (10, 4, 4)])
def test_windows_cover_video(duration, width, stride):
    ranges = list(windows(duration, width, stride))
    assert ranges[0][0] == 0
    assert ranges[-1][1] == duration
    assert all(0 <= a < b <= duration for a, b in ranges)
    assert all(next_start <= end for (_, end), (next_start, _) in zip(ranges, ranges[1:]))


def test_bad_window_settings():
    with pytest.raises(AutoEditorError):
        AnalysisSettings(window=4, stride=5).validate()


def test_static_frame_not_discarded():
    image = Image.new("RGB", (128, 72), (110, 110, 110))
    metrics = technical_metrics(image, image)
    assert metrics["frame_difference"] == 0
    assert metrics["quality"] > 0.6


def test_motion_does_not_penalize_quality():
    image = Image.new("RGB", (128, 72), (110, 110, 110))
    previous = Image.new("RGB", (128, 72), (30, 30, 30))
    assert technical_metrics(image, previous)["quality"] == technical_metrics(image, image)["quality"]


def test_portrait_dimensions():
    assert sample_dimensions({"width": 1920, "height": 1080, "rotation": -90}, 384) == (216, 384)


def test_local_media_path_escape(tmp_path):
    (tmp_path / "inside").mkdir()
    (tmp_path / "secret").write_text("test")
    with pytest.raises(AutoEditorError):
        local_media(tmp_path / "inside", "../secret")


def test_lock_refuses_second_writer(tmp_path):
    with project_lock(tmp_path):
        with pytest.raises(AutoEditorError), project_lock(tmp_path):
            pass
    assert not (tmp_path / ".autoeditor.lock").exists()


def test_init_refuses_existing(catalog_project):
    with pytest.raises(AutoEditorError):
        init_project(catalog_project.root, catalog_project.media_root)


def test_backup_has_all_segments(catalog_project, tmp_path):
    import sqlite3
    from contextlib import closing
    backup = tmp_path / "backup.db"
    catalog_project.backup(backup)
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 8
    with pytest.raises(AutoEditorError):
        catalog_project.backup(backup)


def test_feedback_unknown_segment(catalog_project):
    with pytest.raises(AutoEditorError):
        catalog_project.feedback("does-not-exist", "reject")


def test_relink_refuses_changed_original(catalog_project, tmp_path):
    import shutil
    target = tmp_path / "moved"
    shutil.copytree(catalog_project.media_root, target)
    next(target.glob("*.mp4")).write_text("wrong file")
    with pytest.raises(AutoEditorError):
        catalog_project.relink(target)


def test_relink_preserves_analysis(catalog_project, tmp_path):
    import shutil
    target = tmp_path / "moved"
    shutil.copytree(catalog_project.media_root, target)
    catalog_project.relink(target)
    assert catalog_project.media_root == target
    assert len(catalog_project.segments()) == 8


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost:8080/v1",
                                  "http://10.0.0.1:8000/v1", "file:///tmp/model",
                                  "http://user:pass@127.0.0.1:8000/v1",
                                  "http://127.0.0.1:8000/v1?redirect=other", "http://127.0.0.1:8000/admin"])
def test_reject_remote_or_ambiguous_endpoint(url):
    with pytest.raises(AutoEditorError):
        validate_endpoint(url)


def test_loopback_endpoints():
    assert validate_endpoint("http://127.0.0.1:8081/v1/") == "http://127.0.0.1:8081/v1"
    assert validate_endpoint("http://[::1]:8081/v1") == "http://[::1]:8081/v1"


def action(**overrides):
    base = {"start": 1, "end": 3, "anchor": 2, "label": "putting on helmet",
            "stage": "preparation", "shot": "medium", "confidence": 0.7, "interest": 0.8}
    base.update(overrides)
    return {"segments": [base]}


def test_valid_actions():
    assert validate_actions(action(), 8)[0]["label"] == "putting on helmet"


@pytest.mark.parametrize("change", [{"start": -1}, {"end": 99}, {"anchor": 8},
                                    {"confidence": float("nan")}, {"confidence": 5},
                                    {"stage": "invented"}, {"shot": "bogus"}, {"label": ""},
                                    {"end": 1}, {"tags": [1]}, {"start": True}])
def test_invalid_actions(change):
    with pytest.raises(AutoEditorError):
        validate_actions(action(**change), 8)


@pytest.mark.parametrize("payload", [{"execute": "rm"}, {"max_pov_fraction": 2},
                                      {"order": "unknown"}, {"preferred_tags": "not a list"},
                                      {"start_stage": "flying"}])
def test_invalid_intent(payload):
    with pytest.raises(AutoEditorError):
        validate_intent(payload)


def test_good_intent():
    assert validate_intent({"max_pov_fraction": 0.2, "preferred_tags": ["landscape"]})["max_pov_fraction"] == 0.2
