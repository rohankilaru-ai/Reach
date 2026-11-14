"""Reach silent speech worker (EXPERIMENTAL, research weights).

  python -m reach_silent.worker --hyp "explain this" "explain miss"   # typed hypotheses -> backend router; no camera
  python -m reach_silent.worker --selftest                            # model on SYNTHETIC crops: shapes + timing only
  python -m reach_silent.worker --camera-check                        # permission status; opens the camera only if granted
  python -m reach_silent.worker --on                                  # explicit activation: preview + push-to-mouth

Nothing is always-listening: the camera opens only with `--on` (or `--camera-check`), and only after macOS reports
Camera permission as granted. Frames stay in memory; no video or image file is written. A clip is recorded only
while you hold the push-to-mouth key (SPACE: tap to start, tap again to stop; max 4 s) in the preview window.
The backend's answer `uncertain` never acts.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

from . import CKPT, FPS, ROOT
from .commands import PHRASES, local_summary, rescore, transcript_message

LABEL = "EXPERIMENTAL silent speech (research weights)"
PERMISSION_MSG = (
    "Camera permission is not granted for the app running this process (your terminal). macOS: System Settings > "
    "Privacy & Security > Camera, enable it for the terminal, quit and reopen the terminal, then rerun. The camera "
    "was NOT opened."
)
MAX_CLIP_S = 4.0


# ---- camera permission (no camera access needed to ask)


def camera_status() -> str:
    """'authorized' | 'denied' | 'restricted' | 'not_determined' | 'unknown'. Never opens the camera."""
    try:
        import AVFoundation as AVF

        s = int(AVF.AVCaptureDevice.authorizationStatusForMediaType_(AVF.AVMediaTypeVideo))
    except Exception:  # noqa: BLE001 - no pyobjc: treat as not granted
        return "unknown"
    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}.get(s, "unknown")


def open_camera(index: int = 0):
    """The only place the camera is opened. Callers must check camera_status() == 'authorized' first."""
    import cv2

    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def require_camera() -> bool:
    st = camera_status()
    if st != "authorized":
        print(f"[silent] camera permission: {st}. {PERMISSION_MSG}")
        return False
    return True


# ---- backend bridge (localhost WebSocket, contracts.py)


def reach_dir() -> Path:
    return Path(os.environ.get("REACH_DIR", ROOT.parents[1] / ".reach"))


def send_hypotheses(msg: dict, timeout: float = 45.0) -> dict | None:
    """Send the `transcript` message and wait for the routed result. None when no backend is running."""
    run = reach_dir() / "run"
    try:
        port = int(json.loads((run / "server.json").read_text())["port"])
        token = (run / "token").read_text().strip()  # read by code only, never printed
    except (OSError, ValueError, KeyError):
        return None
    try:
        from websockets.sync.client import connect

        with connect(f"ws://127.0.0.1:{port}", open_timeout=3) as ws:
            ws.send(json.dumps({"type": "hello", "token": token, "role": "perception"}))
            if json.loads(ws.recv(timeout=3)).get("type") != "ready":
                return {"error": "backend refused hello"}
            ws.send(json.dumps(msg))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                m = json.loads(ws.recv(timeout=max(0.1, end - time.monotonic())))
                if m.get("type") == "error":
                    return {"error": m.get("message")}
                if m.get("type") == "transcript" and m.get("source") == msg["source"] and m.get("command"):
                    return m
    except Exception as e:  # noqa: BLE001 - backend down is a normal state
        return {"error": f"{type(e).__name__}"}
    return {"error": "timed out waiting for the routed result"}


def describe(result: dict | None, local: dict) -> str:
    """One preview line. `uncertain` always reads as 'did nothing'."""
    if result is None:
        tail = f"not routed (no backend running); local guess {local['command']} p={local['probability']:.2f}, did nothing"
    elif "error" in result:
        tail = f"not routed ({result['error']}); did nothing"
    elif result.get("uncertain"):
        tail = f"uncertain p={result.get('probability') or 0:.2f}: did nothing"
    else:
        tail = f"{result['command']} p={result.get('probability') or 0:.2f}"
    return f"[{LABEL}] {tail}"


def route_hypotheses(nbest, closed=None, send: bool = True) -> tuple[dict, dict | None, dict]:
    ranked = rescore(nbest, closed)
    msg = transcript_message(ranked)
    local = local_summary(ranked, closed)
    result = send_hypotheses(msg) if send else None
    return msg, result, local


# ---- modes


def cmd_hyp(args) -> int:
    msg, result, local = route_hypotheses([(h, None) for h in args.hyp], send=not args.no_send)
    print("message:", json.dumps(msg))
    if result and "error" not in result:
        print("result:", json.dumps({k: result.get(k) for k in ("command", "probability", "uncertain", "reason")}))
    print(describe(result, local))
    return 0


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # bytes on macOS


def cmd_selftest(args) -> int:
    """SYNTHETIC mouth-crop tensors through the real model: proves shapes, load time, memory and latency. This is
    NOT an accuracy test (random pixels have no words in them)."""
    import numpy as np

    print(f"[selftest] {LABEL}. Synthetic crops: NOT an accuracy test.")
    if not CKPT.exists():
        print(f"[selftest] checkpoint missing ({CKPT}); run setup.sh. Typed path only: --hyp")
        return 2
    t0 = time.perf_counter()
    from .lipreader import LipReader

    reader = LipReader(args.device, beam_size=args.beam)
    load_s = time.perf_counter() - t0
    print(f"[selftest] model loaded in {load_s:.1f}s on {reader.device_name}; peak RSS {_rss_mb():.0f} MB")
    rng = np.random.default_rng(0)
    report = {"load_s": round(load_s, 1), "device": reader.device_name, "beam": args.beam, "clips": []}
    try:
        for secs in (1.5, 1.5, 3.0):  # first clip includes MPS warm-up
            crops = rng.integers(0, 255, (int(secs * FPS), 96, 96, 3), dtype=np.uint8)
            t = time.perf_counter()
            out = reader.read(crops, list(PHRASES.values()), n=5)
            total = time.perf_counter() - t
            assert len(out["closed"]) == len(PHRASES) and out["nbest"], "empty output"
            msg, result, local = route_hypotheses(out["nbest"], out["closed"], send=args.send)
            assert msg["type"] == "transcript" and msg["hypotheses"] and msg["experimental"] is True
            report["clips"].append({"seconds": secs, "total_ms": round(total * 1000), **out["timings_ms"]})
            print(f"[selftest] {secs:.1f}s clip ({len(crops)} frames): {total * 1000:.0f} ms {out['timings_ms']}; "
                  f"top hyp {msg['hypotheses'][0]!r}; {describe(result, local) if args.send else 'not sent'}")
    finally:
        reader.close()
    report["peak_rss_mb"] = round(_rss_mb())
    print("[selftest] " + json.dumps(report))
    print("[selftest] OK (shapes and timing only; no accuracy measured)")
    return 0


def cmd_camera_check(args) -> int:
    if not require_camera():
        return 2
    cap = open_camera(args.camera)
    try:
        n, t0 = 0, time.monotonic()
        shape = None
        while time.monotonic() - t0 < 3:
            ok, frame = cap.read()
            if ok:
                n, shape = n + 1, frame.shape
        print(f"[camera-check] {n} frames in 3 s ({n / 3:.1f} fps), frame {shape}; nothing written to disk")
        return 0 if n else 2
    finally:
        cap.release()


def cmd_on(args) -> int:
    """Explicit activation: preview window with push-to-mouth. Frames live in memory only."""
    if not require_camera():
        return 2
    import cv2
    import numpy as np

    from .lipreader import LipReader
    from .mouth import MouthCropper, resample_to_fps

    print(f"[silent] {LABEL}: loading the model ...", flush=True)
    reader, cropper = LipReader(args.device, beam_size=args.beam), MouthCropper()
    cap = open_camera(args.camera)
    win = "Reach silent speech - EXPERIMENTAL"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    frames, stamps, recording, last = [], [], False, "SPACE: start/stop a clip   q: quit"
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[silent] camera stopped delivering frames")
                return 2
            now = time.monotonic()
            if recording:
                frames.append(frame)
                stamps.append(now)
            disp = cv2.flip(frame, 1)
            cv2.putText(disp, "EXPERIMENTAL - research weights", (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
            cv2.putText(disp, ("REC " if recording else "") + last[:90], (16, disp.shape[0] - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255) if recording else (0, 220, 0), 2)
            cv2.imshow(win, disp)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            stop = recording and (key == 32 or now - stamps[0] > MAX_CLIP_S)
            if key == 32 and not recording:
                frames, stamps, recording = [], [], True
                continue
            if not stop:
                continue
            recording = False
            if len(frames) < 10:
                last = "clip too short; hold the clip for the whole command"
                continue
            try:
                clip = resample_to_fps(frames, stamps)[..., ::-1]  # BGR -> RGB, in memory
                crops, _found = cropper(np.ascontiguousarray(clip))
                out = reader.read(crops, list(PHRASES.values()), n=5)
                msg, result, local = route_hypotheses(out["nbest"], out["closed"])
                last = f"'{msg['hypotheses'][0]}' -> " + describe(result, local).split("] ", 1)[1]
            except RuntimeError as e:
                last = f"no read: {e}"
            frames, stamps = [], []
            print(f"[silent] {last}", flush=True)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        cropper.close()
        reader.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--on", action="store_true", help="turn silent speech on: camera preview + push-to-mouth")
    mode.add_argument("--hyp", nargs="+", metavar="TEXT", help="typed hypotheses, best first (no camera, no model)")
    mode.add_argument("--selftest", action="store_true", help="model on synthetic crops: shapes/timing, NOT accuracy")
    mode.add_argument("--camera-check", action="store_true", help="report camera permission; 3 s probe if granted")
    ap.add_argument("--no-send", action="store_true", help="with --hyp: print the message, do not contact the backend")
    ap.add_argument("--send", action="store_true", help="with --selftest: also send the synthetic hypotheses")
    ap.add_argument("--device", default="hybrid", choices=["hybrid", "cpu", "mps"])
    ap.add_argument("--beam", type=int, default=20)
    ap.add_argument("--camera", type=int, default=0)
    args = ap.parse_args(argv)
    if args.hyp:
        return cmd_hyp(args)
    if args.selftest:
        return cmd_selftest(args)
    if args.camera_check:
        return cmd_camera_check(args)
    if args.on:
        return cmd_on(args)
    print(f"{LABEL} is OFF. Turn it on explicitly with --on (see --help). The camera was not opened.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
