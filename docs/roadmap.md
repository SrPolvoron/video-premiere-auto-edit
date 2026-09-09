# Roadmap

## Delivered alpha

Local CLI; safe media inventory; per-project SQLite; technical and local-model adapters;
resumable windows; multiple proposed actions per window; persistent feedback; immutable plans;
soft presets; validated prompt-to-policy conversion; optional beat tracking; xmeml serialization;
synthetic demo; automated unit/integration tests; public-repository hygiene; CI configuration.

## Gate 1: validate the actual workstation

Run the technical demo on Windows, import its XML in Premiere, and record the result.
Load the chosen Qwen/llama.cpp combination on the RTX 4060 and record memory, offload and throughput.
Manually label a small set of original preparation, riding and nature clips to measure actual
recognition and boundary errors. A unit-test double is never evidence of semantic quality.

Success requires reproducible results, not just an attractive demonstration montage.

## Gate 2: make rough cuts reliably editable

Validate mixed FPS, phone VFR, rotation, media relinking, original audio and long source offsets.
Consider an OTIO adapter after verifying the precise legacy format and adapter distribution;
OTIO by itself is not a guarantee of Premiere interoperability.

Add human-labeled boundary regression fixtures, then refine coarse candidates with denser local
sampling around likely action transitions. Track dropped actions, false highlights, unnecessary
cuts and time spent correcting the proposed timeline.

## Gate 3: better editorial decisions

Add true visual embeddings and diversity selection, richer conditional story graphs, coherent
repeated scene groups, explicit include/exclude constraints and candidate alternatives.
Make required closing/opening shots distinct from soft preferences. Add stable seeding for
alternative deterministic proposals and more precise why-selected/why-rejected reports.

## Gate 4: music and feedback

Add evaluated downbeat/section estimators separately from basic beat tracking. Align visual
anchors to selected musical accents without cutting gestures unintentionally. Import explicit
Premiere-exported edit decisions and compare them to stored plan IDs. Learning from feedback
must be an implemented, measurable policy update, not a claim that SQLite trains a model.

## Gate 5: product experience

Only after import and local inference gates pass: a small local UI, a Windows package built/tested
on Windows, dependency/model installation with pinned checksums, and reproducible releases.
No need for Docker, an account system, a hosted service or another full video editor.
