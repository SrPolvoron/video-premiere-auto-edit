# Changelog

## Sin publicar

- Planner V2 con duración editorial variable, rebalanceo al target y diversidad global.
- Perfiles de prioridad declarativos para balanced, dance, sensual, moto, training,
  martial-arts, nature y action.
- Ritmo calm/balanced/dynamic, límites de clips por medio, preferencias de plano y presupuesto
  de primeros planos.
- Composición auditable defaults → preset → priority → user → prompt → CLI, con listas aditivas.
- Timeline interna canónica con IDs estables, rangos racionales de origen, rangos de secuencia,
  audio, música y campos progresivos para retiming, transiciones, efectos, mejoras y color.
- Políticas `--timing auto|strict|interpret|conform`, detección CFR/VFR y conformado no destructivo.
- Cache de derivados ProRes/PCM por fingerprint y preservación de tasas high-FPS.
- Exportación con fuentes multistream silenciadas y música independiente conservada.
- Arquitectura común de decisiones editoriales con modos off/auto/manual/hybrid, overrides,
  locks por propiedad y procedencia automatic/manual/accepted/rejected/modified.
- IDs internos persistentes `clip-001`, historial append-only y resolución determinista por
  precedencia integrada en snapshots de timeline.
- Migración SQLite V1→V2 que conserva media, análisis, feedback y planes existentes.
- Microcuts/jump cuts como primer consumidor del Decision System, con modos off/auto/manual/hybrid,
  niveles 0–3, propuestas idempotentes, rangos manuales racionales y locks por propiedad.
- Hijos de timeline deterministas con parent identity, source ranges no solapados y duración de
  secuencia exacta mediante cobertura fuente contigua no destructiva.
- Retiming canónico normal/constante/por regiones con tiempos racionales y slow motion
  off/auto/manual/hybrid que conserva la duración y la música.
- Priorización high-FPS, seguridad ante FPS insuficiente y targets estables de parent, hijo o rango.
- Transiciones declarativas escasas y efectos editoriales de catálogo pequeño, integrados con
  procedencia, locks, overrides y snapshots del Decision System.

## 0.1.0a1

Initial experimental source release: local media inventory, resumable temporal analysis,
SQLite project history, local VLM adapter, policy-driven rough-cut planning, optional beat
tracking, legacy xmeml export, synthetic demo, tests, documentation and GitHub CI configuration.

Known acceptance gates: real RTX 4060 inference and actual Premiere import remain unverified.
No commercial quality, exact gesture timing, GPU performance or noise-reduction claim is made.
