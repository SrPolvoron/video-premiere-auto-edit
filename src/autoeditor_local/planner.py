"""Planner V2: selección editorial determinista con duraciones y diversidad globales."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict
from fractions import Fraction
from typing import Any

from .decisions import ensure_clip_ids, resolve_decisions
from .editorial_features import apply_effects, apply_transitions
from .microcuts import apply_microcuts
from .profiles import PRESETS as PRESETS
from .profiles import Policy, resolve_policy
from .project import Project
from .retiming import apply_slow_motion
from .timeline import build_timeline, validate_timeline
from .util import AutoEditorError, digest, finite, fps_fraction, json_text, now, seconds_to_frames


def make_policy(
    preset: str,
    intent: dict | None,
    priority: str = "balanced",
    prompt_intent: dict | None = None,
    cli_overrides: dict | None = None,
) -> Policy:
    """API compatible con V1 y punto único de composición de políticas V2."""
    return resolve_policy(preset, priority, intent, prompt_intent, cli_overrides)[0]


def _pace_ratio(pace: str) -> float:
    return {"calm": 0.76, "balanced": 0.5, "dynamic": 0.27}[pace]


def cut_length(position: int, remaining: int, fps: Fraction, policy: Policy, beats: list[float]) -> int:
    """Duración base compatible; el planner la adapta después a cada candidato."""
    low = max(1, seconds_to_frames(policy.min_shot, fps))
    high = max(low, seconds_to_frames(policy.max_shot, fps))
    target = round(low + (high - low) * _pace_ratio(policy.pace))
    choices = [seconds_to_frames(t, fps) - position for t in beats]
    choices = [value for value in choices if low <= value <= min(high, remaining)]
    if choices:
        target = min(choices, key=lambda value: (abs(value - target), value))
    return max(1, min(target, remaining))


def _range(segment: dict, length: int, fps: Fraction) -> tuple[float, float] | None:
    seconds = float(Fraction(length, 1) / fps)
    if segment["end"] - segment["start"] + 1e-8 < seconds:
        return None
    source_rate = fps_fraction(segment["metadata"]["fps"])
    minimum = math.ceil(segment["start"] * float(source_rate) - 1e-8)
    maximum = math.floor((segment["end"] - seconds) * float(source_rate) + 1e-8)
    if maximum < minimum:
        return None
    desired = max(0, segment["anchor"] - seconds / 2)
    first_frame = max(minimum, min(seconds_to_frames(desired, source_rate), maximum))
    start = first_frame / float(source_rate)
    return start, start + seconds


def _overlap(media_id: int, start: float, end: float, chosen: list[dict], exclude: dict | None = None) -> bool:
    return any(
        clip is not exclude and clip["media_id"] == media_id
        and start < clip["source_out"] - 1e-8 and end > clip["source_in"] + 1e-8
        for clip in chosen
    )


def _text(segment: dict) -> str:
    return " ".join([segment["label"], *segment["prediction"].get("tags", [])]).casefold()


def _matched_tags(segment: dict, wanted: list[str]) -> list[str]:
    text = _text(segment)
    return [tag for tag in wanted if tag.casefold() in text]


def _score(
    segment: dict,
    clips: list[dict],
    progress: float,
    policy: Policy,
    chronology_rank: dict[str, int],
    media_limit_relaxed: bool = False,
) -> float:
    """Score global: todo el historial influye, no solo el clip anterior."""
    confidence = segment["confidence"]
    score = 0.45 * segment["quality"] + 0.3 * segment["interest"] * confidence
    if segment.get("decision") == "prefer":
        score += 0.8

    preferred = _matched_tags(segment, policy.preferred_tags)
    avoided = _matched_tags(segment, policy.avoid_tags)
    tag_history = Counter(tag for clip in clips for tag in clip.get("_matched_preferences", []))
    score += sum(0.24 / (1 + tag_history[tag.casefold()]) for tag in preferred)
    score -= 0.8 * len(avoided)

    media_count = sum(clip["media_id"] == segment["media_id"] for clip in clips)
    shot_count = sum(clip["shot"] == segment["shot"] for clip in clips)
    stage_count = sum(clip["stage"] == segment["stage"] for clip in clips)
    score -= 0.22 * media_count
    if media_limit_relaxed:
        score -= 0.45 * media_count
    if segment["shot"] in policy.preferred_shots:
        score += 0.24
    if segment["shot"] in policy.avoid_shots:
        score -= 1.2
    if segment["shot"] != "unknown":
        score -= 0.1 * shot_count

    recent = clips[-3:]
    score -= 0.2 * sum(clip["media_id"] == segment["media_id"] for clip in recent)
    score -= 0.12 * sum(
        clip["shot"] == segment["shot"] and segment["shot"] != "unknown" for clip in recent
    )
    current_hash = segment["prediction"].get("metrics", {}).get("dhash")
    if current_hash:
        distances = [
            (int(current_hash, 16) ^ int(clip["dhash"], 16)).bit_count()
            for clip in clips if clip.get("dhash")
        ]
        if distances and min(distances) < 8:
            score -= 0.35
        elif distances and min(distances) < 16:
            score -= 0.15

    if policy.order == "variety":
        score -= 0.28 * media_count + 0.2 * shot_count + 0.12 * stage_count
        if clips and segment["shot"] != "unknown" and not shot_count:
            score += 0.18
        score += 0.12 * sum(tag_history[tag.casefold()] == 0 for tag in preferred)
    if not clips and segment["stage"] == policy.start_stage:
        score += 1.2
    if progress > 0.78 and segment["stage"] == policy.end_stage:
        score += 0.9
    if policy.order == "story":
        stage = segment["stage"]
        if stage in {"preparation", "departure"} and progress < 0.25:
            score += 0.35
        if stage == "action" and 0.2 <= progress < 0.85:
            score += 0.3
        if stage in {"closing", "arrival"} and progress < 0.6:
            score -= 0.7
        if stage == "preparation" and any(
            clip["stage"] in {"action", "arrival", "closing"} for clip in clips
        ):
            score -= 1.5
    if policy.order == "chronological":
        score -= 0.08 * chronology_rank[segment["id"]]
    return score


def _capacity(segment: dict, fps: Fraction, policy: Policy) -> tuple[int, int] | None:
    low = max(1, seconds_to_frames(policy.min_shot, fps))
    high = min(
        max(low, seconds_to_frames(policy.max_shot, fps)),
        math.floor((segment["end"] - segment["start"]) * float(fps) + 1e-8),
    )
    while high >= low and _range(segment, high, fps) is None:
        high -= 1
    return (low, high) if high >= low else None


def _editorial_length(segment: dict, low: int, high: int, policy: Policy) -> int:
    """Convierte ritmo y contenido en una duración estable, no en un midpoint fijo."""
    ratio = _pace_ratio(policy.pace)
    if segment["shot"] == "wide":
        ratio += 0.1
    elif segment["shot"] in {"close", "detail"}:
        ratio -= 0.08
    if segment["stage"] in {"establishing", "pause", "closing"}:
        ratio += 0.08
    elif segment["stage"] == "action":
        ratio -= 0.07
    ratio += 0.06 * segment["quality"] + 0.04 * segment["interest"] * segment["confidence"]
    stable = int(digest(segment["id"])[:8], 16) / 0xFFFFFFFF
    ratio += (stable - 0.5) * 0.16
    ratio = max(0.0, min(1.0, ratio))
    return max(low, min(high, round(low + (high - low) * ratio)))


def _snap_to_beat(position: int, length: int, low: int, high: int,
                  fps: Fraction, beats: list[float]) -> int:
    choices = [seconds_to_frames(beat, fps) - position for beat in beats]
    choices = [value for value in choices if low <= value <= high]
    return min(choices, key=lambda value: (abs(value - length), value)) if choices else length


def _fraction_allowed(shot: str, length: int, clips: list[dict], target: int, policy: Policy,
                      exclude: dict | None = None) -> bool:
    pov = sum(clip["_length"] for clip in clips if clip is not exclude and clip["shot"] == "pov")
    close = sum(clip["_length"] for clip in clips if clip is not exclude and clip["shot"] == "close")
    return not (
        shot == "pov" and pov + length > target * policy.max_pov_fraction + 1e-8
        or shot == "close" and close + length > target * policy.max_close_fraction + 1e-8
    )


def _choice(
    segment: dict,
    clips: list[dict],
    position: int,
    target: int,
    fps: Fraction,
    policy: Policy,
    beats: list[float],
) -> tuple[int, tuple[float, float], int, int] | None:
    capacity = _capacity(segment, fps, policy)
    if capacity is None:
        return None
    low, high = capacity
    remaining = target - position
    ideal = _editorial_length(segment, low, high, policy)
    if remaining >= low:
        ideal = min(ideal, remaining)
        beat_high = min(high, remaining)
    else:
        ideal = low
        beat_high = high
    length = _snap_to_beat(position, ideal, low, beat_high, fps, beats)
    for candidate_length in range(length, low - 1, -1):
        bounds = _range(segment, candidate_length, fps)
        if (bounds and not _overlap(segment["media_id"], *bounds, clips)
                and _fraction_allowed(segment["shot"], candidate_length, clips, target, policy)):
            return candidate_length, bounds, low, high
    return None


def _rebalance(clips: list[dict], target: int, fps: Fraction, policy: Policy) -> int:
    """Ajusta clips completos hasta el target sin fabricar un último residual submínimo."""
    total = sum(clip["_length"] for clip in clips)
    if total > target:
        excess = total - target
        for clip in sorted(clips, key=lambda item: (item["_hold"], item["segment_id"])):
            reduction = min(excess, clip["_length"] - clip["_min_frames"])
            if reduction:
                clip["_length"] -= reduction
                bounds = _range(clip["_segment"], clip["_length"], fps)
                if bounds is None:
                    raise AutoEditorError("El rebalanceo produjo un rango fuente no válido.")
                clip["source_in"], clip["source_out"] = bounds
                excess -= reduction
            if not excess:
                break
        total = sum(clip["_length"] for clip in clips)
    if total < target:
        deficit = target - total
        for clip in sorted(clips, key=lambda item: (-item["_hold"], item["segment_id"])):
            upper = min(clip["_max_frames"], clip["_length"] + deficit)
            for new_length in range(upper, clip["_length"], -1):
                bounds = _range(clip["_segment"], new_length, fps)
                if (bounds and not _overlap(clip["media_id"], *bounds, clips, exclude=clip)
                        and _fraction_allowed(clip["shot"], new_length, clips, target, policy, exclude=clip)):
                    deficit -= new_length - clip["_length"]
                    clip["_length"] = new_length
                    clip["source_in"], clip["source_out"] = bounds
                    break
            if not deficit:
                break
        total = sum(clip["_length"] for clip in clips)
    return total


def _public_clips(clips: list[dict]) -> list[dict]:
    position = 0
    result = []
    for clip in clips:
        public = {key: value for key, value in clip.items() if not key.startswith("_")}
        public["timeline_start"] = position
        position += clip["_length"]
        public["timeline_end"] = position
        result.append(public)
    return result


def create_plan(
    project: Project,
    duration: float = 30,
    preset: str = "balanced",
    fps: str = "30",
    width: int = 1920,
    height: int = 1080,
    intent: dict | None = None,
    prompt: str = "",
    music: dict | None = None,
    include_original_audio: bool = True,
    priority: str = "balanced",
    prompt_intent: dict | None = None,
    cli_overrides: dict | None = None,
) -> dict[str, Any]:
    finite(duration, "duration", 0.5, 600)
    finite(width, "width", 128, 8192)
    finite(height, "height", 128, 8192)
    rate = fps_fraction(fps)
    policy, policy_sources = resolve_policy(preset, priority, intent, prompt_intent, cli_overrides)
    candidates = [segment for segment in project.segments() if segment.get("decision") != "reject"]
    if not candidates:
        raise AutoEditorError("No completed, accepted candidates. Finish analyze before planning.")
    warnings = list(policy.unsupported_requests)
    available_media = {candidate["media_id"] for candidate in candidates}
    if len(available_media) < len(project.media()):
        warnings.append("Some indexed media have no completed/accepted candidates and were not used.")
    if all(candidate["prediction"].get("backend") == "technical" for candidate in candidates):
        warnings.append("TECHNICAL ONLY: no action recognition or semantic narrative was performed.")
    if policy.start_stage not in {candidate["stage"] for candidate in candidates}:
        warnings.append(f"No {policy.start_stage} candidate; opening preference could not be applied.")
    if policy.end_stage not in {candidate["stage"] for candidate in candidates}:
        warnings.append(f"No {policy.end_stage} candidate; closing preference could not be applied.")
    if policy.order == "chronological":
        if any(not candidate["metadata"].get("creation_time") for candidate in candidates):
            warnings.append("Missing camera dates: chronology falls back to relative filenames and offsets.")
        else:
            warnings.append("Chronology trusts camera clocks; clock synchronization is not verified.")
    chronological = sorted(candidates, key=lambda candidate: (
        candidate["metadata"].get("creation_time") or "", candidate["relative_path"],
        candidate["start"], candidate["id"],
    ))
    ranks = {candidate["id"]: index for index, candidate in enumerate(chronological)}
    requested = seconds_to_frames(duration, rate)
    target = requested
    if music:
        target = min(target, seconds_to_frames(music["available_duration"], rate))
        if target < requested:
            warnings.append("Music is shorter than requested; no looping or stretching was applied.")
        if music["method"] != "disabled" and not music["beats"]:
            warnings.append("No reliable beats returned; falling back to ordinary shot durations.")
    beats = music.get("beats", []) if music else []

    clips: list[dict] = []
    used_ids: set[str] = set()
    position = 0
    previous_rank = -1
    media_fallback_used = False
    while position < target:
        choices = []
        for relaxed in (False, True):
            for segment in candidates:
                if segment["id"] in used_ids:
                    continue
                if policy.order == "chronological" and ranks[segment["id"]] < previous_rank:
                    continue
                media_count = sum(clip["media_id"] == segment["media_id"] for clip in clips)
                if not relaxed and media_count >= policy.max_clips_per_media:
                    continue
                candidate = _choice(segment, clips, position, target, rate, policy, beats)
                if candidate is None:
                    continue
                length, bounds, low, high = candidate
                score = _score(
                    segment, clips, position / target, policy, ranks,
                    media_limit_relaxed=relaxed,
                )
                choices.append((score, segment["id"], segment, length, bounds, low, high))
            if choices or relaxed:
                if relaxed and choices:
                    media_fallback_used = True
                break
        if not choices:
            position = _rebalance(clips, target, rate, policy)
            break
        score, _, chosen, length, (source_in, source_out), low, high = max(
            choices, key=lambda row: (row[0], row[1]),
        )
        matched = [tag.casefold() for tag in _matched_tags(chosen, policy.preferred_tags)]
        item = {
            "segment_id": chosen["id"], "media_id": chosen["media_id"],
            "relative_path": chosen["relative_path"], "fingerprint": chosen["fingerprint"],
            "metadata": chosen["metadata"], "label": chosen["label"],
            "stage": chosen["stage"], "shot": chosen["shot"],
            "tags": list(chosen["prediction"].get("tags", [])),
            "source_in": source_in, "source_out": source_out,
            "confidence": chosen["confidence"], "analysis_key": chosen["analysis_key"],
            "dhash": chosen["prediction"].get("metrics", {}).get("dhash"),
            "_segment": chosen, "_length": length, "_min_frames": low, "_max_frames": high,
            "_matched_preferences": matched, "_hold": score,
        }
        clips.append(item)
        used_ids.add(chosen["id"])
        position += length
        previous_rank = ranks[chosen["id"]]
        if position >= target:
            position = _rebalance(clips, target, rate, policy)
            break
    if not clips:
        raise AutoEditorError("No candidate meets duration/POV constraints. Relax the policy or add footage.")
    if position < target:
        warnings.append("Insufficient non-overlapping candidates under this policy: returned a shorter cut.")
    if media_fallback_used:
        warnings.append(
            "Se relajó max_clips_per_media porque no quedaban alternativas válidas para completar el montaje."
        )

    public_clips = _public_clips(clips)
    clip_ids = ensure_clip_ids(project, [clip["segment_id"] for clip in public_clips])
    for clip in public_clips:
        clip["clip_id"] = clip_ids[clip["segment_id"]]
    rates = {clip["metadata"]["fps"] for clip in public_clips}
    if rates != {str(rate)}:
        warnings.append(
            "La timeline conserva FPS de origen distintos; timing auto decidirá el tratamiento al exportar."
        )
    for clip in public_clips:
        metadata = clip["metadata"]
        if metadata.get("vfr_suspected"):
            warnings.append(
                "Posible VFR detectado; timing auto verificará o conformará la fuente al exportar."
            )
        if metadata.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
            warnings.append("HDR source detected: color management/normalization must be checked in Premiere.")
    count = project.db.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    plan = {
        "schema_version": 2, "id": f"cut-{count + 1:04d}", "created_at": now(),
        "project": project.config["name"], "preset": preset, "priority_profile": priority,
        "policy": asdict(policy), "policy_sources": policy_sources,
        "policy_merge": {
            "precedence": ["defaults", "preset", "priority", "user", "prompt", "cli"],
            "lists": "stable-additive", "scalars": "last-defined-wins",
        },
        "prompt": prompt,
        "sequence": {
            "fps": str(rate), "width": int(width), "height": int(height),
            "duration_frames": position,
        },
        "requested_duration": duration, "actual_duration": position / float(rate),
        "original_audio": include_original_audio, "music": music, "clips": public_clips,
        "warnings": list(dict.fromkeys(warnings)),
        "planner": "deterministic-greedy-v2", "editorial_quality": "unbenchmarked",
    }
    plan["timeline"] = build_timeline(plan)
    plan["timeline"]["editorial_decisions"] = resolve_decisions(project, plan["timeline"])
    plan["timeline"], microcut_warnings = apply_microcuts(
        project, plan["timeline"], pace=policy.pace, music=music,
    )
    plan["timeline"], retiming_warnings = apply_slow_motion(
        project, plan["timeline"], pace=policy.pace, music=music,
    )
    plan["timeline"], transition_warnings = apply_transitions(
        project, plan["timeline"], pace=policy.pace, music=music,
    )
    plan["timeline"], effect_warnings = apply_effects(
        project, plan["timeline"], pace=policy.pace,
    )
    plan["timeline"]["metadata"]["operation_order"] = [
        "clip_selection", "microcuts", "slow_motion", "transitions", "effects",
    ]
    plan["warnings"] = list(dict.fromkeys(
        plan["warnings"] + microcut_warnings + retiming_warnings
        + transition_warnings + effect_warnings
    ))
    validate_plan(plan)
    with project.db:
        project.db.execute(
            "INSERT INTO plans(id,created_at,payload) VALUES (?,?,?)",
            (plan["id"], plan["created_at"], json_text(plan)),
        )
    return plan


def validate_plan(plan: dict) -> None:
    rate = fps_fraction(plan["sequence"]["fps"])
    position = 0
    for clip in plan["clips"]:
        if clip["timeline_start"] != position or clip["timeline_end"] <= position:
            raise AutoEditorError("Plan has a timeline gap, overlap, or empty clip.")
        start, end, duration = clip["source_in"], clip["source_out"], clip["metadata"]["duration"]
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 1e-6):
            raise AutoEditorError("Plan contains out-of-bounds source media.")
        timeline_seconds = (clip["timeline_end"] - position) / float(rate)
        if abs(timeline_seconds - (end - start)) > 1e-6:
            raise AutoEditorError("Plan unexpectedly changes playback speed.")
        position = clip["timeline_end"]
    if position != plan["sequence"]["duration_frames"]:
        raise AutoEditorError("Plan duration does not match its clips.")
    if "timeline" in plan:
        validate_timeline(plan["timeline"])
        if plan["timeline"].get("timeline_id") != plan.get("id"):
            raise AutoEditorError("La timeline canónica no pertenece a este plan.")


def load_plan(project: Project, plan_id: str | None = None) -> dict:
    if plan_id:
        row = project.db.execute("SELECT payload FROM plans WHERE id=?", (plan_id,)).fetchone()
    else:
        row = project.db.execute("SELECT payload FROM plans ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        raise AutoEditorError("No matching plan exists.")
    return json.loads(row["payload"])
