#!/usr/bin/env bash
# Descarga los modelos que no se guardan en el repositorio (pesados o con licencia propia).
#  - Parakeet TDT 0.6B v3 (NVIDIA, CC-BY-4.0) vía sherpa-onnx: transcripción ES/EN con timestamps
#  - Silero VAD: divide el audio en pausas antes de transcribir
#  - RNNoise "somnolent-hogwash" (GregorR/rnnoise-models): reducción de ruido de voz
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/assets/models"
mkdir -p "$DIR"
cd "$DIR"
REL=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
if [ ! -d sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 ]; then
  curl -fL -o parakeet.tar.bz2 "$REL/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
  tar xjf parakeet.tar.bz2 && rm parakeet.tar.bz2
fi
[ -f silero_vad.onnx ] || curl -fL -o silero_vad.onnx "$REL/silero_vad.onnx"
[ -f sh.rnnn ] || curl -fL -o sh.rnnn \
  https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn
echo "Modelos listos en $DIR"
