"""Reach camera worker (EXPERIMENTAL): hand pointer + pinch-drag selection.

  python -m reach_perception.worker               # run with preview window (q quits, c calibrates, r resets map)
  python -m reach_perception.worker --selftest    # synthetic landmark fixtures, no camera
  python -m reach_perception.worker --camera-check  # open the camera 3 s, print frame size, fps, p50 latency
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from .pinch import MIN_DRAG
from .screen_map import CORNERS, ScreenMap, box_around, screen_size


def camera_check(seconds: float = 3.0) -> int:
    from .camera import PERMISSION_MSG, Camera
    from .hands import Hands

    cam = Camera()
    try:
        if not cam.ok():
            print(PERMISSION_MSG)
            return 2
        hands = Hands()
        lat, frames, with_hand, size = [], 0, 0, None
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            frame = cam.read()
            if frame is None:
                continue
            size = frame.shape[1], frame.shape[0]
            s = time.perf_counter()
            lm = hands.detect(frame, int((time.monotonic() - t0) * 1000))
            lat.append((time.perf_counter() - s) * 1000)
            frames += 1
            with_hand += lm is not None
        dur = time.monotonic() - t0
        hands.close()
        print(f"frame size: {size[0]}x{size[1]}" if size else "frame size: none")
        print(f"frames: {frames} in {dur:.1f}s ({frames / dur:.1f} fps processed)")
        print(f"frames with a hand: {with_hand} ({with_hand / dur:.1f} fps detected-hand)")
        if lat:
            print(f"per-frame HandLandmarker latency: p50 {statistics.median(lat):.1f} ms, max {max(lat):.1f} ms")
        return 0
    finally:
        cam.release()


def run(no_preview: bool = False, no_ocr: bool = False) -> int:
    from . import preview
    from .bridge_client import BridgeClient
    from .camera import PERMISSION_MSG, Camera
    from .capture import capture_region, ocr
    from .hands import Hands
    from .pinch import GestureEngine

    cam = Camera()
    if not cam.ok():
        print(PERMISSION_MSG)
        cam.release()
        return 2
    hands = Hands()
    sw, sh = screen_size()
    smap = ScreenMap.load(sw, sh)
    eng = GestureEngine()
    bridge = BridgeClient()
    bridge.connect()
    print(f"[worker] screen {sw:.0f}x{sh:.0f} pt, mapping={'calibrated' if smap.calibrated else 'fixed'}, "
          f"bridge: {bridge.status}")
    calib: list | None = None  # corners collected during calibration
    last = "ready"
    ocr_label = "OCR off: pointer demo only" if no_ocr else "OCR on"
    t0 = time.monotonic()
    try:
        while True:
            frame = cam.read()
            if frame is None:
                continue
            eng.w, eng.h = frame.shape[1], frame.shape[0]
            t_ms = int((time.monotonic() - t0) * 1000)
            lm = hands.detect(frame, t_ms)
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
                        smap = ScreenMap(sw, sh, calib)
                        last = f"calibration saved to {smap.save(calib)}"
                        calib = None
                elif e.kind == "select":
                    s, en = e.data["start"], e.data["end"]
                    if abs(s[0] - en[0]) < MIN_DRAG and abs(s[1] - en[1]) < MIN_DRAG:
                        rect = box_around(smap.to_screen(en), sw, sh)
                    else:
                        rect = smap.rect_to_screen(s, en)
                    text, img = None, None
                    if not no_ocr:
                        img = capture_region(rect)
                        text = ocr(img) if img else ""
                        if not text:
                            ocr_label = "no text read (Screen Recording permission?): pointer demo only"
                        else:
                            ocr_label = f"OCR: {len(text)} chars"
                    sent = bridge.select(rect, text or None, str(img) if img else None)
                    last = f"select {rect} {'sent' if sent else 'local only'}"
                    print(f"[worker] {last}; {ocr_label}")
            if eng.pointer:
                sx, sy = smap.to_screen(eng.pointer)
                bridge.pointer(round(sx), round(sy), eng.pinching, True)
            elif not eng.tracking:
                bridge.pointer(None, None, False, False)
            if no_preview:
                continue
            ratio = f"{eng.ratio:.2f}" if eng.ratio is not None else "-"
            todo = f"CALIBRATE: reach to {CORNERS[len(calib)]} and pinch" if calib is not None else ""
            lines = [f"hand={'yes' if eng.tracking else 'no'} pinch={'ON' if eng.pinching else 'off'} ratio={ratio} "
                     f"palm={'yes' if eng.palm else 'no'} map={'calib' if smap.calibrated else 'fixed'}",
                     f"{last} | {ocr_label}", f"bridge: {bridge.status} | q quit, c calibrate, r reset map"]
            if todo:
                lines.insert(0, todo)
            key = preview.show(preview.draw(frame, lm, eng, lines))
            if key == ord("q"):
                break
            if key == ord("c"):
                calib = []
            if key == ord("r"):
                smap = ScreenMap(sw, sh)
    except KeyboardInterrupt:
        pass
    finally:
        hands.close()
        cam.release()
        bridge.close()
        if not no_preview:
            preview.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reach_perception.worker", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="run on synthetic landmark fixtures, no camera")
    ap.add_argument("--camera-check", action="store_true", help="open the camera for 3 s and print timings")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--no-ocr", action="store_true", help="send rect only; preview says 'pointer demo only'")
    a = ap.parse_args(argv)
    if a.selftest:
        from .selftest import main as st

        return st()
    if a.camera_check:
        return camera_check()
    return run(a.no_preview, a.no_ocr)


if __name__ == "__main__":
    sys.exit(main())
