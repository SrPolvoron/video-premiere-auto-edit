# Timing, FPS y conformado

AutoEditor usa una timeline interna propia como representación canónica. Los tiempos de origen
se guardan como fracciones de segundo, y los tiempos de secuencia como frames enteros junto a un
FPS racional. XMEML es solo una salida derivada: sus limitaciones no definen el modelo interno.

## Modo recomendado

`--timing auto` es el valor por defecto. Comprueba los timestamps cuando la inspección inicial de
FFprobe no basta y decide por cada original:

- usa el archivo directamente cuando es CFR y su tasa se puede expresar con seguridad;
- conserva directamente fuentes 50/60/100/120/240 fps aunque la secuencia tenga otro FPS;
- crea una copia CFR cuando detecta VFR, una tasa no representable o un desfase A/V que requiere
  normalización;
- registra la decisión y la tasa efectiva en el JSON que acompaña al XML.

No hace falta indicar `--source-fps 30` para el uso normal.

## Modos disponibles

```powershell
autoeditor export .\work\ruta --timing auto
autoeditor export .\work\ruta --timing strict
autoeditor export .\work\ruta --timing interpret --source-fps 30
autoeditor export .\work\ruta --timing conform
```

- `auto`: selección segura por original; es el modo recomendado y predeterminado.
- `strict`: no crea derivados y detiene la exportación ante VFR, FPS mixtos, tasas que XMEML no
  representa exactamente o desfases A/V relevantes.
- `interpret`: no convierte media. Declara al XML una tasa elegida, de forma automática o mediante
  `--source-fps`. Es una herramienta avanzada: puede cambiar la interpretación temporal y requiere
  revisar velocidad, cortes y sincronía en el editor.
- `conform`: fuerza una derivada CFR para cada fuente usada en la timeline.

`--allow-unverified-timing` se mantiene por compatibilidad y equivale al flujo antiguo de
`interpret`. Los proyectos y planes guardados anteriormente siguen siendo exportables mediante un
adaptador a la timeline interna.

## Cache y originales

Las derivadas se escriben en `cache/conform` dentro del proyecto. La clave incluye el fingerprint
del original, FPS de destino, presencia de audio, receta de codec y versión del conformado. Una
segunda exportación con los mismos datos reutiliza el archivo y valida su tamaño y fecha contra un
manifest. `clean-cache --yes` elimina estas derivadas, por lo que un XML que las referencie deberá
regenerarse.

El conformado actual usa vídeo ProRes proxy 10-bit 4:2:2 y audio PCM cuando el audio original está
activo. Los originales se abren solo para lectura: nunca se recortan, renombran, sobrescriben ni
transcodifican in situ.

Para material de alta velocidad, `auto` conserva el FPS fuente. Si es necesario conformar, elige
una tasa CFR normalizada cercana de 50/60/100/120/240 fps en lugar de reducirla al FPS de secuencia.
Esto mantiene disponibles los frames capturados para un futuro plan de cámara lenta. Este milestone
no implementa slow motion ni interpolación.

## Audio

El audio original se representa por clip en la timeline. Con `--mute-original`, los streams de
audio de las fuentes no se serializan ni bloquean el exportador, incluso si el vídeo contiene varios
streams o audio multicanal. Una pista de música opcional permanece separada y se conserva.

Cuando el audio original sí está activo, XMEML admite actualmente el primer stream mono o estéreo.
El conformado mantiene el desfase temporal relativo respecto al primer stream de vídeo.

## Límites de validación

Las pruebas automatizadas ejecutan FFmpeg sobre media sintética, verifican reutilización del cache,
hash inalterado del original, tasas racionales, high-FPS y audio silenciado con varios streams. No
constituyen una validación de importación en Premiere ni de material real de todos los teléfonos y
cámaras. Esa aceptación sigue siendo necesaria.
