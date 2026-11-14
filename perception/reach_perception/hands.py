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


def hand_size(lms: list[tuple[float, float, float]], w: int, h: int) -> float:
    """Apparent size in pixels (wrist to middle-finger base plus the knuckle span): bigger = closer to the camera."""
    def d(a: int, b: int) -> float:
        return ((lms[a][0] - lms[b][0]) * w) ** 2 + ((lms[a][1] - lms[b][1]) * h) ** 2

    return (d(0, 9) ** 0.5) + (d(5, 17) ** 0.5)


def pick_closest(hands: list[list[tuple[float, float, float]]], w: int, h: int,
                 prev_wrist: tuple[float, float] | None = None) -> int:
    """Index of the hand to use: the closest one (largest on screen). To stop flicker between two similar hands, among
    those within 15% of the biggest, keep the one nearest where the tracked hand was last frame."""
    sizes = [hand_size(x, w, h) for x in hands]
    top = max(sizes)
    near = [i for i, sz in enumerate(sizes) if sz >= 0.85 * top]
    if prev_wrist is None or len(near) == 1:
        return max(near, key=lambda i: sizes[i])
    return min(near, key=lambda i: (hands[i][0][0] - prev_wrist[0]) ** 2 + (hands[i][0][1] - prev_wrist[1]) ** 2)


class Hands:
    def __init__(self, model: Path = MODEL) -> None:
        if not model.exists():
            raise FileNotFoundError(f"model missing: {model}. Download it:\n  curl -L -o {model} {MODEL_URL}")
        opts = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.lm = vision.HandLandmarker.create_from_options(opts)
        self._last_ts = -1
        self._prev_wrist: tuple[float, float] | None = None

    def detect(self, frame_bgr, t_ms: int) -> list[tuple[float, float, float]] | None:
        """Landmarks of the closest hand (normalised, un-mirrored) or None. t_ms must increase; we enforce it."""
        t_ms = max(int(t_ms), self._last_ts + 1)
        self._last_ts = t_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self.lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), t_ms)
        if not res.hand_landmarks:
            self._prev_wrist = None
            return None
        hands = [[(p.x, p.y, p.z) for p in lm] for lm in res.hand_landmarks]
        h, w = frame_bgr.shape[:2]
        pick = hands[pick_closest(hands, w, h, self._prev_wrist)]
        self._prev_wrist = (pick[0][0], pick[0][1])
        return pick

    def close(self) -> None:
        self.lm.close()
