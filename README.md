# LA TEACHER KRISTAL — sistema de edición de video

Convierte grabaciones crudas en videos verticales (9:16) de clases de inglés con calidad
profesional: mejor toma, cortes limpios, hook al inicio, zooms con intención, ejemplos en
pantalla, subtítulos dinámicos, voz restaurada y control de calidad sobre el render final.

Todo es **no destructivo**: las decisiones viven en `projects/<clase>/project.json`
(anclado a tiempos de las grabaciones originales) y se puede volver a renderizar
cuando quieras cambiar algo.

## Instalación

```bash
sudo apt install ffmpeg espeak-ng          # ffmpeg ≥ 6 con libass, libx264, vidstab
pip install -r requirements.txt
scripts/fetch_models.sh                    # Parakeet (transcripción), Silero VAD, RNNoise
```

## Flujo

```bash
# 1. Crear el proyecto (enlaza las grabaciones; --copy para copiarlas)
python3 -m kristal init projects/mi-clase grabacion1.mov grabacion2.mov --vocab edible,eatable

# 2. Revisar especificaciones técnicas
python3 -m kristal inspect projects/mi-clase

# 3. Transcribir palabra por palabra
python3 -m kristal transcribe projects/mi-clase --engine parakeet \
    --model-dir assets/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 --vad assets/models/silero_vad.onnx
#    (o --engine whisper si hay acceso a Hugging Face, o --import subtitulos.srt)

# 4. Análisis editorial automático → analysis.md + borrador de cortes
python3 -m kristal analyze projects/mi-clase

# 5. (Opcional) Plan editorial palabra por palabra en project.json → cortes exactos
python3 -m kristal plan projects/mi-clase

# 6. Render + entregables + control de calidad
python3 -m kristal render projects/mi-clase            # --preview para una versión rápida
```

## Qué genera `render` (en `projects/<clase>/exports/`)

| Archivo | Contenido |
|---|---|
| `<clase>_final.mp4` | Versión principal: 1080×1920, H.264 + AAC, faststart, FPS de la grabación |
| `<clase>_sin_subtitulos.mp4` | Igual, sin subtítulos incrustados (los gráficos se mantienen) |
| `<clase>.srt` | Subtítulos para subir a la plataforma |
| `<clase>_transcripcion.md` | Transcripción corregida con tiempos del video final |
| `<clase>_registro_cortes.md` | Cada corte, toma usada, encuadre, zooms, gráficos, audio e imagen aplicados |
| `<clase>.edl` | Lista de edición CMX3600 para abrir en DaVinci Resolve / Premiere |
| `captions.ass`, `graphics.ass` | Subtítulos y gráficos editables (Aegisub) |
| `<clase>_proyecto.json` | Copia del proyecto editable usado en este render |
| `qc/report.md`, `qc/contact_sheet.jpg` | Control de calidad del archivo renderizado y fotogramas de revisión |

## `project.json`, lo editable

- `transcript_fixes` / `transcript_edits`: correcciones de transcripción (palabra o rango).
- `plan`: bloques `{src, from, to, skip, role, framing}` con tiempos de inicio de palabras.
- `segments`: cortes exactos (generados por `plan` o `analyze`, también editables a mano).
- `zooms`: punch-ins `{src, at, why}`.
- `graphics`: `word`, `vs`, `example` (`[resaltado]`), `title`, `tag`, siempre con texto aprobado.
- `captions`, `audio`, `look`, `brand_overrides`: ajustes por proyecto.

La identidad visual (colores, tipografía Inter, zonas seguras, animaciones) está en
`brand/style.json`.
