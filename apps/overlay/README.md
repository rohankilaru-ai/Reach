# Reach overlay

Native macOS panel for Reach (SwiftPM, AppKit + SwiftUI, macOS 14+). It speaks only the WebSocket contract in
`backend/reach/contracts.py` and holds no API keys, only the session token from `.reach/run/token`.

- **Option-Space** shows or hides the panel. Type a question, press Return.
- **Option-Shift-Space** drags a rectangle on screen. Reach draws a halo around it, saves a PNG to
  `.reach/captures/`, reads its text locally with Vision, and sends it as `selection` with the next ask.
- **Esc** aborts a running ask (and hides the panel when idle).
- The panel shows the state (idle, working, needs you, done, failed), streamed text, evidence cards (click opens
  the PDF, at the page in Safari when a page is known), the receipt line and the last three Drex decisions.

## Build and run

```sh
scripts/build-overlay.sh                     # swift build -c release + ad-hoc signed apps/overlay/build/Reach.app
cd backend && uv run reach serve &           # or: uv run reach serve --replay ../fixtures/events/ask.jsonl
open apps/overlay/build/Reach.app
```

The app finds `.reach/` from `--reach-dir`, then `REACH_DIR`, then the repo path baked into the bundle.

## Key-free check

```sh
cd backend && REACH_DIR=$PWD/../.reach uv run reach serve --replay ../fixtures/events/ask.jsonl &
apps/overlay/.build/release/Reach --replay --reach-dir .reach --selftest "what is 17 times 23"
```

`--selftest "<prompt>"` runs headless (no panel, hotkeys or capture), submits the prompt, prints `[reach]` log
lines and exits 0 when the state reaches done. `swift test --package-path apps/overlay` runs the unit tests.

## Permissions

Region capture needs **Screen Recording** for Reach (System Settings > Privacy & Security). Without it the panel
says so and typed asks still work. Hotkeys use Carbon `RegisterEventHotKey` and need no Accessibility permission.
Each ad-hoc re-sign can reset the Screen Recording grant.
