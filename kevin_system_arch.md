# AirTouch: camera-based touchscreen emulation with Drex

> Working name. Rename freely.

This README is both the project spec and the instruction file for Claude Code. Read all of it before writing code. Build in the milestone order in section 11, and stop to ask the user whenever something in "Known unknowns" (section 13) blocks you.

---

## 1. What we are building

A platform that makes a laptop (or a TV driven by a laptop or mini-PC over HDMI) behave as if it had a touchscreen, using only a camera. The user moves a finger in front of the camera and:

- the pointer follows the fingertip,
- a pinch gesture clicks or drags,
- a two-finger vertical motion scrolls the page (primary demo: scrolling through a research-paper PDF in a browser),
- (stretch) a virtual keyboard where a pinch "presses" the key under the fingertip.

This is for the **Nace AI Drex hackathon**. Drex must be a real, measurable part of the system (see section 3, rule 2), not decoration.

---

## 2. Architecture: System 0 / 1 / 2

```
Camera
  |
  v
[System 0: perception]  local, every frame (~30 fps)
  - hand tracking (MediaPipe), smoothing, velocity, frame gating
  |
  +-- fast lane (direct, no model in the loop) ----------------------+
  |     fingertip position, scroll velocity                          |
  |                                                                  v
  +-- gesture window (landmark JSON) --> [System 1: Drex] --> Decision --> [policy] --> Mode
        async, ~5-10 Hz or on change     classification only                              |
                                                                                          v
                                                          [System 2: actor] local, every frame
                                                          reads Mode (from S1) + Frame (from S0)
                                                          and emits OS input events
```

| System | Responsibility | Where it runs |
|---|---|---|
| **0: perception** | Camera frames to hand landmarks, smoothing, velocity features, frame gating. Drives the fast lane. | Local, every frame |
| **1: classification** | Landmark window in, gesture label plus probabilities out. Classification only. Uses a Drex model. | Async, throttled; API or local weights |
| **2: actor** | Turns the current mode plus S0's live parameters into real input events (move, click, drag, scroll). | Local, every frame, deterministic code |

**System 2 is not an LLM agent.** It is plain deterministic code. System 1 decides *what mode the user is in*; System 2 *does the thing*.

---

## 3. Non-negotiable design rules

1. **No model in the pointer loop.** Cursor position and scroll speed come directly from System 0 to System 2 every frame. Drex only chooses the *mode*. Reported Drex Max median latency is ~183 ms (vendor-reported, API); that is far too slow to drive a cursor.
2. **Drex must add measurable value.** Always keep a local heuristic classifier (finger-extension and pinch-distance rules) as a baseline and fallback. The evaluation harness (section 10) must report Drex vs the heuristic baseline. If Drex does not beat the baseline on some metric, find out why and say so honestly in the demo.
3. **System 1 never blocks the frame loop.** Drex calls run on a background thread/async task. Frames keep flowing with the last known mode.
4. **Graceful degradation.** If the Drex API is slow, times out, or errors, System 1 falls back to the local heuristic classifier. The demo must never freeze.
5. **Sticky modes.** A mode changes only when the policy layer accepts a decision (section 6). No flicker.
6. **Safety first for actuation.** Kill hotkey, clicks only in the right modes, no destructive actions (see section 12).

---

## 4. Data contracts

Use typed objects (dataclasses or pydantic) so each system can be tested alone and models swapped.

```python
# System 0 -> System 1 and System 2   (one per frame)
Frame = {
  "t": float,                        # monotonic seconds
  "landmarks": list[tuple[float, float, float]],  # 21 x (x, y, z), normalized image coords
  "handedness": "left" | "right",
  "velocity": (float, float),        # smoothed fingertip velocity, normalized units/sec
  "pinch_dist": float,               # thumb-tip to index-tip, scaled by hand size
  "fingers_extended": dict[str, bool],  # thumb/index/middle/ring/pinky (derived)
  "hand_present": bool,
}

# System 1 -> policy
Decision = {
  "label": "idle" | "point" | "pinch" | "scroll",
  "probs": dict[str, float],         # probability per label
  "margin": float,                   # top prob minus runner-up prob
  "source": "drex" | "heuristic",    # which classifier produced it
  "latency_ms": float,
}

# policy -> System 2
Mode = {
  "name": "IDLE" | "POINT" | "CLICK" | "DRAG" | "SCROLL",
  "since_t": float,
}
```

System 2 consumes `Mode` (from S1 via the policy) and `Frame` (from S0) every frame.

---

## 5. Gestures and modes

Defaults (make configurable in `config.yaml`; tune after testing with real hands):

| Mode | Gesture | System 2 behavior |
|---|---|---|
| `IDLE` | Open palm, fist, or no hand | Do nothing |
| `POINT` | Index finger extended, others curled | Move cursor to calibrated fingertip position |
| `CLICK` | Quick pinch then release (< ~400 ms) | Left click at the cursor position |
| `DRAG` | Pinch held and moved | Mouse down, move, mouse up on release |
| `SCROLL` | Index + middle extended, moving vertically | Wheel events proportional to vertical velocity (optional inertia, optional natural-scroll flip) |

Drex classifies the **static pose** into `idle | point | pinch | scroll`. The `CLICK` vs `DRAG` split comes from *timing* handled by the policy/actor (pinch duration and movement), not from Drex.

---

## 6. Policy layer (between System 1 and System 2)

Starting parameters (all configurable):

- `p_min = 0.70`: top probability must exceed this to accept a decision.
- `margin_min = 0.20`: top probability minus runner-up must exceed this.
- `confirm_n = 2`: require N consecutive agreeing decisions before switching mode.
- `min_dwell_ms = 150`: don't switch mode again sooner than this.
- `click_cooldown_ms = 300`: ignore new clicks right after one.
- `idle_timeout_ms = 800`: if no hand for this long, go to `IDLE`.
- Rejected or low-confidence decisions: keep the current mode (and log them).

---

## 7. System 0 (perception) details

- **Hand tracking:** MediaPipe Hands / Hand Landmarker, 21 landmarks per hand (verify the current MediaPipe Tasks API; the legacy `mp.solutions.hands` API may be deprecated).
- **Smoothing:** One Euro filter on the fingertip position (start with `min_cutoff=1.0, beta=0.007` and tune).
- **Calibration:** Four-corner routine at startup. Fit a homography or simple affine map from a camera-space active region to screen coordinates. Persist the calibration to disk.
- **Derived features:** per-finger extended flags, `pinch_dist` normalized by hand size (e.g., wrist-to-middle-MCP distance), fingertip velocity.
- **Frame gating (OpenCV):** Frame differencing / hand-presence gating so Drex is not called when nothing is happening. This is cost control.
- **Do NOT feed Canny/edge images to MediaPipe.** It expects ordinary RGB; edges would hurt landmarks. OpenCV edge/contour work belongs only in: keyboard/screen localization (contours + perspective transform), frame gating, cropping, and fallback fingertip detection. Use CLAHE/exposure normalization for bad lighting.
- Mirror the camera image horizontally so motion feels natural.

---

## 8. System 1 (Drex classification) details

### Models

| Model | Use here | Notes |
|---|---|---|
| **Drex v1.5** (~9B, text-only, open weights) | Default: gesture classification from landmark JSON | Lightest; best candidate for local inference |
| **Drex v2.0** (35B total / ~3B active MoE, text/JSON, 128K ctx, open weights) | Upgrade if v1.5 accuracy is short | Large weights (~70 GB bf16, ~37 GB 8-bit); use API unless a big GPU is available |
| **Drex Max** (multimodal, API-only) | **Stretch only**: screen-aware decisions (semantic scroll landing, verify action, snap click target) | Takes screenshots/images; do not use for per-frame work |

Open-weight models use a modified Open RAIL-M license with restricted commercial use; fine for the hackathon, check before productizing.

### Input to Drex

State = JSON for the last N frames (start N = 8-10) of **normalized** features, not raw pixels:

- landmarks translated to be wrist-relative and scaled by hand size,
- `pinch_dist`, `fingers_extended`, fingertip velocity.

Question = a `choice` question over `["idle", "point", "pinch", "scroll"]`, with a clear description of each option. Optionally add a `noul` (yes/no) question such as "is the user intentionally interacting?" in the same request (several questions about one state share a single pass).

Derived features (finger-extended flags, pinch distance) probably make Drex more reliable than raw coordinates alone. Test that as an ablation.

### API notes (from third-party reporting; VERIFY against official docs)

- Drex is wire-compatible with TypeSafe's Jev: `POST /v1/systemone` with `model`, `state`, `questions` (and `images` for Drex Max).
- Model IDs: `drex-v1.5`, `drex-v2.0`, `drex-max`. **`drex-latest` does NOT point at Drex Max.**
- Question types: `choice` (probability per option), `noul` (yes/no, returns P(true)), `score` (ordered levels).
- The model returns probabilities only, no text.
- The official docs are at `console.nace.ai/docs/drex`. The exact base URL, auth header, request/response schema, rate limits, and image limits were NOT verified. **Read the docs (or ask the user for them) before writing the real client.** Until then, implement the client behind an interface with a `StubDrexClient` so everything else can be built and tested.
- The official TypeSafe SDK reportedly works against Drex with only the base URL, key, and default model changed. This is an option if the schema matches.

### Client requirements

- Interface: `classify(window: list[Frame]) -> Decision`.
- Implementations: `HeuristicClassifier` (local), `DrexClient` (real), `StubDrexClient` (fake, configurable latency and noise for testing).
- Runs in a background worker; the main loop reads the latest `Decision` non-blockingly.
- Hard timeout (e.g., 400 ms). On timeout or error: log it, fall back to `HeuristicClassifier` for that decision.
- Throttle: ~5-10 calls/s, plus an immediate call on a change trigger from S0 (for example, pinch distance crossing a threshold).
- Log every request/response (latency, probs, label) to a JSONL file for evaluation.

---

## 9. System 2 (actor) details

- Library: `pynput` or `pyautogui` for cursor, click, drag, wheel. Prefer whichever gives smooth wheel events on the target OS.
- Runs every frame; reads `Mode` + latest `Frame`.
- `POINT`: move cursor to the calibrated position.
- `CLICK`/`DRAG`: press/release with the timing rules in section 5.
- `SCROLL`: wheel delta = gain x vertical velocity, with a dead zone and optional inertia decay. Gain, dead zone, and direction are configurable.
- Platform notes: macOS needs Accessibility and Camera permissions for the terminal/app. Linux Wayland often blocks synthetic input from pyautogui; use X11 or `uinput`. Windows works with `pynput`/`pyautogui`; touch-injection APIs are a stretch goal.
- A global kill switch (hotkey) must stop all actuation immediately and be tested first.

---

## 10. Evaluation (this is what proves Drex helps)

Build `eval/` early. It should work without a camera.

**Datasets**

- **HaGRID**: 552,992 FullHD images, 18 gesture classes plus a `no_gesture` class, with bounding boxes and 21 landmark annotations; CC BY-SA 4.0; the full set is ~716 GB, so **download only a small subset** (e.g., ~500 images per used class). Verify class names and map them to our four labels (e.g., `one` -> `point`, `two_up`/`peace` -> `scroll`, `palm`/`fist`/`no_gesture` -> `idle`). Pinch is not a clean HaGRID class.
- **Own recorded data**: write a `record.py` that captures labeled landmark windows from the webcam for `pinch` and `scroll` (a few hundred windows per label, several lighting conditions and people if possible). This is likely necessary because HaGRID is static and lacks a proper pinch.
- **Stretch (screen decisions)**: ScreenSpot (600+ screenshots, 1,200+ instructions across iOS/Android/macOS/Windows/web) for click-snapping evaluation. For semantic scroll, generate labels automatically: render arXiv PDFs to images with PyMuPDF, which gives the exact positions of headings and figures.

**Pipeline:** images -> MediaPipe landmarks -> windowed features -> {HeuristicClassifier, Drex v1.5, Drex v2.0} -> compare to labels.

**Metrics to report:**

- classification accuracy and per-class F1,
- mode-flicker rate (mode changes per minute while holding a pose),
- accidental clicks per minute (live),
- decision latency (p50/p95),
- calibration quality: are the probabilities trustworthy? (accept/reject curves vs threshold),
- end-to-end task success: scroll to a target section in a PDF within N seconds.

Report **Drex vs heuristic baseline** in one table. Be honest if the baseline wins on a metric.

---

## 11. Milestones (build in this order)

Each milestone must run and be demonstrable before starting the next.

1. **M0, skeleton:** project layout, config loading, camera capture, MediaPipe landmarks drawn on a preview window. *Done when:* live overlay at ~30 fps.
2. **M1, fast lane:** calibration routine, One Euro smoothing, cursor follows fingertip. *Done when:* the cursor tracks smoothly with calibration saved and reloaded. Kill hotkey works.
3. **M2, local modes + actor (no Drex):** `HeuristicClassifier`, the policy layer, the System 2 actor (point, click, drag, scroll). *Done when:* you can scroll a PDF in a browser and click links by hand with no network.
4. **M3, Drex integration:** `StubDrexClient`, then the real `DrexClient` (after the docs are checked), async worker, timeout, fallback, JSONL logging. *Done when:* switching between heuristic and Drex via config works, and killing the network gracefully falls back.
5. **M4, evaluation harness:** the section 10 pipeline on a HaGRID subset plus own recordings; produce the comparison table. *Done when:* a single command prints Drex vs baseline.
6. **M5, demo polish:** on-screen HUD showing the current mode, probabilities, and latency; a scripted research-paper demo; a fallback-mode demo. *Done when:* a 2-3 minute demo runs reliably in poor lighting.
7. **M6, stretch (only if time):** Drex Max for semantic scroll landing (screenshot -> "next heading / next figure / next page" choice), post-action verification (yes/no), click snapping to nearby elements, and the virtual keyboard (pinch presses the key nearest the fingertip, with a Drex choice among the 2-4 nearest keys).

---

## 12. Safety

- Global kill hotkey, implemented and tested in M1.
- Actuation only when `hand_present` and mode is valid; auto-release mouse buttons on hand loss, mode change to `IDLE`, or crash (use `try/finally`).
- No keyboard typing, no destructive shortcuts, and no actions outside normal mouse/scroll in the core milestones.
- Do not log or upload camera images. Only landmarks (and screenshots in the M6 stretch) leave the machine, and only to the Drex API. Say so in the demo.
- Keep API keys in environment variables, never in the repo.

---

## 13. Known unknowns (ask the user / verify; do not guess)

- **Hackathon rules:** required model(s), track, deadline, judging criteria, submission format. The user has not shared these. Ask early.
- **Drex API specifics:** base URL, auth, exact request/response schema, rate limits, image limits, pricing/credits.
- **Target platform:** OS of the demo laptop; whether a TV is used (assumed: laptop or mini-PC over HDMI).
- **Meaning of "key presses":** assumed a virtual/on-screen keyboard with pinch-to-press. Confirm before building M6.
- **Local Drex latency:** unmeasured. Benchmark Drex v1.5 locally vs the API.
- **Whether Drex Max handles non-screenshot photos (e.g., hand crops) well:** untested; A/B it if used.

---

## 14. Suggested repo layout

```
airtouch/
  README.md
  config.yaml
  pyproject.toml
  src/airtouch/
    __init__.py
    config.py
    contracts.py            # Frame, Decision, Mode
    system0/
      capture.py            # camera loop
      tracker.py            # MediaPipe wrapper, derived features
      smoothing.py          # One Euro filter
      calibration.py
      gating.py             # OpenCV frame/hand gating
    system1/
      base.py               # classify(window) -> Decision interface
      heuristic.py
      drex_client.py        # real client (after docs are verified)
      stub_client.py
      worker.py             # async/threaded, throttle, timeout, fallback
      features.py           # window -> normalized JSON state
    policy.py               # thresholds, sticky modes, click/drag timing
    system2/
      actor.py              # OS input events
      killswitch.py
    hud.py                  # on-screen mode/probs/latency overlay
    main.py
  eval/
    record.py               # record labeled landmark windows
    prepare_hagrid.py       # subset download notes + landmark extraction
    run_eval.py             # Drex vs heuristic comparison table
  data/                     # gitignored
  logs/                     # gitignored (JSONL decision logs)
  tests/
```

## 15. Tech stack

Python 3.11+, `opencv-python`, `mediapipe`, `numpy`, `httpx` (or the vendor SDK), `pynput` or `pyautogui`, `pydantic`, `pytest`. Keep dependencies minimal. Pin versions in `pyproject.toml`.


## 16. Working agreements for Claude Code

- Build milestone by milestone; run and test each before moving on.
- Prefer small, tested modules; unit-test `policy.py`, `features.py`, and `smoothing.py` with synthetic data.
- Never call the real Drex API in tests; use `StubDrexClient`.
- When a fact about the Drex API is uncertain, say so, read the docs, or ask the user. Do not invent endpoints or response fields.
- Keep the README's architecture and rules in sync with the code; update this file when decisions change.