"""Políticas de timing y cache no destructiva de media conformada."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .media import probe, require_binary, run_media
from .project import Project
from .util import AutoEditorError, digest, fps_fraction, read_json, write_json

TIMING_MODES = {"auto", "strict", "interpret", "conform"}
TIMING_SCAN_VERSION = 1
CONFORM_VERSION = 2
NORMAL_RATES = tuple(Fraction(value) for value in (
    "24000/1001", "24", "25", "30000/1001", "30", "48000/1001", "48", "50",
    "60000/1001", "60", "100", "120000/1001", "120", "240000/1001", "240",
))


def xmeml_rate_values(rate: Fraction) -> tuple[int, bool]:
    if rate.denominator == 1:
        return rate.numerator, False
    for timebase in (24, 30, 48, 60, 120, 240):
        if rate == Fraction(timebase * 1000, 1001):
            return timebase, True
    raise AutoEditorError(f"Frame rate {rate} cannot be represented exactly in xmeml.")


def xmeml_representable(rate: Fraction) -> bool:
    try:
        xmeml_rate_values(rate)
        return True
    except AutoEditorError:
        return False


def _candidate_rate(value: Any) -> Fraction | None:
    try:
        return fps_fraction(value)
    except AutoEditorError:
        return None


def normalized_rate(metadata: dict[str, Any], sequence_rate: Fraction) -> Fraction:
    """Elige un CFR estándar; conserva 50/60/100/120/240 cuando el origen es high-FPS."""
    measured = fps_fraction(metadata["fps"])
    nominal = _candidate_rate(metadata.get("nominal_fps"))
    if nominal and xmeml_representable(nominal):
        difference = abs(float(nominal / measured) - 1)
        if difference <= 0.05:
            return nominal
    nearest = min(NORMAL_RATES, key=lambda rate: (abs(float(rate - measured)), rate))
    relative = abs(float(nearest / measured) - 1)
    if float(measured) >= 45 and relative <= 0.08:
        return nearest
    if xmeml_representable(sequence_rate):
        return sequence_rate
    return nearest


def inspect_vfr(project: Project, source: Path, fingerprint: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Decodifica una vez con vfrdet y cachea la clasificación por fingerprint."""
    key = digest({"fingerprint": fingerprint, "version": TIMING_SCAN_VERSION})
    cache = project.root / "cache" / "timing" / f"{key}.json"
    if cache.is_file():
        result = read_json(cache)
        if result.get("fingerprint") == fingerprint:
            return result
    command = [
        require_binary("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "info",
        "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", "vfrdet", "-f", "null", "-",
    ]
    try:
        process = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=3600, check=False,
        )
        stderr = process.stderr.decode(errors="replace")[-20000:]
        match = re.findall(r"VFR:([0-9]+(?:\.[0-9]+)?)", stderr)
        if process.returncode or not match:
            result = {
                "fingerprint": fingerprint, "status": "failed", "vfr": True,
                "reason": "No se pudo verificar CFR; auto usará una copia conformada.",
            }
        else:
            ratio = float(match[-1])
            result = {
                "fingerprint": fingerprint, "status": "complete",
                "vfr": ratio > 0.001, "vfr_ratio": ratio,
                "method": "ffmpeg-vfrdet-full-decode-v1",
            }
    except (OSError, subprocess.TimeoutExpired):
        result = {
            "fingerprint": fingerprint, "status": "failed", "vfr": True,
            "reason": "La verificación CFR falló o agotó el tiempo; auto usará conform.",
        }
    write_json(cache, result)
    return result


@dataclass(frozen=True)
class ConformedMedia:
    path: Path
    metadata: dict[str, Any]
    cache_hit: bool
    cache_key: str


def conform_media(
    project: Project,
    source: Path,
    fingerprint: str,
    metadata: dict[str, Any],
    rate: Fraction,
    include_audio: bool,
) -> ConformedMedia:
    """Crea una copia CFR ProRes/PCM dentro del cache. El original nunca se escribe."""
    xmeml_rate_values(rate)
    key = digest({
        "fingerprint": fingerprint, "rate": str(rate), "audio": include_audio,
        "codec": "prores-ks-proxy-pcm-v1", "version": CONFORM_VERSION,
    })
    directory = project.root / "cache" / "conform"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{key}.mov"
    manifest = directory / f"{key}.json"
    if output.is_file() and manifest.is_file() and not output.is_symlink():
        cached = read_json(manifest)
        stat = output.stat()
        if (cached.get("cache_key") == key and cached.get("size") == stat.st_size
                and cached.get("mtime_ns") == stat.st_mtime_ns):
            return ConformedMedia(output, cached["metadata"], True, key)

    partial = directory / f".{key}.partial.mov"
    partial.unlink(missing_ok=True)
    filter_video = f"setpts=PTS-STARTPTS,fps=fps={rate.numerator}/{rate.denominator}"
    command = [
        require_binary("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
        "-noautorotate", "-i", str(source), "-map", "0:v:0", "-sn", "-dn",
        "-vf", filter_video, "-fps_mode", "cfr", "-c:v", "prores_ks", "-profile:v", "1",
        "-pix_fmt", "yuv422p10le", "-map_metadata", "0",
    ]
    if include_audio and metadata.get("audio_channels", 0):
        audio_offset = float(metadata.get("audio_start", 0)) - float(metadata.get("video_start", 0))
        if audio_offset >= 0:
            audio_filter = f"asetpts=PTS-STARTPTS+{audio_offset:.9f}/TB"
        else:
            audio_filter = f"atrim=start={-audio_offset:.9f},asetpts=PTS-STARTPTS"
        command += ["-map", "0:a:0?", "-af", audio_filter, "-c:a", "pcm_s16le"]
    else:
        command += ["-an"]
    rotation = int(metadata.get("rotation", 0))
    if rotation:
        command += ["-metadata:s:v:0", f"rotate={rotation}"]
    command.append(str(partial))
    try:
        run_media(command, timeout=86400)
        converted_metadata = probe(partial)
        converted_rate = fps_fraction(converted_metadata["fps"])
        if converted_rate != rate or converted_metadata.get("vfr_suspected"):
            raise AutoEditorError("La copia conformada no tiene el CFR solicitado.")
        os.replace(partial, output)
        stat = output.stat()
        converted_metadata["timing_check"] = "cfr-conformed-v2"
        write_json(manifest, {
            "cache_key": key, "source_fingerprint": fingerprint, "fps": str(rate),
            "include_audio": include_audio, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "metadata": converted_metadata,
        })
        return ConformedMedia(output, converted_metadata, False, key)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class TimingResolution:
    path: Path
    metadata: dict[str, Any]
    rate: Fraction
    method: str
    vfr_detected: bool
    cache_hit: bool | None = None
    cache_key: str | None = None

    def report(self, original_rate: Fraction) -> dict[str, Any]:
        return {
            "original_fps": str(original_rate), "effective_fps": str(self.rate),
            "method": self.method, "vfr_detected": self.vfr_detected,
            "conform_cache_hit": self.cache_hit, "conform_cache_key": self.cache_key,
            "high_fps_preserved": float(original_rate) >= 50 and float(self.rate) >= 50,
        }


def resolve_timing(
    project: Project,
    source: Path,
    fingerprint: str,
    metadata: dict[str, Any],
    sequence_rate: Fraction,
    mode: str,
    include_audio: bool,
    source_fps_override: str | None = None,
) -> TimingResolution:
    if mode not in TIMING_MODES:
        raise AutoEditorError(f"Modo de timing desconocido: {mode}")
    measured = fps_fraction(metadata["fps"])
    representable = xmeml_representable(measured)
    vfr = bool(metadata.get("vfr_suspected"))
    scan_failed = False
    if mode in {"auto", "strict"} and metadata.get("timing_check") in {
        None, "metadata_only_not_a_full_CFR_verification",
    }:
        inspection = inspect_vfr(project, source, fingerprint, metadata)
        vfr = vfr or bool(inspection["vfr"])
        scan_failed = inspection["status"] != "complete"
    offset = (
        include_audio and metadata.get("audio_channels", 0)
        and abs(metadata.get("audio_start", 0) - metadata.get("video_start", 0))
        > 1 / float(measured)
    )

    if mode == "strict":
        issues = []
        if not representable:
            issues.append(f"frame rate {measured} is not exactly representable in xmeml")
        if measured != sequence_rate:
            issues.append("source/sequence frame-rate mismatch")
        if vfr:
            issues.append("potential variable frame rate")
        if offset:
            issues.append("audio/video start-time offset")
        if scan_failed:
            issues.append("CFR verification failed")
        if issues:
            raise AutoEditorError("Export paused: " + ", ".join(issues))
        return TimingResolution(source, metadata, measured, "strict-direct", vfr)

    if mode == "interpret":
        interpreted = fps_fraction(source_fps_override) if source_fps_override else (
            measured if representable else normalized_rate(metadata, sequence_rate)
        )
        xmeml_rate_values(interpreted)
        return TimingResolution(source, metadata, interpreted, "interpret", vfr)

    needs_conform = mode == "conform" or not representable or vfr or bool(offset) or scan_failed
    if not needs_conform:
        return TimingResolution(source, metadata, measured, "auto-direct", False)
    target_rate = normalized_rate(metadata, sequence_rate)
    converted = conform_media(project, source, fingerprint, metadata, target_rate, include_audio)
    method = "forced-conform" if mode == "conform" else "auto-conform"
    return TimingResolution(
        converted.path, converted.metadata, target_rate, method, vfr,
        converted.cache_hit, converted.cache_key,
    )
