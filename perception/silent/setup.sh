#!/bin/zsh
# Reach silent speech setup (EXPERIMENTAL). Apple Silicon, no sudo. Run from anywhere: perception/silent/setup.sh
#
# Installs, all into this folder (gitignored, never committed):
#   .venv/                         torch, torchvision, mediapipe, opencv (~1 GB)   -- `uv sync --extra model`
#   third_party/auto_avsr/         Auto-AVSR code (Apache-2.0) at commit 182b628 + patches/auto_avsr-ctc-device.patch
#   models/vsr_trlrs2lrs3vox2avsp_base.pth   Auto-AVSR VSR checkpoint (~1 GB). RESEARCH / NON-COMMERCIAL weights:
#                                  trained on LRS2, LRS3, VoxCeleb2 and AVSpeech. See THIRD_PARTY.md.
#   models/blaze_face_short_range.tflite      MediaPipe face detector (Apache-2.0)
# Same sources and pins as github.com/Input-Labs-HQ/silent-chat setup.command (MIT).
# Flags: --no-model skips the 1 GB checkpoint (typed --hyp path and tests still work).
set -euo pipefail
cd "$(dirname "$0")"
AVSR_COMMIT=182b628
CKPT_ID=1r1kx7l9sWnDOCnaFHIGvOtzuhFyFA88_
CKPT=models/vsr_trlrs2lrs3vox2avsp_base.pth
FACE_URL=https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite
WANT_MODEL=1
[[ "${1:-}" == "--no-model" ]] && WANT_MODEL=0

for t in uv git curl; do command -v $t >/dev/null || { echo "Missing: $t"; exit 1; }; done
[[ "$(uname -m)" == arm64 ]] || echo "Note: not Apple Silicon; the recognizer will run on CPU only."

echo "== Python environment (own venv; nothing is added to backend/ or perception/)"
timeout 900 uv sync --extra model

echo "== Auto-AVSR code @ $AVSR_COMMIT"
if [[ ! -d third_party/auto_avsr ]]; then
  mkdir -p third_party
  timeout 300 git clone --quiet https://github.com/mpc001/auto_avsr third_party/auto_avsr
  git -C third_party/auto_avsr checkout --quiet $AVSR_COMMIT
  git -C third_party/auto_avsr apply "$PWD/patches/auto_avsr-ctc-device.patch"
fi
git -C third_party/auto_avsr rev-parse --short HEAD

echo "== Models (gitignored)"
mkdir -p models
if [[ ! -f models/blaze_face_short_range.tflite ]]; then
  code=$(curl -sIL -o /dev/null -w '%{http_code}' --max-time 30 "$FACE_URL")
  [[ "$code" == 200 ]] || { echo "Face detector URL answered $code; not downloading."; exit 1; }
  curl -fsSL --max-time 120 -o models/blaze_face_short_range.tflite "$FACE_URL"
fi
if [[ $WANT_MODEL == 1 && ! -f $CKPT ]]; then
  code=$(curl -sIL -o /dev/null -w '%{http_code}' --max-time 30 "https://drive.google.com/uc?id=$CKPT_ID&export=download")
  [[ "$code" == 200 ]] || { echo "Checkpoint URL (Google Drive id $CKPT_ID) answered $code; not downloading."; exit 1; }
  echo "Downloading the Auto-AVSR checkpoint. RESEARCH / NON-COMMERCIAL use only (see THIRD_PARTY.md)."
  timeout 1200 .venv/bin/gdown "$CKPT_ID" -O "$CKPT"
fi

echo "== Sizes"
du -sh .venv third_party/auto_avsr models/* 2>/dev/null || true
echo "Done. Try: uv run python -m reach_silent.worker --selftest"
