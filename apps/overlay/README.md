# Reach overlay

Native macOS panel for Reach (SwiftPM, AppKit + SwiftUI, macOS 14+). It speaks only the WebSocket contract in
`backend/reach/contracts.py` and holds no API keys, only the session token from `.reach/run/token`.

- Reach lives as a compact dark glass pill (status dot + "Reach") at the top of the main screen, just under the menu
  bar and centred on the notch when there is one. It breathes softly when idle. **Move the mouse up to the top-centre
  (the notch / menu-bar strip above the pill) and it expands**; move away and it folds back after 0.6 s (only when the
  hover opened it), or after about 8 s idle (never while an ask runs, while listening, speaking, with the lens open or
  with text in the field). Drag it anywhere; the spot is remembered per display. Double-click the pill or the panel
  header to snap back under the notch.

One key, **Option-Space**, and one calm hint line: "Hold Option-Space, circle what you mean, say what to do."

- **Hold** (over 0.25 s): listening starts, a level meter shows in the pill and header, and the screen goes soft and
  ready to circle (edge vignette + a halo around the cursor). Draw a freehand loop with your mouse **while you speak**.
  On release the transcript and the circle go out as **one** `ask` (`selection {text, image_path, region, path}`, the
  image masked outside the loop and read locally with Vision). The circle belongs to that one ask; the next hold
  starts clean.
  - Speak without circling: a plain voice ask.
  - Circle without speaking (or the mic/speech permission is off): the panel opens focused on the text field with the
    circle attached as a chip.
  - Say just "zoom" after circling: the zoom lens opens locally (no ask, no network).
  - An empty or low-confidence transcript (average segment confidence below 0.4) is never sent.
- **Tap** (under 0.25 s) opens the typing panel or folds it back. Type, press Return. Typing "zoom" also opens the lens.
- **Esc** cancels everything: the hold, the circle, listening, speech and the lens; it aborts a running ask and folds
  the panel when idle.

The panel stays calm and shows only what matters. A header with the status dot (idle grey, listening and working
ripple, done green, needs-you amber, failed red), one line and a speaker toggle. While listening: only the level meter
and the live words. While working: one line of progress from the running tool ("Looking at your screen…",
"Searching your documents…", "Using the browser…"), never a log. When done: the answer as plain text (markdown and
citation brackets stripped, numbered steps kept; measured and scrollable, up to about 60% of the screen, never past the
bottom), at most two evidence cards (click opens the PDF at the page), a Speak button, and a collapsed **Details**
disclosure (remembered) holding the receipt line, tools, cost and Drex decisions. Failures and needs-you show one plain
sentence and one action (Try again, Open Settings, Retry, or "Hold Option-Space and circle it"). A new ask replaces the
previous answer; the panel folds back to the pill about 8 s after the answer and any spoken reply end. The zoom lens is
placed so it never covers the panel or the circled area; guide pointer labels are cut to 30 characters and fade after
4 s. The text field shows when you tap to type or circle without words.

**Verdicts** come from the receipt's `status`, `reason`, `write_outcome` and `outcome`, never from the answer text:
`Done`; `Needs you: Circle the thing you mean` for `needs_selection`; `Failed: the note write failed`;
`Unverified: the note write could not be read back`; `Failed: the browser task did not finish`; `Failed: <reason>`.

**Spoken replies** use the on-device `AVSpeechSynthesizer`: automatic for voice asks while the header's speaker toggle
is on (default on, remembered), on request (Speak button) for typed asks. At most about 400 characters are spoken,
without citation brackets or markdown. A new hold, a tap, a new ask or Esc stops speech.

**Zoom lens**: a floating, draggable lens showing the masked circle capture at 2x to 4x (slider, pinch, or
Command-scroll). Esc or a click outside closes it.

Voice recognition runs on this Mac (`requiresOnDeviceRecognition`); only if on-device speech is unavailable does it
fall back to Apple's server, and the panel says so. Audio is held in memory, never saved. Reach's own windows are
excluded from captures.

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
`--hub-selftest` drives the hub headlessly (no windows, input events or network) and prints one PASS/FAIL line per
scenario: hold -> circle -> release is one ask with transcript + selection, release before the capture finishes,
circle without speech, speech without circle, no stale circle on the next hold, Esc cancel, the zoom word, hover
expand/collapse, failed / unverified / needs_selection / verified receipts, and spoken-text cleaning.

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
