"""Reach camera worker (EXPERIMENTAL): hand pointer + pinch-drag selection.

Hands come from `finger_processing` (repo root), the single camera + MediaPipe pipeline. The primary hand
(`hands.primary` in finger_processing/config.yaml, left by default) drives the pointer and pinch.

  python -m reach_perception.worker               # run with preview window (q quits, c calibrates, r resets map)
  python -m reach_perception.worker --selftest    # synthetic landmark fixtures, no camera
  python -m reach_perception.worker --camera-check  # open the camera 3 s, print frame size, fps, p50 latency
"""

from __future__ import annotations

import argparse
import queue
import statistics
import sys
import threading
import time

from .pinch import MIN_DRAG
from .screen_map import CORNERS, ScreenMap, box_around, build_map, loop_path, path_bbox, reset_calibration, screen_size

FRAME_FAIL_MSG = "[worker] camera stopped delivering frames; exiting. Check that it is connected and not in use."


class ReadWorker:
    """Runs capture + OCR for selections on one background thread so the camera loop and preview never block.

    `submit(rect)` is non-blocking; `poll()` returns finished results (rect, text, image path) without waiting.
    `capture` and `recognise` are injected so tests need neither Screen Recording nor Vision.
    """

    def __init__(self, capture, recognise, max_pending: int = 4) -> None:
        self._capture, self._recognise = capture, recognise
        self._jobs: queue.Queue = queue.Queue(maxsize=max_pending)
        self._done: queue.Queue = queue.Queue()
        self.pending = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self.pending > 0

    def submit(self, rect) -> bool:
        with self._lock:
            try:
                self._jobs.put_nowait(rect)
            except queue.Full:
                return False
            self.pending += 1
            return True

    def _loop(self) -> None:
        while True:
            rect = self._jobs.get()
            if rect is None:
                return
            text, img = "", None
            try:
                img = self._capture(rect)
                text = self._recognise(img) if img else ""
            except Exception as exc:  # a failed read must not kill the thread
                print(f"[worker] read failed: {exc}")
            self._done.put((rect, text, img))

    def poll(self) -> list:
        out = []
        while True:
            try:
                out.append(self._done.get_nowait())
            except queue.Empty:
                break
        with self._lock:
            self.pending -= len(out)
        return out

    def close(self) -> None:
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass


PERMISSION_MSG = (
    "Could not read from the camera. macOS needs Camera permission for the app running this process "
    "(your terminal, e.g. Terminal/iTerm/Ghostty, or Python): System Settings > Privacy & Security > Camera. "
    "Grant it, quit and reopen the terminal, then rerun."
)


def open_hands():
    """Start the shared hand pipeline (finger_processing: the one camera + MediaPipe owner), or None."""
    from finger_processing import FingerProcessor

    try:
        return FingerProcessor().start()
    except RuntimeError as e:
        print(f"{e}\n{PERMISSION_MSG}")
        return None


def unmirrored(hand) -> list[tuple[float, float, float]] | None:
    """GestureEngine takes MediaPipe's un-mirrored landmarks; finger_processing streams mirrored ones."""
    if hand is None:
        return None
    return [(1.0 - x, y, z) for x, y, z in hand.landmarks]


def camera_check(seconds: float = 3.0) -> int:
    fp = open_hands()
    if fp is None:
        return 2
    frames = []
    fp.subscribe(frames.append)
    try:
        time.sleep(seconds)
    finally:
        fp.stop()
    w, h = fp.source.size
    with_hand = sum(1 for f in frames if f.hands)
    print(f"frame size: {w}x{h}")
    print(f"frames: {len(frames)} in {seconds:.1f}s ({len(frames) / seconds:.1f} fps processed)")
    print(f"frames with a hand: {with_hand} (left {sum(1 for f in frames if f.left)}, "
          f"right {sum(1 for f in frames if f.right)})")
    lm = fp.pipeline.timer.summary().get("landmarker")
    if lm:
        print(f"per-frame HandLandmarker latency: p50 {lm[0]:.1f} ms, p95 {lm[1]:.1f} ms")
    return 0


def run(no_preview: bool = False, no_ocr: bool = False) -> int:
    from . import preview
    from .bridge_client import BridgeClient
    from .capture import capture_region, ocr
    from .pinch import GestureEngine

    fp = open_hands()
    if fp is None:
        return 2
    sw, sh = screen_size()
    smap = ScreenMap.load(sw, sh)
    eng = GestureEngine()
    bridge = BridgeClient()
    bridge.connect()
    print(f"[worker] screen {sw:.0f}x{sh:.0f} pt, mapping={'calibrated' if smap.calibrated else 'fixed'}, "
          f"bridge: {bridge.status}")
    def captures_dir():
        from .bridge_client import server_reach_dir  # same .reach the server checks image_path against

        return server_reach_dir() / "captures"

    paths: dict[tuple, list] = {}  # rect -> loop path, picked up when its read finishes
    reader = None if no_ocr else ReadWorker(lambda r: capture_region(r, captures_dir()), ocr)
    calib: list | None = None  # corners collected during calibration
    last = "ready"
    ocr_label = "OCR off: pointer demo only" if no_ocr else "OCR on"
    eng.w, eng.h = fp.source.size
    last_id = -1
    try:
        while True:
            hf = fp.wait_next(last_id, timeout=1.0)
            if hf is None:
                if fp.finished.is_set():  # finger_processing gave up on the camera after repeated failures
                    print(FRAME_FAIL_MSG)
                    return 3
                if not no_preview and preview.poll_key() == ord("q"):
                    break
                continue
            last_id = hf.frame_id
            t_ms = int(hf.t * 1000)
            lm = unmirrored(hf.primary)  # the primary hand (left by default) drives pointer and pinch
            for e in eng.update(t_ms, lm):
                if e.kind == "pause":
                    last = "PAUSE (open palm, local)"
                elif e.kind == "lost":
                    last = "hand lost: pointer hidden" + (", drag cancelled" if e.data["drag_cancelled"] else "")
                elif e.kind == "pinch_down":
                    last = "pinch down"
                elif e.kind == "select" and calib is not None:
                    calib.append(e.data["end"])
                    last = f"calibration: got {CORNERS[len(calib) - 1]}"
                    if len(calib) == 4:
                        smap, warn = build_map(sw, sh, calib)
                        if warn:
                            last = warn
                            print(f"[worker] WARNING: {warn}")
                        else:
                            last = f"calibration saved to {smap.save(calib)}"
                        calib = None
                elif e.kind == "select":
                    s, en = e.data["start"], e.data["end"]
                    path = loop_path([smap.to_screen(p) for p in e.data.get("trail", [])])
                    if path:  # a drawn loop: region is its bounding box
                        rect = [round(v) for v in path_bbox(path)]
                        paths[tuple(rect)] = path
                    elif abs(s[0] - en[0]) < MIN_DRAG and abs(s[1] - en[1]) < MIN_DRAG:
                        rect = box_around(smap.to_screen(en), sw, sh)
                    else:
                        rect = smap.rect_to_screen(s, en)
                    if reader is not None and reader.submit(rect):
                        ocr_label = "reading..."
                        last = f"select {rect}: reading..."
                    else:
                        sent = bridge.select(rect, None, None, paths.pop(tuple(rect), None))
                        last = f"select {rect} {'sent' if sent else 'local only'}"
                        print(f"[worker] {last}; {ocr_label}")
            for rect, text, img in (reader.poll() if reader else []):
                ocr_label = f"OCR: {len(text)} chars" if text else \
                    "no text read (Screen Recording permission?): pointer demo only"
                sent = bridge.select(rect, text or None, str(img) if img else None, paths.pop(tuple(rect), None))
                last = f"select {rect} {'sent' if sent else 'local only'}"
                print(f"[worker] {last}; {ocr_label}")
            if eng.pointer:
                sx, sy = smap.to_screen(eng.pointer)
                bridge.pointer(round(sx), round(sy), eng.pinching)
            # hand lost: send nothing; the overlay fades the ghost after 1 s
            if no_preview:
                continue
            ratio = f"{eng.ratio:.2f}" if eng.ratio is not None else "-"
            todo = f"CALIBRATE: reach to {CORNERS[len(calib)]} and pinch" if calib is not None else ""
            lines = [f"hand={'yes' if eng.tracking else 'no'} pinch={'ON' if eng.pinching else 'off'} ratio={ratio} "
                     f"palm={'yes' if eng.palm else 'no'} map={'calib' if smap.calibrated else 'fixed'}",
                     f"{last} | {ocr_label}", f"bridge: {bridge.status} | q quit, c calibrate, r reset map"]
            if todo:
                lines.insert(0, todo)
            raw = fp.latest_raw()
            key = preview.poll_key() if raw is None else preview.show(preview.draw(raw.image, lm, eng, lines))
            if key == ord("q"):
                break
            if key == ord("c"):
                calib = []
            if key == ord("r"):
                smap = reset_calibration(sw, sh)
                last = "mapping reset; saved calibration deleted"
    except KeyboardInterrupt:
        pass
    finally:
        if reader is not None:
            reader.close()
        fp.stop()
        bridge.close()
        if not no_preview:
            preview.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reach_perception.worker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="run on synthetic landmark fixtures, no camera")
    ap.add_argument("--camera-check", action="store_true", help="open the camera for 3 s and print timings")
    ap.add_argument("--bridge", action="store_true",
                    help="with --selftest: send the replayed pointer and select to a running `reach serve`")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--no-ocr", action="store_true", help="send rect only; preview says 'pointer demo only'")
    a = ap.parse_args(argv)
    if a.selftest:
        from .selftest import main as st

        return st(bridge=a.bridge)
    if a.camera_check:
        return camera_check()
    return run(a.no_preview, a.no_ocr)


if __name__ == "__main__":
    sys.exit(main())
