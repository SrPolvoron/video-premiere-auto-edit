"""Legacy Final Cut Pro XML (xmeml), not modern .fcpxml or native .prproj.

The generated XML is structurally tested. Actual Premiere import is a separate acceptance
step. Mixed-rate/VFR timelines require explicit opt-in until validated in the target editor.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from .planner import validate_plan
from .project import Project
from .util import AutoEditorError, atomic_text, file_hash, fps_fraction, local_media, seconds_to_frames, write_json


def _text(parent: ET.Element, tag: str, value) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = re.sub(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]", "", str(value))
    return element


def xml_rate(rate: Fraction) -> tuple[int, bool]:
    if rate.denominator == 1:
        return rate.numerator, False
    for timebase in (24, 30, 48, 60, 120, 240):
        if rate == Fraction(timebase * 1000, 1001):
            return timebase, True
    raise AutoEditorError(f"Frame rate {rate} cannot be represented exactly in xmeml.")


def _rate(parent: ET.Element, fps: Fraction):
    timebase, ntsc = xml_rate(fps)
    element = ET.SubElement(parent, "rate")
    _text(element, "timebase", timebase)
    _text(element, "ntsc", "TRUE" if ntsc else "FALSE")


def _timecode(parent: ET.Element, fps: Fraction):
    timecode = ET.SubElement(parent, "timecode")
    _rate(timecode, fps)
    _text(timecode, "frame", 0)
    _text(timecode, "displayformat", "NDF")


def _sample_characteristics(parent: ET.Element, width: int, height: int, fps: Fraction):
    sample = ET.SubElement(parent, "samplecharacteristics")
    _rate(sample, fps)
    _text(sample, "width", width)
    _text(sample, "height", height)
    _text(sample, "anamorphic", "FALSE")
    _text(sample, "pixelaspectratio", "square")
    _text(sample, "fielddominance", "none")


def _file(parent: ET.Element, file_id: str, path: Path, meta: dict, fps: Fraction,
          defined: set[str], video: bool = True):
    file_element = ET.SubElement(parent, "file", {"id": file_id})
    if file_id in defined:
        return
    defined.add(file_id)
    _text(file_element, "name", path.name)
    _text(file_element, "pathurl", path.resolve().as_uri())
    _rate(file_element, fps)
    _text(file_element, "duration", math.floor(meta["duration"] * float(fps) + 1e-7))
    _timecode(file_element, fps)
    media = ET.SubElement(file_element, "media")
    if video:
        v = ET.SubElement(media, "video")
        _sample_characteristics(v, meta["width"], meta["height"], fps)
    if meta["audio_channels"]:
        a = ET.SubElement(media, "audio")
        sample = ET.SubElement(a, "samplecharacteristics")
        _text(sample, "depth", 16)
        _text(sample, "samplerate", meta["audio_sample_rate"])
        _text(a, "channelcount", meta["audio_channels"])


def _fit(clip: ET.Element, meta: dict, width: int, height: int):
    source_width, source_height = meta["width"], meta["height"]
    if abs(meta.get("rotation", 0)) % 180 == 90:
        source_width, source_height = source_height, source_width
    scale = 100 * min(width / source_width, height / source_height)
    effect = ET.SubElement(ET.SubElement(clip, "filter"), "effect")
    _text(effect, "name", "Basic Motion")
    _text(effect, "effectid", "basic")
    _text(effect, "effectcategory", "motion")
    _text(effect, "effecttype", "motion")
    _text(effect, "mediatype", "video")
    parameter = ET.SubElement(effect, "parameter")
    _text(parameter, "parameterid", "scale")
    _text(parameter, "name", "Scale")
    _text(parameter, "valuemin", 0)
    _text(parameter, "valuemax", 10000)
    _text(parameter, "value", f"{scale:.6f}")


def _clip(parent: ET.Element, clip_id: str, label: str, meta: dict, source_fps: Fraction,
          timeline_start: int, timeline_end: int, source_start: float, source_end: float,
          source_type: str, channel: int = 1) -> ET.Element:
    clip = ET.SubElement(parent, "clipitem", {"id": clip_id})
    _text(clip, "name", label)
    _text(clip, "enabled", "TRUE")
    _text(clip, "duration", math.floor(meta["duration"] * float(source_fps) + 1e-7))
    _rate(clip, source_fps)
    _text(clip, "start", timeline_start)
    _text(clip, "end", timeline_end)
    source_length = seconds_to_frames(source_end - source_start, source_fps)
    total_frames = math.floor(meta["duration"] * float(source_fps) + 1e-7)
    source_in = min(seconds_to_frames(source_start, source_fps), total_frames - source_length)
    source_in = max(0, source_in)
    # Round the duration once, never both endpoints independently: half-frame ties
    # otherwise turn a 71-frame shot into 72 frames and can introduce a retime.
    source_out = min(total_frames, source_in + source_length)
    if source_out <= source_in:
        raise AutoEditorError("A cut became empty when quantized to source frames.")
    _text(clip, "in", source_in)
    _text(clip, "out", source_out)
    source = ET.SubElement(clip, "sourcetrack")
    _text(source, "mediatype", source_type)
    _text(source, "trackindex", channel)
    return clip


def _links(elements: list[tuple[ET.Element, str, int, int]]):
    if len(elements) < 2:
        return
    for element, _, _, _ in elements:
        for reference, media_type, track_index, clip_index in elements:
            link = ET.SubElement(element, "link")
            _text(link, "linkclipref", reference.attrib["id"])
            _text(link, "mediatype", media_type)
            _text(link, "trackindex", track_index)
            _text(link, "clipindex", clip_index)
            if media_type == "audio":
                _text(link, "groupindex", 1)


def export_plan(project: Project, plan: dict, output: Path | None = None,
                allow_unverified_timing: bool = False, overwrite: bool = False,
                verify_media: bool = False) -> tuple[Path, Path]:
    validate_plan(plan)
    sequence = plan["sequence"]
    fps = fps_fraction(sequence["fps"])
    xml_rate(fps)
    paths = {}
    inventory = {m["relative_path"]: m for m in project.media()}
    timing_issues = []
    for c in plan["clips"]:
        meta = c["metadata"]
        source_rate = fps_fraction(meta["fps"])
        xml_rate(source_rate)
        if source_rate != fps:
            timing_issues.append("source/sequence frame-rate mismatch")
        if meta.get("vfr_suspected"):
            timing_issues.append("potential variable frame rate")
        if meta["audio_channels"] > 2 or meta.get("audio_streams", 1) > 1:
            raise AutoEditorError("V1 exports only a first mono/stereo source audio stream. Multistream/surround media needs an extension.")
        if meta["audio_channels"] and abs(meta.get("audio_start", 0) - meta.get("video_start", 0)) > 1 / float(source_rate):
            timing_issues.append("audio/video start-time offset")
        rel = c["relative_path"]
        if rel not in paths:
            source = local_media(project.media_root, rel)
            current = inventory.get(rel)
            stat = source.stat()
            needs_hash = (verify_media or not current or current["fingerprint"] != c["fingerprint"]
                          or stat.st_size != current["size"] or stat.st_mtime_ns != current["mtime_ns"])
            if needs_hash and file_hash(source) != c["fingerprint"]:
                raise AutoEditorError(f"Original no longer matches the plan: {rel}. Re-ingest and create a new plan.")
            paths[rel] = source
    if timing_issues and not allow_unverified_timing:
        raise AutoEditorError(
            "Export paused: " + ", ".join(sorted(set(timing_issues)))
            + ". Use --allow-unverified-timing only for a Premiere import test; see docs/premiere.md."
        )
    root = ET.Element("xmeml", {"version": "5"})
    seq = ET.SubElement(root, "sequence", {"id": plan["id"]})
    _text(seq, "name", f"{plan['project']} - {plan['id']}")
    _text(seq, "duration", sequence["duration_frames"])
    _rate(seq, fps)
    _timecode(seq, fps)
    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    format_element = ET.SubElement(video, "format")
    _sample_characteristics(format_element, sequence["width"], sequence["height"], fps)
    video_track = ET.SubElement(video, "track")
    audio = ET.SubElement(media, "audio")
    _text(audio, "numOutputChannels", 2)
    audio_format = ET.SubElement(ET.SubElement(audio, "format"), "samplecharacteristics")
    _text(audio_format, "depth", 16)
    _text(audio_format, "samplerate", 48000)
    audio_tracks = [ET.SubElement(audio, "track") for _ in range(4)]
    audio_indices = [0, 0, 0, 0]
    defined: set[str] = set()
    for index, c in enumerate(plan["clips"], start=1):
        meta = c["metadata"]
        source_fps = fps_fraction(meta["fps"])
        args = (c["label"], meta, source_fps, c["timeline_start"], c["timeline_end"], c["source_in"], c["source_out"])
        v = _clip(video_track, f"v-{index}", *args, "video")
        _file(v, f"file-{c['media_id']}", paths[c["relative_path"]], meta, source_fps, defined)
        _fit(v, meta, sequence["width"], sequence["height"])
        logging = ET.SubElement(v, "logginginfo")
        _text(logging, "description", f"segment={c['segment_id']}; stage={c['stage']}; confidence={c['confidence']:.2f}")
        group = [(v, "video", 1, index)]
        if plan["original_audio"]:
            for channel in range(meta["audio_channels"]):
                audio_indices[channel] += 1
                a = _clip(audio_tracks[channel], f"a-{index}-{channel + 1}", *args, "audio", channel + 1)
                _file(a, f"file-{c['media_id']}", paths[c["relative_path"]], meta, source_fps, defined)
                group.append((a, "audio", channel + 1, audio_indices[channel]))
        _links(group)
        marker = ET.SubElement(seq, "marker")
        _text(marker, "name", f"{c['stage']}: {c['label']}")
        _text(marker, "in", c["timeline_start"])
        _text(marker, "out", -1)
        _text(marker, "comment", f"AutoEditor segment {c['segment_id']}; sampled boundary, review the gesture.")
    if plan.get("music"):
        music = plan["music"]
        path = (project.root / music["path"]).resolve()
        if not path.is_file() or file_hash(path) != music["fingerprint"]:
            raise AutoEditorError("Music file is missing or changed.")
        meta = music["metadata"]
        if not 1 <= meta["audio_channels"] <= 2:
            raise AutoEditorError("V1 music export supports mono or stereo audio only.")
        group = []
        for channel in range(meta["audio_channels"]):
            a = _clip(audio_tracks[2 + channel], f"music-{channel + 1}", "Music", meta, fps,
                      0, sequence["duration_frames"], music["offset"], music["offset"] + plan["actual_duration"],
                      "audio", channel + 1)
            _file(a, "file-music", path, meta, fps, defined, video=False)
            group.append((a, "audio", 3 + channel, 1))
        _links(group)
    # Do not leave trailing empty audio tracks unless they precede an occupied music track.
    while len(audio_tracks) and not audio_tracks[-1].findall("clipitem"):
        audio.remove(audio_tracks.pop())
    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"
    ET.fromstring(xml)  # Well-formedness check, not a Premiere compatibility claim.
    destination = output.resolve() if output else project.root / "exports" / f"{plan['id']}.xml"
    json_path = destination.with_suffix(".json")
    if json_path == project.root / "project.json":
        raise AutoEditorError("Export would overwrite project.json; choose the exports directory.")
    if destination.suffix.lower() != ".xml":
        raise AutoEditorError("Export destination must have the .xml extension.")
    if not overwrite and (destination.exists() or json_path.exists()):
        raise AutoEditorError("Export already exists. Use a different --output or explicitly --overwrite.")
    report = dict(plan)
    report["export_validation"] = {
        "structurally_checked": True, "premiere_import_tested": False,
        "timing_warnings": sorted(set(timing_issues)),
        "scaling": "fit-with-letterboxing; validate rotation and Basic Motion on import",
    }
    atomic_text(destination, xml)
    write_json(json_path, report)
    return destination, json_path
