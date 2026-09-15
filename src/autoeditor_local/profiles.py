"""Perfiles de prioridad declarativos y composición determinista de políticas."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from typing import Any

from .util import AutoEditorError, finite

STAGES = {
    "establishing", "preparation", "departure", "action", "detail", "pause",
    "arrival", "closing", "unknown",
}
SHOTS = {"wide", "medium", "close", "detail", "pov", "unknown"}
PACES = {"calm", "balanced", "dynamic"}
ORDERS = {"story", "chronological", "variety"}
LIST_FIELDS = {
    "preferred_tags", "avoid_tags", "preferred_shots", "avoid_shots",
    "unsupported_requests",
}
OPPOSITE_LISTS = {
    "preferred_tags": "avoid_tags", "avoid_tags": "preferred_tags",
    "preferred_shots": "avoid_shots", "avoid_shots": "preferred_shots",
}


@dataclass
class Policy:
    min_shot: float = 1.2
    max_shot: float = 3.5
    max_pov_fraction: float = 0.55
    max_close_fraction: float = 0.45
    max_clips_per_media: int = 3
    preferred_tags: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)
    preferred_shots: list[str] = field(default_factory=list)
    avoid_shots: list[str] = field(default_factory=list)
    start_stage: str = "establishing"
    end_stage: str = "closing"
    order: str = "story"
    pace: str = "balanced"
    unsupported_requests: list[str] = field(default_factory=list)


# Los patches son parciales para la composición; PRESETS conserva los objetos Policy de V1.
PRESET_PATCHES: dict[str, dict[str, Any]] = {
    "balanced": {},
    "moto": {
        "min_shot": 0.8, "max_shot": 2.8, "max_pov_fraction": 0.5,
        "start_stage": "preparation", "preferred_tags": ["motorcycle", "landscape"],
    },
    "nature": {
        "min_shot": 2.5, "max_shot": 5.5, "max_pov_fraction": 1.0,
        "preferred_tags": ["landscape", "water", "forest"], "order": "variety",
    },
    "travel": {
        "min_shot": 1.5, "max_shot": 4.0, "order": "chronological",
        "max_pov_fraction": 1.0,
    },
}
PRESETS: dict[str, Policy] = {
    name: Policy(**(asdict(Policy()) | patch)) for name, patch in PRESET_PATCHES.items()
}


def _profile_document() -> dict[str, Any]:
    try:
        resource = files("autoeditor_local").joinpath("priority_profiles.json")
        document = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise AutoEditorError(f"No se pueden cargar los perfiles de prioridad: {exc}") from exc
    if document.get("schema_version") != 1 or not isinstance(document.get("profiles"), dict):
        raise AutoEditorError("El catálogo de perfiles de prioridad tiene una versión no compatible.")
    return document


def priority_profile_names() -> tuple[str, ...]:
    return tuple(sorted(_profile_document()["profiles"]))


def load_priority_profile(name: str) -> dict[str, Any]:
    profiles = _profile_document()["profiles"]
    if name not in profiles:
        raise AutoEditorError(f"Perfil de prioridad desconocido: {name}")
    return validate_policy_patch(profiles[name])


def _string_list(value: Any, name: str, maximum: int, allowed: set[str] | None = None) -> list[str]:
    if (not isinstance(value, list) or len(value) > maximum
            or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in value)):
        raise AutoEditorError(f"{name} debe ser una lista de como máximo {maximum} textos breves.")
    result = [item.strip() for item in value]
    if allowed is not None and any(item not in allowed for item in result):
        raise AutoEditorError(f"{name} contiene un tipo no compatible.")
    return result


def validate_policy_patch(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise AutoEditorError("La política debe ser un objeto JSON.")
    allowed = set(asdict(Policy()))
    unknown = set(payload) - allowed
    if unknown:
        raise AutoEditorError(f"Campos de política no compatibles: {sorted(unknown)}")
    result = dict(payload)
    for key, low, high in (
        ("min_shot", 0.4, 10), ("max_shot", 0.5, 20),
        ("max_pov_fraction", 0, 1), ("max_close_fraction", 0, 1),
    ):
        if key in result:
            result[key] = finite(result[key], key, low, high)
    if "max_clips_per_media" in result:
        value = result["max_clips_per_media"]
        if type(value) is not int or not 1 <= value <= 100:
            raise AutoEditorError("max_clips_per_media debe ser un entero entre 1 y 100.")
    for key in ("preferred_tags", "avoid_tags", "unsupported_requests"):
        if key in result:
            result[key] = _string_list(result[key], key, 20)
    for key in ("preferred_shots", "avoid_shots"):
        if key in result:
            result[key] = _string_list(result[key], key, len(SHOTS), SHOTS)
    for preferred, avoided in (("preferred_tags", "avoid_tags"),
                               ("preferred_shots", "avoid_shots")):
        overlap = {
            item.casefold() for item in result.get(preferred, [])
        } & {
            item.casefold() for item in result.get(avoided, [])
        }
        if overlap:
            raise AutoEditorError(
                f"Una misma preferencia no puede aparecer en {preferred} y {avoided}: {sorted(overlap)}"
            )
    for key in ("start_stage", "end_stage"):
        if key in result and result[key] not in STAGES:
            raise AutoEditorError(f"{key} no es compatible.")
    if "order" in result and result["order"] not in ORDERS:
        raise AutoEditorError("El orden editorial no es compatible.")
    if "pace" in result and result["pace"] not in PACES:
        raise AutoEditorError("El ritmo editorial no es compatible.")
    return result


def _merge_list(current: list[str], incoming: list[str]) -> list[str]:
    result = list(current)
    seen = {item.casefold() for item in result}
    for item in incoming:
        if item.casefold() not in seen:
            result.append(item)
            seen.add(item.casefold())
    return result


def resolve_policy(
    preset: str = "balanced",
    priority: str = "balanced",
    user: dict[str, Any] | None = None,
    prompt: dict[str, Any] | None = None,
    cli: dict[str, Any] | None = None,
) -> tuple[Policy, list[dict[str, Any]]]:
    """Combina defaults → preset → priority → user → prompt → CLI.

    Los escalares usan la última definición. Las listas se acumulan con orden estable para
    que un prompt no elimine preferencias declaradas por el preset o el perfil.
    """
    if preset not in PRESET_PATCHES:
        raise AutoEditorError(f"Preset desconocido: {preset}")
    sources = [
        ("defaults", asdict(Policy())),
        ("preset", validate_policy_patch(PRESET_PATCHES[preset])),
        ("priority", load_priority_profile(priority)),
        ("user", validate_policy_patch(user)),
        ("prompt", validate_policy_patch(prompt)),
        ("cli", validate_policy_patch(cli)),
    ]
    values: dict[str, Any] = {}
    provenance = []
    for name, patch in sources:
        provenance.append({"source": name, "values": patch})
        for key, value in patch.items():
            if key in LIST_FIELDS:
                opposite = OPPOSITE_LISTS.get(key)
                if opposite:
                    incoming = {item.casefold() for item in value}
                    values[opposite] = [
                        item for item in values.get(opposite, []) if item.casefold() not in incoming
                    ]
                values[key] = _merge_list(values.get(key, []), value)
            else:
                values[key] = value
    policy = Policy(**values)
    if policy.max_shot < policy.min_shot:
        raise AutoEditorError("max_shot debe ser mayor o igual que min_shot.")
    return policy, provenance
