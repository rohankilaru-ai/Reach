"""Hand signals while hands mode is on: thumbs-up and a spread peace sign, each held steadily.

Pure (frames + a clock value in, a signal name out). The engine never acts on a signal itself: run.py prints
`GESTURE <name>` and the overlay decides what it means. Off with REACH_HAND_SIGNALS=0.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

SIGNALS = ("thumbs_up", "peace")


def enabled(env=None) -> bool:
    return (os.environ if env is None else env).get("REACH_HAND_SIGNALS", "1") != "0"


def tip_spread(frame) -> float:
    """Index tip to middle tip over index knuckle to middle knuckle (landmarks 8-12 over 5-9), 0 when unknown.
    A ratio of two near-parallel gaps in the same normalized image coords, so the camera's aspect cancels."""
    lm = frame.landmarks
    if len(lm) < 13:
        return 0.0
    knuckles = math.hypot(lm[5][0] - lm[9][0], lm[5][1] - lm[9][1])
    if knuckles < 1e-6:
        return 0.0
    return math.hypot(lm[8][0] - lm[12][0], lm[8][1] - lm[12][1]) / knuckles


def _all(frame, names):
    f = getattr(frame, "fingers", None) or {}
    return f if all(n in f for n in names) else None


def is_thumbs_up(frame) -> bool:
    f = _all(frame, ("thumb", "index", "middle", "ring", "pinky"))
    return bool(f) and f["thumb"].extended and not any(f[n].extended for n in ("index", "middle", "ring", "pinky"))


def is_peace(frame, spread: float = 1.8) -> bool:
    """Index and middle up and apart, ring and pinky curled. Together (the scroll pose) is not a peace sign."""
    f = _all(frame, ("index", "middle", "ring", "pinky"))
    return (bool(f) and f["index"].extended and f["middle"].extended and not (f["ring"].extended or f["pinky"].extended)
            and tip_spread(frame) > spread)


def pose_of(frame, spread: float = 1.8) -> str | None:
    if is_peace(frame, spread):
        return "peace"
    if is_thumbs_up(frame):
        return "thumbs_up"
    return None


@dataclass
class SignalCfg:
    hold_s: float = 0.6
    cooldown_s: float = 1.5
    spread: float = 1.8        # peace: fingertips apart by more than this many knuckle gaps (~0.5 hand scale)


class SignalWatcher:
    def __init__(self, cfg: SignalCfg | None = None):
        self.cfg = cfg or SignalCfg()
        self._pose: str | None = None
        self._since = 0.0
        self._fired = False
        self._quiet_until = -1e9

    def reset(self) -> None:
        self._pose, self._fired = None, False

    def update(self, frame, now: float, busy: bool = False) -> str | None:
        """One frame; returns a signal name the moment one has been held for hold_s, else None.
        `busy`: the engine holds a button or draws a selection. A pinch or a missing hand also resets."""
        if (busy or not frame.hand_present or frame.pointer_screen is None or frame.pinch_active
                or frame.pinch_edge is not None or getattr(frame, "source", "landmarker") == "hold"):
            self.reset()
            return None
        pose = pose_of(frame, self.cfg.spread)
        if pose != self._pose:
            self._pose, self._since, self._fired = pose, now, False
            return None
        if pose is None or self._fired or now < self._quiet_until or now - self._since < self.cfg.hold_s:
            return None
        self._fired = True                       # once per hold: lower the hand to signal again
        self._quiet_until = now + self.cfg.cooldown_s
        return pose
