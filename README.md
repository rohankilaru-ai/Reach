# Reach

**A desktop assistant you can type to, talk to, and point at. Drex decides, Intern runs it, and every action is checked.**

Reach sits at the top of your Mac screen. Ask it a question, hold a key and speak, or select something on screen. It answers from your documents with cited quotes, looks at the part of the screen you selected, runs a browser task, and writes results into your notes. It then reads the destination back to check the write landed.

Built for the Drexathon, Wildcard track. Hackathon code written on 2026-11-14.

## What runs where

| Part | What it is | Role |
| --- | --- | --- |
| **Intern** (Nace harness, v0.30.1) | The agent loop. Every ask opens a real Intern Session. | Runs tools, keeps the journal, holds the tool guard |
| **Drex Max** (`drex-max-v1.1`) | Nace's decision model | **System 1.** Supervises each turn, judges each tool call, picks which of 12 commands you meant |
| **Qwen3.8-27B on DeepInfra** | Language model | **System 2.** Plans and writes the answers you read |
| **NDI** (`nace-sdk`) | Document pipeline | Parses PDFs, and grounds each quoted passage to a page and region |
| **Nace browser harness** (patched) | Browser agent | Runs web tasks; Drex Max makes the page actions |
| **Qwen3-VL on DeepInfra** | Vision model | Reads a screen region; also the browser harness's helper |

Drex Max does not write the answers. It answers typed questions with calibrated probabilities in about 250 ms, and Reach uses those to supervise, gate and route. The text you read comes from Qwen.

```
 overlay (Swift)  ──► localhost WebSocket ──►  Reach backend (Python)
 hotkeys, voice,          token-protected       │
 selection, pinch*                              ▼
                                         Intern Session
                                   System 2 Qwen3.8-27B  ◄─ judged by ─►  System 1 Drex Max
                                                │
        ┌───────────────┬───────────────────────┼─────────────────┬──────────────┐
   evidence_search   browser_task        note_append /       see_region     reach_now
   (NDI, cited)    (Nace harness)       shelf_to_note       (Qwen3-VL)
                                        (read back)
```
`*` experimental

## Status

Labels: **implemented** = a gate passed on a fresh input; **experimental** = runs, not reliable or not tested live; **planned** = not built.

| Feature | Label | Evidence |
| --- | --- | --- |
| Intern as the harness, Drex Max as System 1, Qwen3.8-27B on DeepInfra as System 2 | implemented | Drex log and receipt: recorded at release (`docs/evidence/drex-log.jsonl`, `docs/evidence/receipt.json`, not yet committed) |
| Drex decisions in the log (supervise, guard, wrap-up, memory, command routing) | implemented | `reach log` |
| NDI evidence: ingest a PDF, cited quotes, ground to page and region | implemented | two real PDFs, 5 of 5 cited quotes found verbatim; `docs/GROUND_SHAPE.md` |
| Browser task through an Intern tool, with Drex judging each call | implemented | `docs/evidence/browser-run.json`; abort stops the browser in under 0.3 s |
| Verified notes: write to a notes file, TextEdit or Notes, then read back (`write_outcome`) | implemented | receipt field `write_outcome` |
| Screen region question (`see_region`) | implemented, accuracy varies | a labelled image was read correctly; a chess position was read but the model did not call checkmate |
| Overlay: hotkeys, streamed answers, evidence cards, receipt line | implemented | `apps/overlay/README.md`; not every path tried by hand |
| Voice: hold Option-Space, on-device speech | experimental | transcribed a generated audio file at 98% confidence; live microphone untested |
| 12-command router and benchmark against Qwen | experimental | `docs/evidence/bench_v2.md`: on 90 held-out hard utterances Drex Max got 87 right (96.7%), Qwen3.8-27B 83 (one-label) and 82 (12-way); the intervals overlap, so this is a lead, not a proven win. Drex made 0 wrong commands, Qwen 2 to 3. Median latency 265 ms vs 544 ms (one-label Qwen). Command line only |
| Pinch-to-select with MediaPipe hands | experimental | passes synthetic landmark tests; no real-hand run yet |
| Silent speech: lip-reading -> 12-command router (`perception/silent/`, research-only weights) | experimental | model runs on this Mac (0.4 s per 1.5 s clip after warm-up); typed hypotheses route through Drex live; no accuracy measured, no live mouthed run yet. See `perception/silent/README.md` |
| Per-finger tracking, calibration and a cursor-follows-fingertip demo (`finger_processing/`, `cursor_demo/`) | experimental | added by a teammate with its own tests; not part of the agent tools |
| Lip reading / silent speech | planned | not built |
| Controlling other apps by mouse or keyboard | planned (deliberately not done) | Reach only acts through apps' scripting interfaces and files |
| Gaze, Adobe and Keenable integrations | planned | |

## Quick start

Needs macOS 14+ on Apple Silicon, [uv](https://docs.astral.sh/uv/), Node 22.18+, a recent Xcode (for the overlay), and two keys.

```bash
cp .env.example .env        # then add NACE_API_KEY and DEEPINFRA_API_KEY yourself
cd backend && uv sync
export INTERN_HOME=$PWD/../.intern-home && mkdir -p "$INTERN_HOME" && ln -sf "$PWD/../.env" "$INTERN_HOME/.env"

uv run reach add ~/Downloads/some.pdf          # NDI parses and caches it
uv run reach ask "what does this paper conclude? cite it"
uv run reach log                               # Drex's decisions for each ask
uv run reach replay ../fixtures/events/ask.jsonl   # no keys needed

../scripts/setup-browser.sh                    # optional: builds the patched browser harness
../scripts/build-overlay.sh && open ../apps/overlay/build/Reach.app
```

Controls: one key. **Option-Space**: tap to type, hold to talk. The lasso button in the panel circles something on screen. **Esc** cancels.
Permissions macOS may ask for: Screen Recording (drag-select), Microphone and Speech Recognition (voice), Automation (TextEdit and Notes), Camera (pinch worker only).

## How NDI grounds answers

`reach add` uploads the PDF to NDI and keeps the parsed passages locally, keyed by file hash. An Intern tool, `evidence_search`, returns passages with ids like `E3`. The model cites ids; Reach turns each cited id into a card holding the exact stored quote, and asks NDI to ground that quote to a page and a region box. Quotes come from the stored passage, never from the model's wording. Grounding finds where text appears; it does not prove the model's reasoning from it.

## Safety

- The local server listens on 127.0.0.1 only and needs a token file readable by you alone.
- Reach's agent tools send no synthetic keystrokes or mouse events. Native writes use an allowlist of destinations and are verified by reading back. The one exception is the separate, opt-in `cursor_demo/` (a teammate's experiment), which moves the pointer to follow your fingertip and pauses when you touch the mouse; it is not an Intern tool and does not run unless you start it.
- A policy hook refuses tool calls that touch key files or dump the environment. See `backend/reach/guard_policy.py` and its tests.
- Documents, web pages and selected text are data, not instructions.

## Limits (read before judging)

- Drex Max supervises and routes; it does not generate text.
- The browser is the harness's own headless Chromium, not your own Chrome tab. In our one risky-form test the page was not submitted, but Drex judged the call routine, so we do not claim it blocks risky actions. Loop mode is used because default mode did not complete the click test.
- Intern's memory feature refuses the first `evidence_search` in each session; the model retries, costing one extra call per ask.
- Cost figures are list-price estimates from token counts; Drex responses carry no price. Provider balances are the truth.
- Vision models misread chess boards and similar dense images; treat `see_region` answers as a second opinion.
- Nothing here was benchmarked against a frontier model. The router comparison is Drex Max against Qwen3.8-27B only, on 90 hand-written held-out utterances written by us, with simulated lip-reader noise; the confidence intervals overlap and two Qwen misses are judgement calls on items we labelled ourselves. See `docs/evidence/bench_v2.md`.

## Repository

`backend/` Python bridge and Intern tools · `apps/overlay/` Swift overlay · `perception/` MediaPipe pinch worker · `finger_processing/` and `cursor_demo/` teammate's hand tracking and cursor demo · `patches/` two small patches to the Nace browser harness · `fixtures/` replay events and router test sets · `docs/evidence/` real logs and benchmark output · `docs/SOURCES.md` verified upstream facts · `THIRD_PARTY.md` licences.

MIT licensed. Third-party components and their terms are in `THIRD_PARTY.md`.
