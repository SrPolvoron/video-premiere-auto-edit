# Microcuts y jump cuts

Un microcut elimina uno o varios intervalos breves dentro de un clip lógico sin cambiar a otro
vídeo. Es útil para condensar una toma con movimiento, pose, gesto o acción cuando el salto aporta
ritmo. No recorta ni modifica el archivo original.

La selección de clips ocurre antes. El orden canónico preparado para los consumidores siguientes
es:

```text
selección → microcuts → retiming → transiciones/efectos
```

Retiming, transiciones y efectos se aplican después, mediante el mismo Decision System. La música
permanece en su pista y no se corta ni se estira al aplicar microcuts.

## Modos e intensidad

`microcuts` usa el sistema común de decisiones:

- `off`: no crea propuestas, hijos ni otros efectos en la timeline.
- `auto`: propone y aplica microcuts seguros de forma determinista.
- `manual`: el usuario decide el clip o los rangos; puede delegar la colocación.
- `hybrid`: guarda propuestas automáticas para aceptarlas, rechazarlas o modificarlas.

El nivel es `0` a `3`: `0` desactiva la feature; `1` es sutil (hasta un salto por clip), `2`
moderado (hasta dos) y `3` fuerte (hasta tres). El nivel fuerte sigue respetando fragmentos mínimos
y no se aplica a todos los clips. El ritmo `calm` reduce aún más la frecuencia.

Activa el modo automático a nivel de proyecto mediante el comando existente de decisiones:

```json
{
  "feature": "microcuts",
  "target": {"kind": "feature"},
  "provenance": "manual",
  "properties": {"mode": "auto", "level": 2}
}
```

```powershell
autoeditor decisions .\work\ruta --apply .\microcuts-auto.json
autoeditor plan .\work\ruta --duration 90 --fps 30
autoeditor decisions .\work\ruta
```

El último comando muestra las propuestas y decisiones persistentes. Los planes y reportes JSON
incluyen la timeline resultante.

## Manual

Para elegir el clip pero delegar su colocación al planificador, usa `placement: "auto"`:

```json
{
  "feature": "microcuts",
  "target": {"kind": "clip", "clip_id": "clip-007"},
  "provenance": "manual",
  "properties": {"placement": "auto", "level": 2}
}
```

Para fijar eliminaciones exactas, `remove_ranges` usa tiempos racionales absolutos de la fuente
original. No uses floats: `1.20` segundos se expresa como `120/100` (que se simplifica a `6/5`).

```json
{
  "feature": "microcuts",
  "target": {"kind": "clip", "clip_id": "clip-007"},
  "provenance": "manual",
  "properties": {
    "level": 2,
    "remove_ranges": [
      {
        "start": {"value": 6, "timescale": 5},
        "duration": {"value": 17, "timescale": 100}
      },
      {
        "start": {"value": 141, "timescale": 50},
        "duration": {"value": 11, "timescale": 50}
      }
    ]
  }
}
```

También se puede representar cada eliminación como un target `range`, con `space: "source"` y
`properties: {"remove": true}`. Los rangos deben caer exactamente en frames de la secuencia,
no solaparse y dejar subclips suficientemente largos. Un manual inválido se rechaza al regenerar
el plan, antes de escribir un snapshot nuevo.

En modo `hybrid`, acepta/rechaza/modifica la propuesta mediante `supersedes`, igual que las demás
features. `modified` puede sustituir `remove_ranges` y/o `level`. Los locks por propiedad siguen
siendo los del sistema común: por ejemplo, bloquear `remove_ranges` impide cambiar esos saltos sin
un `unlock` explícito.

## Timeline y duración

Los hijos se materializan solo en la timeline interna. Un clip lógico `clip-007` puede aparecer
como IDs derivados deterministas tales como `clip-007-mc-…`. Cada hijo conserva:

- `parent_clip_id` y `parent_source_range`;
- `media_id`, media, audio y metadata del padre;
- su `source_range` y `timeline_range` exactos;
- `microcut.parent_source_coverage`, rangos eliminados y decision IDs/provenance.

La suma de las duraciones de los hijos es exactamente la duración de su padre lógico, por lo que
la duración solicitada de la secuencia no cambia. Para compensar el tiempo eliminado, AutoEditor
amplía de forma determinista el source range contiguo libre, primero hacia el final y luego hacia el
inicio. Nunca invade otro clip del mismo medio. Si no hay fuente contigua suficiente, `auto` omite
la propuesta y una orden manual falla de forma explícita en vez de acortar silenciosamente el vídeo.

Los candidatos automáticos priorizan señales ya disponibles: etapa/etiquetas de acción y movimiento,
tipo de plano, confianza y, si existen beats, posiciones cercanas a un beat. Son heurísticas
deterministas, no reconocimiento frame-perfect ni garantía de sincronía musical. El presupuesto por
clip y la continuidad visual prevalecen sobre cortar en cada beat.

## Limitaciones

La localización de acciones proviene del análisis existente y puede ser gruesa. Revisa los saltos en
el editor final, sobre todo gestos rápidos, diálogos y VFR real. XMEML recibe los hijos como clips
ordinarios, pero todavía no materializa retiming, transiciones o efectos; estos permanecen en la
timeline interna y provocan un error explícito de exportación. No hay speed ramps ni integración
avanzada de Premiere.
