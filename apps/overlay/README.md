# Reach overlay

Native macOS panel for Reach (SwiftPM, AppKit + SwiftUI, macOS 14+). It speaks only the WebSocket contract in
`backend/reach/contracts.py` and holds no API keys, only the session token from `.reach/run/token`.

- Reach lives as a compact dark glass pill (status dot + "Reach") at the top of the main screen, just under the menu
  bar and centred on the notch when there is one. It breathes softly when idle. **Move the mouse up to the top-centre
  (the notch / menu-bar strip above the pill) and it expands**; move away and it folds back after 0.6 s (only when the
  hover opened it), or after about 8 s idle (never while an ask runs, while listening, speaking, with the lens open or
  with text in the field). Drag it anywhere; the spot is remembered per display. Double-click the pill or the panel
  header to snap back under the notch.

**Pill visibility** (menu bar item > Pill, remembered): **Auto** (default) draws nothing at the top of the screen
when idle; the pill appears when the mouse moves to the top-centre notch area, when Option-Space goes down, or when an
answer or needs-you state arrives, and it folds away again about 8 s later (never while listening, working, speaking or
typing). **Always** keeps the pill on screen. **Hidden (hotkey only)** ignores hover; Option-Space and answers still
show it.

**Menu bar item** (a small monochrome dashed circle): Show/Hide pill, Pill: Auto / Always / Hidden, Voice: Cloud /
On-device, Speak replies (checkmark), Set up Reach…, Quit Reach. All settings persist in UserDefaults.

**Set up Reach** (permissions checklist): opened from the menu bar item, from the Fix action, and automatically once
per launch when a required permission is missing. Each row has a live dot (green granted, grey not yet) and a Grant
button: Screen Recording, Microphone and Speech Recognition are required; Accessibility (Do mode only) and Camera (hand
tracking only) are optional and never block; Automation for Chrome, Finder, Notes and TextEdit is listed as "asked the
first time it is used". Grant raises the native prompt and, for Screen Recording, Accessibility or anything already
denied, also opens the exact Privacy & Security pane. The window polls every second; when the required set turns green
it shows "All set" and closes after 1.5 s (a window opened with everything already green stays open). Screen Recording
uses a robust check: `CGPreflightScreenCaptureAccess`, and when that says no, one real 1x1 ScreenCaptureKit capture of
the main display decides. If Reach asked during this run and capture still fails, the row offers **Relaunch Reach**
(opens its own bundle, then quits). The panel's needs-you state for Screen Recording uses the same wording: one
sentence, a Fix action, and "If Reach is already switched on there, remove it with the minus button, add it with the
plus button, then reopen Reach." While Option-Space is held the panel drops under the circle layer, so a loop drawn
under the notch is never covered. Ghost pointer windows size to their label (up to about 220 pt).

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
sentence and one action (Try again, Fix, Open Settings, Relaunch Reach, Retry, or "Hold Option-Space and circle it"). A new ask replaces the
previous answer; the panel folds back to the pill about 8 s after the answer and any spoken reply end. The zoom lens is
placed so it never covers the panel or the circled area; guide pointer labels are cut to 30 characters and fade after
4 s. The text field shows when you tap to type or circle without words.

**Quiet task status.** While a run works, only the slim pill shows: a small spinner, one short line ("Using the
browser…", "Looking at your screen…") and a tiny stop control, which exists only while working. A run that used an
acting tool (browser or Chrome task, note write, Do mode, folder open, pointer steps) never opens the panel: the pill
shows one line for about 3 s and folds away: a green check with "Done · Note saved", an amber "!" with
"Needs you: <one short sentence>" and a small Retry chip, or a red "×" with "Couldn't finish" and Retry (needs-you and
failures stay about 6 s). A soft system sound plays on completion (Tink when done, a quiet Basso otherwise); the menu
bar has a **Sounds** toggle (on by default, remembered). Click or hover the pill for the full answer panel with Details.
Plain questions (no acting tool) still end in the answer panel; it folds about 8 s after reading, and no Stop button
stays once the run has ended (Speak only; the speaker toggle or Esc silences a reply). Done never shows next to Stop or
Try again; Try again shows only on failure. A run that needs you puts a small dot on the menu bar icon until the next
ask. The mapping is pure logic in `TaskEnd.of(receipt, verdict:)` (ReachCore). The expanded card is never empty: with
nothing to show it shows the "Ask Reach…" row (`PanelContent`). In Auto mode nothing is drawn when idle: a pill that
kept key focus after a typed ask no longer counts as busy. Acting tools are the backend's real names (`TaskEnd.actingTools`:
ax_press, ax_set_text, browser_task, chrome_* except list, finder_open/reveal, note_append, shelf/table_to_note, point_at);
reading tools (see_screen, evidence_search, open_source, …) keep a question in the answer panel.

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
`uv run python smoke/pinch_relay_gate.py --overlay apps/overlay/build/Reach.app/Contents/MacOS/Reach`.
`--panel-selftest` shows pill -> panel -> pill and logs each frame (real windows, no input events).
`--permissions-selftest` prints each permission's status and exits (reads only: no prompts, no ScreenCaptureKit, so
Screen Recording is preflight only there).
`--lasso-selftest` runs the circle state machine and the tap/hold key logic on synthetic strokes (no windows).
`--hub-selftest` drives the hub headlessly (no windows, input events or network) and prints one PASS/FAIL line per
scenario: hold -> circle -> release is one ask with transcript + selection, release before the capture finishes,
circle without speech, speech without circle, no stale circle on the next hold, Esc cancel, the zoom word, hover
expand/collapse, failed / unverified / needs_selection / verified receipts, spoken-text cleaning, the quiet status
(note saved -> pill Done, browser not done -> Couldn't finish + Retry, needs you -> amber + Retry, plain question ->
answer panel, stopped -> nothing), the never-empty card, a receipt after run_end ending the working state, and Auto mode hiding
the idle pill.

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

## Cloud voice (default) and on-device fallback
`Voice` setting (UserDefaults `voiceMode`: `cloud` default, `on-device`). In cloud mode the hold-to-talk mic is also
written as 16 kHz mono WAV under `.reach/audio/`; on release the overlay sends `stt` and uses Whisper large-v3-turbo's
text if it is back within 2.5 s, is non-empty and Apple heard something; otherwise Apple's final transcript as before.
The recording is deleted after use. Voice-ask replies ask the backend for `tts` (Kokoro-82M) and play the WAV with
AVAudioPlayer (deleted after playback); on error or after 3 s, AVSpeechSynthesizer speaks instead. Esc or a new hold
stops playback and drops late audio. The overlay never holds a key; the backend calls DeepInfra.
Headless check (needs `reach serve` running): `.build/debug/Reach --reach-dir <repo>/.reach --cloud-voice-selftest "Add 17 apples"`.
Evidence: `docs/evidence/cloud-voice.md`.

## Hands-free

Option-Shift-Space (tap) or the menu bar item **Hands-free listening** turns it on or off; say "Reach, stop listening"
to turn it off by voice. It always starts off at launch. While on, the pill shows a small pulsing dot with an ear
(always visible, even in Auto pill mode), the menu bar icon becomes an ear, and **Pause for 5 minutes** is in the menu.

**Hands mode.** Option-Shift-H (tap), the menu bar item **Hands mode (⌥⇧H)**, or saying "Reach, hands mode on" / "Reach, hands mode off" while hands-free is on (wake word optional), asks the backend to start or stop `hands_mode` (your hand moves the real mouse; started with `--no-preview`, so there is no separate camera window). It refuses to start without Accessibility and shows "needs you" instead; starting twice is a no-op. The backend broadcasts `hands_state` {on, pid, reason, phase} to every overlay when hands mode starts, stops, changes phase, or exits on its own (open palm, crash, camera failure), and once on connect; the menu checkmark and the pill follow that truth, never the last request. While on, the pill always shows a small hand (grey no hand, blue pointing, green pulse pinch/drag, amber paused) with a short label, a one-time 3 s hint "Point to move, pinch to click, open palm to stop", and "Hands off" for 2 s when it ends. A pinch while hands mode is on is a real click and never starts the old selection; starting hands mode stops the old pinch worker (`reach_perception`). The answer card's Details shows one plain line ("Checked by Drex · opened a Chrome tab · 5 s"); raw receipt lines, Drex decisions and cost show only with the menu's **Show technical details** (off by default). `--hands-selftest` checks the toggle, state sync, phases and decoding headlessly.

**Hover.** The panel opens on hover only after a deliberate rest: the cursor must stay within 3 pt inside a 180 x 4 pt strip at the very top-centre edge for 0.7 s, with no mouse button down and no drag; a pass toward the browser's tab bar does nothing. Option-Space and incoming answers still open it at once.

- Listening is on-device only (`SFSpeechRecognizer`, `requiresOnDeviceRecognition`); if this Mac cannot recognize
  on-device, hands-free refuses to start. No audio is saved; only text leaves the recognizer. The task restarts
  about every 50 s, after each utterance, and on errors.
- Utterances end after about 0.9 s without new words or at a sentence end. Fewer than 2 words is ignored unless it
  starts with the wake phrase. The wake phrase is strict: "Reach" or "Hey Reach" at the start, stripped from the ask.
- Each utterance is sent as `utterance {text, source: "always_listening", wake_matched}`; the backend answers
  `addressed` (see `docs/evidence/addressed.md`). Addressed: a normal ask (with a hand selection or circle attached
  in the last 60 s, spoken reply as for voice asks). Not addressed, unreachable, or no reply within 1.5 s: nothing
  happens except a faint flicker of the dot; the text is dropped and never shown, logged or stored.
- A router command shows as a quiet chip; `stop` / `cancel` / `pause` stop speech and abort a running ask locally.
- While Reach speaks, listening pauses and resumes 0.5 s after it stops.
- Pinch: while hands-free is on, a held pinch from the hand worker counts as holding Option-Space (no wake word needed);
  releasing the pinch, or no pinch pointer for 0.6 s, ends it.
- Check: `.build/debug/Reach --handsfree-selftest` (headless: fake segments and replies; no mic, network or prompts).

## Dictation

When the caret is in a text box, Reach types what you say.

- Toggle: Option-Shift-D (tap), the menu bar item **Dictation**, or (with hands-free on) "Reach, start dictating" /
  "Reach, stop dictating". Option-Shift-D works with hands-free off: dictation runs its own recognizer.
- Signal: while on, the pill shows a mic with a red-orange pulse and **Dictating** (hint: Speak to type. Say "stop
  dictating" or press Option-Shift-D to stop). It stops on Esc, on "stop dictating", after 60 s of silence ("Dictation
  off" flashes), and when the focused app changes while a segment is waiting to be typed.
- Where it types: only into the focused editable text field, text area, combo box, or a web input that exposes a value
  and a caret (read through Accessibility). If nothing like that is focused, the pill says "Click a text box first".
- How it types: sets the selected text through Accessibility (inserts at the caret). If the app refuses (many web
  inputs), it saves the clipboard, puts the text on it, posts one Command-V, and restores the clipboard 0.4 s later.
- Speech: each finished phrase (about 1 s pause; never partials) is typed with a leading space when needed, sentence
  capitals, and spoken punctuation ("period", "comma", "question mark", "new line", "new paragraph").
- Commands (exact phrases only): "delete that" removes the last typed phrase (Accessibility range, else Delete keys
  capped at 200, or one Command-Z after a paste), "new line", "stop dictating". Everything else is text.
- **Privacy and safety:** dictation is on-device only (it refuses to start without on-device recognition), saves no
  audio, and makes no model or backend calls. It never types into password fields, never into Terminal, iTerm,
  1Password, Keychain Access, System Settings or any app whose bundle id contains "password", never into Reach's own
  panel, never presses Return, and caps each phrase at 800 characters. The only synthetic keys it sends are one
  Command-V, one Command-Z, and Delete. Without Accessibility it shows "needs you" and opens the Accessibility pane.
- Check: `.build/debug/Reach --dictation-selftest` (headless: fake focused field, fake clipboard, fake keys; no mic, no
  real typing). Unit tests: `swift test --filter DictationTests`.

## Backend auto-start
When no backend is reachable the app starts `uv run reach serve` from the repo's `backend/` folder itself (at most once every 25 s) and logs to `.reach/serve.log`. Server options live in `.reach/server.env` as plain `REACH_*=value` lines (for example `REACH_DO_MODE=1`); keys are never read from it.
