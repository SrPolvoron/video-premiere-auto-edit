# Validation record - 0.1.0a1

Validation performed while producing this source release. This file separates executed checks
from configured or planned checks; it is not a hardware or editor compatibility certification.

## Executed successfully

- **98 automated tests passed**, with zero test failures and no pytest warnings in the final run.
- Statement coverage: **86.57%** (1199 of 1385 statements).
- Real FFmpeg synthetic demo: three generated sources, ten analyzed windows, an 8-second/24-fps
  rough cut, legacy XML plus JSON export, and source audio linkage.
- Real beat detection on a generated 120-BPM click track; approximate tempo/timestamps checked.
- Real FFprobe ingestion, changed/missing media handling, source hash checks and cache reuse.
- Analysis pause/resume and failure/retry through an explicitly labeled test model.
- Local HTTP adapter contract: timestamped inline images, JSON response validation, rejected redirects,
  rejected truncated output, and prompt policy validation. This server is a test double, not Qwen.
- Timeline continuity, source bounds, reject/prefer feedback, immutable plan versions, conservative
  mixed-FPS/VFR gates, XML links/markers/URLs, and original-media preservation.
- Python 3.11 syntax compatibility parsed using the running interpreter's compatibility mode.
- Python wheel build from the actual source using setuptools/pip without downloading build dependencies.
- Built wheel installed into an isolated target directory; its `doctor` command executed successfully.
- Public-source guard checked 45 tracked files with no issues; staged whitespace checks passed.

A test exposed independent endpoint rounding that could add a source frame. The serializer now
rounds source duration once, and the planner snaps source starts to the source frame grid. A regression
test checks that same-rate source and sequence clip durations remain equal.

## Development environment

- OS: Linux
- Python: 3.13.5
- ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers
- NumPy 2.3.5; Pillow 12.3.0; librosa 0.11.0; pytest 9.0.2.
- No NVIDIA device/runtime available for a real GPU inference trial.
- No Premiere application available in this environment.

## Not executed or not established

- **Actual Qwen3-VL inference, GPU offload, peak VRAM, throughput or semantic accuracy on an RTX 4060.**
- **Windows runtime execution and actual Premiere 26 XML import.**
- End-to-end real motorcycle/nature footage quality or equality with any commercial auto-editor.
- Fully precise action boundaries, musical downbeats/drops, auto-denoising, grading or learned taste.
- Hosted GitHub Actions runs: the workflow is included but no remote repository was created/pushed.
- Ruff execution in this environment: Ruff is configured for CI/development; it was not installed here.
  Python syntax and import-use checks were performed separately and are not called a Ruff result.
- Full reproducible dependency locking, independent security audit, GPU benchmark or a Windows installer.

## Reproduce

```bash
python -m pip install -e ".[dev,audio]"
python -m pytest -q --cov=autoeditor_local --cov-report=term-missing
python -m ruff check src tests scripts
python scripts/check_public_tree.py
python -m build
```

Integration tests skip when FFmpeg/FFprobe are unavailable; the beat test also skips without librosa.
Read the skipped count when comparing results. Test-double action labels are not recognition evidence.
The public source archive deliberately excludes original/generated media, project SQLite files,
model weights, runtime binaries, logs, environments and local build products.
