"""Retiming racional y slow motion como consumidor del Decision System."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any

from .decisions import record_decisions, resolve_decisions
from .project import Project
from .timeline import (
    RationalRate, RationalTime, positive_fraction, rate_fraction, time_fraction,
    timeline_frames, validate_timeline, video_clips,
)
from .util import AutoEditorError

SLOWMO_LEVELS = (0, 1, 2, 3)
_DESIRED_SPEED = {1: Fraction(4, 5), 2: Fraction(1, 2), 3: Fraction(2, 5)}


def _time(value: Fraction) -> dict[str, int]:
    return RationalTime(value.numerator, value.denominator).to_dict()


def _source_range(start: Fraction, duration: Fraction) -> dict[str, dict[str, int]]:
    return {"start": _time(start), "duration": _time(duration)}


def _rate(value: Fraction) -> dict[str, int]:
    return RationalRate(value.numerator, value.denominator).to_dict()


def _frames_exact(value: Fraction, fps: Fraction, name: str) -> int:
    frames = value * fps
    if frames.denominator != 1:
        raise AutoEditorError(f"{name} debe coincidir exactamente con frames de secuencia.")
    return frames.numerator


def _round_fraction(value: Fraction) -> int:
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _level(value: Any, name: str, default: int = 2) -> int:
    if value is None:
        return default
    if type(value) is not int or value not in SLOWMO_LEVELS:
        raise AutoEditorError(f"{name} debe ser un entero entre 0 y 3.")
    return value


def _property(properties: dict[str, dict[str, Any]], name: str, default: Any = None) -> Any:
    value = properties.get(name)
    return default if value is None else value["value"]


def _origin(properties: dict[str, dict[str, Any]], *names: str) -> dict[str, str]:
    for name in names:
        if name in properties:
            value = properties[name]
            return {"decision_id": value["decision_id"], "provenance": value["provenance"]}
    raise AutoEditorError("La decisión de slow motion perdió su procedencia.")


def _speed(value: Any, name: str) -> Fraction:
    if not isinstance(value, dict):
        raise AutoEditorError(f"{name} debe ser una tasa racional numerator/denominator.")
    speed = positive_fraction(value, name)
    if speed > 1:
        raise AutoEditorError("Slow motion no admite una velocidad superior a 1.")
    return speed


def _clean_speed(level: int, clip: dict[str, Any], sequence_fps: Fraction) -> Fraction | None:
    source_fps = rate_fraction(clip["source_fps"])
    minimum = sequence_fps / source_fps
    chosen = max(_DESIRED_SPEED[level], minimum)
    return chosen if chosen < 1 else None


def _slowmo_score(clip: dict[str, Any], sequence_fps: Fraction) -> float:
    editorial = clip["editorial"]
    text = " ".join([
        editorial.get("stage", ""), editorial.get("shot", ""), *editorial.get("tags", []),
    ]).casefold()
    rate_ratio = float(rate_fraction(clip["source_fps"]) / sequence_fps)
    score = min(1.2, max(0.0, rate_ratio - 1))
    score += float(editorial.get("confidence", 0)) * 0.25
    score += float(editorial.get("interest", 0)) * 0.2
    if editorial.get("stage") == "action":
        score += 0.45
    if editorial.get("shot") in {"wide", "medium", "pov"}:
        score += 0.08
    score += 0.1 * sum(tag in text for tag in (
        "action", "movement", "dance", "gesture", "jump", "martial", "training", "motorcycle",
    ))
    score -= 0.25 * sum(tag in text for tag in ("static", "pause", "landscape"))
    return score


def _actual_targets(clips: list[dict[str, Any]], target_id: str) -> list[dict[str, Any]]:
    direct = [clip for clip in clips if clip["clip_id"] == target_id]
    if direct:
        return direct
    return [clip for clip in clips if clip.get("parent_clip_id") == target_id]


def _target_items(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in resolved["targets"] if item["feature"] == "slow_motion"]


def _has_generated_target(resolved: dict[str, Any]) -> bool:
    return any(
        any(
            value["provenance"] in {"automatic", "accepted", "modified"}
            for value in item["properties"].values()
        )
        for item in _target_items(resolved)
    )


def _proposal_payloads(
    timeline: dict[str, Any], resolved: dict[str, Any], level: int, pace: str,
) -> list[dict[str, Any]]:
    if _has_generated_target(resolved):
        return []
    sequence_fps = rate_fraction(timeline["sequence"]["fps"])
    candidates = []
    for clip in video_clips(timeline):
        if any(item["target"].get("clip_id") in {clip["clip_id"], clip.get("parent_clip_id")}
               for item in _target_items(resolved)):
            continue
        speed = _clean_speed(level, clip, sequence_fps)
        if speed is None:
            continue
        score = _slowmo_score(clip, sequence_fps)
        if score >= 0.75:
            candidates.append((score, clip["clip_id"], speed))
    maximum = {1: 1, 2: 1, 3: 2}[level]
    if pace == "calm":
        maximum = 1
    payloads = []
    for _, clip_id, speed in sorted(candidates, key=lambda item: (-item[0], item[1]))[:maximum]:
        payloads.append({
            "feature": "slow_motion", "target": {"kind": "clip", "clip_id": clip_id},
            "provenance": "automatic",
            "properties": {"placement": "auto", "level": level, "speed": _rate(speed)},
        })
    return payloads


def _music_frames(music: dict[str, Any] | None, fps: Fraction) -> list[int]:
    if not music:
        return []
    return sorted({
        _round_fraction(Fraction(str(beat)).limit_denominator(1_000_000_000) * fps)
        for beat in music.get("beats", []) if beat >= 0
    })


def _auto_region(
    clip: dict[str, Any], level: int, speed: Fraction, fps: Fraction,
    music_frames: list[int],
) -> tuple[Fraction, Fraction] | None:
    source_start = time_fraction(clip["source_range"]["start"])
    source_duration = time_fraction(clip["source_range"]["duration"])
    total_frames = _frames_exact(source_duration, fps, "La duración fuente")
    timeline_start, _ = timeline_frames(clip)
    wanted = {1: Fraction(3, 5), 2: Fraction(9, 10), 3: Fraction(6, 5)}[level]
    region_frames = max(8, _round_fraction(wanted * fps))
    region_frames -= region_frames % speed.numerator
    extra = region_frames * speed.denominator // speed.numerator - region_frames
    minimum_normal = max(12, _round_fraction(Fraction(2, 5) * fps))
    if region_frames <= 0 or total_frames - extra < region_frames + minimum_normal:
        maximum = total_frames - minimum_normal
        while region_frames > 0:
            extra = region_frames * speed.denominator // speed.numerator - region_frames
            if region_frames + extra <= maximum:
                break
            region_frames -= speed.numerator
    if region_frames < 8:
        return None
    extra = region_frames * speed.denominator // speed.numerator - region_frames
    consumed_frames = total_frames - extra
    minimum_side = minimum_normal // 2
    desired = (consumed_frames - region_frames) // 2
    beat_candidates = [
        frame - timeline_start for frame in music_frames
        if minimum_side <= frame - timeline_start <= consumed_frames - region_frames - minimum_side
    ]
    offset = min(beat_candidates, key=lambda frame: (abs(frame - desired), frame)) \
        if beat_candidates else desired
    offset = max(minimum_side, min(offset, consumed_frames - region_frames - minimum_side))
    trim_left = min(extra // 2, offset)
    new_start = source_start + Fraction(trim_left, 1) / fps
    return new_start + Fraction(offset, 1) / fps, Fraction(region_frames, 1) / fps


def _audio_snapshot(clip: dict[str, Any], original_source_range: dict[str, Any]) -> None:
    if clip["audio"].get("enabled"):
        clip["audio"]["retiming"] = {"mode": "none"}
        clip["audio"]["source_range"] = deepcopy(original_source_range)
        clip["audio"]["timeline_range"] = deepcopy(clip["timeline_range"])


def _retiming_provenance(origin: dict[str, str], target_id: str) -> dict[str, Any]:
    return {
        "decision_id": origin["decision_id"], "provenance": origin["provenance"],
        "target_clip_id": target_id,
    }


def _apply_constant(
    clip: dict[str, Any], speed: Fraction, fps: Fraction, origin: dict[str, str], target_id: str,
) -> dict[str, Any]:
    if speed == 1:
        return clip
    source_start = time_fraction(clip["source_range"]["start"])
    source_duration = time_fraction(clip["source_range"]["duration"])
    total_frames = _frames_exact(source_duration, fps, "La duración fuente")
    consumed = Fraction(total_frames, 1) * speed
    if consumed.denominator != 1 or consumed < 12:
        raise AutoEditorError("La velocidad constante no produce una duración exacta y segura.")
    trim = total_frames - consumed.numerator
    new_start = source_start + Fraction(trim // 2, 1) / fps
    original = deepcopy(clip["source_range"])
    result = deepcopy(clip)
    result["source_range"] = _source_range(new_start, Fraction(consumed.numerator, 1) / fps)
    result["retiming"] = {
        "mode": "constant", "speed": _rate(speed), "regions": [],
        "provenance": [_retiming_provenance(origin, target_id)],
    }
    _audio_snapshot(result, original)
    return result


def _apply_region(
    clip: dict[str, Any], region_start: Fraction, region_duration: Fraction,
    speed: Fraction, fps: Fraction, origin: dict[str, str], target_id: str,
) -> dict[str, Any]:
    if speed == 1:
        return clip
    old_start = time_fraction(clip["source_range"]["start"])
    old_duration = time_fraction(clip["source_range"]["duration"])
    old_end = old_start + old_duration
    region_end = region_start + region_duration
    if region_start < old_start or region_end > old_end:
        raise AutoEditorError("El rango de slow motion queda fuera del clip seleccionado.")
    total_frames = _frames_exact(old_duration, fps, "La duración fuente")
    start_offset = _frames_exact(region_start - old_start, fps, "El inicio de slow motion")
    region_frames = _frames_exact(region_duration, fps, "La duración de slow motion")
    slow_frames = Fraction(region_frames, 1) / speed
    if slow_frames.denominator != 1:
        raise AutoEditorError("La velocidad no produce un rango slow motion de frames exactos.")
    extra = slow_frames.numerator - region_frames
    consumed = total_frames - extra
    if region_frames < 8 or consumed < region_frames + 12:
        raise AutoEditorError("El rango slow motion no deja suficiente material normal.")
    low_trim = max(0, start_offset + region_frames - consumed)
    high_trim = min(extra, start_offset)
    if low_trim > high_trim:
        raise AutoEditorError("No se puede conservar el rango slow motion dentro del presupuesto temporal.")
    trim_left = max(low_trim, min(extra // 2, high_trim))
    new_start = old_start + Fraction(trim_left, 1) / fps
    new_end = new_start + Fraction(consumed, 1) / fps
    timeline_start, timeline_end = timeline_frames(clip)
    cursor_source, cursor_timeline = new_start, timeline_start
    regions = []
    parts = [
        (new_start, region_start, Fraction(1)),
        (region_start, region_end, speed),
        (region_end, new_end, Fraction(1)),
    ]
    for index, (start, end, part_speed) in enumerate(parts, start=1):
        if end <= start:
            continue
        source_frames = _frames_exact(end - start, fps, "Una región de retiming")
        timeline_frames_ = Fraction(source_frames, 1) / part_speed
        if timeline_frames_.denominator != 1:
            raise AutoEditorError("Una región de retiming no coincide con frames enteros.")
        regions.append({
            "region_id": f"{clip['clip_id']}-rt-{index}",
            "source_range": _source_range(start, end - start),
            "timeline_range": {
                "start_frames": cursor_timeline,
                "duration_frames": timeline_frames_.numerator,
            },
            "speed": _rate(part_speed),
        })
        cursor_source = end
        cursor_timeline += timeline_frames_.numerator
    if cursor_source != new_end or cursor_timeline != timeline_end:
        raise AutoEditorError("El retiming regional no conserva la duración exacta del clip.")
    original = deepcopy(clip["source_range"])
    result = deepcopy(clip)
    result["source_range"] = _source_range(new_start, new_end - new_start)
    result["retiming"] = {
        "mode": "regions", "speed": _rate(Fraction(consumed, total_frames)),
        "regions": regions,
        "provenance": [_retiming_provenance(origin, target_id)],
    }
    _audio_snapshot(result, original)
    return result


def _operation_for_item(
    item: dict[str, Any], clips: list[dict[str, Any]], default_level: int,
    fps: Fraction, music_frames: list[int],
) -> tuple[dict[str, Any], str, Fraction, Fraction | None, Fraction | None, dict[str, str]] | None:
    target = item["target"]
    properties = item["properties"]
    targets = _actual_targets(clips, target["clip_id"])
    if not targets or _property(properties, "enabled", True) is False:
        return None
    level = _level(_property(properties, "level"), "El nivel de slow motion", default_level)
    if level == 0:
        return None
    explicit_speed = _property(properties, "speed")
    placement = _property(properties, "placement", "explicit" if target["kind"] == "range" else "auto")
    if placement not in {"auto", "constant", "explicit"}:
        raise AutoEditorError("placement de slow motion debe ser auto, constant o explicit.")
    if target["kind"] == "range":
        target_start = time_fraction(target["start"])
        region_duration = time_fraction(target["duration"])
        if target.get("space") == "source":
            containing = [
                clip for clip in targets
                if time_fraction(clip["source_range"]["start"]) <= target_start
                and target_start + region_duration <= time_fraction(clip["source_range"]["start"])
                + time_fraction(clip["source_range"]["duration"])
            ]
        else:
            containing = [
                clip for clip in targets
                if Fraction(timeline_frames(clip)[0], 1) / fps <= target_start
                and target_start + region_duration <= Fraction(timeline_frames(clip)[1], 1) / fps
            ]
        if len(containing) != 1:
            raise AutoEditorError("El rango slow motion debe pertenecer a un único clip o hijo microcut.")
        clip = containing[0]
        if target.get("space") == "timeline":
            clip_timeline_start = Fraction(timeline_frames(clip)[0], 1) / fps
            region_start = (
                time_fraction(clip["source_range"]["start"])
                + target_start - clip_timeline_start
            )
        else:
            region_start = target_start
        speed = _speed(explicit_speed, "speed")
        return clip, "region", speed, region_start, region_duration, _origin(properties, "speed")
    ranked = sorted(
        targets,
        key=lambda clip: (-_slowmo_score(clip, fps), clip["clip_id"]),
    )
    clip = ranked[0]
    speed = _speed(explicit_speed, "speed") if explicit_speed is not None else _clean_speed(level, clip, fps)
    if speed is None:
        return None
    origin = _origin(properties, "speed", "placement", "level")
    if placement == "constant":
        return clip, "constant", speed, None, None, origin
    region = _auto_region(clip, level, speed, fps, music_frames)
    if region is None:
        return None
    return clip, "region", speed, region[0], region[1], origin


def apply_slow_motion(
    project: Project, timeline: dict[str, Any], *, pace: str = "balanced",
    music: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Aplica slow motion sin cambiar ningún timeline_range ni la música."""
    if pace not in {"calm", "balanced", "dynamic"}:
        raise AutoEditorError("El ritmo de slow motion no es compatible.")
    result = deepcopy(timeline)
    resolved = resolve_decisions(project, result)
    feature = resolved["feature_modes"]["slow_motion"]
    mode = feature["value"]
    level = _level(
        _property(feature.get("settings", {}), "level"),
        "El nivel global de slow motion",
    )
    if mode == "off" or level == 0:
        result["editorial_decisions"] = resolved
        validate_timeline(result)
        return result, []
    if mode in {"auto", "hybrid"}:
        payloads = _proposal_payloads(result, resolved, level, pace)
        if payloads:
            record_decisions(project, payloads)
            resolved = resolve_decisions(project, result)
    result["editorial_decisions"] = resolved
    clips = video_clips(result)
    music_frames = _music_frames(music, rate_fraction(result["sequence"]["fps"]))
    operations = []
    for item in _target_items(resolved):
        operation = _operation_for_item(
            item, clips, level, rate_fraction(result["sequence"]["fps"]), music_frames,
        )
        if operation:
            direct = any(clip["clip_id"] == item["target"]["clip_id"] for clip in clips)
            provenance = max(
                (value["provenance"] for value in item["properties"].values()),
                key=lambda value: {"automatic": 1, "accepted": 2, "modified": 3, "manual": 4}[value],
            )
            operations.append((
                {"automatic": 1, "accepted": 2, "modified": 3, "manual": 4}[provenance],
                int(direct), item["target"]["clip_id"], operation,
            ))
    selected: dict[str, tuple[Any, ...]] = {}
    for rank, direct, target_id, operation in sorted(operations):
        clip = operation[0]
        selected[clip["clip_id"]] = (rank, direct, target_id, operation)
    replacements = {}
    fps = rate_fraction(result["sequence"]["fps"])
    for clip_id, (_, _, target_id, operation) in selected.items():
        clip, kind, speed, start, duration, origin = operation
        minimum_clean = fps / rate_fraction(clip["source_fps"])
        if speed < minimum_clean:
            if origin["provenance"] == "automatic":
                continue
            raise AutoEditorError("El slow motion solicitado no tiene suficientes frames de origen.")
        replacements[clip_id] = _apply_constant(clip, speed, fps, origin, target_id) \
            if kind == "constant" else _apply_region(
                clip, start, duration, speed, fps, origin, target_id,
            )
    result["video_tracks"][0]["clips"] = [
        replacements.get(clip["clip_id"], clip) for clip in clips
    ]
    validate_timeline(result)
    return result, []
