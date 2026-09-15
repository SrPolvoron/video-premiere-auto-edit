# Exportación XML y lista de aceptación en Premiere

## Formato

AutoEditor escribe Final Cut Pro XML heredado, con raíz `<xmeml version="5">`. No es `.fcpxml`
moderno ni el formato nativo `.prproj`. Adobe indica que el XML moderno de Final Cut Pro X no se
importa directamente sin conversión. El modelo de intercambio empleado está descrito en la
referencia archivada de Apple.

- [Adobe: importación de FCP XML](https://helpx.adobe.com/premiere/desktop/organize-media/import-files/migrate-from-final-cut-pro-x.html)
- [Apple: elementos y timing de xmeml](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Elements/Elements.html)

El serializador incluye URLs de media, tasas de secuencia y origen, puntos in/out, vídeo, audio
original mono/estéreo, música mono/estéreo opcional, componentes enlazados, nombres descriptivos y
marcadores. Emite Basic Motion para encajar la fuente en la secuencia; no implementa recorte
inteligente ni seguimiento de sujetos.

XMEML es un adaptador de salida. La timeline interna de AutoEditor es la representación canónica.

## Qué se ha probado

Las pruebas automatizadas comprueban XML bien formado, referencias, enlaces, timing entero,
límites de origen, cuantización, snapshots inmutables y preservación de originales. También
ejecutan conformado CFR real sobre media sintética y verifican su cache. No abren Premiere ni
demuestran que su importador interprete cada campo como se espera.

El modo predeterminado `--timing auto` inspecciona CFR/VFR y crea una copia conformada cuando una
fuente no es segura para el intercambio directo. Consulta [Timing, FPS y conformado](timing.md).
El audio original multicanal o con varios streams sigue sin estar soportado por el adaptador; puede
omitirse con `--mute-original` sin eliminar la música.

## Primera importación

1. Ejecuta la demo sintética e importa `exports/cut-0001.xml` con **Archivo > Importar**.
2. Confirma que la secuencia abre, los enlaces resuelven, la duración es de 8 segundos a 24 fps,
   aparecen tres cortes de vídeo y el audio de fuente esperado está enlazado.
3. Revisa el primer y último frame de cada corte, sincronía, escala/bandas y la posibilidad de
   extender un clip dentro del material original disponible.
4. Guarda un `.prproj`. El XML es un artefacto de intercambio, no sustituye al proyecto nativo.

Después prueba por separado un archivo real corto de cada cámara a su tasa original. Solo entonces
combina DJI, teléfono y otras fuentes. Registra las versiones exactas de Premiere, sistema y formato;
no marques una combinación como compatible si no se ha probado.

## Material mixto y VFR

El flujo normal no requiere conocer CFR/VFR:

```powershell
.\.venv\Scripts\autoeditor.exe export .\work\ruta
```

Para investigar una incompatibilidad sin crear derivados:

```powershell
.\.venv\Scripts\autoeditor.exe export .\work\ruta --timing strict
```

`strict` informa de tasas no representables, material mixto, VFR o desfases A/V. Si se quiere una
prueba manual de interpretación, sin corregir timestamps:

```powershell
.\.venv\Scripts\autoeditor.exe export .\work\ruta --timing interpret --source-fps 30
```

`interpret` solo cambia la tasa declarada en XML. Revisa velocidad, sincronía y puntos de corte. La
opción heredada `--allow-unverified-timing --source-fps 30` se conserva para scripts existentes.

Para forzar derivados CFR aunque la fuente parezca segura:

```powershell
.\.venv\Scripts\autoeditor.exe export .\work\ruta --timing conform
```

El JSON complementario registra `timing_mode`, `timing_resolutions`, tasa medida/efectiva, decisión
y acierto de cache. Si se ejecuta `clean-cache --yes`, hay que regenerar los XML que enlazaban media
conformada.

Si cambia la letra o ubicación de la unidad:

```powershell
.\.venv\Scripts\autoeditor.exe relink .\work\ruta --media-root "F:\Videos\Ruta"
.\.venv\Scripts\autoeditor.exe export .\work\ruta --overwrite
```

`relink` compara hashes para no asociar un plan a archivos distintos con el mismo nombre. Un
proyecto Premiere ya importado puede requerir además su propio relink.

## Color, exposición y audio

El exportador no normaliza HDR, D-Log M, HLG ni perfiles de color distintos. El modelo recibe
previews pequeñas sin una canalización dedicada de color, por lo que sus métricas técnicas pueden
ser engañosas en log/HDR. Revisa las transformaciones en Premiere.

Los controles de color de Premiere no equivalen a reducción temporal avanzada de ruido. Este
milestone no añade denoiser, estabilización, LUT, efectos, transiciones, rampas de velocidad ni
interpolación de movimiento.

Audio original y música permanecen en pistas separadas; no hay mezcla, fundidos ni ducking
automáticos. Una combinación fuerte puede saturar hasta que se ajuste la mezcla.

## Aceptación pendiente

Falta validar en Premiere muestras reales 24/25/30/50/60/120 y NTSC, teléfonos VFR, rotación,
timecode de origen, offsets largos y combinaciones mixtas. La suite sintética prueba la mecánica,
pero no autoriza afirmar compatibilidad universal ni precisión de frame en el editor.
