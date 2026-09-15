# Roadmap

## Alpha entregada

CLI local; inventario seguro de media; SQLite por proyecto; adaptadores técnico y de modelo local;
ventanas reanudables; varias acciones propuestas por ventana; feedback persistente; planes
inmutables; presets; conversión validada de prompt a política; beats opcionales; serialización
xmeml; demo sintética; pruebas unitarias/integración; higiene del repositorio y CI.

Planner V2 incorpora perfiles de prioridad declarativos, composición explícita de políticas, ritmo
y duración variables, diversidad global, límites de repetición por medio y presupuesto de primeros
planos.

La timeline interna es la representación canónica. Timing V2 incorpora modos automático,
estricto, interpretado y conformado, inspección CFR/VFR, cache no destructiva y preservación de
material high-FPS. XMEML continúa como adaptador limitado.

La arquitectura común de decisiones también está entregada: IDs de clip persistentes, modos
off/auto/manual/hybrid, overrides, locks por propiedad, procedencia auditable y migración SQLite
V1→V2. Microcuts, retiming/slow motion, transiciones y efectos declarativos ya son consumidores.
Siguen pendientes speed ramps, interpolación, estabilización, denoise, mejora nocturna, color e
integración avanzada de Premiere.

## Gate 1: validar el equipo real

Ejecutar la demo técnica en Windows, importar su XML en Premiere y registrar el resultado. Cargar
la combinación elegida de Qwen/llama.cpp en la RTX 4060 y medir memoria, offload y rendimiento.
Etiquetar manualmente un conjunto pequeño de vídeos reales de preparación, conducción y naturaleza
para medir errores de reconocimiento y límites. Un test double nunca demuestra calidad semántica.

El gate exige resultados reproducibles, no solo un montaje atractivo.

## Gate 2: cortes realmente editables

Validar en Premiere el flujo implementado de FPS mixtos/conform con teléfonos VFR reales,
rotación, relink, audio original y offsets largos. Añadir regresiones por formato cuando proceda.
Considerar un adaptador OTIO después de verificar el formato heredado y la distribución del
adaptador; OTIO no garantiza por sí solo interoperabilidad con Premiere.

Añadir fixtures de límites etiquetados por personas y refinar candidatos gruesos con muestreo
local más denso cerca de transiciones. Medir acciones perdidas, falsos highlights, cortes
innecesarios y tiempo de corrección de la timeline propuesta.

## Gate 3: mejores decisiones editoriales

Añadir embeddings visuales reales, grafos narrativos condicionales más ricos, grupos coherentes de
escenas repetidas, inclusiones/exclusiones explícitas y alternativas. Separar planos inicial/final
obligatorios de preferencias suaves. Incorporar semillas estables para propuestas deterministas
alternativas e informes de selección/rechazo más precisos.

## Gate 4: música y feedback

Añadir estimadores evaluados de downbeats/secciones separados del tracking básico de beats. Alinear
anclas visuales con acentos sin cortar gestos. Importar decisiones de edición exportadas desde
Premiere y compararlas con IDs de plan. Aprender del feedback exige una actualización de política
implementada y medible; guardar datos en SQLite no entrena un modelo.

## Gate 5: experiencia de producto

Solo tras superar los gates de importación e inferencia local: UI local pequeña, paquete Windows
construido/probado en Windows, instalación de dependencias/modelos con checksums fijados y releases
reproducibles. No hacen falta Docker, cuentas, servicio alojado ni otro editor completo.
