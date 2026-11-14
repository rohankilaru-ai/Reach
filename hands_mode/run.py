"""Hands mode (EXPERIMENTAL): your hand is the mouse.

    python -m hands_mode.run              dry run: prints the actions it would take (no events)
    python -m hands_mode.run --control    real cursor control (move, click, drag, scroll)

Gestures (the closest hand drives)
  point (index up)                          move the cursor
  pinch thumb to index                      click; hold and move to drag
  two quick pinches                         double click
  curl the index quickly from pointing      click
  pinch thumb to middle, draw a loop        select what is inside the loop
  index + middle up, move up/down           scroll
  open palm held 1 s                        leave hands mode
Touching the real mouse/trackpad pauses hands mode for 2 s. Ctrl+C stops it.
Each state change is appended to .reach/logs/hands.jsonl for the overlay.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from pathlib import Path

from finger_processing import FingerProcessor, load_config
from finger_processing.processing import resolve_path

from hands_mode.actions import ActionCfg, HandsEngine, is_pointing
from hands_mode.signals import SignalWatcher
from hands_mode.signals import enabled as signals_enabled
from hands_mode.mouse import MacActionMouse, PrintMouse

STALL_S, MAX_REOPENS = 1.0, 3   # no new camera frame for STALL_S: release, reopen the camera (at most 3 times)
REPO = Path(__file__).resolve().parent.parent


def feed_path() -> Path:
    """Gesture feed for the overlay: $REACH_DIR/logs/hands.jsonl (default <repo>/.reach)."""
    return Path(os.environ.get("REACH_DIR") or REPO / ".reach") / "logs" / "hands.jsonl"


def log_state(feed: Path, state: str) -> None:
    with feed.open("a") as fh:
        fh.write(json.dumps({"t": time.time(), "state": state}) + "\n")


MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"


def ensure_model() -> None:
    """Download the MediaPipe hand model next to finger_processing on first run (it is not committed)."""
    from pathlib import Path
    from urllib.request import urlretrieve

    path = Path(__file__).resolve().parents[1] / "finger_processing" / "hand_landmarker.task"
    if not path.exists():
        print(f"first run: downloading the hand model (8 MB) to {path.name} ...", flush=True)
        urlretrieve(MODEL_URL, path)


def hands_config(config_path: str | None = None, region=None):
    cfg = load_config(config_path)
    cfg.hands.primary = "closest"
    cfg.hands.max_hands = 2
    cfg.hands.hold_frames = 2           # a hand missing for more than 2 frames is lost (releases the button)
    cfg.features.pinch_fingers = "index_or_middle"   # thumb-middle pinch = select
    cfg.features.pinch_exit = max(cfg.features.pinch_exit, 0.40)   # wider hysteresis: a drag does not drop mid-way
    cfg.pointer.pinch_freeze_ms = max(cfg.pointer.pinch_freeze_ms, 150.0)
    if region:
        cfg.calibration.default_region = tuple(region)
    shared = Path(__file__).resolve().parents[1] / "perception" / "models" / "hand_landmarker.task"
    if not resolve_path(cfg.tracker.model_path).exists() and shared.exists():
        cfg.tracker.model_path = str(shared)   # same MediaPipe model the pinch worker already downloaded
    return cfg


def make_selector(enabled: bool):
    if not enabled:
        return None
    try:
        from hands_mode.select import Selector
    except ImportError:
        print("[hands] selection unavailable (hands_mode.select not found)", flush=True)
        return None
    return Selector()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--control", action="store_true", help="send real mouse events (default: dry run)")
    ap.add_argument("--no-preview", action="store_true", help="no camera preview window")
    ap.add_argument("--no-select", action="store_true", help="thumb-middle pinch does nothing")
    ap.add_argument("--config", help="finger_processing config (default: its config.yaml)")
    ap.add_argument("--region", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--scroll-invert", action="store_true", help="fingers up scrolls up instead")
    ap.add_argument("--video", help="run on a recorded video instead of the camera")
    args = ap.parse_args(argv)

    mouse = MacActionMouse() if args.control and sys.platform == "darwin" else PrintMouse()
    if args.control and sys.platform != "darwin":
        print("--control is macOS only; running as a dry run.")
    selector = make_selector(not args.no_select)
    engine = HandsEngine(mouse, ActionCfg(scroll_invert=args.scroll_invert), selector=selector)
    watcher = SignalWatcher() if signals_enabled() else None   # REACH_HAND_SIGNALS=0 turns hand signals off
    engine.signals = watcher is not None

    def on_signal(signum, _frame):
        engine.stop("stopped")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    if mouse.mode == "dry-run":
        print("Hands mode (EXPERIMENTAL, DRY RUN): prints the actions it would take; no mouse events. "
              "Add --control to act.")
    else:
        print("Hands mode ON (EXPERIMENTAL): your hand controls the mouse. Ctrl+C stops.")
        if not mouse.can_click:
            print("No Accessibility permission: the cursor will move (warp) but clicks and scrolls are off.\n"
                  "Grant it in System Settings > Privacy & Security > Accessibility.")

    feed = feed_path()
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text("")

    cfg = hands_config(args.config, args.region)
    ensure_model()
    fp = FingerProcessor(cfg, video=args.video).start()
    reopens, last_frame_t = 0, time.monotonic()
    show = not args.no_preview
    if show:
        import cv2

        from hands_mode.hud import render

        cv2.namedWindow("Hands mode", cv2.WINDOW_AUTOSIZE)
        try:
            cv2.setWindowProperty("Hands mode", cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            pass

    last_id, last_draw, last_state = -1, 0.0, ""
    try:
        while engine.active and not fp.finished.is_set():
            frame = fp.wait_next(last_id, timeout=0.05 if show else 0.25)
            if frame is None:
                stalled = time.monotonic() - last_frame_t
                if stalled > 0.25:
                    engine.no_frames()               # never hold a button without fresh frames
                if stalled > STALL_S and args.video is None:
                    engine.release("camera stalled")
                    print("[hands] camera stalled", flush=True)
                    fp.stop()
                    if reopens >= MAX_REOPENS:
                        print("Camera did not come back; leaving hands mode.")
                        break
                    reopens += 1
                    fp = FingerProcessor(cfg).start()
                    last_id, last_frame_t = -1, time.monotonic()
            else:
                last_id, last_frame_t = frame.frame_id, time.monotonic()
                engine.update(frame)
                if watcher is not None and engine.active and (
                        sig := watcher.update(frame, time.monotonic(),
                                              busy=engine.button_down or engine.selecting or is_pointing(frame)
                                              or engine.state.startswith("paused"))):
                    print(f"GESTURE {sig}", flush=True)   # the overlay acts on it; the engine never clicks
            if selector is not None and (status := selector.poll()):
                print(f"[select] {status}", flush=True)
            if engine.state != last_state:
                last_state = engine.state
                print(f"[hands] {engine.state}", flush=True)
                log_state(feed, engine.state)
            now = time.monotonic()
            if show and now - last_draw > 1 / 20:
                last_draw = now
                raw = fp.latest_raw()
                if raw is not None:
                    cv2.imshow("Hands mode", render(raw.image, fp.latest(), engine.state))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        pass
    except Exception:
        engine.stop("error")
        raise
    finally:
        engine.stop(engine.state if not engine.active else "stopped")
        log_state(feed, engine.state)
        fp.stop()
        if selector is not None:
            selector.close()
        if show:
            cv2.destroyAllWindows()
        if fp.error:
            print(fp.error)
        print("Hands mode off. Mouse button released.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
