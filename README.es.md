# AutoEditor Local

**Un preeditor local para seleccionar fragmentos y proponer montajes editables en Premiere.**

## Estado real de esta entrega

Versión `0.1.0a1`, experimental. Hay código ejecutable, no solo una descripción del proyecto.
El flujo de CPU está probado con vídeos sintéticos. La integración con el modelo visual local
está implementada, pero su calidad con tus grabaciones y su rendimiento en una RTX 4060
**todavía deben medirse**. Tampoco se ha abierto Premiere en el entorno de desarrollo.

No se promete un resultado igual a Filmora, reconocimiento perfecto de acciones ni un porcentaje
de ahorro de tiempo. El objetivo es producir una primera propuesta que puedas corregir en Premiere.

## Qué incluye

- Inventario de originales, metadatos, hashes y detección de cambios.
- Análisis reanudable por ventanas; una ventana puede contener varias acciones propuestas.
- Métricas técnicas y un adaptador para un modelo visual local mediante llama.cpp.
- Un SQLite por proyecto, con historial y versiones de montaje independientes.
- Presets generales, prompt opcional y políticas JSON que puedes revisar.
- Selección sin reutilizar el mismo tramo, preferencias narrativas y límite de POV.
- Música opcional, detección básica de beats y audio original separado.
- Exportación XML legacy `xmeml` y un informe JSON; no se recodifican tus originales.
- Pruebas, CI, documentación, licencia MIT y protecciones para un repositorio público.

No incluye todavía interfaz gráfica, instalador autónomo, detección fiable de drops,
refinamiento de gestos al fotograma ni lectura automática de tus correcciones en Premiere.

## Arranque en Windows

Necesitas Python 3.11 o superior y FFmpeg/FFprobe disponibles en `PATH`.
Desde PowerShell, dentro de la carpeta del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\autoeditor.exe doctor
.\.venv\Scripts\autoeditor.exe demo .\work\smoke
```

No necesitas activar el entorno ni cambiar la política de ejecución de PowerShell.
La prueba genera sus propios vídeos y produce `work/smoke/exports/cut-0001.xml`.
Es una prueba **técnica**, no una demostración de reconocimiento por IA.

## Activar la IA local

El candidato inicial es Qwen3-VL 4B cuantizado, con su proyector visual compatible y una
compilación CUDA de llama.cpp. Los modelos y binarios no van incluidos en el repositorio.
Consulta [la configuración del modelo](docs/local-model.md) y copia el ejemplo:

```powershell
Copy-Item .\examples\model.local.example.toml .\model.local.toml
```

Edita las rutas del archivo. La aplicación arranca el proceso local del modelo durante el trabajo
y lo cierra después. No exige Docker, Ollama, una API de pago ni otro editor.
El proceso auxiliar usa un puerto temporal de localhost; no es un servicio permanente.

```powershell
.\.venv\Scripts\autoeditor.exe --verbose run .\work\ruta `
  --media-root "E:\Videos\Ruta" `
  --runtime .\model.local.toml `
  --backend llama `
  --preset moto --duration 30 --fps 30 `
  --prompt "Empieza con la preparación. Prioriza exteriores y paisajes. Usa pocos POV."
```

Empieza por dos grabaciones cortas. Después comprueba qué acciones detecta y los límites
que propone antes de analizar horas de material. Las muestras no garantizan que encuentre todos
los gestos ni que distinga una acción completa de un objeto visible.

## Crear una segunda versión

```powershell
.\.venv\Scripts\autoeditor.exe plan .\work\ruta `
  --preset nature --duration 45 --fps 30 `
  --intent-file .\examples\landscape.intent.json
.\.venv\Scripts\autoeditor.exe export .\work\ruta
```

Esto consulta el mismo SQLite, sin volver a analizar los vídeos. Cada montaje se guarda
como `cut-0001`, `cut-0002`, etc. También puedes rechazar o priorizar candidatos con `feedback`;
no se entrenan pesos del modelo y no se leen automáticamente cambios hechos en Premiere.

Para analizar música instala el extra con `pip install -e ".[audio]"` y usa `--music`.
Sin música se puede conservar el sonido ambiente. La mezcla final se hace en Premiere.

## Importante sobre el XML

La estructura se valida automáticamente, pero la importación real debe probarse en Premiere.
La versión inicial bloquea la exportación normal si detecta FPS distintos, posible VFR o ciertos
desfases de audio. `--allow-unverified-timing` permite generar un XML para una **prueba controlada**;
no corrige esos problemas. Consulta [la lista de comprobaciones](docs/premiere.md).

El modo vertical ajusta el encuadre con bandas cuando corresponde. No sigue sujetos ni recorta
inteligentemente. Tampoco aplica color, LUT, estabilización o reducción de ruido.

## Publicación y privacidad

Publica el código, no tus proyectos. Los SQLite, miniaturas, XML, JSON y logs pueden contener
imágenes privadas, fechas, descripciones y rutas de tu ordenador. Los modelos, binarios,
vídeos, canciones y archivos locales están excluidos de Git por defecto.

```powershell
python scripts/check_public_tree.py
```

Revisa igualmente los archivos que vas a subir. Esta entrega no ha creado ni publicado ningún
repositorio remoto en tu cuenta. La guía de publicación está en [docs/publishing.md](docs/publishing.md).

El proyecto y los modelos pueden guardarse en un NVMe externo. El entorno Python y los binarios
siguen dependiendo del sistema operativo y de sus controladores: no se promete un entorno virtual
portable entre equipos. `relink` sirve para actualizar la ubicación de los originales.

## Continuar el desarrollo

[AGENTS.md](AGENTS.md) contiene las instrucciones para otro worker.
[Roadmap](docs/roadmap.md) define las siguientes fases.
[Validación](docs/validation.md) distingue las pruebas ejecutadas de lo que falta comprobar.
