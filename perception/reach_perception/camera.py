"""The one camera owner. Frames are returned in memory and never written anywhere."""

from __future__ import annotations

import time

import cv2

PERMISSION_MSG = (
    "Could not read from the camera. macOS needs Camera permission for the app running this process "
    "(your terminal, e.g. Terminal/iTerm/Ghostty, or Python): System Settings > Privacy & Security > Camera. "
    "Grant it, quit and reopen the terminal, then rerun."
)


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480) -> None:
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def ok(self, wait_s: float = 2.0) -> bool:
        """True once a frame arrives. The first open can fail while macOS shows the permission prompt."""
        if not self.cap.isOpened():
            return False
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            got, frame = self.cap.read()
            if got and frame is not None:
                return True
            time.sleep(0.05)
        return False

    def read(self):
        got, frame = self.cap.read()
        return frame if got else None

    def release(self) -> None:
        self.cap.release()
