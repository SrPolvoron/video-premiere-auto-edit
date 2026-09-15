"""Microcuts deterministas sobre la timeline canónica.

Este módulo es un consumidor del historial editorial: no crea un formato de decisiones
paralelo ni modifica los originales. Los cortes eliminan intervalos fuente y compensan
su duración ampliando el rango fuente contiguo que esté libre en el mismo medio.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any

from .decisions import record_decisions, register_derived_clip_ids, resolve_decisions
from .project import Project
from .timeline import (
    RationalTime, rate_fraction, time_fraction, timeline_frames, validate_timeline, video_clips,
)
from .util import AutoEditorError, digest

MICROCUT_LEVELS = (0, 1, 2, 3)
_MAX_CUTS = {1: 1, 2: 2, 3: 3}


def _time(value: Fraction) -> dict[str, int]:
    if value < 0:
        raise AutoEditorError("Un microcut no admite tiempos negativos.")
    return RationalTime(value.numerator, value.denominator).to_dict()


def _range(start: Fraction, duration: Fraction) -> dict[str, dict[str, int]]:
    return {"start": _time(start), "duration": _time(duration)}


def _integer(value: Any, name: str, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if type(value) is not int or value not in MICROCUT_LEVELS:
        raise AutoEditorError(f"{name} debe ser un entero entre 0 y 3.")
    return value


def _frame_count(value: Fraction, fps: Fraction, name: str) -> int:
    frames = value * fps
    if frames.denominator != 1:
        raise AutoEditorError(
            f"{name} debe caer exactamente en un frame de la secuencia para conservar la duración."
        )
    return frames.numerator


def _round_frames(seconds: Fraction, fps: Fraction) -> int:
    value = seconds * fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _minimum_frames(fps: Fraction) -> tuple[int, int]:
    """Mínimos editoriales para fragmentos visibles y saltos perceptibles."""
    def ceil_frames(seconds: Fraction) -> int:
        value = seconds * fps
        return (value.numerator + value.denominator - 1) // value.denominator

    return max(12, ceil_frames(Fraction(2, 5))), max(3, ceil_frames(Fraction(1, 10)))


def _property(properties: dict[str, dict[str, Any]], name: str, default: Any = None) -> Any:
    entry = properties.get(name)
    return default if entry is None else entry["value"]


def _property_origin(properties: dict[str, dict[str, Any]], name: str) -> dict[str, str] | None:
    entry = properties.get(name)
    if entry is None:
        return None
    return {"decision_id": entry["decision_id"], "provenance": entry["provenance"]}


def _target_properties(resolved: dict[str, Any], clip_id: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    clip_properties: dict[str, dict[str, Any]] = {}
    ranges = []
    for item in resolved["targets"]:
        if item["feature"] != "microcuts" or item["target"].get("clip_id") != clip_id:
            continue
        if item["target"]["kind"] == "clip":
            clip_properties = item["properties"]
        elif item["target"]["kind"] == "range":
            ranges.append(item)
    return clip_properties, ranges


def _parse_ranges(value: Any, name: str) -> list[tuple[Fraction, Fraction]]:
    if not isinstance(value, list) or not value:
        raise AutoEditorError(f"{name} debe ser una lista no vacía de rangos racionales.")
    result = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != {"start", "duration"}:
            raise AutoEditorError(f"{name}[{index}] necesita start y duration racionales.")
        start = time_fraction(item["start"])
        duration = time_fraction(item["duration"])
        if duration <= 0:
            raise AutoEditorError(f"{name}[{index}] tiene duración no positiva.")
        result.append((start, duration))
    return result


def _ranges_from_properties(
    clip_properties: dict[str, dict[str, Any]], range_targets: list[dict[str, Any]],
) -> list[tuple[Fraction, Fraction, dict[str, str]]]:
    result = []
    if "remove_ranges" in clip_properties:
        origin = _property_origin(clip_properties, "remove_ranges")
        if origin is None:
            raise AutoEditorError("remove_ranges perdió su procedencia editorial.")
        for start, duration in _parse_ranges(clip_properties["remove_ranges"]["value"], "remove_ranges"):
            result.append((start, duration, origin))
    for item in range_targets:
        target = item["target"]
        if target.get("space") != "source":
            raise AutoEditorError("Los rangos manuales de microcuts solo admiten space=source.")
        remove = item["properties"].get("remove")
        if remove is None or remove["value"] is not True:
            continue
        result.append((
            time_fraction(target["start"]), time_fraction(target["duration"]),
            {"decision_id": remove["decision_id"], "provenance": remove["provenance"]},
        ))
    return result


def _auto_score(clip: dict[str, Any]) -> float:
    editorial = clip["editorial"]
    text = " ".join([editorial.get("stage", ""), editorial.get("shot", ""), *editorial.get("tags", [])]).casefold()
    score = float(editorial.get("confidence", 0)) * 0.35
    if editorial.get("stage") == "action":
        score += 0.5
    if editorial.get("shot") in {"wide", "medium"}:
        score += 0.15
    if editorial.get("shot") in {"detail", "close"}:
        score -= 0.08
    score += 0.12 * sum(tag in text for tag in (
        "action", "movement", "moving", "dance", "body", "gesture", "pose", "training",
        "martial", "motorcycle",
    ))
    score -= 0.25 * sum(tag in text for tag in ("static", "landscape", "establishing", "pause"))
    motion = clip.get("metadata", {}).get("motion")
    if isinstance(motion, (int, float)) and not isinstance(motion, bool):
        score += max(-0.2, min(0.35, float(motion)))
    return score


def _level_cut_count(level: int, pace: str) -> int:
    count = _MAX_CUTS[level]
    if pace == "calm":
        return max(1, count - 1)
    return count


def _automatic_ranges(
    clip: dict[str, Any], level: int, fps: Fraction, pace: str, beats: list[Fraction],
) -> list[tuple[Fraction, Fraction]]:
    """Propone pocos saltos, alineados con beats solo cuando encajan editorialmente."""
    start = time_fraction(clip["source_range"]["start"])
    timeline_start, timeline_end = timeline_frames(clip)
    frames = timeline_end - timeline_start
    minimum_child, minimum_gap = _minimum_frames(fps)
    count = _level_cut_count(level, pace)
    if frames < (count + 1) * minimum_child + count * minimum_gap:
        count = max(0, (frames - minimum_child) // (minimum_child + minimum_gap))
    if count <= 0:
        return []
    removal_frames = {
        1: max(minimum_gap, _round_frames(Fraction(3, 25), fps)),
        2: max(minimum_gap, _round_frames(Fraction(9, 50), fps)),
        3: max(minimum_gap, _round_frames(Fraction(6, 25), fps)),
    }[level]
    removal_frames = min(removal_frames, max(minimum_gap, frames // (count + 3)))
    beat_frames = sorted({
        int(beat * fps + Fraction(1, 2))
        for beat in beats if 0 < beat * fps < timeline_end
    })
    starts: list[int] = []
    for index in range(1, count + 1):
        desired = round(frames * index / (count + 1))
        candidates = [
            frame - timeline_start for frame in beat_frames
            if minimum_child <= frame - timeline_start <= frames - minimum_child - removal_frames
        ]
        selected = min(candidates, key=lambda frame: (abs(frame - desired), frame)) if candidates else desired
        selected = max(minimum_child, min(selected, frames - minimum_child - removal_frames))
        starts.append(selected)
    starts = sorted(set(starts))
    result = []
    previous_end = minimum_child
    for start_frame in starts:
        start_frame = max(start_frame, previous_end + minimum_child)
        if start_frame + removal_frames > frames - minimum_child:
            continue
        result.append((start + Fraction(start_frame, 1) / fps, Fraction(removal_frames, 1) / fps))
        previous_end = start_frame + removal_frames
    return result


def _available_coverage(parent: dict[str, Any], siblings: list[dict[str, Any]]) -> tuple[Fraction, Fraction]:
    start = time_fraction(parent["source_range"]["start"])
    duration = time_fraction(parent["source_range"]["duration"])
    end = start + duration
    left, right = Fraction(), Fraction(str(parent["metadata"]["duration"]))
    for sibling in siblings:
        if sibling is parent or sibling["media_id"] != parent["media_id"]:
            continue
        sibling_start = time_fraction(sibling["source_range"]["start"])
        sibling_end = sibling_start + time_fraction(sibling["source_range"]["duration"])
        if sibling_end <= start:
            left = max(left, sibling_end)
        elif sibling_start >= end:
            right = min(right, sibling_start)
        else:
            raise AutoEditorError("Los clips lógicos ya solapan source ranges antes de microcuts.")
    return left, right


def _validated_cuts(
    parent: dict[str, Any], cuts: list[tuple[Fraction, Fraction, dict[str, str]]],
    level: int, fps: Fraction, siblings: list[dict[str, Any]],
) -> tuple[list[tuple[Fraction, Fraction, dict[str, str]]], Fraction, Fraction]:
    if not cuts:
        raise AutoEditorError("No hay rangos de microcut aplicables.")
    if level == 0:
        raise AutoEditorError("El nivel 0 no admite microcuts.")
    if len(cuts) > _MAX_CUTS[level]:
        raise AutoEditorError(f"El nivel {level} admite como máximo {_MAX_CUTS[level]} microcuts por clip.")
    source_start = time_fraction(parent["source_range"]["start"])
    source_duration = time_fraction(parent["source_range"]["duration"])
    source_end = source_start + source_duration
    minimum_child, minimum_gap = _minimum_frames(fps)
    normalized = sorted(cuts, key=lambda item: (item[0], item[1], item[2]["decision_id"]))
    previous_end = source_start
    removed = Fraction()
    for start, duration, _ in normalized:
        end = start + duration
        if start < source_start or end > source_end:
            raise AutoEditorError("Un microcut debe quedar dentro del source range lógico del clip.")
        _frame_count(start - source_start, fps, "El inicio del microcut")
        duration_frames = _frame_count(duration, fps, "La duración del microcut")
        if duration_frames < minimum_gap:
            raise AutoEditorError("Un microcut es demasiado corto para ser editorialmente perceptible.")
        if start < previous_end:
            raise AutoEditorError("Los rangos eliminados de microcuts no pueden solaparse.")
        kept_frames = _frame_count(start - previous_end, fps, "El fragmento entre microcuts")
        if kept_frames < minimum_child:
            raise AutoEditorError("Los microcuts dejarían un subclip demasiado corto.")
        previous_end = end
        removed += duration
    if _frame_count(source_end - previous_end, fps, "El último fragmento") < minimum_child:
        raise AutoEditorError("Los microcuts dejarían un subclip final demasiado corto.")
    left_bound, right_bound = _available_coverage(parent, siblings)
    right_extension = min(removed, right_bound - source_end)
    left_extension = removed - right_extension
    if left_extension > source_start - left_bound:
        raise AutoEditorError(
            "No hay source contiguo libre suficiente para conservar la duración de la timeline."
        )
    return normalized, left_extension, right_extension


def _child_id(parent_id: str, start: Fraction, duration: Fraction) -> str:
    identity = {"parent": parent_id, "start": _time(start), "duration": _time(duration)}
    return f"{parent_id}-mc-{digest(identity)[:12]}"


def _children(
    parent: dict[str, Any], cuts: list[tuple[Fraction, Fraction, dict[str, str]]], level: int,
    fps: Fraction, siblings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cuts, left_extension, right_extension = _validated_cuts(parent, cuts, level, fps, siblings)
    parent_start = time_fraction(parent["source_range"]["start"])
    parent_duration = time_fraction(parent["source_range"]["duration"])
    parent_end = parent_start + parent_duration
    coverage_start, coverage_end = parent_start - left_extension, parent_end + right_extension
    timeline_start, timeline_end = timeline_frames(parent)
    cursor_source, cursor_timeline = coverage_start, timeline_start
    origins = sorted({(item[2]["decision_id"], item[2]["provenance"]) for item in cuts})
    provenance = [
        {"decision_id": decision_id, "provenance": value}
        for decision_id, value in origins
    ]
    pieces = []
    for cut_start, cut_duration, _ in cuts:
        pieces.append((cursor_source, cut_start))
        cursor_source = cut_start + cut_duration
    pieces.append((cursor_source, coverage_end))
    children = []
    for source_start, source_end in pieces:
        source_duration = source_end - source_start
        frame_duration = _frame_count(source_duration, fps, "La duración del subclip")
        child = deepcopy(parent)
        child["clip_id"] = _child_id(parent["clip_id"], source_start, source_duration)
        child["parent_clip_id"] = parent["clip_id"]
        child["parent_source_range"] = deepcopy(parent["source_range"])
        child["source_range"] = _range(source_start, source_duration)
        child["timeline_range"] = {
            "start_frames": cursor_timeline,
            "duration_frames": frame_duration,
        }
        child["microcut"] = {
            "level": level,
            "parent_source_coverage": _range(coverage_start, coverage_end - coverage_start),
            "removed_ranges": [_range(start, duration) for start, duration, _ in cuts],
            "provenance": provenance,
        }
        children.append(child)
        cursor_timeline += frame_duration
    if cursor_timeline != timeline_end:
        raise AutoEditorError("Los microcuts no conservaron la duración exacta del clip lógico.")
    return children


def _proposal_payloads(
    timeline: dict[str, Any], resolved: dict[str, Any], level: int, pace: str,
    beats: list[Fraction],
) -> list[dict[str, Any]]:
    # Una propuesta automática vigente ya define el montaje determinista. Sin este corte,
    # regenerar añadiría propuestas para el "siguiente" clip elegible en cada ejecución.
    if any(
        item["feature"] == "microcuts" and any(
            property_["provenance"] in {"automatic", "accepted", "modified"}
            for property_ in item["properties"].values()
        )
        for item in resolved["targets"]
    ):
        return []
    fps = rate_fraction(timeline["sequence"]["fps"])
    parents = video_clips(timeline)
    candidates = []
    for parent in parents:
        properties, ranges = _target_properties(resolved, parent["clip_id"])
        if ranges or any(name in properties for name in ("placement", "remove_ranges", "enabled")):
            continue
        score = _auto_score(parent)
        if score >= 0.72:
            candidates.append((score, parent["clip_id"], parent))
    fraction = {"calm": 0.34, "balanced": 0.5, "dynamic": 0.67}[pace]
    count = min(_MAX_CUTS[level], max(1, round(len(candidates) * fraction))) if candidates else 0
    payloads = []
    for _, _, parent in sorted(candidates, key=lambda item: (-item[0], item[1]))[:count]:
        ranges = _automatic_ranges(parent, level, fps, pace, beats)
        cuts = [(start, duration, {"decision_id": "automatic", "provenance": "automatic"})
                for start, duration in ranges]
        try:
            _validated_cuts(parent, cuts, level, fps, parents)
        except AutoEditorError:
            continue
        payloads.append({
            "feature": "microcuts", "target": {"kind": "clip", "clip_id": parent["clip_id"]},
            "provenance": "automatic",
            "properties": {
                "level": level,
                "placement": "explicit",
                "remove_ranges": [_range(start, duration) for start, duration in ranges],
            },
        })
    return payloads


def _music_beats(music: dict[str, Any] | None) -> list[Fraction]:
    if not music:
        return []
    return [Fraction(str(beat)).limit_denominator(1_000_000_000) for beat in music.get("beats", [])]


def apply_microcuts(
    project: Project, timeline: dict[str, Any], *, pace: str = "balanced",
    music: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Proyecta decisiones de microcuts en hijos de timeline sin alterar su duración."""
    if pace not in {"calm", "balanced", "dynamic"}:
        raise AutoEditorError("El ritmo de microcuts no es compatible.")
    result = deepcopy(timeline)
    resolved = resolve_decisions(project, result)
    feature = resolved["feature_modes"]["microcuts"]
    mode = feature["value"]
    level = _integer(
        _property(feature.get("settings", {}), "level", 2),
        "El nivel global de microcuts",
    )
    if mode == "off" or level == 0:
        result["editorial_decisions"] = resolved
        validate_timeline(result)
        return result, []
    if mode in {"auto", "hybrid"}:
        payloads = _proposal_payloads(result, resolved, level, pace, _music_beats(music))
        if payloads:
            record_decisions(project, payloads)
            resolved = resolve_decisions(project, result)
    result["editorial_decisions"] = resolved
    fps = rate_fraction(result["sequence"]["fps"])
    parents = video_clips(result)
    transformed = []
    warnings = []
    for parent in parents:
        properties, range_targets = _target_properties(resolved, parent["clip_id"])
        if _property(properties, "enabled", True) is False:
            transformed.append(parent)
            continue
        parent_level = _integer(_property(properties, "level", level), "El nivel de microcuts")
        if parent_level == 0:
            transformed.append(parent)
            continue
        cuts = _ranges_from_properties(properties, range_targets)
        placement = _property(properties, "placement")
        if placement is not None and placement not in {"auto", "explicit"}:
            raise AutoEditorError("placement de microcuts debe ser auto o explicit.")
        if placement == "auto":
            origin = _property_origin(properties, "placement")
            if origin is None:
                raise AutoEditorError("placement perdió su procedencia editorial.")
            cuts = [
                (start, duration, origin)
                for start, duration in _automatic_ranges(
                    parent, parent_level, fps, pace, _music_beats(music),
                )
            ]
        if not cuts:
            transformed.append(parent)
            continue
        try:
            transformed.extend(_children(parent, cuts, parent_level, fps, parents))
        except AutoEditorError:
            if all(origin["provenance"] == "automatic" for _, _, origin in cuts):
                transformed.append(parent)
                continue
            raise
    result["video_tracks"][0]["clips"] = transformed
    register_derived_clip_ids(project, {
        f"microcut:{clip['clip_id']}": clip["clip_id"]
        for clip in transformed if clip.get("parent_clip_id")
    })
    validate_timeline(result)
    return result, warnings
