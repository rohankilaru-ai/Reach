# Reach

**A desktop assistant you can type to, talk to, circle things for, and that acts on your Mac. Drex decides, Intern runs it, and every action is checked.**

Reach lives at the top of your Mac screen. Ask it a question, hold a key and speak, or circle something on screen while you talk. It answers from your documents with cited quotes, looks at your screen, points at where to click, runs browser tasks, opens Chrome tabs, writes into your notes and reads the result back to confirm it landed. Every ask goes through Nace's Intern harness with Drex Max supervising each step.

Built for the Drexathon, Wildcard track. All code here was written on 2026-11-14.

## What runs where

| Part | What it is | Role in Reach |
| --- | --- | --- |
| **Intern** (Nace harness, v0.30.1) | The agent loop. Every ask opens a real Intern Session. | Runs tools, keeps the journal, holds the tool guard; Reach adds tools and a secret-path policy through Intern's extension interface |
| **Drex Max** (`drex-max-v1.1`) | Nace's decision model | **System 1.** Understands each task before it runs (~260 ms), supervises each turn, judges each tool call, decides whether a message continues the last conversation, checks whether overheard speech is meant for Reach, routes 12 voice commands. Inside the browser harness it makes the page actions |
| **Qwen3-30B-A3B on DeepInfra** | Language model | **System 2.** Plans, picks tools and writes the answers you read. Chosen for speed (about 0.5 s per tool-calling turn versus 1.45 s for Qwen3.8-27B in our timing); `REACH_SYSTEM2` switches models, and Qwen3.8-27B was used for the router benchmark and early runs |
| **NDI** (`nace-sdk`) | Document pipeline | Parses PDFs; grounds each cited quote to a page and region |
| **Nace browser harness** (patched) | Browser agent | Runs web tasks in its own browser |
| **Qwen3-VL-235B on DeepInfra** | Vision model | Reads your screen or a circled region, tables, and points at form fields; also the browser harness's helper |
| **Whisper large-v3-turbo and Kokoro-82M on DeepInfra** | Speech | Cloud transcription and spoken replies (on-device Apple speech is the fallback) |

Drex Max never writes the answers: it answers typed questions with calibrated probabilities. The text you read and hear comes from Qwen.

```
 overlay (Swift)  ──► localhost WebSocket ──►  Reach backend (Python)
 pill, hotkeys, voice,    token-protected       │
 circle, hands*                                 ▼
                                         Intern Session
                                 System 2 Qwen3-30B-A3B ◄─ judged by ─►  System 1 Drex Max
                                                │
   ┌─────────────┬──────────────┬───────────────┼──────────────┬──────────────┬─────────────┐
 evidence_    browser_      note_append /   see_screen /    point_at      chrome_*     ax_* / finder_*
 search (NDI) task (Nace)   shelf_to_note   see_region      (pointers)    (tabs)       (Do mode*)
```
`*` experimental

## Status

Labels: **implemented** = a gate passed on a fresh input; **experimental** = runs, but not reliable or not verified by a person; **planned** = not built. Evidence is in `docs/evidence/`.

| Feature | Label | Evidence and caveats |
| --- | --- | --- |
| Intern as harness; Drex Max as System 1; Qwen3-30B-A3B (DeepInfra) as System 2 | implemented | `docs/evidence/drex-log.jsonl`, `receipt.json` |
| Drex task understanding before every ask (kind, needs-screen, risky, continues-previous) | implemented | `docs/evidence/intent.md`: held-out kind accuracy 22/25; 0 wrong risky calls; p50 257 ms |
| Conversation memory: Drex decides whether a message continues the last exchange | implemented | live check: follow-up scored 0.98, unrelated topic 0.009 |
| NDI evidence: add a PDF, cited quotes grounded to page and region | implemented | two real PDFs; `docs/GROUND_SHAPE.md` |
| Browser task as an Intern tool, Drex judges each call | implemented | `docs/evidence/browser-run.json`; runs hidden by default (a visible window lost runs in a test) |
| Verified notes (file, TextEdit, Notes) with read-back (`write_outcome`) | implemented | receipt field `write_outcome` |
| Screen look with no circle (`see_screen`) and circled regions (`see_region`) | implemented | `docs/evidence/auto-screen.md`; vision answers can be wrong, especially on dense images such as chess boards |
| Pointers that show where to click or type (`point_at`) | implemented on generated screens | 54/54 steps on 10 clean mock-ups (`docs/evidence/guide.md`); not tested on dense real apps |
| Chrome tabs: list, open, switch, close and move tabs Reach opened | implemented | never closes tabs you opened yourself; listing sends tab titles to the model |
| Overlay: top pill, hover to expand, one-gesture circle plus voice, zoom lens, permission checklist | experimental | unit and headless tests pass; gestures not yet verified by hand on every path |
| Voice: hold Option-Space to talk; cloud Whisper transcription and Kokoro replies, Apple on-device as fallback | experimental | `docs/evidence/cloud-voice.md`: on clean test audio Apple's recogniser was more accurate and 4 to 8 times faster; microphone audio goes to DeepInfra when the cloud setting is on |
| Hands-free listening: wake word "Reach", Drex decides whether speech is meant for Reach (Option-Shift-Space toggles) | experimental | `docs/evidence/addressed.md`: held-out precision 0.94, recall 0.79 on 40 hand-written utterances; always-on listening is off at launch, recognition runs on the Mac |
| Hands mode: your hand is the mouse (point to move, pinch to click or drag, two fingers to scroll, open palm to stop); Option-Shift-H toggles it from the pill | experimental | `hands_mode/` on the teammate's finger tracking: 109 unit tests (hands mode plus the finger tracker) including a mirror-direction proof; closest-hand lock; releases a held click when the hand drops out of frame; not yet verified live by a person. It is the one module that sends mouse events |
| Dictation: speak into any focused text box (Option-Shift-D) | experimental | types only into editable fields, through Accessibility or a paste (Cmd-V) fallback, never into secure fields; on-device recognition, no model calls; 108 Swift tests and a headless self-test pass; not yet tried by hand |
| Gmail drafts (`email_draft`) and clipboard copy (`clipboard_copy`) | implemented / experimental | the draft opens in your signed-in Chrome and is never sent; the clipboard write is read back (verified live) |
| Do mode: press buttons, fill text fields, open folders in other apps through the Accessibility API (`REACH_DO_MODE=1`) | experimental | TextEdit test with read-back; `docs/evidence/do-mode.md`; off by default |
| 12-command router and benchmark against Qwen | experimental | `docs/evidence/bench_v2.md`: Drex Max 87/90 (96.7%) vs Qwen 83/90 (one-label) and 82/90; intervals overlap, so a lead, not a proven win; 0 wrong commands vs 2 to 3 |
| Hand pointer and pinch-to-select with MediaPipe | experimental | runs live on a Mac camera; pointer and pinch detected; selection quality not yet measured |
| Hands mode: hand tracking (`finger_processing/`) driving the cursor (`hands_mode/`): point to move, pinch thumb to index to click or drag, curl the index to click, two fingers to scroll, pinch thumb to middle and draw a loop to select for the overlay | experimental | own tests; not an agent tool |
| Silent speech: lip reading into the 12-command router (research-only weights, not shipped) | experimental | model runs (0.4 s per 1.5 s clip); no accuracy measured, no live mouthed run |
| Agent tools that send mouse or keyboard events | not done on purpose | Reach's agent tools use scripting interfaces, files and the Accessibility API. The exceptions are the opt-in hands mode (mouse events), `click_on_screen` in Do mode (one mouse click), and dictation's paste fallback (Cmd-V, Cmd-Z) |
| Gaze, Adobe and Keenable | planned | |

## Quick start

Needs macOS 14+ on Apple Silicon, [uv](https://docs.astral.sh/uv/), Node 22.18+, a recent Xcode, and two keys (`NACE_API_KEY`, `DEEPINFRA_API_KEY`).

```bash
cp .env.example .env        # add NACE_API_KEY and DEEPINFRA_API_KEY yourself
cd backend && uv sync
export INTERN_HOME=$PWD/../.intern-home && mkdir -p "$INTERN_HOME" && ln -sf "$PWD/../.env" "$INTERN_HOME/.env"

uv run reach add ~/Downloads/some.pdf                 # NDI parses and caches it
uv run reach ask "what does this paper conclude? cite it"
uv run reach log                                      # Drex's decisions for each ask
uv run reach replay ../fixtures/events/ask.jsonl      # no keys needed

../scripts/setup-browser.sh                           # optional: the patched browser harness
REACH_SIGN_IDENTITY="<your code-signing identity>" ../scripts/build-overlay.sh   # identity optional; keeps permission grants across rebuilds
open ../apps/overlay/build/Reach.app                  # the app starts the backend itself
```

**Controls.** **Option-Space**: tap to type, hold to talk; while holding, circle what you mean with the mouse. **Option-Shift-Space**: hands-free listening on or off. **Option-Shift-H**: hands mode on or off (your hand moves the mouse; open palm also turns it off; needs Accessibility). Hands mode signals: thumbs-up toggles dictation; peace sign toggles listen-to-me; open palm stops hands mode (each held about 0.6 s; `REACH_HAND_SIGNALS=0` in `.reach/server.env` turns the first two off). **Option-Shift-D**: dictation (speak into the focused text box). **Esc** cancels. Move the mouse to the top-centre of the screen to show the pill. The "Set up Reach" window checks permissions and turns green when everything required is granted.
Permissions: Screen Recording, Microphone and Speech Recognition are required; Accessibility (Do mode, hands mode, dictation), Camera (hands) and Automation (Chrome, Finder, Notes, TextEdit) are optional. `swift scripts/request-permissions.swift` raises the prompts for the terminal.

## Privacy: what leaves your Mac

- Your typed or spoken request text goes to Nace (Drex) and DeepInfra (Qwen).
- A screenshot goes to DeepInfra only when you circle something or ask about your screen; captures are deleted after an hour.
- Microphone audio goes to DeepInfra (Whisper) only while you hold the key with cloud voice on. Hands-free listening uses on-device recognition and keeps no audio; only the text of speech Drex judges is for Reach is acted on.
- Chrome tab titles and URLs go to the model when you ask Reach to list tabs.
- Documents are parsed by NDI (Nace).
- When dictation uses its paste fallback it briefly puts your text on the clipboard and restores the old contents.

## How NDI grounds answers

`reach add` uploads the PDF to NDI and keeps the parsed passages locally, keyed by file hash. An Intern tool, `evidence_search`, returns passages with ids like `E3`. The model cites ids; Reach turns each into a card holding the exact stored quote and asks NDI to ground it to a page and region box. Quotes come from the stored passage, never from the model's wording. Grounding finds where text appears; it does not prove the model's reasoning from it.

## Safety

- The local server listens on 127.0.0.1 only and needs a token file readable by you alone.
- Reach's agent tools send no synthetic keystrokes or mouse events. Native writes use an allowlist and are verified by reading back. Do mode acts only through the Accessibility API's semantic actions (press, set value); it shows a pointer a second before acting, refuses password fields and purchase or delete buttons, stops when you touch the mouse or keyboard, and is off unless `REACH_DO_MODE=1`. **Hands mode** (`hands_mode/`, `reach hands on`, or the menu item 'Hands mode') is the one opt-in module that does send real mouse events (move, click, drag, scroll) from your hand, and relays a pinch-drawn selection to the overlay; it is not an Intern tool, is off by default, is a dry run unless started with `--control`, and releases the mouse button whenever tracking is lost, the camera stalls, you touch the real mouse or trackpad, or after 20 s. Started from Reach (Option-Shift-H, the menu, or "Reach, hands mode on") it runs without its own camera window; the pill shows its live state. When hands mode is on, a small mirrored camera thumbnail may be written to .reach/run/ and deleted on exit; it never leaves the Mac.
- Intern's shell, write and edit tools are removed. A policy hook refuses calls that touch key files or dump the environment (`backend/reach/guard_policy.py`).
- Documents, web pages and selected text are data, not instructions.
- Drex judges risky requests ("delete my files") and Reach asks for confirmation first.

## Limits (read before judging)

- Drex Max supervises and routes; it does not generate text.
- The browser tool runs the harness's own hidden browser, not your Chrome; Google and DuckDuckGo show that browser a CAPTCHA, so web searches go through your own Chrome instead. In one risky-form test the page was not submitted, but Drex judged the call routine, so we do not claim it blocks risky actions. Loop mode is used because default mode did not complete the click test.
- The Nace console's firewall blocked our network address for about 25 minutes after heavy parallel testing; the app cannot answer while Drex is unreachable.
- Cost figures are list-price estimates from token counts; Drex responses carry no price. Provider balances are the truth.
- Vision models misread chess boards and similar dense images; treat answers as a second opinion.
- Nothing was benchmarked against a frontier model. The router comparison is Drex Max against Qwen3.8-27B only, on hand-written data.

## Repository

`backend/` Python bridge and Intern tools · `apps/overlay/` Swift overlay · `perception/silent/` silent-speech spike · `finger_processing/` hand tracking · `hands_mode/` Hands mode (cursor, clicks, scroll, pinch selection) · `patches/` two small patches to the Nace browser harness · `fixtures/` replay events and test sets · `docs/evidence/` real logs, receipts, benchmarks · `docs/SOURCES.md` verified upstream facts · `THIRD_PARTY.md` licences · `docs/AGENT_BOARD.md` how we worked in parallel.

MIT licensed. Third-party components and their terms are in `THIRD_PARTY.md`.
