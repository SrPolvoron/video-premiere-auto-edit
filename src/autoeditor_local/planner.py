"""An inspectable, deterministic greedy rough-cut planner, not an autonomous director."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

from .project import Project
from .util import AutoEditorError, finite, fps_fraction, json_text, now, seconds_to_frames
from .vision import validate_intent


@dataclass
class Policy:
    min_shot: float = 1.2
    max_shot: float = 3.5
    max_pov_fraction: float = 0.55
    preferred_tags: list[str] | None = None
    avoid_tags: list[str] | None = None
    start_stage: str = "establishing"
    end_stage: str = "closing"
    order: str = "story"
    unsupported_requests: list[str] | None = None


PRESETS = {
    "balanced": Policy(),
    "moto": Policy(min_shot=0.8, max_shot=2.8, max_pov_fraction=0.5,
                   start_stage="preparation", preferred_tags=["motorcycle", "landscape"]),
    "nature": Policy(min_shot=2.5, max_shot=5.5, max_pov_fraction=1,
                     preferred_tags=["landscape", "water", "forest"], order="variety"),
    "travel": Policy(min_shot=1.5, max_shot=4, order="chronological", max_pov_fraction=1),
}
STAGE_ORDER = {"establishing": 0, "preparation": 1, "departure": 2, "detail": 3,
               "unknown": 3, "action": 4, "pause": 5, "arrival": 6, "closing": 7}


def make_policy(preset: str, intent: dict | None) -> Policy:
    if preset not in PRESETS:
        raise AutoEditorError(f"Unknown preset: {preset}")
    values = asdict(PRESETS[preset])
    values.update(validate_intent(intent or {}))
    policy = Policy(**values)
    if policy.max_shot < policy.min_shot:
        raise AutoEditorError("max_shot must be greater than or equal to min_shot.")
    return policy


def cut_length(position: int, remaining: int, fps: Fraction, policy: Policy, beats: list[float]) -> int:
    target = seconds_to_frames((policy.min_shot + policy.max_shot) / 2, fps)
    low = max(1, seconds_to_frames(policy.min_shot, fps))
    high = max(low, seconds_to_frames(policy.max_shot, fps))
    choices = [seconds_to_frames(t, fps) - position for t in beats]
    choices = [n for n in choices if low <= n <= min(high, remaining)]
    if choices:
        # Pick a musical beat near the intended shot length, not every beat.
        target = min(choices, key=lambda n: (abs(n - target), n))
    target = min(target, remaining)
    if 0 < remaining - target < low and remaining <= high:
        target = remaining
    return max(1, target)


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


def _overlap(media_id: int, start: float, end: float, chosen: list[dict]) -> bool:
    return any(c["media_id"] == media_id and start < c["source_out"] - 1e-8
               and end > c["source_in"] + 1e-8 for c in chosen)


def _score(segment: dict, clips: list[dict], progress: float, policy: Policy,
           chronology_rank: dict[str, int]) -> float:
    # Semantic scores are uncalibrated model judgments, so retain technical and diversity terms.
    confidence = segment["confidence"]
    score = 0.45 * segment["quality"] + 0.3 * segment["interest"] * confidence
    if segment.get("decision") == "prefer":
        score += 0.8
    text = " ".join([segment["label"], *segment["prediction"].get("tags", [])]).casefold()
    score += 0.18 * sum(word.casefold() in text for word in (policy.preferred_tags or []))
    score -= 0.8 * sum(word.casefold() in text for word in (policy.avoid_tags or []))
    if clips:
        if clips[-1]["media_id"] == segment["media_id"]:
            score -= 0.18
        if clips[-1]["shot"] == segment["shot"] and segment["shot"] != "unknown":
            score -= 0.12
        previous_hash = clips[-1].get("dhash")
        current_hash = segment["prediction"].get("metrics", {}).get("dhash")
        if previous_hash and current_hash:
            distance = (int(previous_hash, 16) ^ int(current_hash, 16)).bit_count()
            if distance < 8:
                score -= 0.15  # Weak penalty, not a semantic duplicate detector.
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
        if stage == "preparation" and any(c["stage"] in {"action", "arrival", "closing"} for c in clips):
            score -= 1.5
    if policy.order == "chronological":
        # Chronology is enforced by candidate filtering, not by pretending dates are trustworthy.
        score -= 0.08 * chronology_rank[segment["id"]]
    return score


def create_plan(project: Project, duration: float = 30, preset: str = "balanced",
                fps: str = "30", width: int = 1920, height: int = 1080,
                intent: dict | None = None, prompt: str = "", music: dict | None = None,
                include_original_audio: bool = True) -> dict[str, Any]:
    finite(duration, "duration", 0.5, 600)
    finite(width, "width", 128, 8192)
    finite(height, "height", 128, 8192)
    rate = fps_fraction(fps)
    policy = make_policy(preset, intent)
    candidates = [s for s in project.segments() if s.get("decision") != "reject"]
    if not candidates:
        raise AutoEditorError("No completed, accepted candidates. Finish analyze before planning.")
    warnings = list(policy.unsupported_requests or [])
    available_media = {c["media_id"] for c in candidates}
    if len(available_media) < len(project.media()):
        warnings.append("Some indexed media have no completed/accepted candidates and were not used.")
    if all(c["prediction"].get("backend") == "technical" for c in candidates):
        warnings.append("TECHNICAL ONLY: no action recognition or semantic narrative was performed.")
    if policy.start_stage not in {c["stage"] for c in candidates}:
        warnings.append(f"No {policy.start_stage} candidate; opening preference could not be applied.")
    if policy.end_stage not in {c["stage"] for c in candidates}:
        warnings.append(f"No {policy.end_stage} candidate; closing preference could not be applied.")
    if policy.order == "chronological":
        if any(not c["metadata"].get("creation_time") for c in candidates):
            warnings.append("Missing camera dates: chronology falls back to relative filenames and offsets.")
        else:
            warnings.append("Chronology trusts camera clocks; clock synchronization is not verified.")
    chronological = sorted(candidates, key=lambda c: (
        c["metadata"].get("creation_time") or "", c["relative_path"], c["start"], c["id"]))
    ranks = {c["id"]: i for i, c in enumerate(chronological)}
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
    position, pov_frames = 0, 0
    previous_rank = -1
    while position < target:
        length = cut_length(position, target - position, rate, policy, beats)
        choices = []
        for segment in candidates:
            if segment["id"] in used_ids:
                continue
            if policy.order == "chronological" and ranks[segment["id"]] < previous_rank:
                continue
            bounds = _range(segment, length, rate)
            if bounds is None or _overlap(segment["media_id"], *bounds, clips):
                continue
            if segment["shot"] == "pov" and pov_frames + length > target * policy.max_pov_fraction + 1e-8:
                continue
            score = _score(segment, clips, position / target, policy, ranks)
            choices.append((score, segment["id"], segment, bounds))
        if not choices:
            # Try a shorter valid shot, but never repeat footage to fill a duration request.
            shortest = max(1, seconds_to_frames(min(policy.min_shot, (target - position) / float(rate)), rate))
            if shortest < length:
                length = shortest
                for segment in candidates:
                    if segment["id"] in used_ids:
                        continue
                    if policy.order == "chronological" and ranks[segment["id"]] < previous_rank:
                        continue
                    bounds = _range(segment, length, rate)
                    if bounds and not _overlap(segment["media_id"], *bounds, clips):
                        if segment["shot"] != "pov" or pov_frames + length <= target * policy.max_pov_fraction + 1e-8:
                            choices.append((_score(segment, clips, position / target, policy, ranks),
                                            segment["id"], segment, bounds))
            if not choices:
                break
        _, _, chosen, (source_in, source_out) = max(choices, key=lambda row: (row[0], row[1]))
        item = {
            "segment_id": chosen["id"], "media_id": chosen["media_id"],
            "relative_path": chosen["relative_path"], "fingerprint": chosen["fingerprint"],
            "metadata": chosen["metadata"], "label": chosen["label"],
            "stage": chosen["stage"], "shot": chosen["shot"],
            "source_in": source_in, "source_out": source_out,
            "timeline_start": position, "timeline_end": position + length,
            "confidence": chosen["confidence"], "analysis_key": chosen["analysis_key"],
            "dhash": chosen["prediction"].get("metrics", {}).get("dhash"),
        }
        clips.append(item)
        used_ids.add(chosen["id"])
        position += length
        previous_rank = ranks[chosen["id"]]
        if chosen["shot"] == "pov":
            pov_frames += length
    if not clips:
        raise AutoEditorError("No candidate meets duration/POV constraints. Relax the policy or add footage.")
    if position < target:
        warnings.append("Insufficient non-overlapping candidates under this policy: returned a shorter cut.")
    rates = {c["metadata"]["fps"] for c in clips}
    if rates != {str(rate)}:
        warnings.append("Mixed source/sequence frame rates require explicit export opt-in and Premiere validation.")
    for clip in clips:
        meta = clip["metadata"]
        if meta.get("vfr_suspected"):
            warnings.append("Potential variable-frame-rate source detected; exact source timing is unverified.")
        if meta.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
            warnings.append("HDR source detected: color management/normalization must be checked in Premiere.")
    count = project.db.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    plan = {
        "schema_version": 1, "id": f"cut-{count + 1:04d}", "created_at": now(),
        "project": project.config["name"], "preset": preset, "policy": asdict(policy),
        "prompt": prompt, "sequence": {"fps": str(rate), "width": int(width), "height": int(height),
                                       "duration_frames": position},
        "requested_duration": duration, "actual_duration": position / float(rate),
        "original_audio": include_original_audio, "music": music, "clips": clips,
        "warnings": list(dict.fromkeys(warnings)),
        "planner": "deterministic-greedy-v1", "editorial_quality": "unbenchmarked",
    }
    validate_plan(plan)
    with project.db:
        project.db.execute("INSERT INTO plans(id,created_at,payload) VALUES (?,?,?)",
                           (plan["id"], plan["created_at"], json_text(plan)))
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


def load_plan(project: Project, plan_id: str | None = None) -> dict:
    if plan_id:
        row = project.db.execute("SELECT payload FROM plans WHERE id=?", (plan_id,)).fetchone()
    else:
        row = project.db.execute("SELECT payload FROM plans ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        raise AutoEditorError("No matching plan exists.")
    return json.loads(row["payload"])
