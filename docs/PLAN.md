> **Historical plan written at 11:15 AM.** It was revised as slices landed; `docs/STATUS.md` is the truth.

# Reach build plan

Status: plan, nothing built yet. Facts come from `docs/SOURCES.md` and the pinned upstream repos. Anything marked
**verify in S0** has not been confirmed by a real call and must not be claimed until it has.

**Clock (Pacific, today).** T+0 = 11:15 AM. Build ends T+150 = 1:45 PM. 1:45 to 3:00 PM is README, making the repo
public, the presentation and a final check. **Hard deadline 3:00 PM, no edits after; last push by 2:50 PM.**
Submission needs a presentation and a public GitHub repo. Finalists are judged on use of Drex, Intern, NDI and partner
tools (DeepInfra, Adobe, Keenable earn extra credit), so every Drex decision must be visible in logs.
Machine: macOS, Apple Silicon, 16 GB. One heavy job at a time (Xcode build, Chromium run, torch).

## 1. Architecture recap (verified facts only)

```
Swift overlay (apps/overlay)  <-- local WebSocket, 127.0.0.1 + session token -->  Python backend (backend/reach)
  hotkey, region select, halo, cards, Drex log                                     |
                                                                                   +-- Intern Session (in-process)
one perception process (perception/)   -- pointer / pinch / transcript  -->        |     System 2: Qwen3.8-27B on DeepInfra
  MediaPipe hands + face, single camera owner                                      |     System 1: Drex Max (Nace console)
                                                                                   |     Reach tools via one Extension
                                                                                   +-- NDI (nace-sdk) -> local evidence store
                                                                                   +-- nace-browser CLI (child process)
```

- **Harness.** Intern v0.30.1 (Apache-2.0, Python >=3.12, package `intern-agent`, import `intern`). Reach drives a real
  `Session`: `Bootstrap(Options(cwd, system2, system1, max_turns, extra_extensions=[...])).session(emit)`, then
  `session.start/steer/follow_up/abort`. Fallback transport, same Session: `intern rpc` (JSON-RPC over stdio, methods
  `prompt`, `steer`, `follow_up`, `abort`, `get_state`; events arrive as `method: event`).
- **Install.** Intern is a pinned dependency of the backend venv (`intern-agent @ git+https://github.com/nace-ai/intern@v0.30.1`),
  not a `uv tool` install, because Reach's `Extension` must be importable next to it.
- **System 1.** Nace hosts only System 1, at `https://console.nace.ai/v1`, key `NACE_API_KEY`. The default is
  `drex-latest`, so Drex Max is set explicitly (`[system1] model` in `config.toml`, or `--system1`). The exact id string
  comes from `intern models` (**verify in S0**).
- **System 2.** No Nace-hosted System 2 exists. Reach uses `deepinfra/Qwen/Qwen3.8-27B`, key `DEEPINFRA_API_KEY`
  (Intern's built-in `deepinfra` provider, base URL `https://api.deepinfra.com/v1/openai`). DeepInfra is a partner tool.
- **Reach tools.** One `Extension` subclass whose `tools()` returns `Tool`s (`name`, `description`, `Args` pydantic model,
  `effects` from `read|write|exec|network`, `async run(args, ctx)`). Tools that are not read-only are judged by System 1
  before they run; a refusal comes back as `tool_call_end.status == "refused"` plus a `tool_guard` event.
- **Events Reach renders.** `run_start`, `text_delta`, `tool_call_start`, `tool_call_end` (`ok|error|refused`),
  `decision` (each System 1 ask: `purpose`, `answers`, `cost_usd`), `nudge`, `tool_guard`, `run_end` (`reason`, token
  `usage`, `system1`).
- **Isolation.** `INTERN_HOME` points at a repo-local, gitignored folder: own config, sessions, memory, `SYSTEM1.md`.
- **Documents.** `nace-sdk` (`NaceClient()` reads `NACE_API_KEY`; same balance as System 1):
  `client.documents.upload(path)`, `client.documents.parse(source, output={"formats":["markdown"]})`,
  `client.documents.extract(source, schema=...)`, `client.jobs.wait(job_id)`. Ground is REST:
  `POST /v1/documents/ground` with `targets:[{id,text}]`. The page/bbox response shape is unverified.
- **Browser.** `drex-browser-harness-pub` (MIT, Node >=22.18, built from source). Drex Max picks each move; a helper LLM
  plans, reads and checks. The runner's MCP `browser_task` always uses the Anthropic Haiku helper. The CLI
  `nace-browser run "task" --url URL --json --model M` accepts other catalogued helpers. DeepInfra is not a provider
  upstream; slice B adds it as a patch.
- **Deviation from the spec, on purpose.** Reach wraps the CLI as an Intern tool instead of using the MCP path: it is
  the only path where the helper is configurable, so any Anthropic call stays behind one config value.
- **Silent speech source.** `Input-Labs-HQ/silent-chat` is MIT. Its `vsr.py` (classes `Recorder`, `FaceKeypoints`,
  `MouthActivity`, `LipReader`) runs Auto-AVSR on torch with MediaPipe; `LipReader.mouth_crops(frames)`, `.encode(crops)`
  and `.score(enc, candidates)` work on in-memory frames and a closed candidate list. The weights are
  research/non-commercial, fetched by its `setup.command`, never bundled here.
- **Drex direct call.** `POST /v1/systemone` takes `{model, state: {...}, questions: {name: {type: "choice",
  instructions, criteria: {option: description}}}}` and returns a probability per option (also `noul` and `score` types).

## 2. Slices

Rules: one slice at a time per person; each ends with its gate passing and a commit on `main`. A slice that misses its
kill time is cut as written and the next one starts.

| Slice | Box (T+ min) | Clock | Kill | Lane |
| --- | --- | --- | --- | --- |
| S0 foundation smoke | 0 to 20 | 11:15 to 11:35 | T+30 | driver |
| S1 bridge, typed ask, receipt, abort, Drex log | 20 to 45 | 11:35 to 12:00 | T+50 | driver |
| S2 overlay shell | 45 to 70 | 12:00 to 12:25 | T+75 | driver |
| S3 NDI evidence cards and shelf | 70 to 90 | 12:25 to 12:45 | T+95 | driver |
| B browser helper on DeepInfra | 90 to 120 | 12:45 to 1:15 | T+120 | driver |
| S4 browser task | 120 to 135 | 1:15 to 1:30 | T+140 | driver |
| S5 takeover and verified completion | 135 to 147 | 1:30 to 1:42 | T+147 | driver |
| S6 camera worker and pinch | 45 to 60 (gate checked at T+75) | 12:00 to 12:15 | T+75 | teammate-ok |
| S8a silent speech spike | 60 to 120, go/no-go at T+120 | 12:15 to 1:15 | T+120 | teammate-ok |
| S7 bindings | 120 to 140 | 1:15 to 1:35 | T+145 | teammate-ok |
| S9 extras | spare time only | | T+145 | either |

Freeze T+147 to T+150: no new code, run every passed gate once more.

**Driver alone.** The driver lane is the product (S0 to S5). The driver does not open S6, S8a, S7 or S9, with one
exception: if a Claude key exists and the Haiku helper passes in S0, slice B is skipped and its 30 minutes go to S6.
Browser outranks pinch when forced to choose, because it shows more Drex Max and DeepInfra use to the judges.

### S0. Foundation smoke
- **Goal.** Prove each mandatory piece alone; start every slow download and permission prompt now.
- **Owner.** Driver. **Box.** T+0 to T+20. **Kill.** T+30. **Directory.** `smoke/`, `backend/pyproject.toml`, `config/intern_home/`.
- **Upstream calls.**
  - `uv sync` with `intern-agent` at v0.30.1 and `nace-sdk`; `intern models` to read the Drex Max id.
  - `intern -p --json --system2 deepinfra/Qwen/Qwen3.8-27B --system1 <drex max id> -- "reply with the word ready"`.
  - `NaceClient().documents.upload(path)`, `.documents.parse(..., output={"formats":["markdown"]})`, `.jobs.wait(job_id)`
    if a job comes back; one `POST /v1/documents/ground` to record the real response shape.
  - `npm ci`, `npm run build:release`, `node runner/bin/nace-browser.mjs install` (about 170 MB); then, if a Claude key
    exists, `nace-browser run "<read-only task>" --url <public page> --json --temp-profile --max-cost 0.05`.
- **Also here.** Start the browser build first. Trigger the macOS prompts for Screen Recording, Accessibility and
  Camera. Record in `docs/STATUS.md`: Drex Max id, where the System 1 model name is reported, ground response shape,
  whether the console key works as the runner's `DREX_API_KEY`, whether the Qwen endpoint accepts images.
- **Blocked by.** `NACE_API_KEY` is pending. Until it arrives only the installs, the harness build and the DeepInfra
  one-line check can run; S1 is built against the code and passes its gate when the key lands.
- **Gate.** Three commands in `smoke/` each print PASS alone: an Intern reply with `decision` events present, a Markdown
  parse of one PDF, and the harness build listing its CLI help (plus one succeeded run if a helper key exists).
- **Cut if it fails.** DeepInfra failing: fall back to `openrouter/qwen/qwen3.8-27b`. NDI failing: ask the organizers
  at once, it is mandatory.
- **Hygiene.** Smoke scripts read keys from the environment and print none. Upstream clones live outside the repo.

### S1. Intern bridge, typed ask, receipt, abort, Drex log
- **Goal.** A typed request goes through a real Intern Session and returns a streamed answer, a receipt and a visible
  record of what Drex Max decided.
- **Owner.** Driver. **Box.** T+20 to T+45. **Kill.** T+50. **Directory.** `backend/reach/` (`bridge.py`, `ipc.py`, `contracts.py`, `ledger.py`, `cli.py`).
- **Upstream calls.** `Bootstrap(Options(cwd=..., system2=..., system1=..., max_turns=12, extra_extensions=["reach.intern_ext:ReachTools"])).session(emit)`;
  `session.start(prompt)`, `session.abort()`; events `text_delta`, `tool_call_*`, `decision`, `tool_guard`, `run_end`.
- **Build.** WebSocket server on `127.0.0.1` (random port, token in a 0600 file under `.reach/run/`). In: `ask {text,
  selection?}`, `abort`, `select`. Out: `event` (the Intern event, passed through), `receipt` (`{task_id, status,
  reason, tools[], refused[], system1_asks, tokens, est_cost_usd, duration_ms}`), `evidence`, `pointer`, `transcript`.
  `abort` calls `session.abort()` and signals any child process the backend holds. Every `decision`, `nudge` and
  `tool_guard` event is appended to `.reach/logs/drex.jsonl` and printed by `reach log` as one line each (purpose,
  answer, probability, cost). `reach ask "..."` uses the same socket, so the product works with no overlay.
- **End of slice.** Freeze `contracts.py`; record one real event stream to `fixtures/events/ask.jsonl`; add
  `reach replay <file>` so UI and perception work needs no keys.
- **Gate.** `reach ask "what is 17 times 23"` streams text and prints a receipt with `reason: completed` and
  `system1_asks` above zero, `reach log` shows those asks, and Ctrl-C during an ask yields `reason: aborted`.
- **Cut if it fails.** In-process import breaks: use a child `intern rpc --extension reach.intern_ext:ReachTools`.
  Both fail by T+50: `intern -p --json` per ask (no steer, abort by signal).
- **Hygiene.** After this slice `contracts.py` takes additive edits only.

### S2. Overlay shell: hotkey, region selection, halo
- **Goal.** Reach looks and behaves like its own product: floating panel, selection halo, state line, Drex log strip.
- **Owner.** Driver. **Box.** T+45 to T+70. **Kill.** T+75. **Directory.** `apps/overlay/` (Swift package, AppKit + SwiftUI).
- **Upstream calls.** None. It speaks only the S1 contract (`URLSessionWebSocketTask`) and runs against `reach replay`.
- **Build.** Borderless non-activating `NSPanel` above other windows; global hotkey (default Option-Space) opens a text
  field; a second hotkey starts a drag rectangle; the region gets an animated halo drawn by Reach, is captured to a PNG
  and its text read locally with the Vision framework. The panel shows state (idle, working, needs you, done, failed),
  streamed text, the receipt, and the last three Drex decisions. `pointer` messages draw a ghost pointer (used by S6).
- **Selection payload.** Text is the contract (`selection.text`, `selection.image_path`, `selection.rect`). The image
  reaches System 2 only if S0 showed the Qwen endpoint takes images; otherwise it is shown on the card only.
- **Gate.** Press the hotkey, drag over a paragraph in any app, type "explain this", and the panel streams an answer
  about the selected text while the halo stays on the region.
- **Cut if it fails.** Region capture falls back to the system screenshot tool plus clipboard text. Not stable by T+75:
  ship the hotkey text field and state line only.
- **Hygiene.** No backend code here; no keys ever reach the Swift process, only the session token.

### S3. NDI evidence cards and shelf
- **Goal.** Answers about a document cite passages that NDI produced, and the cards open the source.
- **Owner.** Driver. **Box.** T+70 to T+90. **Kill.** T+95. **Directory.** `backend/reach/tools/ndi.py`, `backend/reach/evidence.py`, cards in `apps/overlay/`.
- **Upstream calls.** `client.documents.upload`, `client.documents.parse(..., output={"formats":["markdown"]})`,
  `client.jobs.wait`, `POST /v1/documents/ground` with `targets:[{id,text}]`.
- **Build.** Dropping a PDF on the overlay (or `reach add file.pdf`) starts an NDI job outside the agent run; the result
  is cached by file hash in SQLite as passages with ids. Intern gets a read-only tool `evidence_search(query)` returning
  passage ids and text. System 2 cites ids like `[E3]`; the backend turns cited ids into `evidence` messages, then sends
  each quoted passage to ground and attaches the location. The shelf is the list of pinned cards, kept in SQLite and
  offered to the next ask.
- **Citation levels.** Level 1 (the gate): file name, exact quote, passage id; click opens the PDF. Level 2: page number
  from ground. Level 3: bbox highlight. Levels 2 and 3 depend on the ground shape recorded in S0.
- **Gate.** Add a PDF never used before, ask a question about it, and every card in the answer shows a quote that is
  found verbatim in the PDF it opens.
- **Cut if it fails.** Ground is dropped first (level 1 only). If parsing is slow, ingest before presenting and say so.
- **Hygiene.** Cache and outputs are gitignored; one small public-domain PDF lives in `fixtures/docs/`.

### B. Browser helper on DeepInfra
- **Goal.** The browser harness's helper LLM runs on DeepInfra, so the browser needs no Anthropic key and adds a second
  partner-tool use.
- **Owner.** Driver, teammate-ok if they read TypeScript. **Box.** T+90 to T+120. **Kill.** T+120, no extension.
  **Directory.** `patches/browser-harness/deepinfra.patch` plus `patches/browser-harness/README.md` (base commit, how to apply).
- **Upstream calls and files.** In the harness checkout, every place that names a provider: `src/providers.ts` (a
  `deepinfra` entry built with `createOpenAI({ apiKey, baseURL: "https://api.deepinfra.com/v1/openai" })`, using the
  chat-completions model `.chat(id)`, not the Responses API); `src/models.ts` (`PROVIDERS.deepinfra` placed last with a
  key prefix that cannot shadow the others, since the runner passes keys by name; one `CATALOG` entry with
  `vision: true` and real list prices, because a model without prices is not offered); `src/manifest.json`
  (`host_permissions` gains `https://api.deepinfra.com/*`, or the extension cannot reach it); `src/types.ts`,
  `src/settings.ts`, `src/keycheck.ts`, `src/llm.ts`; `runner/src/args.mjs` and `runner/src/run.mjs` (the
  `DEEPINFRA_API_KEY` mapping). The side panel and `build.mjs` dev-key paths are left alone. Then
  `npm run build:release` and `nace-browser run "<read-only task>" --url <public page> --json --temp-profile --model <id> --max-cost 0.05`.
- **Model.** First choice a vision model served by DeepInfra such as `Qwen/Qwen3-VL-235B-A22B-Instruct`; second
  `Qwen/Qwen3.8-27B` if it accepts screenshots (**verify with one image call before patching**).
- **Gate.** One read-only browser run returns `status: "succeeded"` with `"model"` set to the DeepInfra id.
- **Cut if it fails.** At T+120: use `claude-haiku-5-5` if `ANTHROPIC_API_KEY` exists (allowed by the rules). If not,
  S4 becomes guided mode: Intern produces the steps and the URL, Reach opens the page and shows the steps as cards, the
  person clicks, and nothing is reported as done by the agent.
- **Hygiene.** The patch is a plain `git diff` against a named upstream commit, MIT notice kept, no upstream code
  copied into this repo beyond the diff. The release build refuses outputs containing key-shaped strings: keep it so.

### S4. Browser task with honest outcomes
- **Goal.** Intern hands a bounded web task to the Nace browser harness and Reach reports exactly what came back.
- **Owner.** Driver. **Box.** T+120 to T+135. **Kill.** T+140. **Directory.** `backend/reach/tools/browser.py`.
- **Upstream calls.** `node $REACH_BROWSER_HARNESS_DIR/runner/bin/nace-browser.mjs run "<task>" --url <url> --json --headed --profile reach --model $REACH_BROWSER_HELPER_MODEL --max-cost 0.10 --max-seconds 180 --max-llm-calls 20`.
  Fields used: `status`, `outcome.kind`, `outcome.reason`, `answer_text`, `final_url`, `usage.cost_usd`,
  `usage.drex_calls`, the `final.png` artifact. Exit 1 still carries a result; exit 3 carries `{"error": {"code"}}`.
- **Build.** Intern tool `browser_task(task, start_url)` with effects `network` and `write`, so System 1 judges each
  call. The backend holds the child so `abort` interrupts it (the runner reports `cancelled`). The child gets
  `DREX_API_KEY` set from `NACE_API_KEY` and only the one helper key. `--allow-risky` is never passed. `SYSTEM1.md` in
  the Reach `INTERN_HOME` says read-only browsing of public pages is normal; sending, paying, deleting, publishing are not.
- **Outcome mapping.** `succeeded` shows done. `partial` shows its kind in words (`blocked_login`, `blocked_captcha`,
  `risky_action_denied`, `limit_reached`) as a handback. `failed`, `timed_out`, `cancelled` show as such. Loop mode (the
  default) returns empty `steps` and `outcome.evidence`, so the receipt never depends on them.
- **Gate.** One ask completes a read-only task on a public page and shows the final screenshot and Drex call count; a
  second ask needing a purchase or send click ends as "stopped before a risky action", not as success.
- **Cut if it fails.** Headed drops to headless; then guided mode as in slice B; the browser is listed as planned.
- **Hygiene.** The helper id is one config value copied verbatim from the harness catalog (unknown ids end `run_refused`).

### S5. Stop, takeover, verified completion
- **Goal.** The person can always take control, and "done" means a postcondition was observed.
- **Owner.** Driver. **Box.** T+135 to T+147. **Kill.** T+147. **Directory.** `backend/reach/verify.py`, `backend/reach/tools/note.py`, stop key in `apps/overlay/`.
- **Upstream calls.** `session.abort()`, `session.steer(text)`; `run_end.reason`; `tool_call_end.status`.
- **Build.** A global stop key (double Escape) handled locally in the overlay: sends `abort` and flips the panel to "you
  have control" without waiting for a model. Typing during a run sends `steer`. A `note_append(text, evidence_ids)` tool
  writes to a Markdown note and reads it back. Receipt status is computed, never written by the model: `verified` (a
  postcondition was read back: note content present, or browser `completed`), `unverified`, `partial`, `refused`,
  `aborted`, `failed`.
- **Gate.** Stop during a browser run closes it within three seconds with receipt `aborted`; "put this in my notes"
  yields `verified` only when the text is in the note file.
- **Cut if it fails.** Steer and the note tool are dropped; stop (already in S1) and the computed status stay.
- **Hygiene.** Reach does not move the mouse or type in native apps in this build; say so in the README.

### S6. Camera worker and pinch selection
- **Goal.** One process owns the camera; a hand moves a Reach pointer and a pinch selects the region under it.
- **Owner.** Teammate-ok. **Box.** T+45 to T+60, gate checked at T+75 once S2 is in. **Kill.** T+75. **Directory.** `perception/camera/` (the one perception process; one venv shared with S8a).
- **Upstream calls.** MediaPipe Hand Landmarker and Face Landmarker from the `mediapipe` Python package, pinned
  to the version `silent-chat` uses (`mediapipe==1.2.2`) so both slices share one venv. No cloud call per frame.
- **Build.** The worker is the only camera owner. It runs hands always and face only while silent mode is armed. It
  smooths the index fingertip and sends `pointer {x, y, pinch, tracking}`; pinch down starts a region, pinch up ends it,
  with a 150 ms hold before a pinch counts. Lost tracking hides the pointer and changes nothing. While silent mode is
  armed it buffers RGB frames in memory and hands them to the S8a module in the same process, so nothing else opens
  the camera. Mouse drag from S2 always works.
- **Gate.** A pinch-drag over a paragraph creates the same selection the mouse does, and covering the camera leaves the
  hotkey and mouse path working.
- **Cut if it fails.** Pinch selects a fixed-size box around the pointer. Unusable tracking: the worker is not started
  in the presentation and pinch is listed as experimental.
- **Hygiene.** `perception/requirements.txt` holds the hand-tracking pins only; frames never leave the process and are
  never written to disk. Tested against `reach replay`, no keys needed.

### S8a. Silent speech spike (experimental)
- **Goal.** Twelve mouthed commands recognised locally and routed through Drex Max, with an honest "uncertain".
- **Owner.** Teammate-ok. **Box.** T+60 to T+120. **Go/no-go.** T+120 (1:15 PM). **Kill.** T+120.
  **Directory.** `perception/silent/` (a module loaded by the S6 process), `backend/reach/commands.py` (router).
- **Upstream calls.** `silent-chat` `vsr.py`, imported from a checkout outside this repo: `LipReader.mouth_crops(frames)`,
  `.encode(crops)`, then `.score(enc, candidates)` over the 12 commands (closed set) and `.decode_enc(enc)` for the raw
  hypothesis; its own `Recorder` is not used. Weights via that repo's `setup.command`. Drex Max intent choice through
  `POST https://console.nace.ai/v1/systemone` with one `choice` question (**verify the hosted id and auth in S0**).
- **Build.** (1) `commands.py`: a 12-command router (explain this, pin this, open this, compare these, put this here,
  scroll down, scroll up, next, pause, continue, stop, cancel) mapping a command to an `ask`, `abort` or shelf action.
  Typed text and macOS dictation into the text field go through it first, so the router is useful on its own.
  (2) Silent path: hold a key to arm, mouth a command, `LipReader` returns scored candidates, Drex Max chooses among the 12
  commands plus `uncertain` given those scores, the raw hypothesis and what is selected, and the overlay shows a `transcript` preview
  (raw hypothesis, chosen command, probability) that the person confirms. The upstream Claude correction and dispatch
  steps are not used. `uncertain` does nothing and says so. Each choice lands in the Drex log.
- **Go/no-go at T+120.** Go: at least 8 of 12 commands recognised over two passes with no wrong command executed; the
  feature is shown live, labelled experimental. No-go: silent mode is switched off in config, the measured numbers stay
  in `perception/silent/README.md`, and the presentation uses typed or dictated commands through the same router.
- **Gate.** Typing or dictating each of the 12 commands triggers the right action, and with silent mode armed a mouthed
  command shows a preview with its Drex choice before anything runs.
- **Cut if it fails.** Silent path off as above; the router stays. No simulated transcript is ever shown as recognition.
- **Hygiene.** Extra pins in `perception/silent/requirements.txt` (torch and the rest of that repo's list), installed
  into the perception venv only for this slice; torch loads only when armed, never while Chromium or Xcode is building. Weights are downloaded by
  the user, gitignored, labelled research/non-commercial in the UI and in `THIRD_PARTY.md` with the MIT notice for the
  reused code. Excluded from the default install.

### S7. Configurable bindings
- **Goal.** Hotkeys and gestures map to Reach capabilities from a file the user can edit.
- **Owner.** Teammate-ok. **Box.** T+120 to T+140. **Kill.** T+145. **Directory.** `config/bindings.json`, `apps/overlay/Bindings.swift`.
- **Upstream calls.** None. **Build.** `{trigger, capability, fixed_text?}` entries over the router's commands; reload on save.
- **Gate.** Changing the ask hotkey in the file and saving makes the new key work without a restart.
- **Cut if it fails.** Constants stay; README says "fixed shortcuts". **Hygiene.** Default file committed; user override in `.reach/`.

### S9. Extras, spare time only
Either person, kill T+145, in `perception/camera/`, MediaPipe landmarks only, each behind a config switch that defaults
off. Open palm held half a second sends `abort` (gate: same receipt as the stop key); then two-finger scroll; gaze last.
If an extra fails it is removed and listed as planned.

### After the build (1:45 to 3:00 PM)
README from the slice board with implemented / experimental / planned labels; `THIRD_PARTY.md`; a sample
`docs/evidence/drex-log.jsonl` and one receipt from a real run; secret scan of the full history; make the repo public;
presentation; fresh-clone check of the quick start; last push by 2:50 PM.

## 3. Repo layout and two-person workflow

```
README.md  AGENTS.md  THIRD_PARTY.md  .env.example  .gitignore
docs/        PLAN.md  SOURCES.md  STATUS.md  evidence/
smoke/       S0 scripts, one per upstream piece
backend/     pyproject.toml, reach/ (bridge, ipc, contracts, ledger, evidence, verify, commands, intern_ext, tools/)
apps/overlay/        Swift package
perception/  requirements.txt, camera/ (S6), silent/ (S8a: extra requirements, README with measured numbers)
patches/browser-harness/   deepinfra.patch + README
config/      bindings.json, intern_home/ (config.toml and SYSTEM1.md templates, no keys)
fixtures/    events/*.jsonl (recorded IPC streams), docs/ (one public-domain PDF)
scripts/     dev.sh (backend + overlay), replay.sh
.reach/      runtime state, INTERN_HOME, caches, logs, token: gitignored
```

- **One directory per slice.** A slice edits its own directory plus, at most, an additive change to `contracts.py`.
- **AGENTS.md slice board.** A table at the top: slice, status (`todo`, `doing`, `gate passed`, `cut`), who, branch,
  gate sentence. Below it: how to run, the hard rules (no keys in the repo, no reading `.env` files with tools, no
  upstream code vendored, simulated input is labelled), and the IPC contract summary. The board is updated in the same
  commit that changes a slice's status.
- **Branches.** `s<N>-<short-name>` (for example `s6-camera`). The driver may commit small slices straight to `main`;
  the teammate always uses a branch and the driver merges after running the gate. No force pushes.
- **Conflict avoidance.** The driver owns `backend/reach/` and `apps/overlay/`. The teammate owns `perception/` and the
  directory of any slice they took. Shared files take additive edits only. Nobody refactors during the event.
- **`.env.example`.** Variable names and one-line comments only: no values, no example keys.
- **Teammate pickup in 5 minutes.**
  1. Clone, read the slice board in `AGENTS.md`, pick a `todo` row marked teammate-ok, write your name on it.
  2. `git switch -c s<N>-<name>`.
  3. Run `scripts/replay.sh`: it serves a recorded event stream on the local socket, so no keys are needed.
  4. Build inside the slice directory until the gate sentence is true against the replay.
  5. Push and tell the driver, who runs the gate against the live backend and merges.

## 4. Keys and provider checklist

| Variable | Needed for | Status |
| --- | --- | --- |
| `NACE_API_KEY` | System 1 (Drex Max) in Intern, NDI through `nace-sdk`, the S8a intent call, and the browser runner (passed to the child as `DREX_API_KEY`) | required, working (verified 11:30 AM; Drex Max id is `drex-max-v1.1`) |
| `DEEPINFRA_API_KEY` | System 2 `deepinfra/Qwen/Qwen3.8-27B`; browser helper after slice B | required, working |
| `ANTHROPIC_API_KEY` | browser helper `claude-haiku-5-5` only; allowed by the rules, may not exist | optional fallback |
| `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` or `GEMINI_API_KEY`, `FIREWORKS_API_KEY` | spare System 2 route `openrouter/qwen/qwen3.8-27b`; other upstream helpers (`openai/gpt-6-luna`, `gpt-6-luna`, `gemini-3.8-flash`, `accounts/fireworks/models/glm-5p3-flash`) | optional |
| `DREX_PAIRING_CODE` | only for the paired-extension path (server on 127.0.0.1:47615) that acts in the user's own Chrome; not used in this plan | optional |
| `REACH_SYSTEM2`, `REACH_SYSTEM1` | model ids, so nothing is hard-coded | required |
| `REACH_BROWSER_HELPER_MODEL`, `REACH_BROWSER_HARNESS_DIR` | helper id and path of the built harness checkout | for S4 |
| `REACH_MAX_USD` | session spend cap | default 5 |

**Cost guard.**
- Intern: `max_turns = 12` per ask.
- Browser: `--max-cost 0.10 --max-seconds 180 --max-llm-calls 20` per run (runner defaults: $0.04, 600 s, 20 calls).
- NDI: cached by file hash, so a document is parsed once; only passages an answer cites are grounded.
- Ledger: the backend adds `decision.cost_usd`, browser `usage.cost_usd`, System 2 tokens times a configured price
  (Intern reports tokens, not dollars) and an NDI call count. The receipt shows the running total; past
  `REACH_MAX_USD`, new asks are refused with a clear message until the cap is raised.
- All figures are estimates at list prices and are labelled so; the provider balances are the truth.

## 5. Risk register

| # | Risk | Fallback |
| --- | --- | --- |
| 1 | `NACE_API_KEY` arrives late; Intern refuses to run without System 1 and NDI cannot be called | Build S1 to S3 against the code and replay fixtures; run gates the minute the key lands; if still missing at T+45, ask the organizers for a console key |
| 2 | Slice B patch does not work in 30 minutes | Haiku helper if a Claude key exists; otherwise guided browser mode, labelled as such |
| 3 | Qwen on DeepInfra is slow, rate-limited or weak at tool calls | Short prompts, compact tool results; spare route through OpenRouter |
| 4 | Ground response has no usable page or bbox | Level 1 citations (file, quote, passage id) are the gate |
| 5 | The overlay eats the clock (permissions, window levels, hotkeys) | Hard kill at T+75; `reach ask` and the hotkey text field are still a complete product |
| 6 | Dependency clash or memory pressure (Chromium, Xcode, camera, torch on 16 GB) | Perception has its own venv and process, apart from Intern; one heavy job at a time; silent mode never runs with the browser |
| 7 | Silent speech is inaccurate or its weights are slow to download | Go/no-go at T+120; typed and dictated commands through the same router; label experimental |
| 8 | A secret or private path leaks when the repo goes public at 1:45 PM | Names-only `.env.example`, gitignored `.reach/`, secret scan of the full history before the switch |

## 6. What could be questioned in judging

Labels in the README: **implemented** (gate passed on a fresh input), **experimental** (runs, not reliable, numbers
shown), **planned** (not built). The README table is filled from the slice board, not from this plan.

| Feature | Expected label | What we must be able to show |
| --- | --- | --- |
| Intern as the harness | implemented | Every ask opens a real Session; receipts carry Intern event data |
| Drex Max as System 1 | implemented | The configured id, the Drex log per run, one `tool_guard` refusal |
| Qwen3.8-27B on DeepInfra as System 2 | implemented | `run_start.model` in the log |
| NDI document pipeline | implemented | A new PDF parsed live; cards with verbatim quotes |
| Page or bbox grounding | experimental until S0 confirms the shape | The raw ground response |
| Browser task | implemented if S4 passes; "guided" if not | A partial outcome shown as partial |
| Browser helper | disclosed | A second LLM inside the harness; which model ran; the DeepInfra provider is our patch, not upstream |
| Acting in the user's own Chrome tab | planned | The browser is the harness's own Chromium profile |
| Stop and verified completion | implemented if S5 passes | Status computed from observed results |
| Pinch selection | implemented or experimental | A live pinch, with tracking loss shown |
| Silent speech | experimental at best | Hit, miss and uncertain counts; reused MIT code credited; research-only weights, not shipped |
| Command router (typed, dictated) | implemented if S8a part 1 passes | The 12 commands |
| Gaze, scroll, open palm, Adobe, Keenable | planned unless built | Nothing claimed otherwise |
| Native app control | planned | Reach does not drive the mouse or keyboard |

We will not say: that Reach lip-reads reliably, that it works in any app, that it is faster or cheaper than anything
without a measurement, or that the costs shown are invoices. Pre-existing work is this plan and `docs/SOURCES.md` only.

## 7. Open questions that block work

**Answered at 11:45 AM (2026-11-14):** 1. `NACE_API_KEY` works; Drex Max id is `drex-max-v1.1`. 2. No Claude key; slice B
(DeepInfra helper) runs, Haiku is not planned. 3. A teammate has repo access. The
owner drives S0 to S5. Greptile is on for the repo and is advisory only, not a required check. S0 passed (Intern plus
Drex Max plus Qwen, and an NDI parse of a real PDF). The rest are still open:

1. **`NACE_API_KEY`.** When does it arrive, and how much credit covers System 1, NDI and browser runs? Blocks S0 gates.
2. **Claude key.** Use is allowed; does a key exist? Decides whether slice B runs or its time goes to S6.
3. **Teammate.** Available, and from which minute? Decides whether S6, S8a and S7 happen.
4. **DeepInfra vision model.** Which id is served and takes screenshots, and its list prices (needed for the catalog entry).
5. **Rules on pre-existing and third-party code.** The plan reuses MIT code from `silent-chat` and patches the MIT
   browser harness; confirm both are acceptable when credited.
6. **Research-only weights.** Acceptable in the presentation if labelled and not shipped?
7. **Demo material.** One public PDF and one public page with no login, chosen before S3 and S4.
8. **Adobe and Keenable.** Is there a cheap, honest use (for example an Adobe-produced PDF as the demo document), or skip?
9. **Camera.** Built-in or external, and its position relative to the screen (affects S6 mapping).
