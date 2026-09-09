# Instructions for coding agents and future workers

Read README.md, docs/architecture.md, docs/validation.md and docs/roadmap.md before changes.
This repository is an experimental LOCAL pre-editor for Premiere, not a generator or cloud service.

## Non-negotiable project constraints

- Preserve original videos. Do not trim, rename, overwrite or transcode them in place.
- No hosted inference APIs, subscriptions, telemetry, Docker requirement or invented credentials.
- Use one SQLite database per project; preserve analysis history and immutable plan snapshots.
- Never label a technical heuristic or test double as semantic AI recognition.
- Never claim GPU, Windows or Premiere validation from CPU-only synthetic tests.
- Reject invalid model JSON and out-of-bounds timestamps. No eval, exec or shell=True.
- External model/binary downloads require an explicit, verified installation workflow.
- Keep private media, databases, XML exports, model weights, runtime binaries and logs out of Git.
- Keep prompts subordinate to available footage and implement or report unsupported instructions.

## Commands

```bash
python -m pip install -e ".[dev,audio]"
python -m pytest --cov=autoeditor_local --cov-report=term-missing
python -m ruff check src tests scripts
python scripts/check_public_tree.py
python -m build
```

CI tests Python 3.11/3.13 on Windows/Linux. Do not change a failing assertion merely to make CI green.
Regression tests must accompany fixes to frame math, media identity, cache invalidation or audio links.
Changing SQLite schema requires an explicit migration and old-project tests; do not delete a user's DB.

## Current next work

First verify the Windows technical demo and Premiere XML import. Then benchmark a pinned local
Qwen3-VL + llama.cpp combination on the target RTX 4060 with a small labeled real-footage set.
Improve mixed-rate interoperability and dense action-boundary refinement before adding a GUI.
Do not replace those tasks with another architectural proposal or unsupported quality claims.
