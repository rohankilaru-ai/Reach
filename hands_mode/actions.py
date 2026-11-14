"""Hands mode engine: Frames -> cursor move, click, drag, double click, select, scroll, exit.

Pure (injected mouse, clock and selector), so it is unit tested without a camera or real events.
Safety: the button is released (and a selection cancelled) on hand loss, stalled frames, the hand
dropping out or reaching the frame edge, takeover (you touched the real mouse), mode exit, and after
max_hold_s.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass


def is_pointing(frame) -> bool:
    f = frame.fingers
    return f["index"].extended and not (f["middle"].extended or f["ring"].extended or f["pinky"].extended)


def is_index_curl(frame) -> bool:
    """Index bent, the other three curled: the pose a tap from pointing passes through."""
    f = frame.fingers
    return not (f["index"].extended or f["middle"].extended or f["ring"].extended or f["pinky"].extended)


def is_two_finger(frame) -> bool:
    f = frame.fingers
    return f["index"].extended and f["middle"].extended and not (f["ring"].extended or f["pinky"].extended)


def is_open_palm(frame) -> bool:
    return all(f.extended for f in frame.fingers.values())


def near_edge(frame, margin: float) -> bool:
    """Any joint within `margin` of the image border: a half-visible hand, unsafe for clicks."""
    return any(not (margin <= x <= 1 - margin and margin <= y <= 1 - margin) for x, y, *_ in frame.landmarks)


@dataclass
class ActionCfg:
    takeover_px: float = 25.0
    takeover_pause_s: float = 2.0
    max_hold_s: float = 20.0
    min_down_interval_s: float = 0.25   # click cooldown / rate limit (a deliberate double pinch is slower)
    double_click_s: float = 0.5
    double_click_px: float = 40.0
    drag_threshold_px: float = 12.0     # below this, a pinch is a click at the down point
    dead_zone_px: float = 2.0           # still hand: ignore cursor moves this small (no shiver)
    palm_exit_s: float = 1.0
    scroll_gain: float = 30.0           # lines per (normalized unit / s) per frame step
    scroll_deadzone: float = 0.15       # normalized units / s
    scroll_min_interval_s: float = 0.05
    scroll_invert: bool = False         # default: fingers up = content up, like a touch screen
    edge_margin: float = 0.03           # joints this close to the frame border: no click decisions
    drop_speed: float = 1.2             # downward pointer speed (normalized / s) = hand leaving the frame
    drop_lockout_s: float = 0.5         # after a drop, no new pinch for this long
    curl_deg: float = 135.0             # curl click: index middle-joint angle below this (idle pointing sits ~145-150)
    curl_max_s: float = 0.5             # ... reached within this long of leaving the pointing pose (a sag is not a tap)
    pre_curl_s: float = 0.12            # ... clicks where the pointer was this long before the curl began
    curl_pinch_guard: float = 0.30      # thumb this close to the index tip may be a pinch closing: wait one frame


class HandsEngine:
    def __init__(self, mouse, cfg: ActionCfg | None = None, clock=time.monotonic, selector=None):
        self.mouse, self.cfg, self.clock, self.selector = mouse, cfg or ActionCfg(), clock, selector
        self.active = True
        self.state = "waiting for hand"
        self.button_down = False
        self.selecting = False
        self.target: tuple[int, int] | None = None
        self._down_at: tuple[float, float] | None = None
        self._down_t = 0.0
        self._dragging = False
        self._clicks = 1
        self._last_down_t = -1e9
        self._last_up: tuple[float, float, float] | None = None   # t, x, y
        self._last_set: tuple[float, float] | None = None
        self._paused_until = 0.0
        self._palm_since: float | None = None
        self._last_scroll_t = -1e9
        self._no_pinch_until = -1e9
        self._pending: tuple[float, float, str] | None = None     # pinch seen for one frame: x, y, finger
        self._trail: deque[tuple[float, float, float]] = deque(maxlen=30)   # t, x, y while pointing
        self._curl_from: float | None = None                      # when the index left the pointing pose
        self._curl_waited = False                                  # deferred one frame for a possible pinch

    # -- safety ---------------------------------------------------------------
    def release(self, reason: str = "") -> None:
        """Release a held button and cancel a selection at once. Safe to call any time."""
        self._pending, self._curl_from = None, None
        if self.selecting:
            self.selecting = False
            self.selector.cancel()
        if self.button_down:
            x, y = self._dragging and self.target or self._down_at or self.mouse.position()
            try:
                self.mouse.up(x, y, self._clicks)
            finally:
                self.button_down, self._dragging = False, False
                self._last_up = (self.clock(), float(x), float(y))
        if reason:
            self.state = reason

    def stop(self, reason: str = "hands mode off") -> None:
        self.release()
        self.active = False
        self.state = reason

    def no_frames(self) -> None:
        """Frames stalled or the source ended: never keep a button down blind."""
        self.release("no frames")

    def _moved_by_user(self) -> bool:
        if self._last_set is None or self.mouse.mode == "dry-run":
            return False
        cx, cy = self.mouse.position()
        return math.hypot(cx - self._last_set[0], cy - self._last_set[1]) > self.cfg.takeover_px

    def _move(self, x: float, y: float) -> None:
        if (not self.button_down and self._last_set is not None
                and math.hypot(x - self._last_set[0], y - self._last_set[1]) < self.cfg.dead_zone_px):
            return
        if self.button_down:
            self.mouse.drag(x, y)
        else:
            self.mouse.move(x, y)
        self._last_set = (float(x), float(y))
        self.target = (int(x), int(y))

    def _may_press(self, now: float) -> bool:
        """Lockouts shared by pinch and curl clicks; sets the state when a press is refused."""
        if now < self._no_pinch_until:
            self.state = "hand leaving · pinch ignored"
        elif not self.mouse.can_click:
            self.state = "click needs Accessibility permission"
        else:
            return now - self._last_down_t >= self.cfg.min_down_interval_s
        return False

    def _press(self, x: float, y: float, now: float) -> None:
        lu, c = self._last_up, self.cfg
        double = (lu is not None and now - lu[0] <= c.double_click_s
                  and math.hypot(x - lu[1], y - lu[2]) <= c.double_click_px)
        if double:
            x, y = lu[1], lu[2]   # second click lands exactly on the first
        self._clicks = 2 if double else 1
        self.mouse.down(x, y, self._clicks)
        self.button_down, self._dragging = True, False
        self._down_at, self._down_t, self._last_down_t = (float(x), float(y)), now, now
        self._last_set = (float(x), float(y))
        self.state = "double click" if double else "pinch · click"

    def _pre_curl_point(self) -> tuple[float, float]:
        cutoff = self._curl_from - self.cfg.pre_curl_s
        before = [p for p in self._trail if p[0] <= cutoff]
        t, x, y = before[-1] if before else self._trail[0]
        return x, y

    # -- per frame --------------------------------------------------------------
    def update(self, frame) -> str:
        if not self.active:
            return self.state
        now, c = self.clock(), self.cfg
        if self._moved_by_user():
            self.release()
            self._paused_until, self._last_set = now + c.takeover_pause_s, None
        if now < self._paused_until:
            self.state = "paused · you moved the mouse"
            return self.state
        if (self.button_down or self.selecting) and now - self._down_t > c.max_hold_s:
            self.release("released · held too long")
            return self.state
        if not frame.hand_present or frame.pointer_screen is None:
            self.release("no hand")
            self._palm_since = None
            return self.state

        # hand dropping fast toward the bottom edge, or half out of frame: release, no clicks
        if frame.velocity[1] > c.drop_speed:
            self._no_pinch_until = now + c.drop_lockout_s
            self.release("hand leaving · released")
            return self.state
        if near_edge(frame, c.edge_margin):
            self.release("hand at the edge")
            return self.state

        # open palm held = leave hands mode
        if is_open_palm(frame) and not frame.pinch_active:
            self.release()
            self._palm_since = self._palm_since or now
            if now - self._palm_since >= c.palm_exit_s:
                self.stop("hands mode off · open palm")
                return self.state
            self.state = "open palm · hold to leave"
            return self.state
        self._palm_since = None

        sx, sy = frame.pointer_screen
        edge = frame.pinch_edge

        # middle pinch held: draw the selection; the pointer moves freely
        if self.selecting:
            if edge == "up" or not frame.pinch_active:
                self.selecting = False
                self.selector.finish()
                self.state = "selected"
            else:
                self._move(sx, sy)
                self.selector.add(sx, sy)
                self.state = "selecting"
            return self.state

        if edge == "down" and not self.button_down:   # debounce: a pinch must last 2 frames to count
            self._pending = (float(sx), float(sy), frame.pinch_finger)
            self._curl_from = None
            self.state = "pinch"
            return self.state
        pending, self._pending = self._pending, None
        if pending is not None and frame.pinch_active and not self.button_down:
            px, py, finger = pending                  # act where the pinch began (pointer was frozen there)
            if finger == "middle":
                if self.selector is not None and now >= self._no_pinch_until:
                    self.selecting, self._down_t = True, now
                    self.selector.begin(px, py)
                    self.state = "selecting"
                return self.state
            if self._may_press(now):
                self._press(px, py, now)
                return self.state
        if self.button_down and (edge == "up" or not frame.pinch_active):
            self.release("click" if not self._dragging else "drag · released")
            return self.state
        if self.button_down:
            dx, dy = sx - self._down_at[0], sy - self._down_at[1]
            if self._dragging or math.hypot(dx, dy) > c.drag_threshold_px:
                self._dragging = True
                self._move(sx, sy)
                self.state = "dragging"
            return self.state
        if frame.pinch_active:   # a pinch that did not press (lockout, middle without selector): do nothing
            return self.state

        if is_two_finger(frame):
            vy = frame.scroll_velocity[1]
            settled = self._last_up is None or now - self._last_up[0] > 0.3   # fingers spring apart after a pinch
            if settled and abs(vy) > c.scroll_deadzone and now - self._last_scroll_t >= c.scroll_min_interval_s:
                # image y grows downward: fingers moving up (vy < 0) -> content up -> wheel negative
                lines = int(round(vy * c.scroll_gain * c.scroll_min_interval_s * 10)) or (1 if vy > 0 else -1)
                lines = max(-15, min(15, -lines if c.scroll_invert else lines))
                self.mouse.scroll(lines)
                self._last_scroll_t = now
                self.state = "scrolling"
            else:
                self.state = "two fingers · move up or down to scroll"
            return self.state

        if is_pointing(frame):
            self._curl_from, self._curl_waited = None, False
            self._trail.append((now, float(sx), float(sy)))
            self._move(sx, sy)
            self.state = "point to move · pinch to click"
            return self.state

        # curl click: from pointing, the index bends sharply and quickly (a tap), not a flicker or a slow sag
        if is_index_curl(frame) and self._trail and now - self._trail[-1][0] <= c.curl_max_s:
            self._curl_from = self._curl_from or self._trail[-1][0]
            if now - self._curl_from > c.curl_max_s:
                self._trail.clear()                   # a slow sag: point again to re-arm
            elif frame.finger_angles.get("index", 180.0) < c.curl_deg:
                if frame.pinch_dist < c.curl_pinch_guard and not self._curl_waited:
                    self._curl_waited = True          # maybe a pinch about to register; the pinch path wins if so
                    self.state = "curl"
                    return self.state
                self._curl_waited = False
                x, y = self._pre_curl_point()
                self._trail.clear()                   # one click per curl
                if self._may_press(now):
                    self._press(x, y, now)
                    self.release("curl · click")
                return self.state
            self.state = "curl"
            return self.state
        self._trail.clear()
        self.state = "clutch · point to move"
        return self.state
