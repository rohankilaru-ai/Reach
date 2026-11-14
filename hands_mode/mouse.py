"""Mouse backends: MacActionMouse posts real CoreGraphics events (macOS); PrintMouse is the dry run."""

from __future__ import annotations

import ctypes
import ctypes.util


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class MacMouse:
    """Moves the cursor through CoreGraphics (no extra dependencies)."""

    MOUSE_MOVED = 5
    HID_EVENT_TAP = 0

    def __init__(self):
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        ax = ctypes.CDLL(ctypes.util.find_library("ApplicationServices"))
        cg.CGEventCreate.restype = ctypes.c_void_p
        cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = _Point
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _Point, ctypes.c_uint32]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cg.CGWarpMouseCursorPosition.argtypes = [_Point]
        cg.CGAssociateMouseAndMouseCursorPosition.argtypes = [ctypes.c_bool]
        cg.CGSetLocalEventsSuppressionInterval.argtypes = [ctypes.c_double]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        self._cg, self._cf = cg, cf
        self.trusted = bool(ax.AXIsProcessTrusted())
        self.mode = "post" if self.trusted else "warp"
        if self.mode == "warp":
            # A warp normally freezes the physical trackpad for 0.25 s; disable that so the user can take over.
            cg.CGSetLocalEventsSuppressionInterval(0.0)

    def position(self) -> tuple[float, float]:
        e = self._cg.CGEventCreate(None)
        p = self._cg.CGEventGetLocation(e)
        self._cf.CFRelease(e)
        return p.x, p.y

    def move(self, x: float, y: float) -> None:
        if self.mode == "post":
            e = self._cg.CGEventCreateMouseEvent(None, self.MOUSE_MOVED, _Point(x, y), 0)
            self._cg.CGEventPost(self.HID_EVENT_TAP, e)
            self._cf.CFRelease(e)
        else:
            self._cg.CGWarpMouseCursorPosition(_Point(x, y))
            self._cg.CGAssociateMouseAndMouseCursorPosition(True)


class MacActionMouse(MacMouse):
    """MacMouse plus button and scroll events."""

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
