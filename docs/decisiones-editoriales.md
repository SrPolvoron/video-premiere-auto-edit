# Decisiones editoriales persistentes

AutoEditor dispone de una capa común de decisiones independiente del planner y de XMEML. Su
objetivo es que futuras funciones —slow motion, microcuts, transiciones, efectos, estabilización,
denoise, mejora nocturna, color y selección de clips— compartan el mismo modelo de control,
persistencia y auditoría.

La arquitectura se incorpora a la timeline interna. Microcuts, slow motion, transiciones y efectos
son consumidores del mismo historial; no crean sistemas paralelos. Un exporter debe rechazar una
operación que no soporte en lugar de simularla.

## Modos

Cada feature tiene una propiedad global `mode`:

- `off`: no hay propiedades activas ni modificación.
- `auto`: se aplican propuestas `automatic`; una decisión manual puede prevalecer.
- `manual`: solo se aplican decisiones `manual`.
- `hybrid`: las propuestas automáticas quedan pendientes hasta ser `accepted` o `modified`; las
  propuestas `rejected` no se aplican. También se admiten adiciones manuales.

Todas las features nuevas parten en `off`. `clip_selection` parte en `auto` porque el planner actual
ya realiza la selección automáticamente, aunque el consumo de overrides de selección queda para un
milestone posterior.

## IDs estables

Los IDs internos tienen forma `clip-001`, `clip-002`, etc. Se asignan una sola vez en SQLite a la
identidad del segmento analizado. Regenerar un plan con el mismo candidato conserva su ID aunque
cambie su posición. Las decisiones nunca se dirigen a "tercer clip del XML" ni a IDs `v-3` del
adaptador XMEML.

Un análisis nuevo puede producir una identidad de segmento nueva; en ese caso se asigna otro clip ID
y la decisión anterior se conserva como no resuelta, sin transferirla silenciosamente.

## Precedencia y locks

La resolución es determinista:

```text
manual bloqueada
  > override manual
  > hybrid accepted/modified
  > automatic
  > defaults
```

Los locks son por propiedad, no por objeto completo. Una propuesta automática puede persistirse
sobre una propiedad bloqueada, pero no reemplaza su valor efectivo. Un nuevo override manual sobre
esa propiedad debe incluir el mismo nombre en `locks` para sustituirla manteniendo el bloqueo, o en
`unlocks` para desbloquearla de forma explícita.

Las decisiones son eventos append-only. `accepted`, `rejected` y `modified` incluyen `supersedes`
con el ID de una propuesta `automatic`. Solo se admite una respuesta por propuesta. Las propuestas
automáticas idénticas son idempotentes mediante una clave determinista.

## Targets

Una decisión puede dirigirse a:

- `feature`: configuración global, por ejemplo `mode`;
- `clip`: un clip ID estable;
- `range`: un rango racional dentro de un clip, en espacio `source` o `timeline`.

Ejemplo de configuración y override manual:

```json
{
  "decisions": [
    {
      "feature": "stabilization",
      "target": {"kind": "feature"},
      "provenance": "manual",
      "properties": {"mode": "manual"}
    },
    {
      "feature": "stabilization",
      "target": {"kind": "clip", "clip_id": "clip-001"},
      "provenance": "manual",
      "properties": {"strength": 0.55},
      "locks": ["strength"]
    }
  ]
}
```

Se aplica de forma declarativa:

```powershell
autoeditor decisions .\work\ruta --apply .\decisiones.json
autoeditor decisions .\work\ruta
```

Para aceptar una propuesta:

```json
{
  "feature": "stabilization",
  "target": {"kind": "clip", "clip_id": "clip-001"},
  "provenance": "accepted",
  "supersedes": "decision-000042"
}
```

`modified` añade `properties` que sustituyen parcialmente las de la propuesta. `rejected` no admite
propiedades. Los nombres de feature aceptados son `slow_motion`, `microcuts`, `transitions`,
`effects`, `stabilization`, `denoise`, `night_enhance`, `auto_color`, `color_style` y
`clip_selection`.

Microcuts se documenta con sus propiedades, rangos racionales, hijos y estrategia de duración en
[Microcuts y jump cuts](microcuts.md). Slow motion, transiciones y efectos se describen en
[Retiming, slow motion, transiciones y efectos](retiming-transiciones-efectos.md).

## Persistencia y snapshots

SQLite conserva dos estructuras nuevas:

- `clip_identities`: asignación estable entre segmento y clip ID;
- `editorial_decisions`: historial append-only, procedencia, propiedades, locks y relaciones hybrid.

Cada plan sigue siendo un snapshot inmutable. Al regenerar, el planner resuelve el historial actual
y guarda su vista efectiva en `timeline.editorial_decisions`. Si un target no aparece en el nuevo
montaje, su decision ID figura en `unresolved` y permanece intacto en SQLite.

Los proyectos schema V1 se migran de forma explícita a V2 al abrirlos. La migración crea las tablas e
índices nuevos y actualiza `PRAGMA user_version`/`project.json`; no elimina planes, análisis,
feedback ni media.
