# Live demo script (4 minutes)

Eleven steps, in order. Each has what to do, what to say, a time limit and a fallback. Every flow here has a record in
`docs/evidence/` or the README status table; the "Evidence" line under each step says which. Where a flow is
experimental, the script says so out loud.

**One rule for the whole demo:** if a step shows no answer by its time limit, press **Esc**, say "that one is slow,
here is the recorded run", show the fallback and move on. Do not wait. (During testing one ask hung for 110 s when the
Drex console returned errors: `docs/evidence/latency-after.md`.)

## Before you go on stage

Do these once, then run every step below one time as a warm-up.

1. Backend and overlay: `cd backend && uv sync --extra do`, then `../scripts/build-overlay.sh` and
   `open ../apps/overlay/build/Reach.app`. The pill should appear at the top of the screen.
2. Leave Do mode off for the server (no `REACH_DO_MODE` line in `.reach/server.env`); step 9 uses the test gate only.
3. Browser harness built: `../scripts/setup-browser.sh`.
4. Document added: `uv run reach add <path to the Batch Normalization paper PDF>` (wait for it to finish).
5. Permissions granted (Reach's menu bar item > Set up Reach… lists them): Screen Recording, Microphone, Speech
   Recognition, Automation for TextEdit, Notes and Chrome. For the terminal app: Accessibility (Do mode gate) and
   Camera (hands).
6. Hand model present at `finger_processing/hand_landmarker.task`.
7. Google Chrome open with one ordinary window.
8. A window with something worth circling, for example a chart or a paragraph of a web page, left open behind Chrome.
9. Terminal with three tabs, each with its command typed but not run:
   - Tab A, in `backend/`:
     `uv run reach ask "Go to books.toscrape.com, click the Travel category link in the sidebar, then report the title of the first book listed"`
   - Tab B, in `backend/`: `uv run --extra do python ../smoke/do_mode_gate.py`
   - Tab C, at the repo root: `uv run python -m hands_mode.run --control` (middle pinch + loop to select)
10. A text file open with every prompt below, one per line, to paste instead of typing (the limits leave no time
    for typing long prompts).
11. Open in an editor, ready to show as fallbacks: `docs/evidence/receipt.json`, `docs/evidence/browser-run.json`,
    `docs/evidence/auto-screen.md`, `docs/evidence/do-mode.md`, and slide 5 of the deck (the Drex log).

Not rehearsed in the evidence: running the browser task in the background while other asks run (step 1 below starts
it early). Try that once in the warm-up. If the two interfere, run the browser task on its own at step 6 and drop
step 9.

## The script

| # | Start | Step | Limit |
| --- | --- | --- | --- |
| 1 | 0:00 | Start the browser task in the background | 10 s |
| 2 | 0:10 | Ask a question | 15 s |
| 3 | 0:25 | Circle and talk | 35 s |
| 4 | 1:00 | Screen look, no circle | 25 s |
| 5 | 1:25 | Cited PDF answer | 25 s |
| 6 | 1:50 | Browser task result | 15 s |
| 7 | 2:05 | Verified note | 20 s |
| 8 | 2:25 | Chrome tab | 20 s |
| 9 | 2:45 | Do mode in TextEdit (experimental) | 30 s |
| 10 | 3:15 | Hands (experimental) | 20 s |
| 11 | 3:35 | The Drex log | 25 s |

### 1. Start the browser task (0:00)

- **Do:** Terminal tab A, press Return. Leave it running.
- **Say:** "First I start a browser task, because it takes over a minute. Reach hands it to the Nace browser harness,
  and Drex Max makes the page actions. We will come back to it."
- **Fallback:** none needed yet; the result is checked at step 6.
- **Evidence:** `docs/evidence/browser-run.json` (the same task: 83 s, 2 Drex Max calls).

### 2. Ask a question (0:10)

- **Do:** Tap **Option-Space**, type `What is 17 times 23?`, press Return.
- **Say:** "One key. Tap to type. While the session starts, Drex Max classifies the ask in about a quarter of a second.
  This one is plain chat, so the model answers directly with no tools."
- **Expect:** "391" in about 2 seconds.
- **Fallback (no answer in 10 s):** Esc. In a terminal: `uv run reach ask "What is 17 times 23?"`. If that also
  stalls, say "the model API is slow right now" and go to step 3.
- **Evidence:** `docs/evidence/latency-after.md` (median 1.8 s, correct 3 of 3).

### 3. Circle and talk (0:25)

- **Do:** Hold **Option-Space**, draw a loop with the mouse around the chart or paragraph, and while holding say:
  "Explain this in two sentences." Release.
- **Say (while it works):** "Voice is still experimental. The loop and my words go out as one ask. The circled area goes to Qwen3-VL on DeepInfra.
  Treat the answer as a second opinion: vision models misread dense images."
- **Expect:** a short explanation in the panel within about 10 to 30 seconds.
- **Fallback (voice not heard):** the panel opens with the circle attached and a text field; type
  `Explain this in two sentences.` and press Return.
- **Fallback (no answer in 30 s):** Esc, say "the circled path is slow, the next step asks about the whole screen
  instead", go to step 4.
- **Evidence:** README status table (`see_region` implemented, accuracy varies; voice experimental);
  `docs/evidence/latency-after.md` (7.9 s on a simple image); one logged vision call took 29 s.

### 4. Screen look, no circle (1:00)

- **Do:** Tap **Option-Space**, type `What is on my screen right now? One sentence.`, press Return.
- **Say:** "No circle this time. Drex Max judged it a screen question, so Reach captured the screen itself and the
  model described it."
- **Expect:** one sentence naming the front app and what it shows, in about 15 seconds.
- **Fallback (no answer in 20 s):** Esc. Show `docs/evidence/auto-screen.md`, section "Real run", and read out the
  recorded answer.
- **Fallback ("needs you: allow Screen Recording"):** say "that is the permission check working", show the same file.
- **Evidence:** `docs/evidence/auto-screen.md` (intent `screen_question` p=0.978, answer checked against the capture,
  14.4 s).

### 5. Cited PDF answer (1:25)

- **Do:** Tap **Option-Space**, type
  `According to my Batch Normalization paper, what problem does it say it addresses? Cite it.`, press Return.
- **Say:** "NDI parsed this PDF earlier. The model cites passage ids, and Reach builds each card from the stored
  quote, not from the model's wording. Then NDI locates that quote on the page." When the card appears, click it:
  "It opens the PDF at that page. Grounding shows where the text is; it does not prove the reasoning."
- **Expect:** an answer with an evidence card in about 7 to 10 seconds.
- **Fallback (no answer in 20 s, or no card):** Esc. Show `docs/evidence/receipt.json`: "This is the receipt from the
  same question earlier today: one `evidence_search`, one NDI call, three Drex decisions, 6.5 seconds."
- **Evidence:** `docs/evidence/receipt.json`; README status table (two real PDFs, 5 of 5 cited quotes verbatim);
  `docs/GROUND_SHAPE.md`.

### 6. Browser task result (1:50)

- **Do:** Switch to Terminal tab A.
- **Say (if finished):** "The browser task is done: it clicked the Travel category and read the first title. Drex Max
  made the page actions; the helper model is Qwen3-VL on DeepInfra, through two small patches we keep in the repo."
- **Say (if still running):** "Still going; a run takes about 80 seconds. Here is the recorded one." Show
  `docs/evidence/browser-run.json`, the `loop_mode_run` block: answer "It's Only the Himalayas", 2 Drex Max calls.
- **Fallback (it failed):** say "It reports failed rather than pretending", then show `browser-run.json` as above.
  Reach's receipt says `failed` when the harness exits with an error (`docs/evidence/intent.md`, live run 4).
- **Do not claim:** that Drex blocks risky browser actions. The README says we have not shown that.
- **Evidence:** `docs/evidence/browser-run.json` (browser task as an Intern tool: real click run).

### 7. Verified note (2:05)

- **Do:** Tap **Option-Space**, type `Add "Drexathon demo, buy oat milk" to my notes`, press Return.
- **Say:** "Reach writes the note, then reads the file back. It says done only when the readback matches."
- **Expect:** "Added, verified" in about 4 seconds. Then, in a terminal, `tail -n 1 ~/Documents/"Reach Notes.md"`
  to show only the new line (do not open the whole notes file on the projector).
- **Fallback (panel says Failed or Unverified):** say "That is the point: a write it could not confirm is never shown
  as done." Move on.
- **Evidence:** `docs/evidence/latency-after.md` (`note_append` ok and verified 3 of 3, median 3.7 s).

### 8. Chrome tab (2:25)

- **Do:** Hold **Option-Space** and say: "Open a Chrome tab on water scarcity." Release. Voice is experimental; if you
  want the safe path, tap and paste the same sentence instead.
- **Say:** "This opens a new tab in my own Chrome through Chrome's scripting interface, then reads Chrome back to
  confirm the tab exists. It never navigates, reloads or closes a tab it did not open."
- **Expect:** a new Chrome tab with a search, in about 7 seconds.
- **Fallback (nothing opens in 15 s):** Esc. Show slide 5 of the deck: "Here is the same ask from this morning in the
  log: guard routine, action verified."
- **Evidence:** `docs/evidence/drex-log.jsonl`, task `abb1c7e32dd5` (`chrome_open_url`, outcome verified).

### 9. Do mode in TextEdit, experimental (2:45)

- **Do:** Terminal tab B, press Return, then **take your hands off the mouse and keyboard**. Any input is the kill
  switch and stops the action.
- **Say:** "This part is experimental and off by default. Do mode presses buttons and fills text fields in other apps
  through the Accessibility interface: no synthetic keystrokes, no mouse events. This is our test gate, not a full
  ask: it opens a scratch TextEdit document, sets the text, presses Bold, and reads both back."
- **Expect:** lines ending `ax_set_text: outcome=verified` and `ax_press: outcome=verified`, then
  `close without saving: ok`.
- **Fallback (aborted with "user took over", or no Accessibility permission):** say "The kill switch fired, which is
  what it is for", and show the recorded output in `docs/evidence/do-mode.md`, section "Gate".
- **Do not claim:** that Do mode works through a spoken ask. That path has not been run end to end.
- **Evidence:** `docs/evidence/do-mode.md`.

### 10. Hands, experimental (3:15)

- **Do:** Terminal tab C, press Return. A preview window opens. Raise one hand, move the index finger, then pinch.
- **Say:** "Also experimental. A MediaPipe hand tracker moves a pointer with my fingertip, and a pinch starts a
  selection. In our evidence this has only passed synthetic tests, so this may not work on stage." Press `q` to quit.
- **Fallback (camera or tracking fails):** quit, run `uv run pytest -q hands_mode/tests/test_select.py` in the same
  tab and say "These are the synthetic checks it passes; no real-hand run is in our evidence."
- **If short on time:** skip this step entirely and say "pinch selection is experimental; it is on the last slide."
- **Evidence:** README status table (passes synthetic landmark tests; no real-hand run yet); `hands_mode/select.py`.

### 11. The Drex log (3:35)

- **Do:** In a terminal, in `backend/`: `uv run reach log -n 30`.
- **Say:** "The latest decisions Drex Max made during this demo. `intent` before each ask, `guard` before guarded
  tool calls, `supervise` and `wrap_up` on turns, and the `action` lines with verified outcomes. Each line has a
  probability. Drex Max never wrote a word of the answers; it decided, Intern ran it, and Reach checked the result."
- **Point at:** one `intent` line (the kind and its probability), one `guard` line (`risk=routine`), one `action` line
  ending `verified`.
- **Fallback (log empty or Drex was down):** show slide 5 of the deck, or open `docs/evidence/drex-log.jsonl`:
  "This is the saved log from today's runs."
- **Evidence:** `docs/evidence/drex-log.jsonl`.

## If you are running late

Cut in this order: step 10 (hands), step 9 (Do mode), step 4 (screen look). Never cut step 11: the log is the proof
that Drex Max is doing the deciding.

## Things not to say

- Not "Drex blocks dangerous actions": every guard decision in the saved log was "routine", and the one risky-form
  test does not show blocking. A separate policy hook does refuse tool calls that touch key files.
- Not "better than Qwen": on 90 held-out commands Drex Max got 87 and Qwen3.8-27B 83, and the intervals overlap.
- Not "cloud voice is better": in our test Apple on-device speech was as accurate and faster.
- Not "works with any app": Do mode has one TextEdit gate and one Finder check behind it.
