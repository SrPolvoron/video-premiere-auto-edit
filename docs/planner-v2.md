# Planner V2 y perfiles de prioridad

Planner V2 selecciona fragmentos del catálogo ya analizado. No ejecuta efectos, cámara lenta,
reencuadre ni operaciones específicas de Premiere. Los originales siguen siendo de solo lectura.

## Uso

Los perfiles incorporados son `balanced`, `dance`, `sensual`, `moto`, `training`,
`martial-arts`, `nature` y `action`.

```powershell
.\.venv\Scripts\autoeditor.exe plan .\work\proyecto `
  --preset balanced `
  --priority sensual `
  --pace balanced `
  --duration 30 --fps 30
```

`--preset` se conserva por compatibilidad y expresa la estructura narrativa general. `--priority`
elige un perfil editorial declarativo. También pueden usarse `--max-clips-per-media`,
`--max-close-fraction`, `--preferred-shot` y `--avoid-shot`. Los dos últimos se pueden repetir.

## Composición de la política

La precedencia es siempre:

```text
defaults → preset → priority → user → prompt → CLI
```

`user` corresponde a `--intent-file`; `prompt` es únicamente la política validada producida a
partir del prompt local. Los escalares usan la última definición disponible. Las listas
`preferred_tags`, `avoid_tags`, `preferred_shots`, `avoid_shots` y `unsupported_requests` se
acumulan en orden estable y sin duplicados. Por tanto, las etiquetas de un prompt no borran las
del preset ni las del perfil. Si una capa posterior declara explícitamente una preferencia como
evitada —o al revés—, prevalece la capa posterior. El plan guarda tanto la política efectiva como
todas las capas.

## Duración, ritmo y cierre del target

Cada candidato obtiene una duración propia dentro de `min_shot..max_shot`, calculada a partir de:

- `pace`: `calm`, `balanced` o `dynamic`;
- tipo de plano y etapa narrativa;
- calidad, interés y confianza disponibles;
- una variación estable derivada del ID del candidato;
- beats, cuando se solicitó análisis musical y existe uno cercano válido.

Un candidato cuya duración disponible sea inferior a la duración editorial preferida sigue siendo
válido si alcanza `min_shot`. Al acercarse al target, el planner redistribuye frames entre clips
completos dentro de sus límites. No crea un último clip artificialmente corto para absorber el
residual. Si el material no permite completar el target sin repetir rangos, devuelve un montaje más
corto y lo comunica en `warnings`.

## Diversidad y límites

La puntuación considera todo el historial seleccionado: número de usos del medio, distribución de
planos y etapas, etiquetas preferidas ya representadas, similitud visual aproximada y las últimas
tres elecciones. En modo `variety` estas penalizaciones globales tienen más peso.

`max_clips_per_media` es un límite estricto mientras exista alguna alternativa válida. Si ninguna
alternativa puede continuar el montaje, se permite un fallback determinista y queda registrado en
los avisos del plan. `max_pov_fraction` y `max_close_fraction` siguen siendo presupuestos duros.

El perfil `dance` prioriza baile y movimiento corporal, favorece planos medios/completos y limita
los primeros planos. `sensual` reparte preferencias entre baile, movimiento corporal, poses,
gestos, miradas, rostro, planos medios y planos completos; combina diversidad histórica,
preferencias de plano y un límite de primeros planos para evitar que una sola categoría domine.

## Determinismo y límites de la función

Con el mismo catálogo, feedback, política, música y versión del código, la selección, los rangos y
las duraciones son iguales. La fecha y el ID de un nuevo snapshot cambian deliberadamente. La
inferencia que produjo el catálogo puede variar entre modelos o runtimes; el planner no presenta
esa inferencia como determinista.

Planner V2 no implementa efectos, slow motion, transiciones avanzadas, reencuadre inteligente ni
funciones nuevas de Premiere.
