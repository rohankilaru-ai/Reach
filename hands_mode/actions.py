"""Hands mode actions: Frames -> cursor move, click, drag, double click, scroll, exit.

The engine is pure (injected clock and mouse) so it is unit tested without a camera or real
events. Real mouse events are posted only by MacActionMouse, which run.py uses only with --control.

Safety: the button is released on tracking loss, stalled frames, mode exit, takeover (you touched
the real mouse/trackpad), errors and signals, and never held for more than max_hold_s.
"""

from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass

from cursor_demo.run import MacMouse, _Point

# --------------------------------------------------------------------------- mouse backends


class MacActionMouse(MacMouse):
    """MacMouse plus button and scroll events (CoreGraphics via ctypes)."""

    LEFT_DOWN, LEFT_UP, LEFT_DRAGGED = 1, 2, 6
    CLICK_STATE_FIELD = 1          # kCGMouseEventClickState
    SCROLL_UNIT_LINE = 1           # kCGScrollEventUnitLine

    def __init__(self):
        super().__init__()
        cg = self._cg
        cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
        # CGEventCreateScrollWheelEvent is variadic, which ctypes gets wrong on arm64; the "2" variant is not.
        cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
        cg.CGEventCreateScrollWheelEvent2.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                                      ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        self.can_click = self.trusted  # without Accessibility, CGEventPost of button events is dropped

    def _post_button(self, kind: int, x: float, y: float, clicks: int = 1) -> None:
        e = self._cg.CGEventCreateMouseEvent(None, kind, _Point(x, y), 0)
        self._cg.CGEventSetIntegerValueField(e, self.CLICK_STATE_FIELD, clicks)
        self._cg.CGEventPost(self.HID_EVENT_TAP, e)
        self._cf.CFRelease(e)

    def down(self, x: float, y: float, clicks: int = 1) -> None:
        self._post_button(self.LEFT_DOWN, x, y, clicks)

    def up(self, x: float, y: float, clicks: int = 1) -> None:
        self._post_button(self.LEFT_UP, x, y, clicks)

    def drag(self, x: float, y: float) -> None:
        self._post_button(self.LEFT_DRAGGED, x, y)

    def scroll(self, lines: int) -> None:
        e = self._cg.CGEventCreateScrollWheelEvent2(None, self.SCROLL_UNIT_LINE, 1, int(lines), 0, 0)
        self._cg.CGEventPost(self.HID_EVENT_TAP, e)
        self._cf.CFRelease(e)


class PrintMouse:
    """Dry run: records and prints the actions it would take; moves nothing."""

    mode = "dry-run"
    can_click = True

    def __init__(self, echo: bool = True):
        self.echo = echo
        self.log: list[tuple] = []
        self._pos = (0.0, 0.0)

    def _rec(self, *a) -> None:
        self.log.append(a)
        if self.echo and a[0] != "move":
            print("[dry-run]", " ".join(str(v) for v in a), flush=True)

    def position(self) -> tuple[float, float]:
        return self._pos

    def move(self, x, y) -> None:
        self._pos = (x, y)
        self._rec("move", int(x), int(y))

    def down(self, x, y, clicks=1) -> None:
        self._rec("down", int(x), int(y), f"clicks={clicks}")

    def up(self, x, y, clicks=1) -> None:
        self._rec("up", int(x), int(y), f"clicks={clicks}")

    def drag(self, x, y) -> None:
        self._pos = (x, y)
        self._rec("drag", int(x), int(y))

    def scroll(self, lines) -> None:
        self._rec("scroll", int(lines))


# --------------------------------------------------------------------------- engine


def is_pointing(frame) -> bool:
    f = frame.fingers
    return f["index"].extended and not (f["middle"].extended or f["ring"].extended or f["pinky"].extended)


def is_two_finger(frame) -> bool:
    f = frame.fingers
    return f["index"].extended and f["middle"].extended and not (f["ring"].extended or f["pinky"].extended)


def near_edge(frame, margin: float) -> bool:
    """Any joint within `margin` of the image border: a half-visible hand, unsafe for clicks."""
    return any(not (margin <= x <= 1 - margin and margin <= y <= 1 - margin) for x, y, *_ in frame.landmarks)


def is_open_palm(frame) -> bool:
    return all(f.extended for f in frame.fingers.values())


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


class HandsEngine:
    def __init__(self, mouse, cfg: ActionCfg | None = None, clock=time.monotonic):
        self.mouse, self.cfg, self.clock = mouse, cfg or ActionCfg(), clock
        self.active = True
        self.state = "waiting for hand"
        self.button_down = False
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
        self._pending: tuple[float, float] | None = None   # pinch seen for one frame; pressed on the second
        self.target: tuple[int, int] | None = None

    # -- safety ---------------------------------------------------------------
    def release(self, reason: str = "") -> None:
        """Release a held button at once. Safe to call any time, any number of times."""
        self._pending = None
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
        if self.button_down and now - self._down_t > c.max_hold_s:
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
        if edge == "down" and not self.button_down:   # debounce: a pinch must last 2 frames to count
            self._pending = (float(sx), float(sy))
            self.state = "pinch"
            return self.state
        pending, self._pending = self._pending, None
        if pending is not None and frame.pinch_active and not self.button_down:
            sx, sy = pending                          # press where the pinch began (pointer was frozen there)
            if now < self._no_pinch_until:
                self.state = "hand leaving · pinch ignored"
            elif not self.mouse.can_click:
                self.state = "click needs Accessibility permission"
            elif now - self._last_down_t >= c.min_down_interval_s:
                lu = self._last_up
                double = (lu is not None and now - lu[0] <= c.double_click_s
                          and math.hypot(sx - lu[1], sy - lu[2]) <= c.double_click_px)
                if double:
                    sx, sy = lu[1], lu[2]   # second click lands exactly on the first
                self._clicks = 2 if double else 1
                self.mouse.down(sx, sy, self._clicks)
                self.button_down, self._dragging = True, False
                self._down_at, self._down_t, self._last_down_t = (float(sx), float(sy)), now, now
                self._last_set = (float(sx), float(sy))
                self.state = "double click" if double else "pinch · click"
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
            self._move(sx, sy)
            self.state = "point to move · pinch to click"
            return self.state
        self.state = "clutch · point to move"
        return self.state
