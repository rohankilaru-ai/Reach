"""Small, quiet preview: mirrored camera (what you see in a mirror), one fingertip dot, one line."""

from __future__ import annotations

import cv2
import numpy as np

SIZE = (240, 180)
# BGR dot colours: idle grey-white, moving soft blue, pinch/drag warm amber, paused muted red
COLOURS = {"idle": (225, 225, 225), "move": (255, 190, 120), "pinch": (80, 190, 255), "paused": (110, 110, 220)}


def dot_colour(state: str, pinch: bool) -> tuple[int, int, int]:
    if pinch or state.startswith(("pinch", "drag", "double", "click", "curl", "select")):
        return COLOURS["pinch"]
    if state.startswith(("paused", "click needs")):
        return COLOURS["paused"]
    if state.startswith(("point", "scroll")):
        return COLOURS["move"]
    return COLOURS["idle"]


def render(raw_bgr: np.ndarray, frame, state: str) -> np.ndarray:
    """raw_bgr is the unmirrored camera image; landmarks in `frame` are already mirrored, so the
    image is flipped and the dot is drawn at x * width: both match what the user sees."""
    img = cv2.resize(cv2.flip(raw_bgr, 1), SIZE, interpolation=cv2.INTER_AREA)
    img = cv2.addWeighted(img, 0.55, np.full_like(img, 24), 0.45, 0)   # dimmed, translucent look
    w, h = SIZE
    if frame is not None and frame.hand_present and "index" in frame.fingers:
        x, y = frame.fingers["index"].tip
        p = (int(round(x * (w - 1))), int(round(y * (h - 1))))
        col = dot_colour(state, frame.pinch_active)
        cv2.circle(img, p, 7, col, -1, cv2.LINE_AA)
        cv2.circle(img, p, 9, (255, 255, 255), 1, cv2.LINE_AA)
    bar = img[h - 22:h].copy()
    img[h - 22:h] = cv2.addWeighted(bar, 0.35, np.zeros_like(bar), 0.65, 0)
    label = f"Hands mode · {state}"
    cv2.putText(img, label.encode("ascii", "replace").decode().replace("?", "-"), (8, h - 7),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, (235, 235, 235), 1, cv2.LINE_AA)
    return img
