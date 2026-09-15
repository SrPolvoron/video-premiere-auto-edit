# AutoEditor Local

**Local footage indexing and editable rough cuts, with Premiere Pro as the final editor.**

[Documentacion en espanol](README.es.md) | [Architecture](docs/architecture.md) | [Local model](docs/local-model.md) | [Premiere validation](docs/premiere.md)

> **0.1.0a1 - experimental alpha.** The CPU pipeline and safety invariants have automated
> tests. The local vision adapter is implemented and contract-tested, but has **not** been
> benchmarked on a real RTX 4060 in this development environment. Generated XML has **not**
> yet been imported into Premiere here. This is not a finished alternative to a commercial editor.

AutoEditor reads your original videos, indexes candidate time ranges in a per-project SQLite
file, and builds versioned edit decisions. Exported legacy Final Cut Pro XML (`xmeml`) references
the original media: no extracted subclips, destructive trimming, or final recompression.

```text
Original DJI / phone / camera footage
                 |
         FFprobe + FFmpeg sampling
                 |
      technical metrics + optional local VLM
                 |
      project.db (resumable, versioned catalog)
                 |
       preset + prompt / explicit JSON policy
                 |
       deterministic rough-cut planner
                 |
   cut-0001.xml + auditable cut-0001.json
                 |
  Premiere: review, color, sound, effects, final export
```

## What is implemented

| Component | Status and boundaries |
| --- | --- |
| Media inventory | Metadata, SHA-256 on first ingest, change detection, missing-file tracking |
| Temporal analysis | Overlapping windows, cached thumbnails, resumable completed windows |
| Technical scoring | Simple exposure/sharpness proxies; motion measured but not discarded |
| Local visual analysis | Timestamped JPEG frames sent to a managed, loopback-only llama.cpp process |
| Within-clip actions | Multiple candidate intervals accepted from the VLM; boundaries remain estimates |
| Persistent project | SQLite, old analyses retained, immutable plan snapshots, explicit reject/prefer feedback |
| Editorial decisions | Stable clip IDs, persisted modes/overrides/property locks and auditable provenance |
| Editorial operations | Deterministic microcuts, rational retiming/slow motion, sparse transitions and declarative effects |
| Planning | Soft story preferences, chronological/variety modes, non-overlap and POV limits |
| Prompts | Local model translates supported directions to a validated, inspectable policy |
| Music | Optional librosa beat detection, cut alignment, original audio and music on separate tracks |
| Export | Legacy `xmeml` plus JSON report, source links, clip labels, markers, fit scaling |
| Portability | Relative media root where possible, explicit relink with content verification |
| Demo | Generated test footage; no downloaded or personal sample media |

**Not implemented:** reliable frame-accurate gesture detection, musical downbeat/drop analysis,
exact gesture-on-beat alignment, an optimizing narrative graph, cinematic quality guarantees,
visual embeddings, fine-tuning, a GUI, packaged standalone Windows executable, automatic
Premiere feedback round-trip, denoising or color grading. These are not hidden behind placeholder
success messages. See [roadmap](docs/roadmap.md).

## Requirements

Python 3.11 or newer; FFmpeg and FFprobe available in `PATH`.
For semantic analysis: a compatible local llama.cpp build, model weights, and matching visual
projector. A CUDA build and an appropriate NVIDIA driver are needed for GPU offload.
No Docker, database server, hosted API key, subscription, or separately operated editor is required.

The Python environment and FFmpeg/model runtime are dependencies, not additional editors.
The application starts its model helper for the command and closes it afterwards. This helper
uses an ephemeral localhost HTTP port; it is not a permanently installed service.

## First run on Windows

From the extracted repository directory, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\autoeditor.exe doctor
.\.venv\Scripts\autoeditor.exe demo .\work\smoke
```

There is no need to activate the environment or change the PowerShell execution policy.
The demo generates its own media and creates `work/smoke/exports/cut-0001.xml`. It tests the
**technical path**, not AI quality. Its sequence is 24 fps. Try that import before using real media.

On Linux use `.venv/bin/python` and `.venv/bin/autoeditor`. Windows is the intended Premiere
workstation; Linux supports the preprocessing and test pipeline.

## Configure the local vision model

Copy `examples/model.local.example.toml` to `model.local.toml` in the repository root.
Install your own compatible llama.cpp binary and model/projector files in the configured locations.
See [local model setup](docs/local-model.md) for the exact candidate filenames and memory caveats.
Nothing downloads model weights, executes downloaded shell scripts, or contacts a hosted model API.

```powershell
Copy-Item .\examples\model.local.example.toml .\model.local.toml
# Edit model.local.toml with your local file locations.
.\.venv\Scripts\autoeditor.exe doctor --runtime .\model.local.toml
```

## One foreground command for a project

```powershell
.\.venv\Scripts\autoeditor.exe --verbose run .\work\route `
  --media-root "E:\Videos\Route" `
  --runtime .\model.local.toml `
  --backend llama `
  --preset moto `
  --duration 30 `
  --fps 30 `
  --prompt "Start with preparation. Prefer exterior shots and landscapes. Use few POV shots."
```

The project can contain many files. Start with a **small real sample** before processing hours.
Default analysis uses 8-second windows with a 6-second stride, sampling every 0.5 seconds and
sending up to 8 frames per window. This is coarse action localization, not full video understanding.

**Important:** `--timing auto` is now the default. It verifies CFR/VFR timing and creates a cached
CFR derivative only when needed, without modifying originals or discarding high-FPS frames.
`strict`, `interpret`, and `conform` remain explicit alternatives. Actual Premiere import is still
an acceptance gate. See the Spanish [timing guide](docs/timing.md) and
[Premiere checklist](docs/premiere.md).

A 9:16 sequence uses `--width 1080 --height 1920`. V1 fits footage with letterboxing; it does not
intelligently crop, follow subjects, or apply an automatic cinematic look.

## Music, when wanted

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[audio]"
.\.venv\Scripts\autoeditor.exe plan .\work\route `
  --preset moto --duration 30 --fps 30 `
  --music "E:\Music\licensed-track.wav" --music-offset 12
.\.venv\Scripts\autoeditor.exe export .\work\route
```

`--no-beat-sync` retains music without beat detection. With no `--music`, the original sound can
be retained for nature videos. `--mute-original` excludes original audio. No automatic ducking,
noise removal, fades, or mastering is applied; finish the mix in Premiere.

Beat tracking is approximate, not a promise that every perceived beat or drop is correct. Scene
selection happens under the available candidate durations, so a fallback short cut may be off-beat.
Use your own or appropriately licensed music. The repository contains no tracks.

## Improve a result without rescanning footage

```powershell
.\.venv\Scripts\autoeditor.exe plan .\work\route `
  --preset nature --duration 45 --fps 30 `
  --intent-file .\examples\landscape.intent.json
.\.venv\Scripts\autoeditor.exe export .\work\route
.\.venv\Scripts\autoeditor.exe inspect .\work\route
```

Each `plan` creates a new `cut-000N` snapshot. A structured intent file avoids loading the model
just to translate a prompt. A changed prompt does not invalidate the visual analysis.

```powershell
.\.venv\Scripts\autoeditor.exe catalog .\work\route --output .\work\candidates.json
.\.venv\Scripts\autoeditor.exe feedback .\work\route SEGMENT_ID reject --note "Repetitive POV"
```

Use an actual ID from `catalog`. This explicit feedback affects later plans; it does **not** train
model weights or detect edits made inside Premiere automatically.

## Storage and publication safety

Keep project data outside the source checkout, or under ignored `work/`. Project SQLite files,
thumbnails, logs, and XML/JSON exports can disclose paths, dates, images and descriptions.
**Do not publish them with your code.** Git ignore rules and a public-tree guard are included,
but neither is a guarantee that a manually force-added file is harmless.

A consistent SQLite backup is available with `backup`. Keep `project.json`, original videos,
and music too: the database does not contain the actual footage. An external SSD can store the
files, but the target computer still needs its own Python environment, runtime, and GPU driver.
A Windows virtual environment is not guaranteed to remain relocatable after a drive-letter change.

## Development

```bash
python -m pip install -e ".[dev,audio]"
python -m pytest --cov=autoeditor_local --cov-report=term-missing
python -m ruff check src tests scripts
python scripts/check_public_tree.py
python -m build
```

CI is configured for Windows/Linux and Python 3.11/3.13. FFmpeg integration tests run on Linux;
Windows jobs run them only when FFmpeg is available. CI does not have a GPU or Premiere.
See [validation evidence](docs/validation.md), [contributing](CONTRIBUTING.md), and [publishing](docs/publishing.md).

The common decisions contract is documented in Spanish in
[decisiones editoriales](docs/decisiones-editoriales.md). Its consumers include
[microcuts](docs/microcuts.md) and
[retiming, slow motion, transitions and effects](docs/retiming-transiciones-efectos.md).
Denoise, stabilization, night enhancement and color operations remain unimplemented.

## License and independence

The code in this repository is MIT-licensed. Model weights, FFmpeg distributions, llama.cpp,
Python dependencies, Premiere, and your media have their own licenses. They are not bundled.
This is an independent project, not affiliated with Adobe, DJI, Xiaomi, Qwen, or the separate
`auto-editor` project. The project name is provisional; no package-name availability is claimed.
