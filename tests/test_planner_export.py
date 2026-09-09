from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from autoeditor_local.export import export_plan, xml_rate
from autoeditor_local.planner import Policy, create_plan, cut_length, load_plan, make_policy, validate_plan
from autoeditor_local.util import AutoEditorError, file_hash


def test_plan_is_contiguous_and_bounded(catalog_project):
    plan = create_plan(catalog_project, duration=12, preset="moto")
    validate_plan(plan)
    assert plan["actual_duration"] == 12
    assert plan["clips"][0]["stage"] == "preparation"
    assert plan["clips"][-1]["timeline_end"] == 360
    assert len({c["segment_id"] for c in plan["clips"]}) == len(plan["clips"])
    assert all(0 <= c["source_in"] < c["source_out"] <= 20 for c in plan["clips"])


def test_immutable_versions(catalog_project):
    first = create_plan(catalog_project, duration=6)
    stored = json.loads(catalog_project.db.execute("SELECT payload FROM plans WHERE id=?", (first["id"],)).fetchone()[0])
    second = create_plan(catalog_project, duration=10, preset="nature")
    assert first["id"] != second["id"]
    assert load_plan(catalog_project, first["id"]) == stored
    assert load_plan(catalog_project)["id"] == second["id"]


def test_reject_feedback_changes_new_plan_only(catalog_project):
    first = create_plan(catalog_project, duration=6, preset="moto")
    rejected = first["clips"][0]["segment_id"]
    catalog_project.feedback(rejected, "reject", "wrong gesture")
    second = create_plan(catalog_project, duration=6, preset="moto")
    assert rejected not in [c["segment_id"] for c in second["clips"]]
    assert load_plan(catalog_project, first["id"])["clips"][0]["segment_id"] == rejected


def test_latest_feedback_wins(catalog_project):
    catalog_project.feedback("segment-0", "reject")
    catalog_project.feedback("segment-0", "prefer")
    assert next(s for s in catalog_project.segments() if s["id"] == "segment-0")["decision"] == "prefer"


def test_no_pov_when_forbidden(catalog_project):
    plan = create_plan(catalog_project, duration=12, intent={"max_pov_fraction": 0})
    assert all(c["shot"] != "pov" for c in plan["clips"])


def test_chronological_order(catalog_project):
    plan = create_plan(catalog_project, duration=15, preset="travel")
    dates = [c["metadata"]["creation_time"] for c in plan["clips"]]
    assert dates == sorted(dates)


def test_does_not_invent_footage_to_fill(catalog_project):
    plan = create_plan(catalog_project, duration=300)
    assert plan["actual_duration"] < 300
    assert any("shorter cut" in w for w in plan["warnings"])
    assert len(plan["clips"]) <= 8


def test_failed_analysis_is_not_active_catalog(catalog_project):
    with catalog_project.db:
        catalog_project.db.execute("UPDATE analyses SET status='failed' WHERE key='analysis-0'")
    assert "segment-0" not in [s["id"] for s in catalog_project.segments()]
    assert "segment-0" in [s["id"] for s in catalog_project.segments(include_partial=True)]


def test_unsupported_prompt_requests_are_reported(catalog_project):
    plan = create_plan(catalog_project, duration=4, intent={"unsupported_requests": ["Exact drop alignment is unsupported"]})
    assert "Exact drop alignment is unsupported" in plan["warnings"]


def test_policy_cannot_reverse_bounds():
    with pytest.raises(AutoEditorError):
        make_policy("balanced", {"min_shot": 8, "max_shot": 1})


def test_beat_aligned_duration():
    length = cut_length(0, 300, Fraction(30), Policy(min_shot=1, max_shot=3), [0.5, 1.0, 1.5, 2.0, 2.5])
    assert length == 60


def test_validate_plan_rejects_gap(catalog_project):
    plan = create_plan(catalog_project, duration=6)
    plan["clips"][0]["timeline_start"] = 1
    with pytest.raises(AutoEditorError):
        validate_plan(plan)


def test_validate_plan_rejects_source_overrun(catalog_project):
    plan = create_plan(catalog_project, duration=6)
    plan["clips"][0]["source_out"] = 999
    with pytest.raises(AutoEditorError):
        validate_plan(plan)


def test_xml_rate():
    assert xml_rate(Fraction(30000, 1001)) == (30, True)
    assert xml_rate(Fraction(25)) == (25, False)
    with pytest.raises(AutoEditorError):
        xml_rate(Fraction(1234, 99))


def test_xml_structure_links_paths_and_markers(catalog_project):
    plan = create_plan(catalog_project, duration=10)
    before = {p.name: file_hash(p) for p in catalog_project.media_root.glob("*.mp4")}
    xml, report = export_plan(catalog_project, plan)
    root = ET.parse(xml).getroot()
    assert root.tag == "xmeml" and root.attrib["version"] == "5"
    clips = root.findall("./sequence/media/video/track/clipitem")
    assert len(clips) == len(plan["clips"])
    assert len(root.findall("./sequence/marker")) == len(clips)
    paths = [node.text for node in root.findall(".//pathurl")]
    assert paths and all(path.startswith("file:///") for path in paths)
    assert any("%20" in path and "%26" in path for path in paths)
    ids = {c.attrib["id"] for c in root.findall(".//clipitem")}
    assert all(link.text in ids for link in root.findall(".//linkclipref"))
    assert all(c.findtext("in") is not None and int(c.findtext("out")) > int(c.findtext("in")) for c in clips)
    assert root.findtext("./sequence/name").startswith("Test & project")
    after = {p.name: file_hash(p) for p in catalog_project.media_root.glob("*.mp4")}
    assert before == after
    assert json.loads(report.read_text())["export_validation"]["premiere_import_tested"] is False


def test_duplicate_file_references_only_defined_once(catalog_project):
    plan = create_plan(catalog_project, duration=6)
    xml, _ = export_plan(catalog_project, plan)
    root = ET.parse(xml).getroot()
    definitions = [f.attrib["id"] for f in root.findall(".//file") if f.find("pathurl") is not None]
    assert len(definitions) == len(set(definitions))


def test_refuse_export_overwrite(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    export_plan(catalog_project, plan)
    with pytest.raises(AutoEditorError):
        export_plan(catalog_project, plan)
    export_plan(catalog_project, plan, overwrite=True)


def test_refuse_project_config_overwrite(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    with pytest.raises(AutoEditorError):
        export_plan(catalog_project, plan, output=catalog_project.root / "project.xml", overwrite=True)


def test_refuse_changed_source(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    source = catalog_project.media_root / plan["clips"][0]["relative_path"]
    source.write_bytes(b"changed original")
    with pytest.raises(AutoEditorError):
        export_plan(catalog_project, plan)


def test_mixed_rates_require_opt_in(catalog_project):
    plan = create_plan(catalog_project, duration=4, fps="24")
    with pytest.raises(AutoEditorError, match="frame-rate mismatch"):
        export_plan(catalog_project, plan)
    xml, report = export_plan(catalog_project, plan, allow_unverified_timing=True)
    assert xml.exists()
    assert json.loads(report.read_text())["export_validation"]["timing_warnings"]


def test_vfr_requires_opt_in(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    plan["clips"][0]["metadata"]["vfr_suspected"] = True
    with pytest.raises(AutoEditorError, match="variable frame rate"):
        export_plan(catalog_project, plan)


def test_multichannel_is_explicitly_unsupported(catalog_project):
    plan = create_plan(catalog_project, duration=4)
    plan["clips"][0]["metadata"]["audio_channels"] = 6
    with pytest.raises(AutoEditorError, match="mono/stereo"):
        export_plan(catalog_project, plan)


def test_no_accidental_speed_change_in_xml(catalog_project):
    plan = create_plan(catalog_project, duration=12)
    xml, _ = export_plan(catalog_project, plan)
    root = ET.parse(xml).getroot()
    for clip in root.findall("./sequence/media/video/track/clipitem"):
        assert int(clip.findtext("out")) - int(clip.findtext("in")) == int(clip.findtext("end")) - int(clip.findtext("start"))
