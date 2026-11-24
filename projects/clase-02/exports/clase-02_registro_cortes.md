# Registro de cortes y tomas — Change one's mind

FPS de salida 30.000 · 5 cortes · duración 16.00 s

| # | Salida | Fuente | Entrada | Salida fuente | Dur | Rol | Encuadre | Texto |
|---|---|---|---|---|---|---|---|---|
| 1 | 00:00.000 | A | 00:00.033 | 00:02.133 | 2.10s | hook | close | ¿Cómo dices en inglés cambiar de opinión? |
| 2 | 00:02.100 | A | 00:02.367 | 00:04.667 | 2.30s | curiosity | medium | Change one's mind. |
| 3 | 00:04.400 | A | 00:05.167 | 00:09.333 | 4.17s | explanation | wide | I changed my mind. I don't want pizza, I want tacos. |
| 4 | 00:08.567 | A | 00:09.333 | 00:14.033 | 4.70s | explanation | medium | I hope she changes her mind about going to college that far away. |
| 5 | 00:13.267 | A | 00:14.533 | 00:16.667 | 2.13s | payoff | close | Comprendo, mi amigo. |

## Zooms / punch-ins

- `00:02.233` punch-in — la respuesta: change one's mind
- `00:04.683` punch-in — changed my mind
- `00:07.413` punch-in — tacos
- `00:09.673` punch-in — changes her mind
- `00:11.603` punch-in — college
- `00:13.353` punch-in — cierre

## Gráficos en pantalla

- `00:00.000–00:16.000` header: 
- `00:04.473–00:08.133` example: text="I [changed my mind]. I don't want pizza, I want tacos."
- `00:08.663–00:13.267` example: text='I hope she [changes her mind] about going to college that far away.'

## Audio aplicado

- fuentes A: highpass: 75 Hz (rumble, golpes de mesa); denoise: afftdn nr=6 dB, piso de ruido medido -58.2 dBFS (SNR 36.2 dB)
- eq: -1.5 dB @220 Hz, +2.5 dB @3.2 kHz, +1 dB @9.5 kHz
- deesser: suave
- compresor: 2.6:1, umbral -21 dB (moderado)
- musica: original generada en código (gfunk, 92 BPM) a -28 dB, con ducking (baja aún más cuando hablas)
- sonoridad: ganancia +3.7 dB + limitador de picos (4× sobremuestreo) → -14.0 LUFS, techo -1.5 dBTP (voz antes: -20.58 LUFS, pico -2.20 dBTP)
- bordes: micro-fundidos de 12 ms en cada corte (sin clics)
- efectos: sintetizados en código (sin muestras de terceros): 7 whoosh (-17 dB), 3 pop (-15 dB), 1 ding (-13 dB); nunca superan el techo de pico

## Imagen aplicada

- A: exposicion: luma media 136: bien expuesta, sin cambio; contraste: rango 65-193: correcto, sin cambio; saturacion: sat media 21: sin cambio; balance_blancos: sin dominante fuerte (U 120, V 140), sin cambio; ruido_visual: ruido estimado 0.39, sin reducción

## Material descartado y motivos

`✂` = descartada · `⟳` = toma repetida · `~` = muletilla · `‖` = tartamudeo
