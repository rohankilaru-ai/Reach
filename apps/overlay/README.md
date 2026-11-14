# Reach overlay

Native macOS panel for Reach (SwiftPM, AppKit + SwiftUI, macOS 14+). It speaks only the WebSocket contract in
`backend/reach/contracts.py` and holds no API keys, only the session token from `.reach/run/token`.

- Reach lives as a slim pill (status dot + "Reach", hotkey hint on hover) at the top of the main screen, just under
  the menu bar and centred on the notch when there is one. It expands downward into the panel on the hotkey, a voice
  hold, a hand selection or an incoming answer, and folds back after about 8 s idle (never while an ask runs, while
  listening, or with text in the field). Drag it anywhere; the spot is remembered per display. Double-click the pill
  or the panel header to snap back under the notch. It never takes focus except when you summon it to type.
One key, **Option-Space**. The panel's hint line says it: "Tap Option-Space to type, hold to talk, or circle
something."

- **Tap** (under 0.25 s) opens the panel to type, or folds it back. Type a question, press Return.
- **Hold** talks: listening starts once the key has been held 0.25 s, the live partial transcript fills the text
  field, and releasing sends the final transcript as an ordinary `ask` (the same path as typing). A `heard: …` line
  stays under the field so mistakes are obvious. An empty transcript, or one whose average segment confidence is
  below 0.4, is not sent and the panel says `didn't catch that`; so is a partial when no final transcript arrives
  within 2 s of release (the log says so).
- **Circle** (the lasso button in the panel): a transparent layer covers the screen and you draw a freehand loop
  around anything with your mouse. The stroke glows as you draw and closes itself on release, then a halo traces the
  loop. Reach captures the loop's bounding box, masks everything outside the loop (transparent), saves the PNG to
  `.reach/captures/circle-<ts>.png`, reads its text locally with Vision, and sends `selection {text, image_path,
  region, path}` with the next ask (`path` is the simplified loop, at most 64 points, screen points with a top-left
  origin). Strokes under 40 pt across are ignored with "draw a bigger circle". Esc cancels.
  Recognition runs on this Mac (`requiresOnDeviceRecognition`); only if on-device speech is unavailable does it
  fall back to Apple's server, and the panel says so while listening. Audio is held in memory, never saved.
- **Esc** cancels a circle or listening, aborts a running ask, and folds the panel back when idle.
- The panel shows the state (idle, working, needs you, done, failed), streamed text, evidence cards (click opens
  the PDF, at the page in Safari when a page is known), the receipt line and the last three Drex decisions.

## Build and run

```sh
scripts/build-overlay.sh                     # swift build -c release + ad-hoc signed apps/overlay/build/Reach.app
cd backend && uv run reach serve &           # or: uv run reach serve --replay ../fixtures/events/ask.jsonl
open apps/overlay/build/Reach.app
```

The app finds `.reach/` from `--reach-dir`, then `REACH_DIR`, then the repo path baked into the bundle.

## Pinch selection (EXPERIMENTAL)

The camera worker's pinch-drag reaches the overlay through the server (`backend/reach/relay.py`). A relayed
`selection` opens the panel without taking focus and shows a "Selected by hand · experimental" chip with its first
line, a thumbnail when there is an image, and a clear button. It is used by the next typed or voice ask. Hand
`pointer` messages move the ghost pointer, which fades 1 s after the last one. Real-hand use is untested; the gate is
`uv run --project backend python smoke/pinch_relay_gate.py --overlay apps/overlay/build/Reach.app/Contents/MacOS/Reach`.
`--panel-selftest` shows pill -> panel -> pill and logs each frame (real windows, no input events).
`--lasso-selftest` runs the circle state machine and the tap/hold key logic on synthetic strokes (no windows).

## Key-free check

```sh
cd backend && REACH_DIR=$PWD/../.reach uv run reach serve --replay ../fixtures/events/ask.jsonl &
apps/overlay/.build/release/Reach --replay --reach-dir .reach --selftest "what is 17 times 23"
```

Voice, without a microphone (run through the bundle so macOS reads its usage strings):

```sh
mkdir -p .reach && say -o .reach/voice-test.aiff "what is twelve times thirty four"
open -n -W --stdout /tmp/reach-voice.log apps/overlay/build/Reach.app --args --replay --reach-dir $PWD/.reach \
  --voice-selftest $PWD/.reach/voice-test.aiff --selftest-submit
cat /tmp/reach-voice.log; rm .reach/voice-test.aiff
```

`--voice-selftest <audio-file>` transcribes the file on device with `SFSpeechURLRecognitionRequest`, prints the
transcript, per-segment confidences and the gate verdict; `--selftest-submit` then sends it as an ask and prints the
same lines as `--selftest`. Exit 4 means Speech Recognition is not allowed for Reach.

`--selftest "<prompt>"` runs headless (no panel, hotkeys or capture), submits the prompt, prints `[reach]` log
lines and exits 0 when the state reaches done. `swift test --package-path apps/overlay` runs the unit tests.

## Permissions

Region capture needs **Screen Recording** for Reach (System Settings > Privacy & Security). Without it the panel
says so and typed asks still work. Hotkeys use Carbon `RegisterEventHotKey` and need no Accessibility permission.
Each ad-hoc re-sign can reset the Screen Recording grant.

Voice needs **Microphone** and **Speech Recognition** for Reach. macOS asks on the first hold of
Option-Space (never at launch); if either is refused the panel says which one to allow. The bundle is signed
ad hoc without the hardened runtime, so no `com.apple.security.device.audio-input` entitlement is needed
(`codesign -d --entitlements - apps/overlay/build/Reach.app` shows none); add it if the runtime is ever enabled.
