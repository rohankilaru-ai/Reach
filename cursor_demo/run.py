"""Cursor demo: the mouse cursor follows your index fingertip.

    python cursor_demo/run.py              move the real cursor (Ctrl+C to stop)
    python cursor_demo/run.py --preview    also show the camera window with the HUD
    python cursor_demo/run.py --dry-run    don't move the cursor, just print where it would go

How it behaves
  - Point (index up, middle/ring/pinky curled)  -> cursor follows the index fingertip.
  - Any other pose, or no hand                  -> cursor stays where it is ("clutch":
                                                   curl your finger, move your hand, point again).
  - Touch the trackpad/mouse                    -> demo pauses for 2 s so you can take over.
  - Ctrl+C in the terminal (or q in the preview) -> stop.

macOS: with Accessibility permission for your terminal app the demo posts real
mouse-move events (hover effects update); without it, it falls back to warping
the cursor, which moves it visually but apps may not see hover changes.
System Settings > Privacy & Security > Accessibility.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import math
import sys
import time

from finger_processing import FingerProcessor, Frame, load_config

# ------------------------------------------------------------------ mouse backends


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
            # A warp normally freezes the physical trackpad for 0.25 s; disable that so
            # the user can always take over.
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


class PynputMouse:
    """Windows/Linux fallback (pip install pynput)."""

    mode = "pynput"

    def __init__(self):
        from pynput.mouse import Controller

        self._m = Controller()

    def position(self) -> tuple[float, float]:
        return self._m.position

    def move(self, x: float, y: float) -> None:
        self._m.position = (x, y)


class DryRunMouse:
    mode = "dry-run"

    def __init__(self):
        self._pos = (0.0, 0.0)

    def position(self) -> tuple[float, float]:
        return self._pos

    def move(self, x: float, y: float) -> None:
        self._pos = (x, y)


def make_mouse(dry_run: bool):
    if dry_run:
        return DryRunMouse()
    if sys.platform == "darwin":
        return MacMouse()
    return PynputMouse()


# ------------------------------------------------------------------ cursor logic


def is_pointing(frame: Frame) -> bool:
    """Index extended; middle, ring and pinky curled (thumb doesn't matter)."""
    f = frame.fingers
    return f["index"].extended and not (f["middle"].extended or f["ring"].extended or f["pinky"].extended)


class CursorDriver:
    def __init__(self, mouse, anchor: str, takeover_px: float, takeover_pause_s: float):
        self.mouse = mouse
        self.anchor = anchor
        self.takeover_px = takeover_px
        self.takeover_pause_s = takeover_pause_s
        self._last_set: tuple[float, float] | None = None
        self._paused_until = 0.0
        self.state = "waiting"
        self.target: tuple[int, int] | None = None

    def update(self, frame: Frame) -> None:
        now = time.monotonic()
        # If the cursor isn't where we left it, the user moved the trackpad/mouse.
        if self._last_set is not None and self.mouse.mode != "dry-run":
            cx, cy = self.mouse.position()
            if math.hypot(cx - self._last_set[0], cy - self._last_set[1]) > self.takeover_px:
                self._paused_until = now + self.takeover_pause_s
                self._last_set = None
        if now < self._paused_until:
            self.state = "paused (you moved the mouse)"
            return
        if not frame.hand_present:
            self.state = "no hand"
            return
        if not is_pointing(frame):
            self.state = "clutch (not pointing)"
            return
        if self.anchor == "tip":
            target = frame.fingers["index"].tip_screen
        else:
            target = frame.pointer_screen  # blended tip/knuckle, frozen briefly on pinch
        if target is None:
            return
        self.mouse.move(*target)
        self._last_set = (float(target[0]), float(target[1]))
        self.target = target
        self.state = "moving"


# ------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="finger_processing config (default: its config.yaml)")
    ap.add_argument("--anchor", choices=["tip", "pointer"], default="tip",
                    help="tip = index fingertip (default); pointer = tip/knuckle blend (steadier on pinch)")
    ap.add_argument("--region", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="camera area mapped to the full screen when uncalibrated "
                         "(default 0.2 0.2 0.8 0.8; smaller = less hand travel)")
    ap.add_argument("--preview", action="store_true", help="show the camera window with the HUD")
    ap.add_argument("--dry-run", action="store_true", help="don't move the real cursor")
    ap.add_argument("--video", help="run on a recorded video instead of the camera")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.region:
        cfg.calibration.default_region = tuple(args.region)

    mouse = make_mouse(args.dry_run)
    driver = CursorDriver(mouse, args.anchor, takeover_px=25.0, takeover_pause_s=2.0)
    if mouse.mode == "warp":
        print("Note: no Accessibility permission, so the cursor is warped (moves visually; apps may not "
              "see hover).\n      Grant it in System Settings > Privacy & Security > Accessibility "
              "for real mouse events.")
    print(f"Cursor demo running ({mouse.mode}). Point with your index finger. Ctrl+C to stop.\n")

    fp = FingerProcessor(cfg, video=args.video).start()
    if fp.calibration.is_default:
        print(f"Uncalibrated: camera region {tuple(cfg.calibration.default_region)} -> full screen "
              f"{fp.screen[0]}x{fp.screen[1]}")

    hud = None
    if args.preview:
        import cv2

        from finger_processing.__main__ import draw_hud

        hud = (cv2, draw_hud)

    last_id, last_status, last_draw = -1, 0.0, 0.0
    try:
        while not fp.finished.is_set():
            frame = fp.wait_next(last_id, timeout=0.05 if hud else 0.5)
            if frame is not None:
                last_id = frame.frame_id
                driver.update(frame)

            now = time.monotonic()
            if now - last_status > 0.1:
                last_status = now
                lat = fp.pipeline.timer.summary().get("capture_to_publish", (0.0, 0.0))[0]
                tgt = f"{driver.target}" if driver.target else "-"
                sys.stdout.write(f"\r{driver.state:<30} cursor {tgt:<14} latency {lat:4.1f} ms   ")
                sys.stdout.flush()

            if hud and now - last_draw > 1 / 20:
                last_draw = now
                cv2, draw_hud = hud
                raw = fp.latest_raw()
                if raw is not None:
                    cv2.imshow("Cursor demo", draw_hud(raw.image, fp.latest(), fp))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopped.")
        if fp.error:
            print(fp.error)
        fp.stop()
        if hud:
            hud[0].destroyAllWindows()


if __name__ == "__main__":
    main()
