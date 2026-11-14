# Speak without a key: is it addressed to the assistant?

backend/reach/addressed.py. One Drex Max request (`/v1/systemone`) per utterance with three typed questions:
`addressed_to_assistant` (noul), `is_command_or_question` (noul) and the 12-command `command` choice from commands.py.
Logged to the Drex log as `decision`, purpose `addressed`. It never runs an agent task and never acts.

## Message contract (for the overlay; apps/overlay is not changed here)

Client -> server (after `hello`):
```json
{"type": "utterance", "text": "Reach, scroll down", "source": "always_listening", "wake_matched": true}
```
`wake_matched` is optional; the server also checks the text itself (wake = client flag OR the text starts with
"Reach" / "Hey Reach").

Server -> that client only:
```json
{"type": "addressed", "text": "Reach, scroll down", "p_addressed": 0.9982, "addressed": true,
 "command": "scroll_down", "reason": "p_addressed 1.00 >= 0.85", "wake_matched": true, "latency_ms": 250}
```
`command` is set only when addressed, `is_command_or_question` >= 0.5 and the 12-command router's own abstain rule
picks a command; otherwise null (send the text as a normal `ask`). `addressed: false` means do nothing.

## Decision rule (`addressed.decide`, the only place)

- addressed = p_addressed >= 0.85, OR (wake word at the start AND p_addressed >= 0.5)
- error, missing key, no probability, or no answer within 1.2 s total => not addressed
- thresholds are fixed by the spec; nothing was tuned (dev was only used to check the question wording works)

## Measured (2026-11-14, drex-max-v1.1, sequential real calls, fixtures/addressed/utterances.jsonl)

60 hand-written utterances: 20 commands (10 with wake word), 20 side talk / meeting / lecture / reading aloud,
20 hard cases. Split dev 20 / held-out 40; held-out run once. Raw rows in `addressed_dev.json` and
`addressed_held_out.json`.

| split | n | precision | recall | false-trigger rate | p50 | p90 | errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dev | 20 | 0.875 (7/8) | 0.875 (7/8) | 0.083 (1/12) | 248 ms | 320 ms | 0 |
| held-out | 40 | 0.938 (15/16) | 0.789 (15/19) | 0.048 (1/21) | 272 ms | 335 ms | 0 |

Held-out by group: commands 12/13, side talk 13/13, hard 10/14. p50 is under the 400 ms target.
Cost: 37,086 input tokens total for 60 checks (about $0.0045 at $0.12/M).

Held-out errors (honest list):
- missed `keep going` (p 0.848, just under 0.85), `what does this mean` (0.643), `next` (0.455),
  `show me what this error is about` (0.842). Bare short commands without the wake word are the weak spot; say
  "Reach, ..." to get the lower 0.5 bar.
- false trigger: `did Reach save my note` (p 0.952): talking about the product by name, which text alone cannot tell
  from asking it. A wake word inside the sentence (not at the start) does not lower the bar, but Drex itself was sure.

Dev errors: `hey can you pause the music` said to a person -> triggered (0.893); `okay keep going` missed (0.780).

Text alone cannot separate some cases (the same words said to a person or to the assistant); the fix is context
from the client (gaze, push-to-talk history, whether a meeting app is active), not a lower bar.
