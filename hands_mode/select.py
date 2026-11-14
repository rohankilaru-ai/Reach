"""Pinch-to-select (EXPERIMENTAL): turn a held middle pinch into a screen selection and relay it to the overlay.

Points are screen points (finger_processing's pointer_screen, already calibrated). On release:
  a drawn loop  -> its bounding box, plus the simplified loop as `path`
  a tiny move   -> a fixed box around the point
  otherwise     -> the drag rectangle
The region is then captured (`screencapture`) and OCR'd (macOS Vision) on a background thread, and relayed through
`reach serve` (hands_mode/bridge.py). Nothing here blocks the camera loop. Without Screen Recording permission the
capture is empty and no text is read; without `reach serve` the selection stays local.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

MIN_DRAG = 24.0   # points: a release closer than this to the start (both axes) is a tap
MIN_LOOP = 40.0   # points: a trail smaller than this on both sides is not a loop
MAX_PATH = 64     # most points sent as a loop path
TAP_BOX = (480.0, 160.0)


# ---- geometry ---------------------------------------------------------------------------------------------------

def box_around(p: tuple[float, float], sw: float, sh: float, w: float = TAP_BOX[0], h: float = TAP_BOX[1]) -> list[int]:
    """A fixed box centred on the point, clamped to the screen."""
    x = min(max(p[0] - w / 2, 0), max(sw - w, 0))
    y = min(max(p[1] - h / 2, 0), max(sh - h, 0))
    return [round(x), round(y), round(min(w, sw)), round(min(h, sh))]


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker line simplification."""
    if len(pts) < 3:
        return pts
    (ax, ay), (bx, by) = pts[0], pts[-1]
    dx, dy = bx - ax, by - ay
    n = (dx * dx + dy * dy) ** 0.5
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / n if n else ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        if d > best:
            best, idx = d, i
    if best <= eps:
        return [pts[0], pts[-1]]
    return _rdp(pts[: idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)


def loop_path(pts: list[tuple[float, float]]) -> list[list[float]] | None:
    """A trail in screen points -> simplified loop (<= MAX_PATH points), or None for a tiny or straight trail."""
    if len(pts) < 3:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    ext = max(max(xs) - min(xs), max(ys) - min(ys))
    if ext < MIN_LOOP:
        return None
    eps = ext / 500
    out = _rdp(pts, eps)
    while len(out) > MAX_PATH:
        eps *= 1.5
        out = _rdp(pts, eps)
    if len(out) < 3:
        return None
    return [[round(x, 1), round(y, 1)] for x, y in out]


def path_bbox(path: list[list[float]]) -> list[float]:
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


# ---- macOS capture + OCR ----------------------------------------------------------------------------------------

def screen_size() -> tuple[float, float]:
    """Main display size in points (Quartz); 1440x900 off macOS."""
    try:
        import Quartz

        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return float(b.size.width), float(b.size.height)
    except Exception:  # noqa: BLE001
        return 1440.0, 900.0


def capture_region(rect: list[float], out_dir: Path) -> Path | None:
    """PNG of a screen region via `screencapture` (needs Screen Recording permission), or None."""
    x, y, w, h = (int(round(v)) for v in rect)
    if w < 2 or h < 2:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"selection-{int(time.time() * 1000)}.png"
    try:
        subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(path)], check=True, timeout=5,
                       capture_output=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return path if path.exists() and path.stat().st_size > 0 else None


def ocr(path: Path) -> str:
    """Text in the image, top to bottom, via on-device VNRecognizeTextRequest."""
    import Vision
    from Foundation import NSURL

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return ""
    obs = sorted(req.results() or [], key=lambda o: -o.boundingBox().origin.y)  # Vision origin is bottom-left
    return "\n".join(str(c[0].string()) for c in (o.topCandidates_(1) for o in obs) if c)


def _default_ocr(path: Path) -> str:
    return ocr(path)


def _default_capture(rect: list[float]) -> Path | None:
    from hands_mode.bridge import server_reach_dir  # same .reach the server checks image_path against

    return capture_region(rect, server_reach_dir() / "captures")


# ---- selector ---------------------------------------------------------------------------------------------------

class Selector:
    """begin/add/finish from the camera loop; capture, OCR and relay run on one background thread."""

    def __init__(self, bridge=None, capture=None, ocr=None, screen=None, max_pending: int = 4) -> None:
        if bridge is None:
            from hands_mode.bridge import BridgeClient

            bridge = BridgeClient()
        self._bridge = bridge
        self._capture = capture or _default_capture
        self._ocr = ocr or _default_ocr
        self._sw, self._sh = screen or screen_size()
        self._trail: list[tuple[float, float]] | None = None
        self._jobs: queue.Queue = queue.Queue(maxsize=max_pending)
        self._status: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def active(self) -> bool:
        return self._trail is not None

    def begin(self, x: float, y: float) -> None:
        self._trail = [(float(x), float(y))]

    def add(self, x: float, y: float) -> None:
        if self._trail is not None:
            self._trail.append((float(x), float(y)))

    def cancel(self) -> None:
        self._trail = None

    def finish(self) -> None:
        trail, self._trail = self._trail, None
        if not trail:
            return
        rect, path = self.region(trail)
        try:
            self._jobs.put_nowait((rect, path))
            self._status.put(f"select {rect}: reading...")
        except queue.Full:
            self._status.put(f"select {rect}: busy, dropped")

    def region(self, trail: list[tuple[float, float]]) -> tuple[list[int], list[list[float]] | None]:
        """The selection a trail means: (rect [x, y, w, h], loop path or None)."""
        path = loop_path(trail)
        if path:
            return [round(v) for v in path_bbox(path)], path
        (x0, y0), (x1, y1) = trail[0], trail[-1]
        if abs(x1 - x0) < MIN_DRAG and abs(y1 - y0) < MIN_DRAG:
            return box_around((x1, y1), self._sw, self._sh), None
        return [round(min(x0, x1)), round(min(y0, y1)), round(abs(x1 - x0)), round(abs(y1 - y0))], None

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            rect, path = job
            text, img = "", None
            try:
                img = self._capture(rect)
                text = self._ocr(img) if img else ""
            except Exception as exc:  # noqa: BLE001 - a failed read must not kill the thread
                self._status.put(f"select {rect}: read failed ({type(exc).__name__})")
            try:
                sent = self._bridge.select(rect, text or None, str(img) if img else None, path)
            except Exception:  # noqa: BLE001
                sent = False
            read = f"{len(text)} chars" if text else "no text (Screen Recording permission?)"
            self._status.put(f"selected · {read} sent" if sent else "selected · local only (no reach serve)")

    def poll(self) -> str | None:
        """The newest status line since the last poll, or None."""
        last = None
        while True:
            try:
                last = self._status.get_nowait()
            except queue.Empty:
                return last

    def close(self) -> None:
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        close = getattr(self._bridge, "close", None)
        if close:
            close()
