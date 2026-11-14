"""MediaPipe HandLandmarker (Tasks API) in VIDEO mode. Frames stay in memory."""

from __future__ import annotations

from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

MODEL = Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"

# MediaPipe hand skeleton, for drawing only
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
               (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


class Hands:
    def __init__(self, model: Path = MODEL) -> None:
        if not model.exists():
            raise FileNotFoundError(f"model missing: {model}. Download it:\n  curl -L -o {model} {MODEL_URL}")
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.lm = vision.HandLandmarker.create_from_options(opts)
        self._last_ts = -1

    def detect(self, frame_bgr, t_ms: int) -> list[tuple[float, float, float]] | None:
        """Landmarks of the first hand (normalised, un-mirrored) or None. t_ms must increase; we enforce it."""
        t_ms = max(int(t_ms), self._last_ts + 1)
        self._last_ts = t_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self.lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), t_ms)
        if not res.hand_landmarks:
            return None
        return [(p.x, p.y, p.z) for p in res.hand_landmarks[0]]

    def close(self) -> None:
        self.lm.close()
