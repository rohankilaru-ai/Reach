# Hands mode: evidence

Hands mode makes your hand the mouse. It builds on Kevin's `finger_processing` (MediaPipe, per-finger state,
One Euro smoothing, hand lock, pinch hysteresis, pinch freeze, calibration) and reuses `MacMouse` from
`cursor_demo/run.py`. Start it with:

```
uv run python -m hands_mode.run --control      # real control (repo root); without --control it is a dry run
cd backend && uv run reach hands on|off|status # same, as a child process (pid in .reach/run/hands.pid)
```

Gestures: point to move; pinch thumb to index **or** middle (the closer pair) to click on what is under the
pointer, hold and move to drag; two quick pinches = double click (click state 2, same point); index + middle up
and moving vertically = scroll; open palm held 1 s = leave hands mode.

## What was tested for real (no camera, no real mouse events)

`uv run pytest -q` at the repo root: 74 passed (Kevin's 42 unchanged + 32 hands mode).
`cd backend && uv run pytest -q`: 329 passed (includes 4 new for `reach hands` / the `hands` message, using
`sleep 30` as a harmless stand-in child). `cd apps/overlay && swift test`: 83 passed (includes `handsModeWire`).

- **Direction.** `test_direction_hand_right_moves_cursor_right_and_up_is_up` feeds RAW (unmirrored) camera
  landmarks through Kevin's real `MediaPipeTracker.detect` / `detect_all` (only the model is faked): the hand moving
  to the user's right (raw x falling) moves the cursor right on every step; raising it moves the cursor up.
  Removing Kevin's `1 - x` flip makes both cases fail (mutation checked). Mirroring: Kevin's design flips the
  landmark x once in the tracker (MediaPipe sees the unmirrored image, no pixel copy); mapping and calibration use
  those mirrored landmarks and the preview flips its pixels once for display. This deliberately does NOT mirror the
  image once at capture as was asked: that would restructure Kevin's tracker (and his handedness swap). The two
  flips describe the same view and the tests above check that they agree.
  `test_hud_preview_is_mirrored_and_dot_matches` checks the preview is mirrored and the dot sits on the fingertip.
- **Closest hand, locked.** `control_hand: closest` (additive; default stays `right`) with `tracker.num_hands: 2`:
  unlocked, the largest hand wins; locked, it follows the hand nearest the last wrist and never flips to the other
  hand even when it becomes bigger (stronger than the old worker's 15% hysteresis); if the locked hand leaves it is
  held for `hold_frames` (2 in hands mode), then lost, and only then can the other hand take over.
- **Pinch fingers.** `features.pinch_fingers: index_or_middle` (additive): thumb to the closer of index and
  middle. A point pose with the thumb resting on the curled middle finger is not a pinch (tested). Hands mode
  widens the exit threshold from 0.35 to 0.40 to keep drags from dropping. Only the wider hysteresis was carried over
  from the old worker: its ratio divides by a larger hand size (wrist to knuckle plus knuckle span, about 1.8x
  Kevin's scale), so its 0.25 / 0.40 numbers are not directly comparable.
- **Actions** (pure engine, fake clock, recording mouse): a pinch presses where the cursor is (where you aimed),
  not where the curling index has dragged the pointer, and a drag follows the hand's motion since the press
  (synthetic test; real index-curl landing still needs the live check); index drift below 12 px stays a click; pinch-hold-move = down, dragged events, up; double click; a pinch must
  last 2 frames (1-frame jitter does not click); 0.25 s click cooldown; 2 px dead zone for a still hand; scroll
  direction (fingers up = content up, `--scroll-invert` flips it); no Accessibility = never holds a button.
- **Safety** (the button is released in every case, tested): hand gone, frames stalled, mode stop, takeover (you
  moved the real mouse: 2 s pause), held over 20 s, open palm; fast hand loss: 1-frame flicker keeps the drag,
  3 frames gone release it; a fast downward move with the wrist already low (hand leaving the frame) mid-pinch
  releases and ignores pinches for 0.5 s, while fast downward moves mid-frame still move and drag normally; a
  half-visible hand (thumb/index/middle tip within 3% of the border) never clicks but the cursor still moves, so
  the bottom of the screen stays reachable.
- **Implemented, not exercised by a test:** the SIGINT/SIGTERM handlers (stop the engine, release, exit;
  `reach hands off` sends SIGINT first, then SIGTERM, then SIGKILL), the exception path in `run.py`, the camera
  stall loop (no frame for 1 s: release, "camera stalled", reopen up to 3 times, then exit) and the ipc `hands`
  handler itself (only its message parser is tested).

### Dry-run transcript (synthetic hands through the real pipeline with the hands-mode config)

```
-- point, moving right
   state: point to move · pinch to click
-- middle pinch + release (click)
[dry-run] down 472 479 clicks=1
[dry-run] up 472 479 clicks=1
   state: two fingers · move up or down to scroll
-- index pinch, move, release (drag)
[dry-run] down 472 479 clicks=1
[dry-run] drag 672 479
[dry-run] drag 708 479
[dry-run] drag 747 479
[dry-run] up 747 479 clicks=1
   state: point to move · pinch to click
-- hand vanishes mid-pinch
[dry-run] down 747 479 clicks=1
[dry-run] drag 622 479
[dry-run] up 622 479 clicks=1
   state: no hand
-- open palm held 1 s
   state: hands mode off · open palm
```

### Latency (live camera, dry run, 6 s, no mouse events; MacBook camera, 640x480 MJPG, buffer 1)

```
frames 182  fps 30.1  camera fps 30.0
capture -> cursor target  p50 11.2 ms  p95 18.1 ms
  gate                 p50 0.2  p95 0.5 ms
  landmarker           p50 10.7  p95 17.0 ms
  features             p50 0.1  p95 0.2 ms
  total                p50 11.1  p95 17.4 ms
  capture_to_publish   p50 11.2  p95 18.1 ms
```

Capture to computed cursor target p50 11 ms, p95 18 ms at 30 fps, well under the 50 ms target. The landmarker is
almost all of it; Kevin's pipeline already has the easy wins (newest-frame-only capture thread, buffer 1, MJPG,
idle gating of the landmarker, no pixel copy for mirroring). Camera exposure/transport time before `t_capture` is
not included. The run may have had no hand in view, so `num_hands: 2` cost with two hands is not measured.

## Not tested (owner, live)

- Real cursor control with `--control`: moving, clicking a tab, dragging, double click, scrolling (direction and
  speed on this Mac's natural-scrolling setting), the takeover pause, open-palm exit.
- Two real hands in view (closest-hand lock on real landmarks).
- The overlay's circle gesture by hand: hands mode posts ordinary HID-level left-mouse down/dragged/up events, the
  same path as the trackpad, so holding Option-Space and pinch-dragging should draw the lasso (the lasso view gets
  `mouseDragged` like any drag). Not verified live.
- The menu item 'Hands mode' (sends `{"type":"hands","on":...}`; the backend replies `hands_state`). The checkmark
  shows the last request; it does not yet read `hands_state` back. Spoken "Reach, hands mode on/off" was not added.
- The model: hands mode uses `finger_processing/hand_landmarker.task` and falls back to
  `perception/models/hand_landmarker.task` (already downloaded in the main checkout).
- Scroll speed (up to 15 lines per event) and the HUD label width at 240 px are unverified.
- `perception/` (the old pinch worker) is untouched for comparison.
