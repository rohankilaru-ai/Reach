"""Capture a screen region with `screencapture` and OCR it locally with the macOS Vision framework.

Writes one PNG per selection to .reach/selection-<ts>.png (screen content, never camera frames).
`screencapture` needs Screen Recording permission for the terminal; without it the image is only the wallpaper/empty
and OCR returns nothing, which the preview reports as "pointer demo only".
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .screen_map import reach_dir


def capture_region(rect: list[float], out_dir: Path | None = None) -> Path | None:
    out_dir = out_dir or reach_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    x, y, w, h = (int(round(v)) for v in rect)
    if w < 2 or h < 2:
        return None
    path = out_dir / f"selection-{int(time.time() * 1000)}.png"
    try:
        subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)], check=True, timeout=5,
                       capture_output=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return path if path.exists() and path.stat().st_size > 0 else None


def ocr(path: Path) -> str:
    """Text in the image, top to bottom, via VNRecognizeTextRequest (on-device)."""
    import Vision
    from Foundation import NSURL

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        return ""
    obs = sorted(req.results() or [], key=lambda o: -o.boundingBox().origin.y)  # Vision origin is bottom-left
    lines = []
    for o in obs:
        cands = o.topCandidates_(1)
        if cands:
            lines.append(str(cands[0].string()))
    return "\n".join(lines)
