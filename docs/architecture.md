# Architecture and design decisions

## Scope

This is a pre-editor, not a renderer. Its primary durable artifact is a project database;
its useful product is an editable, inspectable first cut. Original media must remain unchanged.

The code is intentionally split into small modules:

| Module | Responsibility |
| --- | --- |
| `media.py` | Probe, ingest, content hashes, streamed decoding, technical metrics |
| `analysis.py` | Sliding windows, cached thumbnails, resumable temporal analysis |
| `vision.py` | Managed local inference, timestamped images, strict output validation |
| `project.py` | SQLite schema, backups, feedback, project/media-root management |
| `planner.py` | Policy compilation/validation, deterministic selection, immutable decisions |
| `audio.py` | Optional song analysis and beat cache |
| `export.py` | Legacy xmeml serialization, media validation, audio links, markers |
| `cli.py` | User-facing foreground commands |

## Why no Compose or ComfyUI

There is a single project writer. SQLite is embedded in the Python process. FFmpeg is invoked
as a child process. A local llama.cpp helper is created only for an inference command and then
terminated. There is no central database server, permanent worker service, task broker, or
image/video generation graph. The single process model also makes interruption and privacy
boundaries easier to audit.

This is a managed local process, not a claim that inference happens without any localhost port.
The model transport uses HTTP on loopback, rejects redirects, and ignores HTTP proxy variables.
An existing loopback server is supported only when explicitly configured with `endpoint` and
`model_revision`. Off-machine hosts are deliberately rejected.

## Sampling versus understanding

Scene changes alone do not identify putting on a glove inside a continuous tripod take.
V1 therefore uses overlapping temporal windows, not PySceneDetect shot boundaries. Decoding
still reads the original video; sampling reduces semantic inference, not the cost of decoding
all compressed frames. Windows can overlap, but the planner does not reuse the selected
source intervals.

Technical scores describe cheap image statistics. A high Laplacian variance can come from
noise rather than detail. Darkness can be intentional. Static landscape shots can be useful.
Frame-to-frame changes are not a reliable camera-shake detector. No such measurement is
marketed as a semantic highlight decision.

The VLM receives a few ordered frames plus explicit relative times. It may propose several
actions per window, but each boundary is an estimate. Even 0.5-second sampling can miss a
short gesture, and selecting 8 images from a window increases the effective spacing further.
The model's confidence and interest values are **not calibrated probabilities**.

## Persistent state

`media` contains relative paths, content fingerprints, metadata and an active completed analysis.
`analyses` records the media fingerprint, settings, backend/model signature and status.
`windows` stores each completed, failed or paused unit of work. `segments` contains predictions
and source timestamps. `feedback` is an append-only history of explicit user judgments.
`plans` stores immutable JSON snapshots of complete edit decisions, policies and provenance.

A new model signature or relevant analysis setting creates a new analysis, not an in-place
rewrite. The previous completed analysis remains active while the new one is incomplete.
If footage changes, ingest invalidates its active analysis but retains historical records.
Feedback is tied to a segment ID and is not automatically transferred to a different analysis.

Initial ingest calculates full SHA-256. Later scans trust unchanged size/mtime as an optimization;
`ingest --verify` forces a full rehash. A malicious same-size, same-mtime change can evade that
optimization. Use `export --verify-media` when stronger verification is required.

The model signature uses the configured revision and binary/model file size and mtime, not a
full multi-gigabyte weight hash each run. Change `model_revision` after replacing/requantizing
weights. Pin and independently verify your downloaded model artifacts for reproducible work.

## Planner boundaries

The current planner is greedy and deterministic. Presets are soft preferences, not rigid
storyboards. It uses model labels only when available. It enforces source bounds, timeline
continuity, no reuse of overlapping chosen material, a maximum POV budget and optional
chronological ordering. It can return a shorter result rather than invent clips or loop footage.

Opening and closing stages are preferences, not guaranteed constraints. A requirement to
place a particular action on a particular musical beat is not yet supported. The current prompt
compiler reports unsupported requests. This is not yet a global graph optimizer or learned
cinematic editor.

Chronology uses camera dates, then filenames and segment offsets. Camera clocks can disagree;
there is no synchronization or continuity solver. The story ordering is a weak narrative prior,
not a conclusion about the real sequence of events.

## Exactness, portability and storage

Sequence timing uses integer frames with rational frame rates. XML source durations are rounded
once so independent endpoint rounding cannot create an accidental extra frame. Mixed FPS/VFR
are gated for manual validation. Sampled action boundaries remain approximate regardless of
numeric timestamp precision.

Projects use a relative media root where possible. Across Windows drives an absolute root may
be necessary; `relink` checks the replacement files' full content hashes before changing the root.
SQLite contains no media. Backups must also include the project configuration and original media.

The default thumbnail budget is 2 GiB, with a hard stop rather than unlimited writes. Temporary
sampling is streamed; JPEG previews are retained for restartability. `clean-cache --yes` removes
derived files, not completed predictions. An interrupted incomplete analysis may need to decode
a video again if its thumbnail cache was incomplete or deleted.

No embeddings, learned preference model, generated video, or image denoiser is hidden in the V1.
