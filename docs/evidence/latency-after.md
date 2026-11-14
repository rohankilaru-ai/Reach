# Latency after (branch rohan/speed)

Same harness (`scripts/latency.py`), same scratch store and asks as `latency-before.md`; client clock, seconds,
median. Changes under test: Intern memory drive off, reasoning off plus a 1024-token output cap for simple asks
(`reach/speed.py`), the concise persona, see_region images downscaled to 1280 px and capped at 400 tokens.

The run was stopped after 14 asks when the Drex/Nace console began returning HTTP 403 (a Vercel checkpoint; the
coordinator paused all Drex traffic). The last valid screen run and all three "say ready" runs are missing: screen
run #3 hung 110 s on a failing Drex guard call, and "say ready" #1 got no Drex answer. Both are excluded and
pending a re-run once Drex answers.

| ask | before total | after total | before first token | after first token | turns before -> after | notes |
| --- | --- | --- | --- | --- | --- | --- |
| a chat | 2.1 (N=3) | 1.5 (N=3) | 1.6 | 1.2 | 1 -> 1 | one outlier at 4.6 s; answers shorter |
| b 17 x 23 | 2.7 (N=3) | 1.8 (N=3) | 2.5 | 1.7 | 1 -> 1 | "391." / "17 times 23 is 391." (correct 3/3, no bold) |
| c document | 10.8 (N=3) | 8.2 (N=3) | 5.5 | 6.0 | 3-5 -> 2 | refused first evidence_search gone (0/3 vs 3/3); evidence gate PASS 3/3 |
| d notes write | 3.9 (N=2) | 3.7 (N=3) | 3.5 | 3.3 | 2 -> 2 | note_append ok + verified 3/3 |
| e screen (see_region) | 10.3 (N=3) | 7.9 (N=2) | 9.9 | 7.6 | 2 -> 2 | see_region 3.5-5.2 s -> 1.7-2.5 s; "Left is red, right is blue." |
| f say ready | 1.8 (N=3) | pending | 1.6 | pending | 1 | Drex outage |

Drex intent (concurrent with session start, unchanged): 0.4-0.5 s before, 0.2-0.45 s after; it is never on the
critical path for these asks. Memory: every before task logged one `memory_maps` Drex decision (18/18); after: 0/14.

## What each change bought (measured)
- Memory off: removes the `memory_maps` round trip per ask and the refused first `evidence_search` (the document
  ask went from 3-5 model turns to 2). Nothing in Reach reads Intern memory.
- Reasoning off (`chat_template_kwargs.enable_thinking=false`, Qwen only): direct DeepInfra probe on a short
  question, default 1.15 s / 39 output tokens vs 0.58 s / 13 tokens with thinking off (`reasoning_effort: none` also
  disabled thinking but took 7.1 s on that probe). Kept on for document, browser, Mac-control and unclear asks, and
  everywhere with REACH_THINK=1. Whether the hook fired is not visible in the Drex log (it does not store assistant
  messages); a key-free test checks the exact body Intern sends.
- Vision: downscale + 400-token cap. Model comparison on 3 generated images (invoice text, two shapes, bar chart),
  all answers correct: 235B-A22B-Instruct 1.4-2.2 s; Qwen3-VL-30B-A3B-Instruct 1.0-2.3 s (verbose, markdown);
  Qwen3-VL-8B-Instruct 2.0-8.6 s. No smaller model was clearly faster, so the 235B stays the default
  (REACH_VISION_MODEL still overrides).
- Streaming: text_delta events were already forwarded as emitted (first token precedes the end on every ask);
  no buffering found, nothing changed.

## Caveats
- The screen ask here is a simple two-colour 1600x900 PNG (10 s before), not the 55-70 s real-screenshot case; the
  mechanism (fewer image tokens, shorter vision answer, no reasoning) applies, the real-screenshot gain is unmeasured.
- Per-turn model durations are not broken out; only first token, intent, tool durations and total.
- The notes ask writes to the real `~/Documents/Reach Notes.md`: the before and after runs appended
  "latency test, buy oat milk" five times there.

## Raw runs
- a chat 0 {"ttft": 0.9751138749998063, "intent": 0.276432707963977, "total": 1.2983722499920987, "turns": 1, "tools": [], "final": "Hey! All good on my end, thanks. What can I help you with today?"}
- a chat 1 {"ttft": 1.2474244160111994, "intent": 0.26499129098374397, "total": 1.5295129159931093, "turns": 1, "tools": [], "final": "Hey! Doing well, thanks for asking. What can I help you with today?"}
- a chat 2 {"ttft": 4.195405584003311, "intent": 0.22552258399082348, "total": 4.562404667027295, "turns": 1, "tools": [], "final": "Hey! All good here \u2014 just ready to help. What can I do for you today?"}
- b math 0 {"ttft": 1.7446537080104463, "intent": 0.26036933303112164, "total": 1.828351375006605, "turns": 1, "tools": [], "final": "391."}
- b math 1 {"ttft": 1.9398300829925574, "intent": 0.24982979201013222, "total": 2.1776782919769175, "turns": 1, "tools": [], "final": "17 times 23 is 391."}
- b math 2 {"ttft": 1.617925084021408, "intent": 0.24044333398342133, "total": 1.6841830419725738, "turns": 1, "tools": [], "final": "391."}
- c document 0 {"ttft": 6.548051541962195, "intent": 0.21108154201647267, "total": 8.143705041962676, "turns": 2, "tools": ["evidence_search:ok:0.0s"], "final": "The base model uses d_model = 512. The paper states: \"To facilitate these residual connections, all sub-layers in the model, as well as the
- c document 1 {"ttft": 5.996615707990713, "intent": 0.3303377919946797, "total": 8.193275458004791, "turns": 2, "tools": ["evidence_search:ok:0.0s"], "final": "The base Transformer uses d_model = 512 [E10]. The paper states: \"To facilitate these residual connections, all sub-layers in the model, as 
- c document 2 {"ttft": 4.251372041995637, "intent": 0.26841395802330226, "total": 14.285804749990348, "turns": 2, "tools": ["evidence_search:ok:0.0s"], "final": "The base model uses d_model = 512 [E10]. The paper states that \"all sub-layers in the model, as well as the embedding layers, produce outp
- d notes 0 {"ttft": 2.909922207996715, "intent": 0.2934927080059424, "total": 3.1342867920175195, "turns": 2, "tools": ["note_append:ok:0.3s"], "final": "Added and verified in your notes file."}
- d notes 1 {"ttft": 3.2530432919738814, "intent": 0.2630644589662552, "total": 3.7054024169920012, "turns": 2, "tools": ["note_append:ok:0.2s"], "final": "Added to your notes (~/Documents/Reach Notes.md) \u2014 verified it's there."}
- d notes 2 {"ttft": 4.082111124997027, "intent": 0.2560517499805428, "total": 4.258973042014986, "turns": 2, "tools": ["note_append:ok:0.2s"], "final": "Added \u2014 verified in your notes file."}
- e screen 0 {"ttft": 7.697853374993429, "intent": 0.26459108298877254, "total": 7.924635167000815, "turns": 2, "tools": ["see_region:ok:2.5s"], "final": "Left is red, right is blue."}
- e screen 1 {"ttft": 7.516216040996369, "intent": 0.4534934159601107, "total": 7.86553820798872, "turns": 2, "tools": ["see_region:ok:1.7s"], "final": "The left side of your selection is red, and the right side is blue."}
- e screen 2 {"ttft": null, "intent": 0.44616966700414196, "total": 109.59994124999503, "turns": 1, "tools": [], "final": ""}
- f say ready 0 {"ttft": null, "intent": null, "total": 7.114003374997992, "turns": 0, "tools": [], "final": ""}
