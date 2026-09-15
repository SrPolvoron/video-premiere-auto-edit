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
| `decisions.py` | IDs estables, decisiones append-only, locks, overrides y resolución |
| `microcuts.py` | Consumidor determinista de decisiones: saltos internos e hijos de timeline |
| `retiming.py` | Retiming racional, slow motion seguro y selección de regiones |
| `editorial_features.py` | Transiciones y efectos declarativos desacoplados del exporter |
| `profiles.py` | Perfiles declarativos, validación y composición explícita de políticas |
| `planner.py` | Selección determinista V2, duración variable, diversidad global y snapshots inmutables |
| `audio.py` | Optional song analysis and beat cache |
| `timeline.py` | Timeline interna canónica, tasas/tiempos racionales e invariantes |
| `timing.py` | Políticas CFR/VFR, inspección temporal y cache de conformado |
| `export.py` | Adaptador xmeml, validación de media, enlaces de audio y marcadores |
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

El schema V2 añade `clip_identities` y `editorial_decisions`. La primera tabla desacopla la
identidad `clip-001` del orden de una timeline/export. La segunda conserva eventos append-only con
target, propiedades, procedencia, locks/unlocks y relaciones entre propuestas automáticas y
respuestas hybrid. Abrir un proyecto V1 ejecuta una migración aditiva; no reescribe sus planes.

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

## Límites del planner

Planner V2 sigue siendo greedy y determinista; no es un optimizador narrativo global ni un editor
cinematográfico aprendido. Calcula duraciones variables por candidato, acepta material más corto
que la duración preferida cuando respeta el mínimo, y redistribuye frames entre clips completos
para cerrar el target sin fabricar un residual submínimo.

La diversidad considera todo el historial de medios, planos, etapas, etiquetas y similitud visual.
Los presets, perfiles de prioridad, intent, prompt y CLI se combinan con precedencia explícita.
`max_clips_per_media` solo se relaja con un aviso si no queda otra alternativa; los presupuestos
de POV y primeros planos son límites duros. Véase [Planner V2](planner-v2.md).

Las etapas inicial/final siguen siendo preferencias, no constraints garantizadas. Colocar una
acción concreta en un beat concreto continúa sin estar soportado. La cronología usa fecha de
cámara, nombre y offset; no existe todavía sincronización de relojes ni solver de continuidad.

## Exactness, portability and storage

La timeline interna es la fuente de verdad y no depende de XMEML. Usa frames enteros para rangos
de secuencia y fracciones exactas de segundo/FPS para rangos de origen. Representa IDs de clip y
media, audio, música y espacios progresivos para retiming, transiciones, efectos, mejoras y color.
Los planes antiguos sin timeline se adaptan en memoria; no se migra ni elimina su snapshot.

El exportador XMEML consume esa timeline mediante una dependencia unidireccional y rechaza de
forma explícita campos que todavía no sabe serializar. `timing.py` decide si usa el original, lo
interpreta o crea una derivada CFR cacheada. Las duraciones XML se redondean una sola vez para
evitar frames extra por redondeo independiente. Los límites semánticos muestreados siguen siendo
aproximados aunque la representación numérica sea exacta.

La dirección para decisiones es
`project/SQLite → decisions → planner → microcuts → retiming → transitions/effects → timeline snapshot`.
Los exporters leen la timeline y no consultan ni modifican la tabla de decisiones. Los futuros
consumidores reciben exclusivamente la vista efectiva ya resuelta. La precedencia es manual
bloqueada, override manual, hybrid aceptada/modificada, automática y defaults. Un target ausente
queda marcado como no resuelto y sigue persistido.

Microcuts es el primer consumidor: transforma un clip lógico en hijos que conservan `parent_clip_id`,
media, rangos racionales y procedencia. La suma de sus frames se mantiene igual al padre lógico; el
tiempo fuente eliminado se compensa solo con cobertura contigua libre. El contrato implementado es
`selection → microcuts → retiming → transitions → effects`. Retiming conserva el presupuesto de
frames consumiendo menos fuente; la música no cambia. Transiciones y efectos describen intención
sin alterar rangos. El snapshot conserva procedencia y los exporters siguen siendo lectores.

Projects use a relative media root where possible. Across Windows drives an absolute root may
be necessary; `relink` checks the replacement files' full content hashes before changing the root.
SQLite contains no media. Backups must also include the project configuration and original media.

The default thumbnail budget is 2 GiB, with a hard stop rather than unlimited writes. Temporary
sampling is streamed; JPEG previews are retained for restartability. `clean-cache --yes` removes
thumbnails, audio analysis and conformed media derivatives, not completed predictions or originals.
Exports that reference a conformed cache file must be regenerated after cleaning that cache.

No embeddings, learned preference model, generated video, or image denoiser is hidden in the V1.
