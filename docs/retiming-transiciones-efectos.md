# Retiming, slow motion, transiciones y efectos

Estas funciones son intenciones editoriales de la timeline interna. Usan los mismos IDs estables,
modos, overrides, locks y procedencia que el resto del Decision System. No modifican originales ni
dependen de XMEML.

## Orden canónico

```text
selección → microcuts → slow motion → transiciones → efectos → snapshot
```

Microcuts se resuelve primero. Una decisión posterior puede dirigirse al clip lógico
`clip-007`, a un hijo estable `clip-007-mc-…` o a un rango. Un target al padre elige de forma
determinista el hijo aplicable; un target directo al hijo tiene mayor especificidad. La identidad
del padre se conserva y los hijos derivados se registran para aceptar decisiones futuras.

## Retiming y slow motion

Cada clip contiene `retiming.mode`: `none`, `constant` o `regions`. Las velocidades son fracciones
`numerator/denominator`; las regiones particionan exactamente el rango fuente y el rango de frames
de timeline. El formato admite añadir más regiones en el futuro, pero todavía no implementa ramps.

Slow motion tiene niveles `0..3`: desactivado, sutil, moderado y fuerte. Un nivel alto incrementa
la intensidad o el número máximo de propuestas, no obliga a ralentizar todo. `auto` prioriza acción,
movimiento, etiquetas semánticas disponibles y fuentes 50/60/100/120 fps cuando dan frames limpios.
Una fuente con FPS insuficiente se omite; no se inventan frames ni se presenta interpolación como si
existiera.

Activación automática:

```json
{
  "feature": "slow_motion",
  "target": {"kind": "feature"},
  "provenance": "manual",
  "properties": {"mode": "auto", "level": 2}
}
```

Clip elegido con colocación automática dentro de ese clip:

```json
{
  "feature": "slow_motion",
  "target": {"kind": "clip", "clip_id": "clip-007"},
  "provenance": "manual",
  "properties": {"placement": "auto", "level": 3}
}
```

Rango exacto, expresado aquí en tiempo de fuente:

```json
{
  "feature": "slow_motion",
  "target": {
    "kind": "range", "clip_id": "clip-007", "space": "source",
    "start": "12/5", "duration": "4/5"
  },
  "provenance": "manual",
  "properties": {"speed": {"numerator": 2, "denominator": 5}}
}
```

También se admite `space: "timeline"`; `start` es entonces el tiempo absoluto de secuencia. Los
límites deben caer en frames exactos y quedar dentro de un único clip o hijo microcut.

### Estrategia de duración y audio

El intervalo de timeline asignado por Planner V2 no cambia. Al ralentizar, se consume un rango
fuente más corto dentro de ese mismo presupuesto. Las regiones normales y lentas suman exactamente
los frames previos, de modo que `--duration` no crece ni se trunca al final. Esta primera estrategia
prefiere estabilidad del montaje; no rebalancea otros clips ni prolonga una toma.

Los beats pueden ayudar a elegir un inicio entre posiciones ya válidas, pero no alteran la velocidad
para perseguir cada beat. La pista musical conserva source range y timeline range. Si el audio
original está activo, queda descrito explícitamente con `audio.retiming.mode: "none"`: el vídeo lento
no ralentiza implícitamente voz o ambiente.

## Transiciones

El catálogo inicial es `cut`, `cross_dissolve`, `dip_to_black` y `dip_to_white`. `cut` se representa
por ausencia de operación y sigue siendo la opción predominante. El modo automático propone como
máximo una transición en esta versión; considera cambio de medio, etapa/plano y pace, sin colocar
una transición en cada corte.

```json
{
  "feature": "transitions",
  "target": {"kind": "clip", "clip_id": "clip-007"},
  "provenance": "manual",
  "properties": {
    "to_clip_id": "clip-011",
    "type": "cross_dissolve",
    "duration": {"value": 1, "timescale": 5}
  }
}
```

Origen y destino deben ser adyacentes. La duración es racional, coincide con frames enteros y no
puede superar la mitad del clip adyacente más corto.

## Efectos

El catálogo declarativo es `subtle_push_in`, `punch_in`, `soft_zoom_in` y `soft_zoom_out`, con nivel
`0..3`. Son intenciones, no filtros de Premiere incrustados. Auto solo elige un subconjunto pequeño
de planos con señal editorial suficiente.

```json
{
  "feature": "effects",
  "target": {"kind": "clip", "clip_id": "clip-007"},
  "provenance": "manual",
  "properties": {"type": "soft_zoom_in", "level": 2}
}
```

## CLI, hybrid y limitaciones

Todos los ejemplos se guardan y consultan con la interfaz común, sin comandos paralelos:

```powershell
autoeditor decisions .\work\ruta --apply .\slowmo.json
autoeditor decisions .\work\ruta
autoeditor plan .\work\ruta --duration 90 --fps 30 --pace balanced
```

En `hybrid`, la propuesta `automatic` no se aplica hasta recibir `accepted` o `modified`; `rejected`
la descarta. `supersedes` y los locks funcionan igual que en
[decisiones editoriales](decisiones-editoriales.md).

El exportador XMEML actual continúa exportando montajes básicos. Si encuentra retiming, transición o
efecto que no puede representar fielmente, detiene esa exportación con un error explícito; la
operación permanece íntegra en el snapshot JSON. Todavía no hay speed ramps, interpolación óptica,
materialización avanzada en Premiere, estabilización, denoise, mejora nocturna ni color automático.
