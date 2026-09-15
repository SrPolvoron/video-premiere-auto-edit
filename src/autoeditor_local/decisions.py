"""Decisiones editoriales persistentes, declarativas e independientes de exporters."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from fractions import Fraction
from typing import Any

from .project import Project
from .util import AutoEditorError, digest, json_text, now

DECISION_SCHEMA_VERSION = 1
FEATURE_MODES = ("off", "auto", "manual", "hybrid")
PROVENANCE = ("automatic", "manual", "accepted", "rejected", "modified")
FEATURES = (
    "slow_motion",
    "microcuts",
    "transitions",
    "effects",
    "stabilization",
    "denoise",
    "night_enhance",
    "auto_color",
    "color_style",
    "clip_selection",
)
DEFAULT_FEATURE_MODES = {feature: "off" for feature in FEATURES} | {"clip_selection": "auto"}
PROPERTY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
BASE_CLIP_ID_RE = re.compile(r"^clip-[0-9]{3,}$")
CLIP_ID_RE = re.compile(r"^clip-[0-9]{3,}(?:-mc-[0-9a-f]{12})?$")


def ensure_clip_ids(project: Project, identity_keys: list[str]) -> dict[str, str]:
    """Asigna IDs legibles una sola vez; la identidad no depende del orden en XMEML."""
    if any(not isinstance(key, str) or not key for key in identity_keys):
        raise AutoEditorError("Las identidades de clip deben ser strings no vacios.")
    unique_keys = list(dict.fromkeys(identity_keys))
    result: dict[str, str] = {}
    with project.db:
        rows = project.db.execute(
            "SELECT identity_key,clip_id FROM clip_identities"
        ).fetchall()
        existing = {row["identity_key"]: row["clip_id"] for row in rows}
        highest = max(
            (
                int(value.removeprefix("clip-"))
                for value in existing.values() if BASE_CLIP_ID_RE.fullmatch(value)
            ),
            default=0,
        )
        for key in unique_keys:
            clip_id = existing.get(key)
            if clip_id is None:
                highest += 1
                clip_id = f"clip-{highest:03d}"
                project.db.execute(
                    "INSERT INTO clip_identities(identity_key,clip_id,created_at) VALUES(?,?,?)",
                    (key, clip_id, now()),
                )
            result[key] = clip_id
    return result


def register_derived_clip_ids(project: Project, identities: dict[str, str]) -> None:
    """Registra IDs deterministas para que puedan recibir decisiones en planes posteriores."""
    if not isinstance(identities, dict) or any(
        not isinstance(key, str) or not key or not isinstance(clip_id, str)
        or not CLIP_ID_RE.fullmatch(clip_id) or BASE_CLIP_ID_RE.fullmatch(clip_id)
        for key, clip_id in identities.items()
    ):
        raise AutoEditorError("Las identidades derivadas de clip no son válidas.")
    with project.db:
        for identity_key, clip_id in sorted(identities.items()):
            by_identity = project.db.execute(
                "SELECT clip_id FROM clip_identities WHERE identity_key=?", (identity_key,),
            ).fetchone()
            by_clip = project.db.execute(
                "SELECT identity_key FROM clip_identities WHERE clip_id=?", (clip_id,),
            ).fetchone()
            if ((by_identity and by_identity["clip_id"] != clip_id)
                    or (by_clip and by_clip["identity_key"] != identity_key)):
                raise AutoEditorError("Una identidad derivada colisiona con otro clip estable.")
            if not by_identity:
                project.db.execute(
                    "INSERT INTO clip_identities(identity_key,clip_id,created_at) VALUES(?,?,?)",
                    (identity_key, clip_id, now()),
                )


def _feature(value: Any) -> str:
    if not isinstance(value, str):
        raise AutoEditorError("La decision necesita una feature valida.")
    result = value.strip().lower().replace("-", "_")
    if result not in FEATURES:
        raise AutoEditorError(f"Feature editorial no soportada: {value}")
    return result


def _property_names(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AutoEditorError(f"{name} debe ser una lista de propiedades.")
    result = list(dict.fromkeys(value))
    if any(not PROPERTY_RE.fullmatch(item) for item in result):
        raise AutoEditorError(f"{name} contiene un nombre de propiedad no valido.")
    return result


def _rational_time(value: Any, name: str, *, positive: bool = False) -> dict[str, int]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AutoEditorError(f"{name} debe ser un numero finito.")
    try:
        result = Fraction(str(value)).limit_denominator(1_000_000_000)
    except (ValueError, ZeroDivisionError) as exc:
        raise AutoEditorError(f"{name} no es un tiempo valido.") from exc
    if not math.isfinite(float(result)) or result < 0 or positive and result <= 0:
        raise AutoEditorError(f"{name} esta fuera de rango.")
    return {"value": result.numerator, "timescale": result.denominator}


def _target(value: Any) -> dict[str, Any]:
    if value is None:
        value = {"kind": "feature"}
    if not isinstance(value, dict) or set(value) - {
        "kind", "clip_id", "space", "start", "duration",
    }:
        raise AutoEditorError("Target editorial no valido.")
    kind = value.get("kind")
    if kind == "feature":
        if set(value) != {"kind"}:
            raise AutoEditorError("Un target de feature no admite clip ni rango.")
        return {"kind": "feature"}
    clip_id = value.get("clip_id")
    if kind not in {"clip", "range"} or not isinstance(clip_id, str) \
            or not CLIP_ID_RE.fullmatch(clip_id):
        raise AutoEditorError("El target necesita un clip_id estable como clip-001.")
    if kind == "clip":
        if set(value) != {"kind", "clip_id"}:
            raise AutoEditorError("Un target de clip no admite campos de rango.")
        return {"kind": "clip", "clip_id": clip_id}
    if value.get("space") not in {"source", "timeline"}:
        raise AutoEditorError("Un target de rango necesita space source o timeline.")
    if set(value) != {"kind", "clip_id", "space", "start", "duration"}:
        raise AutoEditorError("El target de rango esta incompleto.")
    return {
        "kind": "range", "clip_id": clip_id, "space": value["space"],
        "start": _rational_time(value["start"], "range.start"),
        "duration": _rational_time(value["duration"], "range.duration", positive=True),
    }


def _properties(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise AutoEditorError("properties debe ser un objeto JSON.")
    if any(not PROPERTY_RE.fullmatch(key) for key in value):
        raise AutoEditorError("properties contiene un nombre no valido.")
    try:
        encoded = json_text(value)
    except (TypeError, ValueError) as exc:
        raise AutoEditorError("properties debe contener JSON finito y serializable.") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise AutoEditorError("properties supera el limite de 64 KiB.")
    return deepcopy(value)


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "feature", "target", "provenance", "properties", "locks", "unlocks",
        "supersedes", "automatic_key",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise AutoEditorError("La decision contiene campos desconocidos.")
    feature = _feature(payload.get("feature"))
    target = _target(payload.get("target"))
    provenance = payload.get("provenance")
    if provenance not in PROVENANCE:
        raise AutoEditorError("Provenance debe ser automatic, manual, accepted, rejected o modified.")
    properties = _properties(payload.get("properties"))
    locks = _property_names(payload.get("locks"), "locks")
    unlocks = _property_names(payload.get("unlocks"), "unlocks")
    if set(locks) & set(unlocks):
        raise AutoEditorError("Una propiedad no puede bloquearse y desbloquearse a la vez.")
    if (locks or unlocks) and provenance != "manual":
        raise AutoEditorError("Solo una decision manual puede cambiar locks.")
    if set(locks) - set(properties):
        raise AutoEditorError("Cada lock necesita un valor manual para esa propiedad.")
    if "mode" in properties and (
        target["kind"] != "feature" or properties["mode"] not in FEATURE_MODES
    ):
        raise AutoEditorError("mode solo admite off, auto, manual o hybrid a nivel de feature.")
    if "mode" in properties and provenance != "manual":
        raise AutoEditorError("El modo de una feature solo puede configurarse manualmente.")
    supersedes = payload.get("supersedes")
    if provenance in {"accepted", "rejected", "modified"}:
        if not isinstance(supersedes, str) or not supersedes:
            raise AutoEditorError(f"{provenance} necesita supersedes de una propuesta automatic.")
        if provenance in {"accepted", "rejected"} and properties:
            raise AutoEditorError(f"{provenance} no admite properties; use modified para cambiarlas.")
        if provenance == "modified" and not properties:
            raise AutoEditorError("modified necesita properties.")
    elif supersedes is not None:
        raise AutoEditorError("supersedes solo se usa con accepted, rejected o modified.")
    if provenance in {"automatic", "manual"} and not (properties or locks or unlocks):
        raise AutoEditorError("La decision no contiene propiedades ni cambios de lock.")
    automatic_key = payload.get("automatic_key")
    if automatic_key is not None and (
        provenance != "automatic" or not isinstance(automatic_key, str) or not automatic_key
    ):
        raise AutoEditorError("automatic_key solo se admite en propuestas automatic.")
    return {
        "feature": feature, "target": target, "provenance": provenance,
        "properties": properties, "locks": locks, "unlocks": unlocks,
        "supersedes": supersedes, "automatic_key": automatic_key,
    }


def _rows(project: Project) -> list[dict[str, Any]]:
    rows = project.rows("SELECT * FROM editorial_decisions ORDER BY id")
    result = []
    for row in rows:
        target = {"kind": row["target_kind"]}
        if row["target_id"] is not None:
            target["clip_id"] = row["target_id"]
        if row["target_range"] is not None:
            target.update(json.loads(row["target_range"]))
        result.append({
            "id": row["id"], "decision_id": row["decision_id"],
            "feature": row["feature"], "target": target,
            "provenance": row["provenance"], "properties": json.loads(row["properties"]),
            "locks": json.loads(row["locks"]), "unlocks": json.loads(row["unlocks"]),
            "supersedes": row["supersedes"], "automatic_key": row["automatic_key"],
            "created_at": row["created_at"],
        })
    return result


def list_decisions(project: Project) -> list[dict[str, Any]]:
    return _rows(project)


def _target_key(target: dict[str, Any]) -> str:
    return json_text(target)


def _lock_state(rows: list[dict[str, Any]], feature: str, target: dict[str, Any]) -> dict[str, bool]:
    key = _target_key(target)
    state: dict[str, bool] = {}
    for row in rows:
        if (row["feature"] != feature or _target_key(row["target"]) != key
                or row["provenance"] != "manual"):
            continue
        for name in row["locks"]:
            state[name] = True
        for name in row["unlocks"]:
            state[name] = False
    return state


def _assert_known_clip(project: Project, target: dict[str, Any]) -> None:
    if target["kind"] == "feature":
        return
    if not project.db.execute(
        "SELECT 1 FROM clip_identities WHERE clip_id=?", (target["clip_id"],),
    ).fetchone():
        raise AutoEditorError(f"Clip ID desconocido: {target['clip_id']}")


def _insert_decision(project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    value = _normalize(payload)
    _assert_known_clip(project, value["target"])
    rows = _rows(project)
    if value["provenance"] == "automatic":
        automatic_key = value["automatic_key"] or digest({
            "feature": value["feature"], "target": value["target"],
            "properties": value["properties"], "schema": DECISION_SCHEMA_VERSION,
        })
        existing = project.db.execute(
            "SELECT decision_id FROM editorial_decisions WHERE automatic_key=?", (automatic_key,),
        ).fetchone()
        if existing:
            previous = next(row for row in rows if row["decision_id"] == existing["decision_id"])
            if (previous["feature"] != value["feature"] or previous["target"] != value["target"]
                    or previous["properties"] != value["properties"]):
                raise AutoEditorError("automatic_key ya identifica una propuesta distinta.")
            return previous
        value["automatic_key"] = automatic_key
    if value["supersedes"]:
        proposal = next(
            (row for row in rows if row["decision_id"] == value["supersedes"]), None,
        )
        if proposal is None or proposal["provenance"] != "automatic":
            raise AutoEditorError("supersedes no referencia una propuesta automatic valida.")
        if proposal["feature"] != value["feature"] or proposal["target"] != value["target"]:
            raise AutoEditorError("La respuesta hybrid debe conservar feature y target de la propuesta.")
        if any(row["supersedes"] == value["supersedes"] for row in rows):
            raise AutoEditorError("La propuesta ya tiene una respuesta hybrid persistida.")
    if value["provenance"] == "manual":
        locked = {name for name, active in _lock_state(
            rows, value["feature"], value["target"],
        ).items() if active}
        protected = locked & set(value["properties"])
        explicit = set(value["locks"]) | set(value["unlocks"])
        if protected - explicit:
            names = ", ".join(sorted(protected - explicit))
            raise AutoEditorError(f"Propiedades bloqueadas: {names}. Desbloqueelas explicitamente.")
    next_id = project.db.execute(
        "SELECT COALESCE(MAX(id),0)+1 FROM editorial_decisions"
    ).fetchone()[0]
    decision_id = f"decision-{next_id:06d}"
    target = value["target"]
    target_range = None
    if target["kind"] == "range":
        target_range = json_text({
            "space": target["space"], "start": target["start"],
            "duration": target["duration"],
        })
    project.db.execute(
        """INSERT INTO editorial_decisions(
               id,decision_id,feature,target_kind,target_id,target_range,provenance,
               properties,locks,unlocks,supersedes,automatic_key,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            next_id, decision_id, value["feature"], target["kind"], target.get("clip_id"),
            target_range, value["provenance"], json_text(value["properties"]),
            json_text(value["locks"]), json_text(value["unlocks"]), value["supersedes"],
            value["automatic_key"], now(),
        ),
    )
    return next(row for row in _rows(project) if row["decision_id"] == decision_id)


def record_decision(project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    with project.db:
        return _insert_decision(project, payload)


def record_decisions(project: Project, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(payloads, list) or not payloads:
        raise AutoEditorError("El documento debe contener una lista no vacia de decisiones.")
    with project.db:
        return [_insert_decision(project, payload) for payload in payloads]


def set_feature_mode(
    project: Project, feature: str, mode: str, *, lock: bool = False, unlock: bool = False,
) -> dict[str, Any]:
    if mode not in FEATURE_MODES:
        raise AutoEditorError(f"Modo editorial no soportado: {mode}")
    return record_decision(project, {
        "feature": feature, "target": {"kind": "feature"}, "provenance": "manual",
        "properties": {"mode": mode}, "locks": ["mode"] if lock else [],
        "unlocks": ["mode"] if unlock else [],
    })


def resolve_decisions(project: Project, timeline: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = _rows(project)
    by_id = {row["decision_id"]: row for row in rows}
    outcomes = {row["supersedes"]: row for row in rows if row["supersedes"]}
    candidates: list[dict[str, Any]] = []
    rejected = []
    proposals = []
    for feature, mode in DEFAULT_FEATURE_MODES.items():
        candidates.append({
            "id": 0, "decision_id": "defaults", "feature": feature,
            "target": {"kind": "feature"}, "provenance": "defaults",
            "properties": {"mode": mode},
        })
    for row in rows:
        if row["provenance"] == "manual":
            candidates.append(row)
            continue
        if row["provenance"] != "automatic":
            continue
        outcome = outcomes.get(row["decision_id"])
        if outcome is None:
            candidates.append(row)
            proposals.append(row["decision_id"])
        elif outcome["provenance"] == "rejected":
            rejected.append(outcome["decision_id"])
        else:
            effective = dict(outcome)
            effective["properties"] = deepcopy(row["properties"])
            if outcome["provenance"] == "modified":
                effective["properties"].update(outcome["properties"])
            candidates.append(effective)

    ranks = {"defaults": 1, "automatic": 2, "accepted": 3, "modified": 3, "manual": 4}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(
            (candidate["feature"], _target_key(candidate["target"])), [],
        ).append(candidate)

    def resolved_properties(feature: str, target: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, dict[str, Any]] = {}
        locked = _lock_state(rows, feature, target)
        for candidate in grouped.get((feature, _target_key(target)), []):
            rank = ranks[candidate["provenance"]]
            for name, value in candidate["properties"].items():
                current = values.get(name)
                order = (rank, candidate["id"])
                if current is None or order >= current["_order"]:
                    values[name] = {
                        "value": deepcopy(value), "provenance": candidate["provenance"],
                        "decision_id": candidate["decision_id"],
                        "locked": bool(locked.get(name)), "_order": order,
                    }
        for value in values.values():
            value.pop("_order")
        return {name: values[name] for name in sorted(values)}

    feature_modes = {}
    for feature in FEATURES:
        properties = resolved_properties(feature, {"kind": "feature"})
        mode = deepcopy(properties["mode"])
        mode["settings"] = {
            name: value for name, value in properties.items() if name != "mode"
        }
        feature_modes[feature] = mode

    available_clips = None
    if timeline is not None:
        available_clips = {
            clip["clip_id"] for track in timeline["video_tracks"] for clip in track["clips"]
        }
        available_clips.update(
            clip["parent_clip_id"]
            for track in timeline["video_tracks"] for clip in track["clips"]
            if clip.get("parent_clip_id")
        )
    targets = []
    unresolved = []
    for (feature, encoded_target), _ in sorted(grouped.items()):
        target = json.loads(encoded_target)
        if target["kind"] == "feature":
            continue
        relevant_ids = [
            row["decision_id"] for row in rows
            if row["feature"] == feature and row["target"] == target
        ]
        if available_clips is not None and target["clip_id"] not in available_clips:
            unresolved.extend(relevant_ids)
            continue
        mode = feature_modes[feature]["value"]
        allowed = {
            "off": set(),
            "auto": {"automatic", "accepted", "modified", "manual"},
            "manual": {"manual"},
            "hybrid": {"accepted", "modified", "manual"},
        }[mode]
        properties = resolved_properties(feature, target)
        properties = {
            name: value for name, value in properties.items()
            if value["provenance"] in allowed
        }
        if properties:
            targets.append({"feature": feature, "target": target, "properties": properties})

    hybrid_proposals = [
        decision_id for decision_id in proposals
        if feature_modes[by_id[decision_id]["feature"]]["value"] == "hybrid"
    ]
    return {
        "schema_version": DECISION_SCHEMA_VERSION,
        "feature_modes": feature_modes,
        "targets": targets,
        "proposals": sorted(hybrid_proposals),
        "rejected": sorted(rejected),
        "unresolved": sorted(set(unresolved)),
    }
