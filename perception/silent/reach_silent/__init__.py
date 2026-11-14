"""Reach silent speech (EXPERIMENTAL, research weights): mouthed words -> 12-command hypotheses.

Heavy dependencies (torch, mediapipe, opencv) are imported lazily, only by lipreader.py / mouth.py / the camera path,
so the typed `--hyp` path and the tests run in the light venv.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # perception/silent
AVSR_DIR = ROOT / "third_party" / "auto_avsr"
MODELS = ROOT / "models"
CKPT = MODELS / "vsr_trlrs2lrs3vox2avsp_base.pth"
DETECTOR_MODEL = MODELS / "blaze_face_short_range.tflite"
FPS = 25  # Auto-AVSR was trained on 25 fps video
