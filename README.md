# Reach

**A Mac desktop assistant you type to, talk to, or circle things for, and it acts for you. Drex decides, Intern runs it, and every action is checked.**

Every ask runs in a real Intern session with custom tools. Reach answers questions, finds cited quotes in your PDFs, looks at your screen and points at where to click, clicks what you name, opens Chrome tabs and Gmail drafts (never sent), copies to the clipboard, runs web tasks, and writes notes, then reads each result back to confirm it landed.

You can use it hands-free: hold a key and speak while circling what you mean, dictate into any text box, or use your hand as the mouse (point to move, pinch to click or drag, two fingers to scroll) with hand signals to switch modes.

Built for the Drexathon, Wildcard track. Code written on 2026-11-14.

## How it works

| Part | Role |
| --- | --- |
| **Intern** (Nace harness) | The agent loop. Every ask is a real Session; Reach adds tools and a secret-path policy. |
| **Drex Max** (System 1) | Understands each task in about 260 ms, supervises every turn, judges tool calls before they run, checks whether speech is meant for Reach, routes voice commands. In Nace's browser it makes the page actions. |
| **Qwen3-30B-A3B on DeepInfra** (System 2) | Plans and writes the answers. Chosen for speed: about 0.5 s per tool-calling turn. |
| **NDI** | Parses PDFs and grounds every cited quote to a page and a region box. |
| **Nace browser** | Hosted cloud browser for web tasks; the Chrome extension for your signed-in Chrome. |
| **DeepInfra** | Also vision (screen, tables), Whisper speech-to-text and Kokoro voice. |
| **System 0** (local, no cloud) | Hotkeys, camera, hand tracking, cursor, permissions. |

Drex Max never writes the answers; it answers typed questions with probabilities and Reach uses those to gate and route.

## Controls

| Key | What it does |
| --- | --- |
| **Option-Space** tap | Open the panel and type |
| **Option-Space** hold | Talk; circle what you mean with the mouse while holding |
| **Option-Shift-Space** | Hands-free listening |
| **Option-Shift-H** | Hands mode: your hand is the mouse |
| **Option-Shift-D** | Dictation into the focused text box |
| **Esc** | Cancel |

Hands mode: point to move, pinch (thumb to index or middle) to click or drag, two fingers up or down to scroll, open palm held to stop. Signals (held 0.6 s): thumbs-up toggles dictation, peace sign toggles listen-to-me. Set `REACH_HAND_SIGNALS=0` in `.reach/server.env` to turn the signals off. The demo script is in `docs/DEMO.md`.

## Run it

Needs macOS 14+ on Apple Silicon, [uv](https://docs.astral.sh/uv/), Node 22.18+, Xcode, and two keys (`NACE_API_KEY`, `DEEPINFRA_API_KEY`).

```bash
cp .env.example .env                         # add the two keys yourself
cd backend && uv sync
export INTERN_HOME=$PWD/../.intern-home && mkdir -p "$INTERN_HOME" && ln -sf "$PWD/../.env" "$INTERN_HOME/.env"
uv run reach add ~/Downloads/some.pdf        # NDI parses and caches it
uv run reach ask "what does this paper conclude? cite it"
uv run reach log                             # Drex's decisions for each ask
uv run reach replay ../fixtures/events/ask.jsonl   # no keys needed
../scripts/build-overlay.sh && open ../apps/overlay/build/Reach.app   # the app starts the backend itself
```

Permissions: Screen Recording, Microphone and Speech Recognition are required; Accessibility (Do mode, hands mode, dictation), Camera (hands) and Automation (Chrome, Finder, Notes, TextEdit) are optional. The "Set up Reach" window checks them. The Nace Chrome extension setup is in `docs/EXTENSION.md`.

## Safety

- The local server listens on 127.0.0.1 only and needs a token file readable by you alone.
- Agent tools use scripting interfaces, files and the Accessibility API. Hands mode sends mouse events, `click_on_screen` sends one click, and dictation can paste with Cmd-V; all are opt-in. Do mode is off unless `REACH_DO_MODE=1`.
- Intern's shell and file-write tools are removed; a policy hook refuses calls that touch key files.
- Documents, web pages and selected text are data, not instructions. Risky requests ("delete my files") get a confirmation first.
- Privacy: request text goes to Nace and DeepInfra; screenshots go to DeepInfra vision only when you ask about the screen; held-key voice goes to DeepInfra Whisper when cloud voice is on; hands-free listening and dictation run on-device.

## Evidence and limits

Real logs and measurements are in `docs/evidence/`: the Drex log, a receipt, the NDI grounding shape, and benchmarks (Drex Max routed 87 of 90 held-out commands correctly against 83 for Qwen3.8-27B; the intervals overlap, so that is a lead, not a proven win). Hands mode, dictation and the signals are covered by unit tests and a dry run but had not been tried by a person at the time of writing. Nothing was benchmarked against a frontier model. The Nace console's firewall blocked our network for about 25 minutes after heavy parallel testing.

## Repository

`backend/` Python bridge and Intern tools · `apps/overlay/` Swift overlay · `hands_mode/`, `finger_processing/` hand tracking and control · `perception/` pinch worker and silent-speech spike · `patches/` small patches to the Nace browser harness · `fixtures/` replay events and test sets · `docs/` evidence, demo script, extension setup.

MIT licensed. Third-party components are in `THIRD_PARTY.md`.
