"""Live preview, calibration, and latency stats for finger processing.

  python -m finger_processing                       live preview (c calibrate, r reset mapping, q quit)
  python -m finger_processing --print               print every finger's state + position (no window)
  python -m finger_processing --headless 10         latency stats for 10 s (no window)
  python -m finger_processing --calibrate           start with the four-corner calibration
  python -m finger_processing --video clip.mp4      run on a recorded video
  python -m finger_processing --record obs.jsonl    record landmarks (no images)
"""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np

from finger_processing.processing import (
    FINGERS,
    FINGERTIPS,
    Calibration,
    CalibrationRoutine,
    FingerProcessor,
    Frame,
    Hand,
    finger_stream,
    inset_targets,
)

CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]
TIP_COLORS = {  # BGR
    "thumb": (255, 120, 0), "index": (0, 255, 255), "middle": (255, 0, 255),
    "ring": (0, 165, 255), "pinky": (255, 255, 0),
}


def _dim(color: tuple[int, int, int], k: float = 0.45) -> tuple[int, int, int]:
    return tuple(int(c * k) for c in color)  # type: ignore[return-value]


def draw_hand(img: np.ndarray, hand: Hand, primary: bool) -> None:
    """Skeleton plus color-coded fingertips (filled = extended, ring = curled).
    The primary hand is drawn in full color, the other one dimmed."""
    h, w = img.shape[:2]
    pts = [(int(x * w), int(y * h)) for x, y, _ in hand.landmarks]
    for a, b in CONNECTIONS:
        cv2.line(img, pts[a], pts[b], (200, 200, 200) if primary else (110, 110, 110), 1, cv2.LINE_AA)
    tips = set(FINGERTIPS.values())
    for i, p in enumerate(pts):
        if i not in tips:
            cv2.circle(img, p, 2, (0, 200, 255) if primary else (90, 90, 90), -1)
    for name, finger in hand.fingers.items():
        p = (int(finger.tip[0] * w), int(finger.tip[1] * h))
        color = TIP_COLORS[name] if primary else _dim(TIP_COLORS[name])
        cv2.circle(img, p, 9 if primary else 7, color, -1 if finger.extended else 2, cv2.LINE_AA)
        cv2.circle(img, p, 10 if primary else 8, (0, 0, 0), 1, cv2.LINE_AA)
    if primary and hand.pointer_raw:
        cv2.circle(img, (int(hand.pointer_raw[0] * w), int(hand.pointer_raw[1] * h)), 5, (0, 0, 255), 1)
    label = ("L" if hand.side == "left" else "R") + ("*" if primary else "")
    wx, wy = pts[0]
    for color, thick in (((0, 0, 0), 4), ((255, 255, 255) if primary else (160, 160, 160), 2)):
        cv2.putText(img, label, (wx - 10, wy + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, thick, cv2.LINE_AA)


def draw_hud(raw_img: np.ndarray, frame: Frame | None, fp: FingerProcessor) -> np.ndarray:
    img = cv2.flip(raw_img, 1)  # display only; the pipeline never flips pixels
    h, w = img.shape[:2]
    scr = fp.screen
    lines = [f"cam {getattr(fp.source, 'measured_fps', 0):.0f} fps   * = primary (cursor/gestures)"]
    if frame is not None:
        for side in ("left", "right"):
            hand = frame.hands.get(side)
            primary = side == frame.primary_side
            tag = f"{side}{'*' if primary else ' '}"
            if hand is None:
                lines.append(f"{tag:<6} -")
                continue
            draw_hand(img, hand, primary)
            flags = "".join(f[0].upper() if hand.fingers[f].extended else "." for f in FINGERS)
            line = f"{tag:<6} {flags} pinch {hand.pinch_dist:.2f}{' DOWN' if hand.pinch_active else ''} [{hand.source}]"
            if primary:
                line += f" cursor {hand.pointer_screen}"
            lines.append(line)
        trig = ", ".join(f"{s}:{t}" for s, t in frame.triggers.items())
        lines.append(f"motion {frame.motion:.3f}  classify {'Y' if frame.should_classify else '-'}"
                     + (f"  trig {trig}" if trig else ""))
    for stage, (p50, p95) in sorted(fp.pipeline.timer.summary().items()):
        lines.append(f"{stage:<18} p50 {p50:5.1f}  p95 {p95:5.1f} ms")
    if fp.calibration.is_default:
        lines.append("UNCALIBRATED (press c)")
    for i, text in enumerate(lines):
        for color, thick in (((0, 0, 0), 3), ((255, 255, 255), 1)):
            cv2.putText(img, text, (8, 18 + 17 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, thick, cv2.LINE_AA)

    mw = 160
    mh = int(mw * scr[1] / scr[0])
    x0, y0 = w - mw - 8, h - mh - 8
    cv2.rectangle(img, (x0, y0), (x0 + mw, y0 + mh), (255, 255, 255), 1)
    if frame is not None and frame.pointer_screen:
        sx = x0 + int(frame.pointer_screen[0] / scr[0] * mw)
        sy = y0 + int(frame.pointer_screen[1] / scr[1] * mh)
        cv2.circle(img, (sx, sy), 4, (0, 0, 255) if frame.pinch_active else (0, 255, 0), -1)
    return img


def run_calibration(fp: FingerProcessor) -> None:
    ccfg = fp.cfg.calibration
    scr = fp.screen
    routine = CalibrationRoutine(ccfg.dwell_s, ccfg.still_speed)
    targets = inset_targets(scr[0], scr[1], ccfg.inset)
    win = "Finger processing calibration"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    last_id = -1
    while not routine.done:
        frame = fp.latest()
        if frame is not None and frame.frame_id != last_id:
            last_id = frame.frame_id
            routine.update(frame)
        canvas = np.full((scr[1], scr[0], 3), 30, np.uint8)
        if not routine.done:
            tx, ty = targets[routine.target_index].astype(int)
            cv2.circle(canvas, (tx, ty), 22, (0, 200, 255), 2)
            cv2.circle(canvas, (tx, ty), 4, (0, 200, 255), -1)
            cv2.ellipse(canvas, (tx, ty), (32, 32), -90, 0, 360 * routine.progress, (0, 255, 0), 4)
        hand = frame is not None and frame.hand_present
        msg = f"Point at target {routine.target_index + 1}/4 and hold still" if hand else "Show your hand"
        cv2.putText(canvas, msg, (scr[0] // 2 - 260, scr[1] // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, "Esc to cancel", (scr[0] // 2 - 80, scr[1] // 2 + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 160, 160), 1, cv2.LINE_AA)
        cv2.imshow(win, canvas)
        if cv2.waitKey(15) & 0xFF == 27:
            cv2.destroyWindow(win)
            print("Calibration cancelled.")
            return
    cv2.destroyWindow(win)
    cal = Calibration.from_points(np.array(routine.points), scr, ccfg.inset, fp.source.size,
                                  fp.source.camera_index)
    cal.save(fp.calibration_path)
    fp.calibration = cal
    print(f"Calibration saved to {fp.calibration_path}")


def print_fingers(args) -> None:
    """Example downstream consumer: the continuous finger loop, both hands."""
    for frame in finger_stream(args.config, video=args.video, record=args.record):
        if not frame.hands:
            print(f"{frame.frame_id:6d}  no hands", flush=True)
            continue
        for side, hand in frame.hands.items():
            star = "*" if side == frame.primary_side else " "
            parts = [f"{f.name}:{'EXT' if f.extended else 'cur'}({f.tip[0]:.3f},{f.tip[1]:.3f})"
                     for f in hand.fingers.values()]
            print(f"{frame.frame_id:6d} {side:<5}{star} " + "  ".join(parts), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--video")
    ap.add_argument("--record")
    ap.add_argument("--headless", type=float, metavar="SECONDS")
    ap.add_argument("--print", action="store_true", help="print finger states/positions every frame")
    args = ap.parse_args()

    if args.print:
        try:
            print_fingers(args)
        except KeyboardInterrupt:
            pass
        return

    fp = FingerProcessor(args.config, video=args.video, record=args.record).start()
    try:
        if args.headless:
            end = time.monotonic() + args.headless
            while time.monotonic() < end and not fp.finished.is_set():
                time.sleep(1.0)
                f = fp.latest()
                parts = []
                for side in ("left", "right"):
                    hand = f.hands.get(side) if f else None
                    flags = ("".join(n[0].upper() if g.extended else "." for n, g in hand.fingers.items())
                             if hand else "-----")
                    parts.append(f"{side} {flags}")
                print(f"cam {getattr(fp.source, 'measured_fps', 0):.1f} fps | " + " | ".join(parts))
            print("\nstage                p50 ms   p95 ms")
            for stage, (p50, p95) in sorted(fp.pipeline.timer.summary().items()):
                print(f"{stage:<20} {p50:6.2f}  {p95:7.2f}")
            return
        if args.calibrate:
            run_calibration(fp)
        period = 1.0 / fp.cfg.preview_hz
        while not fp.finished.is_set():
            t0 = time.monotonic()
            raw = fp.latest_raw()
            if raw is not None:
                cv2.imshow("Finger processing", draw_hud(raw.image, fp.latest(), fp))
            key = cv2.waitKey(max(1, int((period - (time.monotonic() - t0)) * 1000))) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                run_calibration(fp)
            if key == ord("r"):
                fp.calibration = Calibration.default(fp.cfg.calibration.default_region, fp.screen,
                                                     fp.source.size)
    finally:
        if fp.error:
            print(f"Finger processing stopped after repeated errors:\n{fp.error}")
        fp.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
