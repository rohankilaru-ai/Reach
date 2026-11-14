"""OpenCV preview window: mirrored camera, landmarks, state line. Labelled EXPERIMENTAL. Never saved to disk."""

from __future__ import annotations

import cv2

# MediaPipe hand skeleton, for drawing only
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
               (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]

WINDOW = "Reach pinch (EXPERIMENTAL)"


def draw(frame_bgr, lm, eng, lines: list[str]):
    img = cv2.flip(frame_bgr, 1)  # mirrored, like a mirror
    h, w = img.shape[:2]
    if lm:
        pts = [(int((1 - p[0]) * w), int(p[1] * h)) for p in lm]
        for a, b in CONNECTIONS:
            cv2.line(img, pts[a], pts[b], (200, 200, 200), 1, cv2.LINE_AA)
        for p in pts:
            cv2.circle(img, p, 3, (0, 200, 255), -1, cv2.LINE_AA)
    if eng.pointer:
        px, py = int(eng.pointer[0] * w), int(eng.pointer[1] * h)
        color = (0, 0, 255) if eng.pinching else (0, 255, 0)
        cv2.circle(img, (px, py), 10, color, 2, cv2.LINE_AA)
        if eng.drag_start:
            sx, sy = int(eng.drag_start[0] * w), int(eng.drag_start[1] * h)
            cv2.rectangle(img, (sx, sy), (px, py), (0, 0, 255), 2)
    cv2.rectangle(img, (0, 0), (w, 22 + 20 * len(lines)), (0, 0, 0), -1)
    cv2.putText(img, "EXPERIMENTAL", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2, cv2.LINE_AA)
    for i, line in enumerate(lines):
        cv2.putText(img, line, (8, 38 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def show(img) -> int:
    cv2.imshow(WINDOW, img)
    return cv2.waitKey(1) & 0xFF


def poll_key() -> int:
    """Key poll without drawing, used when the camera delivered no frame."""
    import cv2

    return cv2.waitKey(30) & 0xFF


def close() -> None:
    cv2.destroyAllWindows()
