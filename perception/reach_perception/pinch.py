"""Gesture state machine: smoothed pointer, pinch with hysteresis, open-palm pause, tracking loss, drag rect.

Pure Python. No camera, cv2 or mediapipe imports; time is injected (milliseconds), so tests are deterministic.
Landmarks are MediaPipe's 21 hand landmarks as (x, y, z) normalised to the camera frame, NOT mirrored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# MediaPipe hand landmark indices
WRIST, THUMB_TIP, INDEX_MCP, INDEX_PIP, INDEX_TIP, MIDDLE_MCP = 0, 4, 5, 6, 8, 9
FINGERS = ((6, 8), (10, 12), (14, 16), (18, 20))  # (pip, tip) for index, middle, ring, pinky

# Pinch ratio = |thumb tip - index tip| / |wrist - middle MCP|, both in pixels.
PINCH_ON = 0.25  # ratio must drop below this ...
PINCH_HOLD_MS = 150  # ... and stay below it this long before the pinch counts (PLAN.md S6)
PINCH_OFF = 0.40  # a held pinch ends only when the ratio rises above this ...
RELEASE_HOLD_MS = 80  # ... for this long (stops a one-frame flicker from ending a drag)
PALM_HOLD_MS = 400  # open palm held this long fires one local `pause`
PALM_CLEAR_MS = 300  # palm must be gone this long before another pause can fire
LOST_MS = 1000  # no hand this long: hide pointer, cancel drag
MIN_DRAG = 0.02  # normalised; smaller drags become a fixed box around the pointer (see worker)


@dataclass
class Event:
    kind: str  # pinch_down | select | pause | lost | found
    t_ms: int
    data: dict = field(default_factory=dict)


class OneEuro:
    """One-Euro filter (Casiez et al. 2012) for one coordinate."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.02, d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x: float | None = None
        self.dx = 0.0
        self.t: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        self.x, self.t, self.dx = None, None, 0.0

    def __call__(self, x: float, t_s: float) -> float:
        if self.x is None or self.t is None or t_s <= self.t:
            self.x, self.t = x, t_s
            return x
        dt = t_s - self.t
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        a = self._alpha(self.min_cutoff + self.beta * abs(self.dx), dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t_s
        return self.x


def _dist(a, b, w: float, h: float) -> float:
    return math.hypot((a[0] - b[0]) * w, (a[1] - b[1]) * h)


def pinch_ratio(lm, w: float = 640, h: float = 480) -> float:
    size = _dist(lm[WRIST], lm[MIDDLE_MCP], w, h) or 1e-6
    return _dist(lm[THUMB_TIP], lm[INDEX_TIP], w, h) / size


def is_open_palm(lm, w: float = 640, h: float = 480) -> bool:
    """All four fingers extended, thumb out, and not pinching."""
    for pip, tip in FINGERS:
        if _dist(lm[WRIST], lm[tip], w, h) < 1.15 * _dist(lm[WRIST], lm[pip], w, h):
            return False
    size = _dist(lm[WRIST], lm[MIDDLE_MCP], w, h) or 1e-6
    thumb_out = _dist(lm[THUMB_TIP], lm[INDEX_MCP], w, h) / size > 0.6
    return thumb_out and pinch_ratio(lm, w, h) > PINCH_OFF


def rect_from_points(a: tuple[float, float], b: tuple[float, float]) -> list[float]:
    """[x, y, w, h] with non-negative size from two opposite corners."""
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    return [x0, y0, x1 - x0, y1 - y0]


class GestureEngine:
    """Feed one frame at a time with `update`; read `.pointer`, `.tracking`, `.pinching`, `.paused_palm`."""

    def __init__(self, frame_w: float = 640, frame_h: float = 480, mirror: bool = True) -> None:
        self.w, self.h, self.mirror = frame_w, frame_h, mirror
        self.fx, self.fy = OneEuro(), OneEuro()
        self.pointer: tuple[float, float] | None = None  # normalised, mirrored to match the preview
        self.tracking = False
        self.pinching = False
        self.palm = False
        self.ratio: float | None = None
        self.drag_start: tuple[float, float] | None = None
        self.drag_last: tuple[float, float] | None = None
        self._last_seen: int | None = None
        self._below_since: int | None = None
        self._above_since: int | None = None
        self._palm_since: int | None = None
        self._palm_gone_since: int | None = None
        self._palm_fired = False

    def _reset_pinch(self) -> None:
        self.pinching = False
        self.drag_start = self.drag_last = None
        self._below_since = self._above_since = None

    def update(self, t_ms: int, lm) -> list[Event]:
        ev: list[Event] = []
        if lm is None:
            if self.tracking and self._last_seen is not None and t_ms - self._last_seen > LOST_MS:
                cancelled = self.drag_start is not None
                self.tracking = False
                self.pointer = None
                self.palm = False
                self._reset_pinch()
                self._palm_since = self._palm_gone_since = None
                self._palm_fired = False
                self.fx.reset(), self.fy.reset()
                ev.append(Event("lost", t_ms, {"drag_cancelled": cancelled}))
            return ev

        if not self.tracking:
            ev.append(Event("found", t_ms))
        self.tracking = True
        self._last_seen = t_ms
        tip = lm[INDEX_TIP]
        x = 1.0 - tip[0] if self.mirror else tip[0]
        self.pointer = (self.fx(x, t_ms / 1000), self.fy(tip[1], t_ms / 1000))
        self.ratio = pinch_ratio(lm, self.w, self.h)

        # Open palm: local pause, debounced, and it suppresses pinch.
        palm_now = is_open_palm(lm, self.w, self.h)
        if palm_now:
            self._palm_gone_since = None
            if self._palm_since is None:
                self._palm_since = t_ms
            if not self._palm_fired and t_ms - self._palm_since >= PALM_HOLD_MS:
                self._palm_fired = True
                self.palm = True
                ev.append(Event("pause", t_ms))
        else:
            self._palm_since = None
            if self._palm_fired:
                if self._palm_gone_since is None:
                    self._palm_gone_since = t_ms
                if t_ms - self._palm_gone_since >= PALM_CLEAR_MS:
                    self._palm_fired = False
                    self.palm = False
        if palm_now:
            self._reset_pinch()  # also clears a stale `_below_since` so an interrupted pinch cannot count
            return ev

        # Pinch with hysteresis and hold times.
        if not self.pinching:
            if self.ratio < PINCH_ON:
                if self._below_since is None:
                    self._below_since = t_ms
                if t_ms - self._below_since >= PINCH_HOLD_MS:
                    self.pinching = True
                    self._above_since = None
                    self.drag_start = self.drag_last = self.pointer
                    ev.append(Event("pinch_down", t_ms, {"at": self.pointer}))
            else:
                self._below_since = None
        else:
            if self.ratio > PINCH_OFF:
                if self._above_since is None:
                    self._above_since = t_ms
                if t_ms - self._above_since >= RELEASE_HOLD_MS:
                    start, end = self.drag_start, self.drag_last or self.pointer
                    self._reset_pinch()
                    if start is not None and end is not None:
                        ev.append(Event("select", t_ms, {"start": start, "end": end,
                                                         "rect": rect_from_points(start, end)}))
            else:
                self._above_since = None
                self.drag_last = self.pointer  # end of drag = last point while still pinched
        return ev
