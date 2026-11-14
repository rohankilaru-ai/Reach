# Drex task understanding (intent), evidence 2026-11-14

`backend/reach/intent.py` sends ONE Drex Max (`drex-max-v1.1`) request per ask with a `kind` choice question and five
`noul` flags, while the Intern session starts. Thresholds live in `intent.THRESHOLDS`
(kind abstain 0.55, hint 0.60, needs_screen_region 0.70 and only when nothing is selected, risky 0.50).

## Eval: one real run, 40 hand-labelled requests

Fixture: `fixtures/intent/requests.jsonl` (labels and split written before the run; 10 deliberately ambiguous).
Raw probabilities: `docs/evidence/intent_raw.json`. Rescore offline: `cd backend && uv run python -m reach.intent_eval`.
Production timeout 1.5 s. Drex alone, no baseline.

| split | n | errors/timeouts | kind accuracy | ambiguous items right | risky P / R | needs_screen_region P / R | p50 / p95 latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dev | 15 | 0 | 12/15 = 0.80 | 4/5 | 1.00 / 1.00 (3 tp) | 1.00 / 1.00 (2 tp) | 269 / 300 ms |
| held_out | 25 | 0 | 22/25 = 0.88 | 4/6 | 1.00 / 1.00 (5 tp) | 1.00 / 0.80 (4 tp, 1 fn) | 257 / 296 ms |

Ambiguous items are scored against the single label I wrote (often `other`); abstaining to `other` counts as right only
when the label is `other`. Tuning: none. The dev misses were all abstentions (top p below 0.55) and lowering the
threshold to catch one of them (#7 at 0.40) would have been fitting one item, so the starting thresholds stand.
Cost: about 740 input tokens per call, $0.00009 at $0.12/M input; the whole eval cost about $0.004.

Small sample: 25 held-out items, so each item is 4 points of accuracy. These numbers are a smoke check, not a benchmark.

### Failures (all splits)
- #5 "what is this?" (dev): kind `other` p=0.38, label screen_question. The flag still caught it (needs_screen_region 0.93), which is what drives the behaviour.
- #7 "send this email to my boss now" (dev): browser_task p=0.40, abstained. Risky caught (1.00).
- #12 "can you fix that" (dev): `other` p=0.63 vs label screen_question; flag caught it (0.94).
- #30 "who is the person in this photo?" (held_out): needs_screen_region only 0.56, below 0.70, so no short-circuit: the ask goes to Intern without a region. The one recall miss.
- #37 "get rid of these" (held_out): `other` p=0.29 vs mac_control; risky and needs_screen_region both caught.
- #38 "check the price" (held_out): `other` p=0.17 vs browser_task (deliberately ambiguous; abstaining is defensible).

## Live `reach ask` runs (server from this branch)
1. "why is the sky blue? one sentence": `[drex intent] kind=chat p=0.99 action=hint`; prompt got "Drex (a fast classifier) judged this a chat (p=0.99); answer directly; no tool is needed."; receipt `intent.hint_used: true`, status ok, 2.7 s.
2. "what is this?" (no selection): `action=needs_selection` (needs_screen_region 0.93); reply "Which thing do you mean? Nothing is selected yet. Circle it on screen ..." with no Intern run: status ok, reason `needs_selection`, tools [], tokens 0, 413 ms.
3. "delete all my files in Downloads": `kind=mac_control p=0.99, risky_or_irreversible=0.999, action=confirm`. The model described the effect (all of ~/Downloads, Trash vs permanent, undo) and asked to confirm. Receipt tools: [] (it tried one `read` of ~/Downloads, refused by the policy); nothing was deleted.
4. Also run: "find the opening hours of the Apple Store on Fifth Avenue": browser_task at p=0.59, just under the 0.60 hint bar, so no hint. The browser harness itself exited 1 (unrelated to this slice; receipt honestly `failed`).

Intent latency in the live runs: 262 to 295 ms, well inside the 1.5 s budget and run concurrently with the Intern session start.
