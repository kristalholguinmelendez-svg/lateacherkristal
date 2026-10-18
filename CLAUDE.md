# Instrucciones para editar videos de LA TEACHER KRISTAL

Actúa como editor senior de contenido educativo vertical. El objetivo y los criterios
completos están en el "MASTER PROMPT" de Kristal; este archivo es el procedimiento.

## Procedimiento

1. **Inspecciona** las grabaciones (`kristal inspect`) y las herramientas disponibles.
   Si faltan modelos: `scripts/fetch_models.sh` (Hugging Face puede estar bloqueado;
   los releases de GitHub de sherpa-onnx suelen funcionar).
2. **Transcribe** con timestamps por palabra (`kristal transcribe --engine parakeet ...`).
3. **Lee la transcripción completa** y `analysis.md` (`kristal analyze`). El análisis
   automático es un borrador: verifica tú las tomas repetidas, el hook y las muletillas.
4. **Verifica los pasajes dudosos** volviendo a decodificar el audio aislado antes de
   ponerlos en subtítulos o gráficos. Corrige con `transcript_fixes` / `transcript_edits`.
   Lo que no puedas verificar, márcalo en el resumen para Kristal; nunca lo inventes.
5. **Escribe el `plan`** en `project.json`:
   - hook primero (la frase más sorprendente o útil, aunque esté a la mitad),
   - sin saludos largos, sin explicaciones repetidas, sin tartamudeos ni muletillas,
   - conserva pausas expresivas; cada corte debe mejorar claridad, ritmo o retención,
   - alterna encuadres (`wide`/`medium`/`close`) entre bloques para disimular saltos.
6. `kristal plan` → revisa la lista de segmentos (texto de cada corte, sin duplicados).
7. **Gráficos** solo con palabras dichas por Kristal o en `approved_terms`.
   Tipos: `word`, `vs`, `example` (`[parte resaltada]`), `title`, `tag` (capítulos).
8. **Audio**: mide el ruido; compara opciones (espectrogramas) antes de elegir. Para
   ruido fuerte de voz usa `denoise_strength: "rnnoise"` con `rnnoise_mix` ≤ 0.9.
9. `kristal render --preview`, **mira los fotogramas de `qc/`** (no solo el reporte),
   corrige y luego `kristal render` final. El QC debe salir APROBADO.
10. No afirmes nada que no se haya ejecutado (restauración, estabilización, verificación).

## Reglas de marca

- Tipografía Inter (Black/ExtraBold/SemiBold), paleta en `brand/style.json`.
- Subtítulos: ≤3 palabras, palabra activa en dorado, términos de la lección en rosa.
- Zonas: cara ~34 % de la altura, gráficos ~58 %, subtítulos ~73 %, nada debajo del 80 %.
- Nunca suavizar la piel, alterar facciones ni "inventar" detalle con IA.
- Los videos (`.mp4`) y modelos no se suben a git; sí el proyecto y los entregables de texto.
