"""Cursor demo: the actor for the hand-gesture commands (finger_processing/commands.py).

Both hands are tracked; only the primary hand (finger_processing config `hands.primary`, default LEFT) commands.

    python cursor_demo/run.py                        Drex decides (falls back to local rules without a key)
    python cursor_demo/run.py --decider heuristic    local rules only: instant, no network
    python cursor_demo/run.py --preview              also show the camera window with the HUD
    python cursor_demo/run.py --dry-run              don't touch the real cursor; print what would happen

Commands and their actions
  idle   open palm, relaxed hand, or no left hand  -> do nothing (cursor stays put)
  point  index up, middle/ring/pinky curled        -> cursor follows the index fingertip
  click  from pointing, curl the index finger      -> left click where the cursor was just before the curl
  (Touch the trackpad/mouse -> pause 2 s so you can take over. Ctrl+C, or q in the preview, stops.)

macOS permissions: moving works without permission (cursor warp), but CLICKING needs Accessibility for your
terminal app: System Settings > Privacy & Security > Accessibility. Without it, clicks are reported, not delivered.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import math
import sys
import time

from finger_processing import FingerProcessor, load_config
from finger_processing.commands import ACTIONS, CommandEngine, CommandOutput, make_engine

# ------------------------------------------------------------------ mouse backends


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class MacMouse:
    """Moves and clicks through CoreGraphics (no extra dependencies)."""

    MOUSE_MOVED, LEFT_DOWN, LEFT_UP = 5, 1, 2
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

    @property
    def can_click(self) -> bool:
        return self.trusted

    def position(self) -> tuple[float, float]:
        e = self._cg.CGEventCreate(None)
        p = self._cg.CGEventGetLocation(e)
        self._cf.CFRelease(e)
        return p.x, p.y

    def _post(self, kind: int, x: float, y: float) -> None:
        e = self._cg.CGEventCreateMouseEvent(None, kind, _Point(x, y), 0)
        self._cg.CGEventPost(self.HID_EVENT_TAP, e)
        self._cf.CFRelease(e)

    def move(self, x: float, y: float) -> None:
        if self.mode == "post":
            self._post(self.MOUSE_MOVED, x, y)
        else:
            self._cg.CGWarpMouseCursorPosition(_Point(x, y))
            self._cg.CGAssociateMouseAndMouseCursorPosition(True)

    def click(self, x: float, y: float) -> bool:
        """Left click at (x, y). Returns False when macOS will drop it (no Accessibility permission)."""
        if not self.trusted:
            return False
        self._post(self.LEFT_DOWN, x, y)
        self._post(self.LEFT_UP, x, y)
        return True


class PynputMouse:
    """Windows/Linux fallback (pip install pynput)."""

    mode, can_click = "pynput", True

    def __init__(self):
        from pynput.mouse import Button, Controller

        self._m, self._left = Controller(), Button.left

    def position(self) -> tuple[float, float]:
        return self._m.position

    def move(self, x: float, y: float) -> None:
        self._m.position = (x, y)

    def click(self, x: float, y: float) -> bool:
        self._m.position = (x, y)
        self._m.click(self._left)
        return True


class DryRunMouse:
    mode, can_click = "dry-run", True

    def __init__(self):
        self._pos = (0.0, 0.0)
        self.clicks: list[tuple[float, float]] = []

    def position(self) -> tuple[float, float]:
        return self._pos

    def move(self, x: float, y: float) -> None:
        self._pos = (x, y)

    def click(self, x: float, y: float) -> bool:
        self.clicks.append((x, y))
        print(f"\n[dry-run] click at ({x:.0f}, {y:.0f})")
        return True


def make_mouse(dry_run: bool):
    if dry_run:
        return DryRunMouse()
    if sys.platform == "darwin":
        return MacMouse()
    return PynputMouse()


# ------------------------------------------------------------------ actor


class Actor:
    """Performs each command's action. Pauses for a moment if the user moves the real mouse."""

    def __init__(self, mouse, takeover_px: float = 25.0, takeover_pause_s: float = 2.0):
        self.mouse = mouse
        self.takeover_px = takeover_px
        self.takeover_pause_s = takeover_pause_s
        self._last_set: tuple[float, float] | None = None
        self._paused_until = 0.0
        self.state = "waiting"
        self.target: tuple[int, int] | None = None
        self.clicks = 0
        self.dropped_clicks = 0

    def act(self, out: CommandOutput, frame) -> None:
        now = time.monotonic()
        if self._last_set is not None and self.mouse.mode != "dry-run":
            cx, cy = self.mouse.position()
            if math.hypot(cx - self._last_set[0], cy - self._last_set[1]) > self.takeover_px:
                self._paused_until, self._last_set = now + self.takeover_pause_s, None
        if now < self._paused_until:
            self.state = "paused (you moved the mouse)"
            return

        if out.click_at is not None:  # click
            x, y = out.click_at
            if self.mouse.click(x, y):
                self.clicks += 1
                self._last_set = (float(x), float(y))
                self.state = f"CLICK at {out.click_at} [{out.source}]"
            else:
                self.dropped_clicks += 1
                self.state = "CLICK not delivered: needs Accessibility permission"
            return
        if out.mode == "point":  # point
            target = frame.fingers["index"].tip_screen
            self.mouse.move(*target)
            self._last_set, self.target = (float(target[0]), float(target[1])), target
            self.state = f"point [{out.source}]"
            return
        # idle: ACTIONS["idle"] -> do nothing
        self.state = f"idle [{out.source}]" if frame.hand_present else f"idle (no {frame.primary_side} hand)"


# ------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="finger_processing config (default: its config.yaml)")
    ap.add_argument("--decider", choices=["drex", "heuristic"], help="override commands.decider")
    ap.add_argument("--region", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="camera area mapped to the full screen when uncalibrated (default 0.2 0.2 0.8 0.8)")
    ap.add_argument("--preview", action="store_true", help="show the camera window with the HUD")
    ap.add_argument("--dry-run", action="store_true", help="don't move or click the real cursor")
    ap.add_argument("--video", help="run on a recorded video instead of the camera")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.region:
        cfg.calibration.default_region = tuple(args.region)
    if args.decider:
        cfg.commands.decider = args.decider

    mouse = make_mouse(args.dry_run)
    actor = Actor(mouse)
    engine: CommandEngine = make_engine(cfg.commands)
    who = "Drex Max (streaming the hand), local rules as fallback" if engine.using_drex else "local rules"
    if cfg.commands.decider == "drex" and not engine.using_drex:
        who += " (Drex requested but no NACE_API_KEY found in the environment, .env or .intern-home/.env)"
    print(f"Commands decided by: {who}")
    print("Commands: " + ", ".join(f"{k} -> {v[1]}" for k, v in ACTIONS.items()))
    if not getattr(mouse, "can_click", True):
        print("Note: clicks need Accessibility permission for this terminal (System Settings > Privacy & Security >"
              " Accessibility); until then they are reported but not delivered. Moving works.")
    print(f"Use your {cfg.hands.primary.upper()} hand. Ctrl+C to stop.\n")

    fp = FingerProcessor(cfg, video=args.video).start()
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
                actor.act(engine.update(frame), frame)

            now = time.monotonic()
            if now - last_status > 0.1:
                last_status = now
                d = engine.last_decision
                drex = (f"drex {d.label} p={max(d.probs.values()):.2f} {d.latency_ms:.0f}ms"
                        if d and d.probs else (f"drex {d.reason}" if d else "drex -"))
                ca = engine.heuristic.curl_min_angle
                curl = f"last curl {ca:.0f}deg (need <{engine.cfg.click_curl_deg:.0f})" if ca is not None else ""
                sys.stdout.write(f"\r{actor.state:<44} clicks {actor.clicks:<3} {drex:<32} {curl:<28}")
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
        print(f"\nStopped. clicks delivered {actor.clicks}, not delivered {actor.dropped_clicks}")
        if engine.streamer is not None:
            print(f"Drex calls {engine.streamer.calls}, errors {engine.streamer.errors}")
        if fp.error:
            print(fp.error)
        engine.close()
        fp.stop()
        if hud:
            hud[0].destroyAllWindows()


if __name__ == "__main__":
    main()
