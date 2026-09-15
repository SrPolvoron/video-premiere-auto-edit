from __future__ import annotations

import json
import shutil
import wave
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from autoeditor_local.audio import music_info
from autoeditor_local.export import export_plan
from autoeditor_local.planner import create_plan

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is not installed")]


@pytest.fixture
def clicks(tmp_path):
    sr = 22050
    waveform = np.zeros(sr * 12, dtype=np.float64)
    envelope = np.exp(-np.arange(1100) / 160)
    pulse = 0.7 * envelope * np.sin(2 * np.pi * 1000 * np.arange(1100) / sr)
    for start in np.arange(0.5, 11.5, 0.5):
        index = int(start * sr)
        waveform[index:index + len(pulse)] += pulse
    path = tmp_path / "synthetic_120_bpm.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sr)
        stream.writeframes((waveform * 32767).astype("<i2").tobytes())
    return path


def test_optional_music_without_beat_analysis(catalog_project, clicks):
    music = music_info(catalog_project, clicks, 6, offset=1, sync=False)
    assert music["beats"] == [] and music["method"] == "disabled"
    plan = create_plan(catalog_project, duration=6, music=music, include_original_audio=False)
    xml, _ = export_plan(catalog_project, plan)
    root = ET.parse(xml).getroot()
    music_clip = root.find(".//clipitem[@id='music-1']")
    assert music_clip is not None
    assert music_clip.findtext("in") == "30"
    assert music_clip.findtext("out") == "210"
    assert not any(c.attrib["id"].startswith("a-") for c in root.findall(".//clipitem"))


def test_multistream_source_muted_exports_without_source_audio_and_keeps_music(
    catalog_project, clicks,
):
    music = music_info(catalog_project, clicks, 6, offset=0, sync=False)
    plan = create_plan(
        catalog_project, duration=6, music=music, include_original_audio=False,
    )
    for clip in plan["timeline"]["video_tracks"][0]["clips"]:
        clip["metadata"]["audio_streams"] = 3
        clip["metadata"]["audio_channels"] = 6
        clip["metadata"]["audio_start"] = 1.5
        clip["audio"]["enabled"] = False
    xml, report_path = export_plan(catalog_project, plan, timing="auto")
    root = ET.parse(xml).getroot()
    ids = [clip.attrib["id"] for clip in root.findall(".//clipitem")]
    assert not any(clip_id.startswith("a-") for clip_id in ids)
    assert any(clip_id.startswith("music-") for clip_id in ids)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["timeline"]["audio"]["original_enabled"] is False
    assert report["timeline"]["music_tracks"][0]["clips"]


def test_real_beat_detection_on_synthetic_clicks(catalog_project, clicks):
    pytest.importorskip("librosa")
    music = music_info(catalog_project, clicks, 10, offset=0, sync=True)
    assert len(music["beats"]) > 8
    assert 105 < music["tempo_estimate"] < 135
    cached = music_info(catalog_project, clicks, 10, offset=0, sync=True)
    assert cached["beats"] == music["beats"]
