# Reach overlay

Native macOS panel for Reach (SwiftPM, AppKit + SwiftUI, macOS 14+). It speaks only the WebSocket contract in
`backend/reach/contracts.py` and holds no API keys, only the session token from `.reach/run/token`.

- **Option-Space** shows or hides the panel. Type a question, press Return.
- **Option-Shift-Space** drags a rectangle on screen. Reach draws a halo around it, saves a PNG to
  `.reach/captures/`, reads its text locally with Vision, and sends it as `selection` with the next ask.
- **Control-Option-Space (hold)** talks: key down starts listening, the live partial transcript fills the text
  field, key up stops and sends the final transcript as an ordinary `ask` (the same path as typing). A
  `heard: …` line stays under the field so mistakes are obvious. An empty transcript, or one whose average segment
  confidence is below 0.4, is not sent and the panel says `didn't catch that`; so is a partial when no final
  transcript arrives within 2 s of key up (the log says so).
  Recognition runs on this Mac (`requiresOnDeviceRecognition`); only if on-device speech is unavailable does it
  fall back to Apple's server, and the panel says so while listening. Audio is held in memory, never saved.
- **Esc** cancels listening, aborts a running ask, and hides the panel when idle.
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
Control-Option-Space (never at launch); if either is refused the panel says which one to allow. The bundle is signed
ad hoc without the hardened runtime, so no `com.apple.security.device.audio-input` entitlement is needed
(`codesign -d --entitlements - apps/overlay/build/Reach.app` shows none); add it if the runtime is ever enabled.
