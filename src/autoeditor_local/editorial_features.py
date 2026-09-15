"""Transiciones y efectos declarativos sobre la timeline canónica."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any

from .decisions import record_decisions, resolve_decisions
from .project import Project
from .timeline import (
    RationalTime,
    rate_fraction,
    time_fraction,
    timeline_frames,
    validate_timeline,
    video_clips,
)
from .util import AutoEditorError, digest

TRANSITION_TYPES = ("cut", "cross_dissolve", "dip_to_black", "dip_to_white")
EFFECT_TYPES = ("subtle_push_in", "punch_in", "soft_zoom_in", "soft_zoom_out")
_PROVENANCE_RANK = {"automatic": 1, "accepted": 2, "modified": 3, "manual": 4}


def _property(properties: dict[str, dict[str, Any]], name: str, default: Any = None) -> Any:
    value = properties.get(name)
    return default if value is None else value["value"]


def _origin(properties: dict[str, dict[str, Any]], *names: str) -> dict[str, str]:
    values = [properties[name] for name in names if name in properties]
    if not values:
        raise AutoEditorError("La operación editorial perdió su procedencia.")
    value = max(values, key=lambda item: _PROVENANCE_RANK[item["provenance"]])
    return {"decision_id": value["decision_id"], "provenance": value["provenance"]}


def _feature_targets(resolved: dict[str, Any], feature: str) -> list[dict[str, Any]]:
    return [item for item in resolved["targets"] if item["feature"] == feature]


def _actual_clips(clips: list[dict[str, Any]], clip_id: str) -> list[dict[str, Any]]:
    direct = [clip for clip in clips if clip["clip_id"] == clip_id]
    if direct:
        return direct
    return [clip for clip in clips if clip.get("parent_clip_id") == clip_id]


def _has_generated_target(resolved: dict[str, Any], feature: str) -> bool:
    return any(
        any(
            value["provenance"] in {"automatic", "accepted", "modified"}
            for value in item["properties"].values()
        )
        for item in _feature_targets(resolved, feature)
    )


def _visual_difference(left: dict[str, Any], right: dict[str, Any]) -> float:
    try:
        first = int(left["editorial"].get("dhash"), 16)
        second = int(right["editorial"].get("dhash"), 16)
    except (TypeError, ValueError):
        return 0.0
    return (first ^ second).bit_count() / 64


def _transition_score(
    left: dict[str, Any], right: dict[str, Any], pace: str,
    fps: Fraction, beats: list[Fraction],
) -> float:
    left_editorial, right_editorial = left["editorial"], right["editorial"]
    score = 0.0
    if left["media_id"] != right["media_id"]:
        score += 0.25
    if left_editorial.get("stage") != right_editorial.get("stage"):
        score += 0.25
    if left_editorial.get("shot") != right_editorial.get("shot"):
        score += 0.1
    score += min(0.15, _visual_difference(left, right) * 0.2)
    if left_editorial.get("stage") in {"closing", "pause"}:
        score += 0.25
    if right_editorial.get("stage") in {"establishing", "preparation"}:
        score += 0.2
    if pace == "dynamic":
        score -= 0.2
    elif pace == "calm":
        score += 0.1
    boundary = Fraction(timeline_frames(right)[0], 1) / fps
    if any(abs(beat - boundary) <= Fraction(3, 20) for beat in beats):
        score += 0.1
    return score


def _transition_proposals(
    timeline: dict[str, Any], resolved: dict[str, Any], pace: str,
    music: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if _has_generated_target(resolved, "transitions"):
        return []
    clips = video_clips(timeline)
    fps = rate_fraction(timeline["sequence"]["fps"])
    beats = [
        Fraction(str(beat)).limit_denominator(1_000_000_000)
        for beat in (music or {}).get("beats", [])
    ]
    targeted = {item["target"]["clip_id"] for item in _feature_targets(resolved, "transitions")}
    candidates = []
    for left, right in zip(clips, clips[1:], strict=False):
        if left["clip_id"] in targeted or left.get("parent_clip_id") in targeted:
            continue
        score = _transition_score(left, right, pace, fps, beats)
        if score >= 0.65:
            candidates.append((score, left["clip_id"], right["clip_id"]))
    if not candidates:
        return []
    _, from_id, to_id = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))[0]
    frames = max(2, round(float(fps) * (0.35 if pace == "calm" else 0.25)))
    return [{
        "feature": "transitions",
        "target": {"kind": "clip", "clip_id": from_id},
        "provenance": "automatic",
        "properties": {
            "to_clip_id": to_id,
            "type": "cross_dissolve",
            "duration": RationalTime(frames, fps.numerator).to_dict()
            if fps.denominator == 1 else RationalTime.from_seconds(Fraction(frames, 1) / fps).to_dict(),
        },
    }]


def _duration_frames(value: Any, fps: Fraction) -> tuple[int, dict[str, int]]:
    if isinstance(value, dict):
        seconds = time_fraction(value)
    elif isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AutoEditorError("La duración de transición debe ser un tiempo racional o segundos.")
    else:
        try:
            seconds = Fraction(str(value)).limit_denominator(1_000_000_000)
        except (ValueError, ZeroDivisionError) as exc:
            raise AutoEditorError("La duración de transición no es válida.") from exc
    frames = seconds * fps
    if seconds <= 0 or frames.denominator != 1:
        raise AutoEditorError("La duración de transición debe coincidir exactamente con frames.")
    return frames.numerator, RationalTime(seconds.numerator, seconds.denominator).to_dict()


def apply_transitions(
    project: Project, timeline: dict[str, Any], *, pace: str = "balanced",
    music: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Materializa transiciones; cut se representa por ausencia de operación."""
    if pace not in {"calm", "balanced", "dynamic"}:
        raise AutoEditorError("El ritmo de transiciones no es compatible.")
    result = deepcopy(timeline)
    result["transitions"] = []
    for clip in video_clips(result):
        clip["transitions"] = {"in": None, "out": None}
    resolved = resolve_decisions(project, result)
    feature = resolved["feature_modes"]["transitions"]
    if feature["value"] == "off":
        result["editorial_decisions"] = resolved
        validate_timeline(result)
        return result, []
    if feature["value"] in {"auto", "hybrid"}:
        proposals = _transition_proposals(result, resolved, pace, music)
        if proposals:
            record_decisions(project, proposals)
            resolved = resolve_decisions(project, result)
    result["editorial_decisions"] = resolved
    clips = video_clips(result)
    clip_index = {clip["clip_id"]: index for index, clip in enumerate(clips)}
    selected: dict[str, tuple[int, int, dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for item in _feature_targets(resolved, "transitions"):
        properties = item["properties"]
        transition_type = _property(properties, "type", _property(properties, "kind", "cut"))
        if transition_type not in TRANSITION_TYPES:
            raise AutoEditorError(f"Tipo de transición no soportado: {transition_type}")
        left_group = _actual_clips(clips, item["target"]["clip_id"])
        to_id = _property(properties, "to_clip_id")
        if not isinstance(to_id, str):
            if left_group:
                index = clip_index[left_group[-1]["clip_id"]]
                to_id = clips[index + 1]["clip_id"] if index + 1 < len(clips) else None
        right_group = _actual_clips(clips, to_id) if isinstance(to_id, str) else []
        if not left_group or not right_group:
            raise AutoEditorError("La transición referencia un clip de origen o destino inexistente.")
        left, right = left_group[-1], right_group[0]
        if clip_index[right["clip_id"]] != clip_index[left["clip_id"]] + 1:
            raise AutoEditorError("La transición manual debe unir clips adyacentes.")
        origin = _origin(properties, "type", "kind", "to_clip_id", "duration")
        rank = _PROVENANCE_RANK[origin["provenance"]]
        direct = int(item["target"]["clip_id"] == left["clip_id"])
        selected[left["clip_id"]] = max(
            selected.get(left["clip_id"], (-1, -1, item, left, right)),
            (rank, direct, item, left, right), key=lambda value: value[:2],
        )
    fps = rate_fraction(result["sequence"]["fps"])
    for _, _, item, left, right in sorted(
        selected.values(), key=lambda value: value[3]["clip_id"],
    ):
        properties = item["properties"]
        transition_type = _property(properties, "type", _property(properties, "kind", "cut"))
        if transition_type == "cut":
            continue
        default_frames = max(2, round(float(fps) * 0.25))
        duration_value = _property(
            properties, "duration", RationalTime.from_seconds(Fraction(default_frames, 1) / fps).to_dict(),
        )
        duration_frames, duration = _duration_frames(duration_value, fps)
        if duration_frames * 2 > min(
            left["timeline_range"]["duration_frames"], right["timeline_range"]["duration_frames"],
        ):
            raise AutoEditorError("La transición es demasiado larga para los clips seleccionados.")
        origin = _origin(properties, "type", "kind", "duration", "to_clip_id")
        transition_id = f"transition-{digest([left['clip_id'], right['clip_id'], origin['decision_id']])[:12]}"
        transition = {
            "transition_id": transition_id,
            "from_clip_id": left["clip_id"],
            "to_clip_id": right["clip_id"],
            "type": transition_type,
            "duration": duration,
            "duration_frames": duration_frames,
            "at_frame": timeline_frames(right)[0],
            **origin,
        }
        left["transitions"]["out"] = transition_id
        right["transitions"]["in"] = transition_id
        result["transitions"].append(transition)
    validate_timeline(result)
    return result, []


def _effect_score(clip: dict[str, Any]) -> float:
    editorial = clip["editorial"]
    text = " ".join([editorial.get("stage", ""), editorial.get("shot", ""), *editorial.get("tags", [])]).casefold()
    score = float(editorial.get("confidence", 0)) * 0.4
    score += float(editorial.get("interest", 0)) * 0.15
    if editorial.get("stage") in {"preparation", "pause", "closing", "establishing"}:
        score += 0.35
    if editorial.get("shot") in {"wide", "medium", "close"}:
        score += 0.15
    if any(word in text for word in ("static", "pose", "look", "face", "establishing")):
        score += 0.2
    if editorial.get("stage") == "action":
        score -= 0.35
    return score


def _effect_proposals(
    timeline: dict[str, Any], resolved: dict[str, Any], level: int,
) -> list[dict[str, Any]]:
    if _has_generated_target(resolved, "effects"):
        return []
    targeted = {item["target"]["clip_id"] for item in _feature_targets(resolved, "effects")}
    candidates = [
        (_effect_score(clip), clip["clip_id"])
        for clip in video_clips(timeline)
        if clip["clip_id"] not in targeted and clip.get("parent_clip_id") not in targeted
    ]
    eligible = [item for item in candidates if item[0] >= 0.68]
    maximum = 1 if level < 3 else 2
    return [
        {
            "feature": "effects", "target": {"kind": "clip", "clip_id": clip_id},
            "provenance": "automatic",
            "properties": {"type": "subtle_push_in", "level": level},
        }
        for _, clip_id in sorted(eligible, key=lambda item: (-item[0], item[1]))[:maximum]
    ]


def _effect_level(value: Any, default: int = 1) -> int:
    value = default if value is None else value
    if type(value) is not int or value not in {0, 1, 2, 3}:
        raise AutoEditorError("El nivel de effects debe ser un entero entre 0 y 3.")
    return value


def _effect_target(
    item: dict[str, Any], clips: list[dict[str, Any]], fps: Fraction,
) -> dict[str, Any] | None:
    target = item["target"]
    candidates = _actual_clips(clips, target["clip_id"])
    if target["kind"] == "range":
        if target.get("space") == "source":
            start = time_fraction(target["start"])
            end = start + time_fraction(target["duration"])
            candidates = [
                clip for clip in candidates
                if time_fraction(clip["source_range"]["start"]) <= start
                and end <= time_fraction(clip["source_range"]["start"])
                + time_fraction(clip["source_range"]["duration"])
            ]
        else:
            start = time_fraction(target["start"])
            end = start + time_fraction(target["duration"])
            candidates = [
                clip for clip in candidates
                if Fraction(timeline_frames(clip)[0], 1) / fps <= start
                and end <= Fraction(timeline_frames(clip)[1], 1) / fps
            ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda clip: (timeline_frames(clip)[1] - timeline_frames(clip)[0], clip["clip_id"]),
    )


def apply_effects(
    project: Project, timeline: dict[str, Any], *, pace: str = "balanced",
) -> tuple[dict[str, Any], list[str]]:
    """Materializa intenciones de efectos sin alterar source/timeline ranges."""
    if pace not in {"calm", "balanced", "dynamic"}:
        raise AutoEditorError("El ritmo de effects no es compatible.")
    result = deepcopy(timeline)
    result["effects"] = []
    for clip in video_clips(result):
        clip["effects"] = []
    resolved = resolve_decisions(project, result)
    feature = resolved["feature_modes"]["effects"]
    feature_level = _effect_level(_property(feature.get("settings", {}), "level"), 1)
    if feature["value"] == "off" or feature_level == 0:
        result["editorial_decisions"] = resolved
        validate_timeline(result)
        return result, []
    if feature["value"] in {"auto", "hybrid"}:
        proposals = _effect_proposals(result, resolved, feature_level)
        if proposals:
            record_decisions(project, proposals)
            resolved = resolve_decisions(project, result)
    result["editorial_decisions"] = resolved
    clips = video_clips(result)
    fps = rate_fraction(result["sequence"]["fps"])
    selected: dict[str, tuple[int, int, dict[str, Any], dict[str, Any]]] = {}
    for item in _feature_targets(resolved, "effects"):
        properties = item["properties"]
        if _property(properties, "enabled", True) is False:
            continue
        effect_type = _property(properties, "type")
        if effect_type not in EFFECT_TYPES:
            raise AutoEditorError(f"Tipo de efecto no soportado: {effect_type}")
        item_level = _effect_level(_property(properties, "level"), feature_level)
        if item_level == 0:
            continue
        clip = _effect_target(item, clips, fps)
        if clip is None:
            raise AutoEditorError("El efecto no encuentra un clip o rango aplicable.")
        origin = _origin(properties, "type", "level", "enabled")
        rank = _PROVENANCE_RANK[origin["provenance"]]
        direct = int(item["target"]["clip_id"] == clip["clip_id"])
        selected[clip["clip_id"]] = max(
            selected.get(clip["clip_id"], (-1, -1, item, origin)),
            (rank, direct, item, origin), key=lambda value: value[:2],
        )
    for clip_id, (_, _, item, origin) in sorted(selected.items()):
        effect_type = _property(item["properties"], "type")
        item_level = _effect_level(_property(item["properties"], "level"), feature_level)
        effect_id = f"effect-{digest([clip_id, effect_type, origin['decision_id']])[:12]}"
        effect = {
            "effect_id": effect_id,
            "clip_id": clip_id,
            "logical_target_clip_id": item["target"]["clip_id"],
            "type": effect_type,
            "level": item_level,
            "target": deepcopy(item["target"]),
            **origin,
        }
        next(clip for clip in clips if clip["clip_id"] == clip_id)["effects"].append(effect)
        result["effects"].append(effect)
    validate_timeline(result)
    return result, []
