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

`uv run pytest -q` at the repo root: 71 passed (Kevin's 42 unchanged + 29 hands mode).
`cd backend && uv run pytest -q`: 329 passed (includes 4 new for `reach hands` / the `hands` message, using
`sleep 30` as a harmless stand-in child). `cd apps/overlay && swift test`: 83 passed (includes `handsModeWire`).

- **Direction.** `test_direction_hand_right_moves_cursor_right_and_up_is_up` feeds RAW (unmirrored) camera
  landmarks through Kevin's real `MediaPipeTracker.detect` / `detect_all` (only the model is faked): the hand moving
  to the user's right (raw x falling) moves the cursor right on every step; raising it moves the cursor up.
  Removing Kevin's `1 - x` flip makes both cases fail (mutation checked). Mirroring happens once: landmarks are
  flipped in the tracker, the preview flips the pixels once, mapping and calibration use the mirrored landmarks.
  `test_hud_preview_is_mirrored_and_dot_matches` checks the preview is mirrored and the dot sits on the fingertip.
- **Closest hand, locked.** `control_hand: closest` (additive; default stays `right`) with `tracker.num_hands: 2`:
  unlocked, the largest hand wins; locked, it follows the hand nearest the last wrist and never flips to the other
  hand even when it becomes bigger (stronger than the old worker's 15% hysteresis); if the locked hand leaves it is
  held for `hold_frames` (2 in hands mode), then lost, and only then can the other hand take over.
- **Pinch fingers.** `features.pinch_fingers: index_or_middle` (additive): thumb to the closer of index and
  middle. A point pose with the thumb resting on the curled middle finger is not a pinch (tested). Hands mode
  widens the exit threshold to 0.40 (the old worker's reliable value) to keep drags from dropping.
- **Actions** (pure engine, fake clock, recording mouse): short pinch = click at the down point (index drift below
  12 px does not turn it into a micro-drag); pinch-hold-move = down, dragged events, up; double click; a pinch must
  last 2 frames (1-frame jitter does not click); 0.25 s click cooldown; 2 px dead zone for a still hand; scroll
  direction (fingers up = content up, `--scroll-invert` flips it); no Accessibility = never holds a button.
- **Safety** (the button is released in every case, tested): hand gone, frames stalled, mode stop, takeover (you
  moved the real mouse: 2 s pause), held over 20 s, open palm; fast hand loss: 1-frame flicker keeps the drag,
  3 frames gone release it; hand leaving downward mid-pinch releases and ignores pinches for 0.5 s; a half-visible
  hand (any joint within 3% of the border) never clicks. SIGINT and SIGTERM stop the engine (release) before exit;
  `reach hands off` sends SIGINT first. Camera stall: no frame for 1 s releases, shows "camera stalled", reopens
  the camera up to 3 times, then exits.

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
- `perception/` (the old pinch worker) is untouched for comparison.
