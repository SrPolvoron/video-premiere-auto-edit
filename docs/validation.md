# Registro de validación — 0.1.0a1 sin publicar

Validación ejecutada el 15 de septiembre de 2026. Este documento separa comprobaciones reales de
tareas pendientes; no certifica hardware, calidad semántica ni compatibilidad con Premiere.

## Resultado automatizado

- Suite completa en Windows con Python 3.13.15: **201 tests superados**, sin warnings incluso
  tratando `ResourceWarning` como error.
- Cobertura total de statements: **87 %**.
- Ruff superado sobre `src`, `tests` y `scripts`.
- `scripts/check_public_tree.py`, construcción de sdist/wheel y `git diff --check` superados.
- Compatibilidad sintáctica comprobada por CI/configuración para Python 3.11; la ejecución local
  de este registro corresponde a Python 3.13.

## Planner V2 y perfiles

- Duración variable, aceptación de candidatos cortos y rebalanceo exacto sin residual artificial.
- Ritmos calm/balanced/dynamic, diversidad global, límite/fallback por medio y presupuesto de
  primeros planos.
- Perfiles balanced, dance, sensual, moto, training, martial-arts, nature y action.
- Composición determinista de defaults, preset, prioridad, usuario, prompt y CLI.

## Timeline y timing V2

- Timeline canónica validada con IDs únicos, rangos racionales de origen, frames de secuencia,
  audio, música y campos progresivos de retiming/transiciones/efectos/mejoras/color.
- Adaptación y exportación de un snapshot V1 guardado sin timeline interna.
- Modos auto, strict, interpret y conform, incluidos FPS racionales no representables y fuentes
  high-FPS 60/120.
- Conformado CFR real con FFmpeg sobre media sintética, reutilización de cache y hash del original
  inalterado.
- Fuente real con dos streams de audio + original silenciado: exportación válida, sin source audio
  y con música preservada.
- Rechazo explícito por XMEML de retiming/efectos no representables, sin perderlos en la
  representación interna.

## Sistema común de decisiones

- Modos `off`, `auto`, `manual` y `hybrid`, con resolución determinista y procedencia
  `automatic`, `manual`, `accepted`, `rejected` y `modified`.
- IDs `clip-NNN` persistentes entre regeneraciones; los targets ausentes se conservan como no
  resueltos en vez de reasignarse por posición.
- Overrides y locks por propiedad, incluido desbloqueo explícito y conservación al regenerar.
- Propuestas automáticas idempotentes, respuestas hybrid únicas y escritura de lotes atómica.
- Persistencia tras reabrir el proyecto y migración aditiva V1 → V2, incluyendo prueba de rollback
  ante un schema V1 dañado y conservación de snapshots anteriores.
- La timeline guarda la vista efectiva; microcuts, slow motion, transiciones y efectos usan el
  mismo contrato. Estabilización, denoise, mejora nocturna y color siguen sin implementarse.

## Microcuts / jump cuts

- `off` no crea decisiones automáticas ni subclips; auto/manual/hybrid usan el mismo historial,
  procedencia, locks y resolución que las demás features.
- Niveles 1/2/3, colocación manual automática, rangos manuales racionales, aceptación, rechazo y
  modificación hybrid cubiertos con tests deterministas.
- Hijos con ID derivado estable, parent identity, rangos fuente válidos y no solapados, fragmentos
  mínimos y duración total exacta de la timeline.
- Beats sintéticos favorecen una posición cercana cuando cabe de forma segura; la música no cambia.
- Serialización, persistencia SQLite, regeneración y adaptación de planes sin microcuts cubiertas.

## Retiming, slow motion, transiciones y efectos

- Retiming normal, constante y por regiones con fracciones exactas; duración solicitada y rangos
  de música conservados.
- Slow motion off/auto/manual/hybrid, niveles 1/2/3, targets por clip/rango/parent/child,
  procedencia, locks, regeneración y seguridad para fuentes con FPS insuficiente.
- Preferencia high-FPS y beats sintéticos probados sin atribuir interpolación ni calidad real.
- Cut limpio, transición manual y automática escasa, aceptación hybrid y referencias entre clips
  y timeline validadas.
- Efectos declarativos off/auto/manual/hybrid, niveles y catálogo inicial validados.
- Serialización, adaptación de planes anteriores y fallo explícito de XMEML ante una operación
  compleja no representable.

## Otras regresiones conservadas

- Demo FFmpeg sintética completa, ingesta FFprobe y gestión de media cambiada/ausente.
- Reanudación de análisis, recuperación de fallos y cache de previews.
- Detección real de beats sobre clicks sintéticos.
- Hashes de media, source bounds, redondeo de frames, enlaces de audio, marcadores y URLs XML.
- Contrato del adaptador HTTP local: imágenes con timestamps, JSON estricto, redirects y respuestas
  truncadas rechazados. El servidor de prueba no demuestra reconocimiento real.

## Entorno local de esta ejecución

- Windows, Python 3.13.15.
- FFmpeg 8.1.2 full build.
- NumPy 2.5.3, Pillow 12.3.0, pytest 9.1.1.
- Sin ejecución de Premiere ni benchmark Qwen/CUDA/RTX 4060 en este registro.

## No demostrado

- Importación real en Premiere de CFR/VFR, FPS mixtos, rotación y offsets de distintas cámaras.
- Inferencia Qwen3-VL, offload, pico de VRAM o rendimiento en RTX 4060.
- Calidad sobre metraje real de moto/naturaleza ni equivalencia con editores comerciales.
- Calidad visual real de slow motion/transiciones/efectos, límites de acción exactos,
  downbeats/secciones musicales, denoise, grading, estabilización o interpolación.
- Auditoría de seguridad independiente, instalador Windows o lock reproducible completo.

## Reproducción

```bash
python -m pip install -e ".[dev,audio]"
python -m pytest --cov=autoeditor_local --cov-report=term-missing
python -m ruff check src tests scripts
python scripts/check_public_tree.py
python -m build
```

Las integraciones se omiten si faltan FFmpeg/FFprobe; la prueba de beats se omite sin librosa.
Comprueba siempre el recuento de skips. El árbol público excluye media, SQLite de proyectos,
exports, pesos, binarios, logs, entornos y caches locales.
