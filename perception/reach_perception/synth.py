"""SYNTHETIC hand-landmark sequences for --selftest and pytest (no camera).

These are generated, not recorded from a real hand. `python -m reach_perception.synth` rewrites the JSON fixtures in
reach_perception/fixtures/. Each fixture: {"name", "synthetic": true, "frame": [w, h], "fps", "frames": [[t_ms, lm|null]]}.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

W, H, FPS = 640, 480, 30
FIXTURES = Path(__file__).parent / "fixtures"
S = 100.0  # hand size in pixels (wrist -> middle MCP)


def hand(cx: float, cy: float, pinch: float = 0.8, palm: bool = False) -> list[list[float]]:
    """21 landmarks, normalised. (cx, cy) is the wrist in pixels. `pinch` = thumb-index distance / hand size.
    palm=False: pointing pose (index out, others curled). palm=True: all fingers and thumb spread."""
    mcp = {5: (-25, -95), 9: (0, -100), 13: (20, -95), 17: (40, -85)}
    pts: dict[int, tuple[float, float]] = {0: (0, 0)}
    for base in (5, 9, 13, 17):
        mx, my = mcp[base]
        extended = palm or base == 5
        pts[base] = (mx, my)
        pts[base + 1] = (mx, my - 40)  # PIP
        if extended:
            pts[base + 2], pts[base + 3] = (mx, my - 62), (mx, my - 80)
        else:  # curled: tip folds back toward the palm
            pts[base + 2], pts[base + 3] = (mx, my - 30), (mx, my - 15)
    ix, iy = pts[8]
    if palm:
        tip = (-95, -70)
    else:  # thumb tip sits `pinch * S` away from the index tip, toward lower-left
        ang = math.radians(225)
        tip = (ix + math.cos(ang) * pinch * S, iy - math.sin(ang) * pinch * S)
    pts[1], pts[2], pts[3], pts[4] = (-30, -20), (-50, -45), ((-50 + tip[0]) / 2, (-45 + tip[1]) / 2), tip
    return [[(cx + pts[i][0]) / W, (cy + pts[i][1]) / H, 0.0] for i in range(21)]


def _seq(n: int, fn, t0: int = 0) -> list:
    dt = 1000 / FPS
    return [[round(t0 + i * dt), fn(i)] for i in range(n)]


def clean_pinch() -> list:
    rnd = random.Random(1)
    j = lambda: rnd.uniform(-1.0, 1.0)  # noqa: E731  ~1 px landmark noise
    a = _seq(15, lambda i: hand(320 + j(), 330 + j(), 0.8))  # hover 0.5 s
    b = _seq(6, lambda i: hand(320 + j(), 330 + j(), 0.8 - 0.12 * i), a[-1][0] + 33)  # close
    c = _seq(25, lambda i: hand(320 - 4 * i + j(), 330 - 2 * i + j(), 0.08), b[-1][0] + 33)  # drag left-up
    d = _seq(15, lambda i: hand(220 + j(), 280 + j(), 0.8), c[-1][0] + 33)  # open
    return a + b + c + d


def jitter_no_pinch() -> list:
    """Normal movement with noisy pinch ratio between 0.28 and 0.6 and one-frame dips to 0.2 (below PINCH_ON)."""
    rnd = random.Random(2)
    out = []
    for i in range(300):  # 10 s
        r = rnd.uniform(0.28, 0.6)
        if i % 37 == 0:
            r = 0.2  # single-frame dip: shorter than the 150 ms hold
        cx = 320 + 60 * math.sin(i / 20) + rnd.uniform(-3, 3)
        cy = 330 + 30 * math.cos(i / 15) + rnd.uniform(-3, 3)
        out.append([round(i * 1000 / FPS), hand(cx, cy, r)])
    return out


def open_palm_10s() -> list:
    rnd = random.Random(3)
    return _seq(300, lambda i: hand(320 + rnd.uniform(-4, 4), 330 + rnd.uniform(-4, 4), palm=True))


def tracking_loss() -> list:
    a = _seq(10, lambda i: hand(320, 330, 0.8))
    b = _seq(15, lambda i: hand(320 - 3 * i, 330, 0.08), a[-1][0] + 33)  # pinch and start a drag
    c = _seq(45, lambda i: None, b[-1][0] + 33)  # hand gone 1.5 s
    return a + b + c


def all_fixtures() -> dict[str, list]:
    return {"clean_pinch": clean_pinch(), "jitter_no_pinch": jitter_no_pinch(),
            "open_palm_10s": open_palm_10s(), "tracking_loss": tracking_loss()}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    for name, frames in all_fixtures().items():
        for f in frames:
            if f[1]:
                f[1] = [[round(v, 5) for v in p] for p in f[1]]
        doc = {"name": name, "synthetic": True, "frame": [W, H], "fps": FPS, "frames": frames}
        (FIXTURES / f"{name}.json").write_text(json.dumps(doc, separators=(",", ":")))
        print(name, len(frames), "frames")


if __name__ == "__main__":
    main()
