"""CLI entry point. All project writes are explicit and use a single-writer lock."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import shutil
import sqlite3
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

from . import __version__
from .analysis import AnalysisSettings, analyze
from .audio import music_info
from .demo import demo
from .export import export_plan
from .media import ingest
from .planner import PRESETS, create_plan, load_plan
from .project import Project, init_project
from .util import AutoEditorError, project_lock, read_json, write_json
from .vision import LocalModel, load_runtime, validate_intent


def _analysis_options(parser):
    parser.add_argument("--backend", choices=["llama", "technical"], default="llama",
                        help="technical has NO action understanding; llama uses your local vision model")
    parser.add_argument("--runtime", type=Path, help="local llama.cpp TOML configuration")
    parser.add_argument("--window", type=float, default=8)
    parser.add_argument("--stride", type=float, default=6)
    parser.add_argument("--sample-step", type=float, default=0.5)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--longest", type=int, default=384)
    parser.add_argument("--cache-mb", type=int, default=2048)
    parser.add_argument("--decode-timeout", type=int, default=3600)
    parser.add_argument("--max-windows", type=int, help="pause after N new windows; same command resumes")
    parser.add_argument("--continue-on-error", action="store_true")


def _plan_options(parser, add_runtime=True):
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--preset", choices=list(PRESETS), default="balanced")
    parser.add_argument("--fps", default="30", help="e.g. 24, 25, 30, or 30000/1001")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", default="")
    prompt.add_argument("--prompt-file", type=Path)
    parser.add_argument("--intent-file", type=Path, help="validated structured intent; overrides prompt fields")
    if add_runtime:
        parser.add_argument("--runtime", type=Path)
    parser.add_argument("--music", type=Path)
    parser.add_argument("--music-offset", type=float, default=0)
    parser.add_argument("--no-beat-sync", action="store_true")
    parser.add_argument("--mute-original", action="store_true")


def _export_options(parser):
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-unverified-timing", action="store_true",
                        help="EXPERIMENTAL: export mixed FPS/VFR for a manual Premiere import test")
    parser.add_argument("--source-fps", help="EXPERIMENTAL: interpret all source timings at this FPS in XML only; requires --allow-unverified-timing")
    parser.add_argument("--overwrite", action="store_true", help="overwrite an existing generated XML/JSON export")
    parser.add_argument("--verify-media", action="store_true", help="rehash all originals before export")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="autoeditor", description="Local-first video indexing and rough-cut planning")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--verbose", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="check local dependencies without loading a model")
    doctor.add_argument("--runtime", type=Path)
    init = commands.add_parser("init", help="create a project, preserving original media")
    init.add_argument("project", type=Path)
    init.add_argument("--media-root", required=True, type=Path)
    init.add_argument("--name")
    inv = commands.add_parser("ingest", help="index new/changed videos")
    inv.add_argument("project", type=Path)
    inv.add_argument("--verify", action="store_true", help="full rehash, even if size and mtime match")
    ana = commands.add_parser("analyze", help="analyze/resume footage and persist candidate segments")
    ana.add_argument("project", type=Path)
    _analysis_options(ana)
    plan = commands.add_parser("plan", help="create a new plan from the existing SQLite catalog")
    plan.add_argument("project", type=Path)
    _plan_options(plan)
    export = commands.add_parser("export", help="export an existing plan as legacy xmeml and JSON")
    export.add_argument("project", type=Path)
    export.add_argument("--plan-id", help="default: latest plan")
    _export_options(export)
    run = commands.add_parser("run", help="ingest, analyze, plan and export in one foreground command")
    run.add_argument("project", type=Path)
    run.add_argument("--media-root", type=Path, help="required only for a new project")
    _analysis_options(run)
    _plan_options(run, add_runtime=False)
    _export_options(run)
    inspect = commands.add_parser("inspect", help="show project inventory, analysis status and plan versions")
    inspect.add_argument("project", type=Path)
    catalog = commands.add_parser("catalog", help="inspect candidate IDs, predictions and timestamps")
    catalog.add_argument("project", type=Path)
    catalog.add_argument("--output", type=Path)
    catalog.add_argument("--include-partial", action="store_true")
    feedback = commands.add_parser("feedback", help="prefer/reject a candidate without deleting its analysis")
    feedback.add_argument("project", type=Path)
    feedback.add_argument("segment_id")
    feedback.add_argument("decision", choices=["prefer", "reject", "neutral"])
    feedback.add_argument("--note", default="")
    relink = commands.add_parser("relink", help="change the media root after moving an external drive")
    relink.add_argument("project", type=Path)
    relink.add_argument("--media-root", required=True, type=Path)
    backup = commands.add_parser("backup", help="write a consistent SQLite backup; originals are separate")
    backup.add_argument("project", type=Path)
    backup.add_argument("output", type=Path)
    clean = commands.add_parser("clean-cache", help="delete derived thumbnails/audio cache, not SQLite or originals")
    clean.add_argument("project", type=Path)
    clean.add_argument("--yes", action="store_true")
    demonstration = commands.add_parser("demo", help="generate synthetic media and exercise the CPU pipeline")
    demonstration.add_argument("project", type=Path)
    return root


def _doctor(args) -> dict:
    result = {"python": platform.python_version(), "platform": platform.system(), "version": __version__,
              "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
              "nvidia_smi": shutil.which("nvidia-smi"), "cuda_inference_tested": False}
    if result["nvidia_smi"]:
        try:
            proc = subprocess.run([result["nvidia_smi"], "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                  capture_output=True, text=True, timeout=10, check=False)
            result["gpu"] = proc.stdout.strip() if proc.returncode == 0 else proc.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["gpu"] = str(exc)
    if args.runtime:
        config = load_runtime(args.runtime.resolve())
        result["runtime_config_valid"] = True
        result["model_revision"] = config.get("model_revision", "file size/mtime fingerprint")
    result["ready_for_technical_demo"] = bool(result["ffmpeg"] and result["ffprobe"])
    return result


def _model(args, project: Project, needed: bool):
    if not needed:
        return nullcontext(None)
    if not args.runtime:
        raise AutoEditorError("This command needs --runtime path/to/model.local.toml. See docs/local-model.md.")
    return LocalModel(load_runtime(args.runtime.resolve()), project.root / "logs" / "llama.log")


def _settings(args):
    return AnalysisSettings(args.window, args.stride, args.sample_step, args.frames,
                            args.longest, args.cache_mb, args.decode_timeout)


def _prompt(args) -> str:
    return args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.prompt


def _plan(args, project: Project, model) -> dict:
    prompt = _prompt(args)
    intent = model.intent(prompt) if prompt and model else {}
    if prompt and not model:
        raise AutoEditorError("A natural-language prompt requires a local model; use --intent-file for a no-model policy.")
    if args.intent_file:
        intent.update(validate_intent(read_json(args.intent_file)))
    music = music_info(project, args.music, args.duration, args.music_offset, not args.no_beat_sync) if args.music else None
    return create_plan(project, args.duration, args.preset, args.fps, args.width, args.height,
                       intent, prompt, music, not args.mute_original)


def _export(args, project, plan):
    xml, report = export_plan(project, plan, args.output, args.allow_unverified_timing,
                              args.overwrite, args.verify_media, source_fps_override=args.source_fps)
    timing_warnings = read_json(report)["export_validation"]["timing_warnings"]
    return {"xml": str(xml), "report": str(report), "plan": plan["id"],
            "warnings": list(dict.fromkeys(plan["warnings"] + timing_warnings))}


def dispatch(args):
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "demo":
        return demo(args.project)
    if args.command == "init":
        return {"project": str(init_project(args.project, args.media_root, args.name))}
    if args.command == "run" and not (args.project / "project.json").exists():
        if not args.media_root:
            raise AutoEditorError("A new project needs --media-root.")
        init_project(args.project, args.media_root)
    with Project(args.project) as project:
        if args.command == "inspect":
            return project.summary()
        if args.command == "catalog":
            rows = project.segments(args.include_partial)
            if args.output:
                if args.output.exists():
                    raise AutoEditorError("Catalog output already exists; choose a new filename.")
                write_json(args.output, rows)
                return {"output": str(args.output), "segments": len(rows)}
            return rows
        with project_lock(project.root):
            if args.command == "ingest":
                return ingest(project, args.verify)
            if args.command == "analyze":
                with _model(args, project, args.backend == "llama") as model:
                    return analyze(project, _settings(args), model, args.max_windows, args.continue_on_error)
            if args.command == "plan":
                with _model(args, project, bool(_prompt(args))) as model:
                    plan = _plan(args, project, model)
                return {"plan": plan["id"], "duration": plan["actual_duration"], "warnings": plan["warnings"]}
            if args.command == "export":
                return _export(args, project, load_plan(project, args.plan_id))
            if args.command == "run":
                if args.media_root and args.media_root.resolve() != project.media_root:
                    raise AutoEditorError("Existing media root differs. Use relink explicitly, not --media-root.")
                inventory = ingest(project)
                with _model(args, project, args.backend == "llama" or bool(_prompt(args))) as model:
                    analysis = analyze(project, _settings(args), model if args.backend == "llama" else None,
                                       args.max_windows, args.continue_on_error)
                    if analysis["paused"] or analysis["failed_windows"]:
                        raise AutoEditorError("Analysis paused or has failed windows. Resume analyze before planning.")
                    plan = _plan(args, project, model)
                return {"inventory": inventory, "analysis": analysis, **_export(args, project, plan)}
            if args.command == "feedback":
                project.feedback(args.segment_id, args.decision, args.note)
                return {"segment": args.segment_id, "decision": args.decision}
            if args.command == "relink":
                project.relink(args.media_root)
                return {"media_root": str(project.media_root)}
            if args.command == "backup":
                project.backup(args.output)
                return {"backup": str(args.output.resolve()), "note": "Keep project.json and source media separately."}
            if args.command == "clean-cache":
                if not args.yes:
                    raise AutoEditorError("Add --yes to delete derived cache files; originals and SQLite remain intact.")
                cache = project.root / "cache"
                if cache.is_symlink() or project.media_root.is_relative_to(cache.resolve()):
                    raise AutoEditorError("Unsafe cache directory: deletion refused.")
                shutil.rmtree(cache)
                cache.mkdir()
                return {"cache_removed": True, "database_preserved": True}
    raise AutoEditorError("Unsupported command.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s: %(message)s", stream=sys.stderr)
    try:
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (AutoEditorError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Completed windows remain in SQLite; rerun to resume.", file=sys.stderr)
        return 130
