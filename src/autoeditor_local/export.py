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

from .project import Project
from .timeline import (
    music_clips, rate_fraction, source_seconds, timeline_frames, timeline_from_plan, video_clips,
)
from .timing import resolve_timing, xmeml_rate_values, xmeml_representable
from .util import AutoEditorError, atomic_text, file_hash, fps_fraction, local_media, seconds_to_frames, write_json


def _text(parent: ET.Element, tag: str, value) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = re.sub(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]", "", str(value))
    return element


def xml_rate(rate: Fraction) -> tuple[int, bool]:
    return xmeml_rate_values(rate)


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
                verify_media: bool = False, source_fps_override: str | None = None,
                timing: str | None = "auto") -> tuple[Path, Path]:
    timeline = timeline_from_plan(plan)
    sequence = timeline["sequence"]
    fps = rate_fraction(sequence["fps"])
    xml_rate(fps)
    timing_mode = timing or "auto"
    if allow_unverified_timing:
        if timing_mode not in {"auto", "interpret"}:
            raise AutoEditorError("--allow-unverified-timing no se combina con --timing strict/conform.")
        timing_mode = "interpret"
    if source_fps_override is not None:
        if timing_mode != "interpret":
            raise AutoEditorError("--source-fps solo se admite con --timing interpret.")
        xml_rate(fps_fraction(source_fps_override))

    clips = video_clips(timeline)
    if any(clip.get("retiming", {}).get("mode") != "none" for clip in clips):
        raise AutoEditorError("XMEML todavía no exporta retiming; la timeline interna sí lo preserva.")
    if any(clip.get("effects") or clip.get("enhancements")
           or any(value is not None for value in clip.get("transitions", {}).values()) for clip in clips):
        raise AutoEditorError("XMEML todavía no exporta effects, enhancements o transitions de la timeline.")

    paths: dict[str, Path] = {}
    source_rates: dict[str, Fraction] = {}
    source_metadata: dict[str, dict] = {}
    resolutions = {}
    interpretations = {}
    inventory = {media["relative_path"]: media for media in project.media()}
    timing_issues = []
    for clip in clips:
        media_ref = clip["media"]
        rel = media_ref["relative_path"]
        metadata = clip["metadata"]
        audio_enabled = bool(clip["audio"].get("enabled"))
        original_audio_enabled = bool(timeline.get("audio", {}).get("original_enabled"))
        if original_audio_enabled and (
            metadata["audio_channels"] > 2 or metadata.get("audio_streams", 1) > 1
        ):
            raise AutoEditorError(
                "La exportación de audio original admite solo el primer stream mono/stereo. "
                "Use --mute-original si no necesita ese audio."
            )
        if rel in paths:
            continue
        source = local_media(project.media_root, rel)
        current = inventory.get(rel)
        stat = source.stat()
        needs_hash = (
            verify_media or not current or current["fingerprint"] != media_ref["fingerprint"]
            or stat.st_size != current["size"] or stat.st_mtime_ns != current["mtime_ns"]
        )
        if needs_hash and file_hash(source) != media_ref["fingerprint"]:
            raise AutoEditorError(f"Original no longer matches the plan: {rel}. Re-ingest and create a new plan.")
        measured_rate = rate_fraction(clip["source_fps"])
        if (allow_unverified_timing and source_fps_override is None
                and not xmeml_representable(measured_rate)):
            raise AutoEditorError(
                f"Export paused for {rel}: frame rate {measured_rate} cannot be represented exactly in xmeml. "
                "Use --allow-unverified-timing --source-fps RATE for the legacy interpretation workflow."
            )
        timing_metadata = dict(metadata)
        timing_metadata["fps"] = str(measured_rate)
        resolution = resolve_timing(
            project, source, media_ref["fingerprint"], timing_metadata, fps, timing_mode,
            audio_enabled, source_fps_override,
        )
        paths[rel] = resolution.path
        source_rates[rel] = resolution.rate
        source_metadata[rel] = resolution.metadata
        record = resolution.report(measured_rate)
        resolutions[rel] = record
        if resolution.method == "interpret":
            timing_issues.append(
                f"source timing explicitly interpreted at {resolution.rate} fps; VFR not conformed"
            )
            interpretations[rel] = {
                "measured_fps": str(measured_rate), "nominal_fps": metadata.get("nominal_fps"),
                "xml_fps": str(resolution.rate),
                "method": "explicit-source-fps-override" if source_fps_override else "automatic-interpretation",
            }
        elif "conform" in resolution.method:
            timing_issues.append(
                f"source conformed to cached CFR {resolution.rate}; original preserved"
            )
        elif resolution.rate != fps:
            timing_issues.append("source/sequence frame-rate mismatch represented by internal timeline")

    root = ET.Element("xmeml", {"version": "5"})
    seq = ET.SubElement(root, "sequence", {"id": timeline["timeline_id"]})
    project_name = timeline.get("metadata", {}).get("project") or "AutoEditor"
    _text(seq, "name", f"{project_name} - {timeline['timeline_id']}")
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
    _text(audio_format, "samplerate", sequence.get("audio_sample_rate", 48000))
    audio_tracks = [ET.SubElement(audio, "track") for _ in range(4)]
    audio_indices = [0, 0, 0, 0]
    defined: set[str] = set()
    for index, clip in enumerate(clips, start=1):
        rel = clip["media"]["relative_path"]
        metadata = source_metadata[rel]
        source_fps = source_rates[rel]
        timeline_start, timeline_end = timeline_frames(clip)
        source_in, source_out = source_seconds(clip)
        editorial = clip["editorial"]
        args = (
            editorial["label"], metadata, source_fps, timeline_start, timeline_end,
            source_in, source_out,
        )
        v = _clip(video_track, f"v-{index}", *args, "video")
        _file(v, f"file-{clip['media_id']}", paths[rel], metadata, source_fps, defined)
        _fit(v, metadata, sequence["width"], sequence["height"])
        logging = ET.SubElement(v, "logginginfo")
        _text(
            logging, "description",
            f"segment={clip['segment_id']}; stage={editorial['stage']}; "
            f"confidence={editorial['confidence']:.2f}",
        )
        group = [(v, "video", 1, index)]
        if clip["audio"].get("enabled"):
            for channel in range(metadata["audio_channels"]):
                audio_indices[channel] += 1
                item = _clip(
                    audio_tracks[channel], f"a-{index}-{channel + 1}", *args,
                    "audio", channel + 1,
                )
                _file(
                    item, f"file-{clip['media_id']}", paths[rel], metadata,
                    source_fps, defined,
                )
                group.append((item, "audio", channel + 1, audio_indices[channel]))
        _links(group)
        marker = ET.SubElement(seq, "marker")
        _text(marker, "name", f"{editorial['stage']}: {editorial['label']}")
        _text(marker, "in", timeline_start)
        _text(marker, "out", -1)
        _text(marker, "comment", f"AutoEditor segment {clip['segment_id']}; sampled boundary, review the gesture.")

    timeline_music = music_clips(timeline)
    for music_index, music_clip in enumerate(timeline_music, start=1):
        path = (project.root / music_clip["media"]["path"]).resolve()
        if not path.is_file() or file_hash(path) != music_clip["media"]["fingerprint"]:
            raise AutoEditorError("Music file is missing or changed.")
        metadata = music_clip["metadata"]
        if not 1 <= metadata["audio_channels"] <= 2:
            raise AutoEditorError("V1 music export supports mono or stereo audio only.")
        timeline_start, timeline_end = timeline_frames(music_clip)
        source_in, source_out = source_seconds(music_clip)
        group = []
        for channel in range(metadata["audio_channels"]):
            clip_id = (
                f"music-{channel + 1}"
                if len(timeline_music) == 1 else f"music-{music_index}-{channel + 1}"
            )
            item = _clip(
                audio_tracks[2 + channel], clip_id, "Music",
                metadata, fps, timeline_start, timeline_end, source_in, source_out,
                "audio", channel + 1,
            )
            file_id = "file-music" if len(timeline_music) == 1 else f"file-music-{music_index}"
            _file(item, file_id, path, metadata, fps, defined, video=False)
            group.append((item, "audio", 3 + channel, music_index))
        _links(group)
    while audio_tracks and not audio_tracks[-1].findall("clipitem"):
        audio.remove(audio_tracks.pop())
    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"
    ET.fromstring(xml)
    destination = (
        output.resolve()
        if output else project.root / "exports" / f"{timeline['timeline_id']}.xml"
    )
    json_path = destination.with_suffix(".json")
    if json_path == project.root / "project.json":
        raise AutoEditorError("Export would overwrite project.json; choose the exports directory.")
    if destination.suffix.lower() != ".xml":
        raise AutoEditorError("Export destination must have the .xml extension.")
    if not overwrite and (destination.exists() or json_path.exists()):
        raise AutoEditorError("Export already exists. Use a different --output or explicitly --overwrite.")
    report = dict(plan)
    report["timeline"] = timeline
    report["export_validation"] = {
        "structurally_checked": True, "premiere_import_tested": False,
        "timing_mode": timing_mode, "timing_warnings": sorted(set(timing_issues)),
        "timing_resolutions": resolutions,
        "source_rate_interpretations": interpretations,
        "scaling": "fit-with-letterboxing; validate rotation and Basic Motion on import",
    }
    atomic_text(destination, xml)
    write_json(json_path, report)
    return destination, json_path
