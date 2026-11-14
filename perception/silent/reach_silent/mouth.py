"""In-memory frames -> 96x96 mouth crops at 25 fps, as in Auto-AVSR preprocessing.

Adapted from github.com/Input-Labs-HQ/silent-chat `vsr.py` (MIT, Copyright (c) 2026 justbenzo; notice in THIRD_PARTY.md): FaceKeypoints (MediaPipe Tasks face detector ->
the 4 keypoints Auto-AVSR's legacy mediapipe detector used), resample_to_fps, and the test-time VIDEO_TRANSFORM.
Changes for Reach: no video file I/O at all (frames never touch disk), lazy imports, and no `--save-crop`.
"""

from __future__ import annotations

import contextlib
import os
import sys

import numpy as np

from . import AVSR_DIR, DETECTOR_MODEL, FPS


def resample_to_fps(frames, stamps, fps: int = FPS):
    """Frames at a variable camera rate (monotonic timestamps, seconds) -> exactly `fps` by nearest timestamp."""
    stamps = np.asarray(stamps, dtype=np.float64)
    if len(stamps) < 2:
        return np.stack(list(frames))
    grid = np.arange(stamps[0], stamps[-1], 1.0 / fps)
    idx = np.abs(stamps[None, :] - grid[:, None]).argmin(axis=1)
    return np.stack([frames[i] for i in idx])


@contextlib.contextmanager
def _quiet_stderr():
    """Hide MediaPipe/LiteRT native INFO logging (it writes straight to fd 2)."""
    sys.stderr.flush()
    saved = os.dup(2)
    with open(os.devnull, "w") as devnull:
        os.dup2(devnull.fileno(), 2)
        try:
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)


def _avsr_path() -> None:
    if str(AVSR_DIR) not in sys.path:
        if not AVSR_DIR.exists():
            raise RuntimeError(f"Auto-AVSR code missing at {AVSR_DIR}; run perception/silent/setup.sh")
        sys.path.append(str(AVSR_DIR))


class FaceKeypoints:
    """MediaPipe face detector -> 4 keypoints per RGB frame (right eye, left eye, nose tip, mouth centre) or None."""

    def __init__(self, model_path=DETECTOR_MODEL, min_confidence: float = 0.5) -> None:
        import mediapipe as mp

        self.mp = mp
        opts = mp.tasks.vision.FaceDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            min_detection_confidence=min_confidence,
        )
        with _quiet_stderr():
            self.detector = mp.tasks.vision.FaceDetector.create_from_options(opts)

    def __call__(self, frames):
        mp, out = self.mp, []
        with _quiet_stderr():
            for frame in frames:
                r = self.detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame)))
                if not r.detections:
                    out.append(None)
                    continue
                face = max(r.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
                h, w = frame.shape[:2]
                out.append(np.array([[k.x * w, k.y * h] for k in face.keypoints[:4]], dtype=np.float64))
        return out

    def close(self) -> None:
        if self.detector is not None:
            with _quiet_stderr():
                self.detector.close()
            self.detector = None


class MouthCropper:
    """RGB frames (T,H,W,3) uint8 at 25 fps -> mouth crops (T,96,96,3) uint8, using Auto-AVSR's VideoProcess."""

    def __init__(self) -> None:
        _avsr_path()
        from preparation.detectors.mediapipe.video_process import VideoProcess

        self.keypoints = FaceKeypoints()
        self.video_process = VideoProcess(convert_gray=False)

    def __call__(self, frames_rgb) -> tuple[np.ndarray, int]:
        landmarks = self.keypoints(frames_rgb)
        found = sum(lm is not None for lm in landmarks)
        if found == 0:
            raise RuntimeError("No face detected in any frame. Face the camera, in good light.")
        crops = self.video_process(frames_rgb, landmarks)
        if crops is None:
            raise RuntimeError("Mouth crop failed (face too close to the frame edge?)")
        return crops, found

    def close(self) -> None:
        self.keypoints.close()
