"""Map normalised (mirrored) camera coordinates to screen points.

Two mappings:
- Fixed (default, no calibration): the central box CAM_BOX of the camera view stretches to the whole screen.
- Calibrated: a 4-corner reach calibration (top-left, top-right, bottom-right, bottom-left), saved once to
  .reach/calibration.json, solved as a perspective homography.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CAM_BOX = (0.15, 0.15, 0.85, 0.85)  # x0, y0, x1, y1 in normalised camera coords for the fixed mapping
CORNERS = ("top-left", "top-right", "bottom-right", "bottom-left")


def reach_dir() -> Path:
    """Same rule as backend/reach/paths.py: $REACH_DIR or <repo>/.reach."""
    return Path(os.environ.get("REACH_DIR", Path(__file__).resolve().parents[2] / ".reach"))


def calibration_path() -> Path:
    return reach_dir() / "calibration.json"


def screen_size() -> tuple[float, float]:
    """Main display size in points (Quartz); falls back to 1440x900 off macOS."""
    try:
        import Quartz

        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return float(b.size.width), float(b.size.height)
    except Exception:
        return 1440.0, 900.0


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (8x8 here); avoids a numpy dependency in the pure core."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("degenerate calibration (corners collinear or repeated)")
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return _solve(a, b) + [1.0]


class ScreenMap:
    def __init__(self, screen_w: float, screen_h: float, cam_corners: list[tuple[float, float]] | None = None):
        self.sw, self.sh = screen_w, screen_h
        self.h: list[float] | None = None
        if cam_corners:
            dst = [(0, 0), (screen_w, 0), (screen_w, screen_h), (0, screen_h)]
            self.h = homography(list(cam_corners), dst)

    @property
    def calibrated(self) -> bool:
        return self.h is not None

    def to_screen(self, p: tuple[float, float]) -> tuple[float, float]:
        x, y = p
        if self.h:
            h = self.h
            d = h[6] * x + h[7] * y + h[8]
            u, v = (h[0] * x + h[1] * y + h[2]) / d, (h[3] * x + h[4] * y + h[5]) / d
        else:
            x0, y0, x1, y1 = CAM_BOX
            u, v = (x - x0) / (x1 - x0) * self.sw, (y - y0) / (y1 - y0) * self.sh
        return min(max(u, 0.0), self.sw), min(max(v, 0.0), self.sh)

    def rect_to_screen(self, a: tuple[float, float], b: tuple[float, float]) -> list[float]:
        (ux, uy), (vx, vy) = self.to_screen(a), self.to_screen(b)
        x0, x1 = sorted((ux, vx))
        y0, y1 = sorted((uy, vy))
        return [round(x0), round(y0), round(x1 - x0), round(y1 - y0)]

    def save(self, cam_corners: list[tuple[float, float]], path: Path | None = None) -> Path:
        path = path or calibration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"corners": dict(zip(CORNERS, map(list, cam_corners))),
                                    "screen": [self.sw, self.sh]}, indent=1))
        return path

    @classmethod
    def load(cls, screen_w: float, screen_h: float, path: Path | None = None) -> "ScreenMap":
        path = path or calibration_path()
        try:
            data = json.loads(path.read_text())
            corners = [tuple(data["corners"][k]) for k in CORNERS]
            return cls(screen_w, screen_h, corners)
        except (OSError, ValueError, KeyError):
            return cls(screen_w, screen_h)


def build_map(sw: float, sh: float, corners: list[tuple[float, float]]) -> tuple["ScreenMap", str | None]:
    """ScreenMap from calibration corners; a bad calibration falls back to the fixed mapping plus a warning."""
    try:
        return ScreenMap(sw, sh, corners), None
    except (ValueError, ZeroDivisionError) as exc:
        return ScreenMap(sw, sh), f"calibration rejected ({exc}); using fixed mapping"


def reset_calibration(sw: float, sh: float, path: Path | None = None) -> "ScreenMap":
    """Fixed mapping, and the saved calibration file is deleted so it does not come back on restart."""
    try:
        (path or calibration_path()).unlink(missing_ok=True)
    except OSError:
        pass
    return ScreenMap(sw, sh)


def box_around(p: tuple[float, float], sw: float, sh: float, w: float = 480, h: float = 160) -> list[float]:
    """Fallback selection when the drag is tiny: a fixed box centred on the pointer, clamped to the screen."""
    x = min(max(p[0] - w / 2, 0), max(sw - w, 0))
    y = min(max(p[1] - h / 2, 0), max(sh - h, 0))
    return [round(x), round(y), round(min(w, sw)), round(min(h, sh))]


MIN_LOOP = 40.0  # points: a loop smaller than this on both sides falls back to the rect
MAX_PATH = 64


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
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


def loop_path(screen_pts: list[tuple[float, float]]) -> list[list[float]] | None:
    """A pinch-drag trail (screen points) -> simplified closed loop (<= 64 points), or None for a tiny/straight drag."""
    if len(screen_pts) < 3:
        return None
    xs, ys = [p[0] for p in screen_pts], [p[1] for p in screen_pts]
    ext = max(max(xs) - min(xs), max(ys) - min(ys))
    if ext < MIN_LOOP:
        return None
    eps = ext / 500
    out = _rdp(screen_pts, eps)
    while len(out) > MAX_PATH:
        eps *= 1.5
        out = _rdp(screen_pts, eps)
    if len(out) < 3:
        return None
    return [[round(x, 1), round(y, 1)] for x, y in out]


def path_bbox(path: list[list[float]]) -> list[float]:
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
