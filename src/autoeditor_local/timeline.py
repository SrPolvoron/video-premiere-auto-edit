"""Timeline interna canónica, independiente de cualquier formato de exportación."""

from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .util import AutoEditorError, fps_fraction

TIMELINE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RationalRate:
    numerator: int
    denominator: int

    @classmethod
    def parse(cls, value: str | int | float | Fraction) -> RationalRate:
        rate = fps_fraction(value)
        return cls(rate.numerator, rate.denominator)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RationalRate:
        try:
            numerator, denominator = value["numerator"], value["denominator"]
            if (not isinstance(numerator, int) or isinstance(numerator, bool)
                    or not isinstance(denominator, int) or isinstance(denominator, bool)):
                raise TypeError
            rate = Fraction(numerator, denominator)
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise AutoEditorError("La timeline contiene una tasa racional no válida.") from exc
        return cls.parse(rate)

    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_dict(self) -> dict[str, int]:
        return {"numerator": self.numerator, "denominator": self.denominator}


@dataclass(frozen=True)
class RationalTime:
    value: int
    timescale: int

    @classmethod
    def from_seconds(cls, seconds: int | float | str | Fraction) -> RationalTime:
        try:
            value = seconds if isinstance(seconds, Fraction) else Fraction(str(seconds))
        except (ValueError, ZeroDivisionError) as exc:
            raise AutoEditorError(f"Tiempo no válido: {seconds}") from exc
        if value < 0:
            raise AutoEditorError("La timeline no admite tiempos negativos.")
        value = value.limit_denominator(1_000_000_000)
        return cls(value.numerator, value.denominator)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RationalTime:
        try:
            raw_value, timescale = value["value"], value["timescale"]
            if (not isinstance(raw_value, int) or isinstance(raw_value, bool)
                    or not isinstance(timescale, int) or isinstance(timescale, bool)):
                raise TypeError
            result = cls(raw_value, timescale)
        except (KeyError, TypeError, ValueError) as exc:
            raise AutoEditorError("La timeline contiene un tiempo racional no válido.") from exc
        if result.value < 0 or result.timescale <= 0:
            raise AutoEditorError("La timeline contiene un tiempo racional fuera de rango.")
        return result

    def fraction(self) -> Fraction:
        return Fraction(self.value, self.timescale)

    def to_dict(self) -> dict[str, int]:
        return {"value": self.value, "timescale": self.timescale}


def _source_range(start: float, end: float) -> dict[str, dict[str, int]]:
    return {
        "start": RationalTime.from_seconds(start).to_dict(),
        "duration": RationalTime.from_seconds(end - start).to_dict(),
    }


def _timeline_range(start: int, end: int) -> dict[str, int]:
    return {"start_frames": int(start), "duration_frames": int(end - start)}


def build_timeline(plan: dict[str, Any]) -> dict[str, Any]:
    """Construye la representación canónica desde un plan nuevo o legado."""
    sequence = plan["sequence"]
    sequence_rate = RationalRate.parse(sequence["fps"])
    clips = []
    for index, clip in enumerate(plan.get("clips", []), start=1):
        metadata = clip["metadata"]
        clips.append({
            "clip_id": clip.get("clip_id", f"legacy-clip-{index:04d}-{clip['segment_id']}"),
            "segment_id": clip["segment_id"],
            "media_id": clip["media_id"],
            "media": {
                "relative_path": clip["relative_path"],
                "fingerprint": clip["fingerprint"],
            },
            "source_range": _source_range(clip["source_in"], clip["source_out"]),
            "timeline_range": _timeline_range(clip["timeline_start"], clip["timeline_end"]),
            "source_fps": RationalRate.parse(metadata["fps"]).to_dict(),
            "sequence_fps": sequence_rate.to_dict(),
            "audio": {
                "enabled": bool(plan.get("original_audio", True) and metadata.get("audio_channels", 0)),
                "stream_index": 0,
                "channels": int(metadata.get("audio_channels", 0)),
                "sample_rate": int(metadata.get("audio_sample_rate", 48000)),
                "linked": True,
            },
            "retiming": {"mode": "none", "speed": {"numerator": 1, "denominator": 1}},
            "transitions": {"in": None, "out": None},
            "effects": [],
            "enhancements": [],
            "color": {"operations": []},
            "metadata": deepcopy(metadata),
            "editorial": {
                "label": clip.get("label", ""), "stage": clip.get("stage", "unknown"),
                "shot": clip.get("shot", "unknown"), "tags": list(clip.get("tags", [])),
                "confidence": clip.get("confidence", 0), "interest": clip.get("interest", 0),
                "quality": clip.get("quality", 0),
                "analysis_key": clip.get("analysis_key"), "dhash": clip.get("dhash"),
            },
        })
    music_clips = []
    music = plan.get("music")
    if music:
        music_clips.append({
            "clip_id": "music-0001",
            "media": {"path": music["path"], "fingerprint": music["fingerprint"]},
            "source_range": _source_range(
                music["offset"], music["offset"] + plan["actual_duration"],
            ),
            "timeline_range": {"start_frames": 0, "duration_frames": sequence["duration_frames"]},
            "audio": {
                "enabled": True, "stream_index": 0,
                "channels": int(music["metadata"].get("audio_channels", 0)),
                "sample_rate": int(music["metadata"].get("audio_sample_rate", 48000)),
            },
            "effects": [], "enhancements": [], "metadata": deepcopy(music["metadata"]),
        })
    timeline = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "timeline_id": plan["id"],
        "sequence": {
            "fps": sequence_rate.to_dict(),
            "width": int(sequence["width"]), "height": int(sequence["height"]),
            "duration_frames": int(sequence["duration_frames"]), "audio_sample_rate": 48000,
        },
        "video_tracks": [{"track_id": "V1", "clips": clips}],
        "audio": {"original_enabled": bool(plan.get("original_audio", True))},
        "music_tracks": [{"track_id": "M1", "clips": music_clips}] if music_clips else [],
        "transitions": [], "effects": [], "enhancements": [], "color": {"operations": []},
        "metadata": {
            "project": plan.get("project", ""), "plan_id": plan["id"],
            "planner": plan.get("planner", "legacy-plan-adapter"),
            "preset": plan.get("preset"), "priority_profile": plan.get("priority_profile"),
        },
    }
    validate_timeline(timeline)
    return timeline


def timeline_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    timeline = plan.get("timeline")
    if timeline is None:
        timeline = build_timeline(plan)
    validate_timeline(timeline)
    return timeline


def rate_fraction(value: dict[str, Any]) -> Fraction:
    return RationalRate.from_dict(value).fraction()


def time_fraction(value: dict[str, Any]) -> Fraction:
    return RationalTime.from_dict(value).fraction()


def positive_fraction(value: dict[str, Any], field: str) -> Fraction:
    try:
        numerator = value["numerator"]
        denominator = value["denominator"]
        if isinstance(numerator, bool) or isinstance(denominator, bool):
            raise TypeError
        if int(numerator) != numerator or int(denominator) != denominator:
            raise ValueError
        result = Fraction(int(numerator), int(denominator))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise AutoEditorError(f"La timeline contiene {field} no valido.") from exc
    if result <= 0:
        raise AutoEditorError(f"La timeline contiene {field} fuera de rango.")
    return result


def source_seconds(clip: dict[str, Any]) -> tuple[float, float]:
    start = time_fraction(clip["source_range"]["start"])
    duration = time_fraction(clip["source_range"]["duration"])
    return float(start), float(start + duration)


def timeline_frames(clip: dict[str, Any]) -> tuple[int, int]:
    value = clip["timeline_range"]
    try:
        start = int(value["start_frames"])
        duration = int(value["duration_frames"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AutoEditorError("La timeline contiene un rango de frames no válido.") from exc
    return start, start + duration


def video_clips(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    return _track_clips(timeline, "video_tracks")


def music_clips(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    return _track_clips(timeline, "music_tracks")


def _track_clips(timeline: dict[str, Any], key: str) -> list[dict[str, Any]]:
    tracks = timeline.get(key)
    if not isinstance(tracks, list):
        raise AutoEditorError(f"La timeline contiene {key} no valido.")
    result = []
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("clips"), list):
            raise AutoEditorError(f"La timeline contiene una pista no valida en {key}.")
        if not isinstance(track.get("track_id"), str) or not track["track_id"]:
            raise AutoEditorError(f"La timeline contiene un track ID no valido en {key}.")
        if not all(isinstance(clip, dict) for clip in track["clips"]):
            raise AutoEditorError(f"La timeline contiene un clip no valido en {key}.")
        result.extend(track["clips"])
    return result


def validate_timeline(timeline: dict[str, Any]) -> None:
    if not isinstance(timeline, dict) or timeline.get("schema_version") != TIMELINE_SCHEMA_VERSION:
        raise AutoEditorError("Versión de timeline interna no compatible.")
    try:
        sequence = timeline["sequence"]
        sequence_rate = rate_fraction(sequence["fps"])
        duration_frames = int(sequence["duration_frames"])
        width, height = int(sequence["width"]), int(sequence["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AutoEditorError("Configuración de secuencia no válida en la timeline.") from exc
    if duration_frames <= 0 or not 128 <= width <= 8192 or not 128 <= height <= 8192:
        raise AutoEditorError("Dimensiones o duración no válidas en la timeline.")
    for key, expected in (
        ("transitions", list), ("effects", list), ("enhancements", list),
        ("color", dict), ("metadata", dict),
    ):
        if not isinstance(timeline.get(key), expected):
            raise AutoEditorError(f"Campo global {key} no valido en la timeline.")
    if "editorial_decisions" in timeline and not isinstance(
        timeline["editorial_decisions"], dict,
    ):
        raise AutoEditorError("editorial_decisions no es valido en la timeline.")
    try:
        original_audio_enabled = bool(timeline["audio"]["original_enabled"])
    except (KeyError, TypeError) as exc:
        raise AutoEditorError("Configuración de audio no válida en la timeline.") from exc
    clips = video_clips(timeline)
    if not clips:
        raise AutoEditorError("La timeline no contiene clips de vídeo.")
    ids: set[str] = set()
    source_ranges: dict[int, list[tuple[Fraction, Fraction, str]]] = defaultdict(list)
    position = 0
    for clip in clips:
        clip_id = clip.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id or clip_id in ids:
            raise AutoEditorError("Los clip IDs de la timeline deben ser únicos y no vacíos.")
        ids.add(clip_id)
        if not isinstance(clip.get("media_id"), int):
            raise AutoEditorError("La timeline contiene un media ID no válido.")
        start_frame, end_frame = timeline_frames(clip)
        if start_frame != position or end_frame <= start_frame:
            raise AutoEditorError("La pista de vídeo contiene gaps, solapes o clips vacíos.")
        if rate_fraction(clip["sequence_fps"]) != sequence_rate:
            raise AutoEditorError("Un clip usa un FPS de secuencia distinto al de la timeline.")
        source_fps = rate_fraction(clip["source_fps"])
        source_start = time_fraction(clip["source_range"]["start"])
        source_duration = time_fraction(clip["source_range"]["duration"])
        media_duration = clip.get("metadata", {}).get("duration")
        if (source_duration <= 0 or media_duration is None or not math.isfinite(float(media_duration))
                or source_start + source_duration > Fraction(str(media_duration)) + Fraction(1, 1_000_000)):
            raise AutoEditorError("La timeline contiene un source range fuera del original.")
        timeline_duration = Fraction(end_frame - start_frame, 1) / sequence_rate
        _validate_retiming(
            clip, source_start, source_duration, start_frame, end_frame,
            sequence_rate, timeline_duration,
        )
        if source_fps <= 0:
            raise AutoEditorError("La timeline contiene un source FPS no válido.")
        parent_clip_id = clip.get("parent_clip_id")
        microcut = clip.get("microcut")
        if parent_clip_id is not None:
            if (not isinstance(parent_clip_id, str) or not parent_clip_id
                    or parent_clip_id == clip_id or not isinstance(microcut, dict)):
                raise AutoEditorError("Un subclip de microcut necesita una identidad de parent válida.")
            if type(microcut.get("level")) is not int or not 1 <= microcut["level"] <= 3:
                raise AutoEditorError("Un subclip de microcut tiene un nivel no válido.")
            try:
                coverage = microcut["parent_source_coverage"]
                coverage_start = time_fraction(coverage["start"])
                coverage_duration = time_fraction(coverage["duration"])
                parent_range = clip["parent_source_range"]
                parent_start = time_fraction(parent_range["start"])
                parent_duration = time_fraction(parent_range["duration"])
            except (KeyError, TypeError) as exc:
                raise AutoEditorError("Un subclip de microcut tiene rangos de parent no válidos.") from exc
            if (coverage_duration <= 0 or parent_duration <= 0
                    or source_start < coverage_start
                    or source_start + source_duration > coverage_start + coverage_duration
                    or parent_start < coverage_start
                    or parent_start + parent_duration > coverage_start + coverage_duration):
                raise AutoEditorError("Un subclip de microcut queda fuera de su cobertura source.")
            if not isinstance(microcut.get("removed_ranges"), list) or not isinstance(
                microcut.get("provenance"), list,
            ):
                raise AutoEditorError("Un subclip de microcut no conserva sus decisiones.")
        elif microcut is not None:
            raise AutoEditorError("La metadata de microcut requiere parent_clip_id.")
        for key, expected in (("effects", list), ("enhancements", list), ("color", dict),
                              ("transitions", dict), ("audio", dict), ("metadata", dict)):
            if not isinstance(clip.get(key), expected):
                raise AutoEditorError(f"Campo {key} no válido en un clip de la timeline.")
        if clip["audio"].get("enabled") and not original_audio_enabled:
            raise AutoEditorError("Un clip no puede activar audio original en una timeline silenciada.")
        source_ranges[clip["media_id"]].append((source_start, source_start + source_duration, clip_id))
        position = end_frame
    if position != duration_frames:
        raise AutoEditorError("La duración de la timeline no coincide con su pista de vídeo.")
    for intervals in source_ranges.values():
        previous_end = Fraction(-1)
        for start, end, _ in sorted(intervals):
            if start < previous_end:
                raise AutoEditorError("La timeline reutiliza source ranges de vídeo solapados.")
            previous_end = end
    for clip in music_clips(timeline):
        clip_id = clip.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id or clip_id in ids:
            raise AutoEditorError("Los clip IDs de la timeline deben ser unicos y no vacios.")
        ids.add(clip_id)
        start, end = timeline_frames(clip)
        if start < 0 or end > duration_frames or end <= start:
            raise AutoEditorError("La timeline contiene un rango de música no válido.")
        source_start = time_fraction(clip["source_range"]["start"])
        source_duration = time_fraction(clip["source_range"]["duration"])
        media_duration = clip.get("metadata", {}).get("duration")
        if (source_duration <= 0 or media_duration is None
                or source_start + source_duration
                > Fraction(str(media_duration)) + Fraction(1, 1_000_000)):
            raise AutoEditorError("La timeline contiene música fuera de los límites de origen.")
        for key, expected in (
            ("audio", dict), ("effects", list), ("enhancements", list), ("metadata", dict),
        ):
            if not isinstance(clip.get(key), expected):
                raise AutoEditorError(f"Campo {key} no valido en un clip de música.")
    _validate_editorial_operations(timeline, clips, ids)


def _validate_retiming(
    clip: dict[str, Any], source_start: Fraction, source_duration: Fraction,
    start_frame: int, end_frame: int, sequence_rate: Fraction,
    timeline_duration: Fraction,
) -> None:
    retiming = clip.get("retiming")
    if not isinstance(retiming, dict):
        raise AutoEditorError("El retiming de un clip debe ser un objeto.")
    mode = retiming.get("mode")
    if mode not in {"none", "constant", "speed", "regions"}:
        raise AutoEditorError("Modo de retiming no compatible en la timeline.")
    speed = positive_fraction(retiming.get("speed", {}), "un speed racional")
    if source_duration / speed != timeline_duration:
        raise AutoEditorError("El retiming no coincide con los rangos source/timeline.")
    if mode == "none":
        if speed != 1 or retiming.get("regions") not in (None, []):
            raise AutoEditorError("El modo normal no admite velocidad ni regiones de retiming.")
        return
    provenance = retiming.get("provenance", [])
    if provenance and not isinstance(provenance, list):
        raise AutoEditorError("La procedencia del retiming no es válida.")
    if mode in {"constant", "speed"}:
        if retiming.get("regions") not in (None, []):
            raise AutoEditorError("El retiming constante no admite regiones internas.")
        return
    regions = retiming.get("regions")
    if not isinstance(regions, list) or not regions:
        raise AutoEditorError("El retiming regional necesita regiones no vacías.")
    source_cursor = source_start
    timeline_cursor = start_frame
    region_ids: set[str] = set()
    for region in regions:
        if not isinstance(region, dict):
            raise AutoEditorError("Una región de retiming no es válida.")
        region_id = region.get("region_id")
        if not isinstance(region_id, str) or not region_id or region_id in region_ids:
            raise AutoEditorError("Las regiones de retiming necesitan IDs únicos.")
        region_ids.add(region_id)
        try:
            region_source = region["source_range"]
            region_start = time_fraction(region_source["start"])
            region_duration = time_fraction(region_source["duration"])
            region_timeline = region["timeline_range"]
            region_start_frame = int(region_timeline["start_frames"])
            region_frames = int(region_timeline["duration_frames"])
            region_speed = positive_fraction(region["speed"], "un speed regional racional")
        except (KeyError, TypeError, ValueError) as exc:
            raise AutoEditorError("Una región de retiming está incompleta.") from exc
        if (region_start != source_cursor or region_duration <= 0
                or region_start_frame != timeline_cursor or region_frames <= 0):
            raise AutoEditorError("Las regiones de retiming tienen gaps, solapes o están vacías.")
        if region_duration / region_speed != Fraction(region_frames, 1) / sequence_rate:
            raise AutoEditorError("Una región de retiming no conserva tiempo racional exacto.")
        source_cursor += region_duration
        timeline_cursor += region_frames
    if source_cursor != source_start + source_duration or timeline_cursor != end_frame:
        raise AutoEditorError("Las regiones no cubren exactamente el clip retimado.")


def _validate_editorial_operations(
    timeline: dict[str, Any], clips: list[dict[str, Any]], all_ids: set[str],
) -> None:
    clip_by_id = {clip["clip_id"]: clip for clip in clips}
    transition_ids: set[str] = set()
    for transition in timeline["transitions"]:
        if not isinstance(transition, dict):
            raise AutoEditorError("Una transición de la timeline no es válida.")
        transition_id = transition.get("transition_id")
        transition_type = transition.get("type")
        from_id, to_id = transition.get("from_clip_id"), transition.get("to_clip_id")
        if (not isinstance(transition_id, str) or not transition_id
                or transition_id in transition_ids):
            raise AutoEditorError("Las transiciones necesitan IDs únicos.")
        if transition_type not in {"cross_dissolve", "dip_to_black", "dip_to_white"}:
            raise AutoEditorError("Tipo de transición no compatible.")
        if from_id not in clip_by_id or to_id not in clip_by_id:
            raise AutoEditorError("Una transición referencia clips inexistentes.")
        from_index, to_index = clips.index(clip_by_id[from_id]), clips.index(clip_by_id[to_id])
        if to_index != from_index + 1:
            raise AutoEditorError("Una transición solo puede unir clips adyacentes.")
        duration_frames = transition.get("duration_frames")
        if type(duration_frames) is not int or duration_frames <= 0:
            raise AutoEditorError("Una transición necesita una duración positiva en frames.")
        duration = time_fraction(transition.get("duration", {}))
        if duration != Fraction(duration_frames, 1) / rate_fraction(timeline["sequence"]["fps"]):
            raise AutoEditorError("La duración racional de una transición no coincide con sus frames.")
        if duration_frames * 2 > min(
            clip_by_id[from_id]["timeline_range"]["duration_frames"],
            clip_by_id[to_id]["timeline_range"]["duration_frames"],
        ):
            raise AutoEditorError("Una transición es demasiado larga para los clips adyacentes.")
        if (clip_by_id[from_id]["transitions"].get("out") != transition_id
                or clip_by_id[to_id]["transitions"].get("in") != transition_id):
            raise AutoEditorError("Las referencias de transición entre timeline y clips no coinciden.")
        transition_ids.add(transition_id)
    referenced = {
        value for clip in clips for value in clip["transitions"].values() if value is not None
    }
    if referenced != transition_ids:
        raise AutoEditorError("Un clip referencia una transición inexistente.")
    effect_ids: set[str] = set()
    for effect in timeline["effects"]:
        if not isinstance(effect, dict):
            raise AutoEditorError("Un efecto de la timeline no es válido.")
        effect_id, clip_id = effect.get("effect_id"), effect.get("clip_id")
        if (not isinstance(effect_id, str) or not effect_id or effect_id in effect_ids
                or clip_id not in clip_by_id):
            raise AutoEditorError("Un efecto necesita ID único y clip válido.")
        if effect.get("type") not in {
            "subtle_push_in", "punch_in", "soft_zoom_in", "soft_zoom_out",
        }:
            raise AutoEditorError("Tipo de efecto no compatible.")
        if type(effect.get("level")) is not int or not 1 <= effect["level"] <= 3:
            raise AutoEditorError("El nivel de un efecto debe estar entre 1 y 3.")
        if not any(item.get("effect_id") == effect_id for item in clip_by_id[clip_id]["effects"]):
            raise AutoEditorError("Las referencias de efecto entre timeline y clip no coinciden.")
        effect_ids.add(effect_id)
    clip_effect_ids = {
        item.get("effect_id") for clip in clips for item in clip["effects"]
        if isinstance(item, dict)
    }
    if clip_effect_ids != effect_ids:
        raise AutoEditorError("Un clip referencia un efecto inexistente.")
