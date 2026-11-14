# Command router benchmark v2: Drex Max vs Qwen3.8-27B (clean, harder set)

Held-out run 2026-11-14 12:08; dev run 2026-11-14 12:06. Fixtures: `fixtures/commands/utterances_v2.jsonl` (120 hand-labelled items, 25 gold `uncertain`; split 30 dev / 90 held-out). Prompt frozen in commit `9588d1f` (prompt hash `e1bbc9e30ad7b686`) before any v2 API call; held-out ran once. Abstain rule: `uncertain` if the top label is `uncertain`, top p < 0.5, or top minus runner-up < 0.15 (qwen_label: only the first clause applies). Experimental (S8a).

## Held-out (N = 90)

| system | accuracy | 95% Wilson | wrong command (confusion + acted on uncertain) | abstain on gold-uncertain | over-abstain on commands | errors | p50 ms | p95 ms | in tok | out tok | list $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| drex | 87/90 (96.7%) | 90.6% to 98.9% | 0 (0.0%): 0 + 0 | 19/19 (100.0%) | 3/71 (4.2%) | 0 | 265 | 405 | 43384 | 31888 | tokens only |
| qwen_probs | 82/90 (91.1%) | 83.4% to 95.4% | 3 (3.3%): 1 + 2 | 17/19 (89.5%) | 5/71 (7.0%) | 0 | 2391 | 5115 | 40864 | 10105 | 0.03343 |
| qwen_label | 83/90 (92.2%) | 84.8% to 96.2% | 2 (2.2%): 0 + 2 | 17/19 (89.5%) | 5/71 (7.0%) | 0 | 544 | 1113 | 39514 | 697 | 0.00965 |

Paired on held-out (same items; exact sign test on items one got right and the other wrong):

| A | B | A right, B wrong | B right, A wrong | p |
|---|---|---|---|---|
| drex | qwen_probs | 6 | 1 | 0.125 |
| drex | qwen_label | 5 | 1 | 0.2188 |
| qwen_probs | qwen_label | 3 | 4 | 1.0 |

Held-out accuracy by tag (an item can carry several tags):

| tag | N | drex | qwen_probs | qwen_label |
|---|---|---|---|---|
| ambiguous | 4 | 4/4 | 3/4 | 3/4 |
| confusable | 13 | 13/13 | 12/13 | 12/13 |
| correction | 4 | 3/4 | 2/4 | 3/4 |
| filler | 7 | 7/7 | 7/7 | 7/7 |
| foreign | 12 | 11/12 | 11/12 | 12/12 |
| garbled | 3 | 3/3 | 3/3 | 3/3 |
| lip_ambiguous | 2 | 2/2 | 2/2 | 2/2 |
| lip_missing | 9 | 8/9 | 8/9 | 7/9 |
| lip_rank2 | 3 | 3/3 | 3/3 | 3/3 |
| multi_intent | 3 | 3/3 | 3/3 | 3/3 |
| negation | 8 | 7/8 | 4/8 | 6/8 |
| noise | 10 | 10/10 | 10/10 | 9/10 |
| oov | 5 | 5/5 | 5/5 | 5/5 |

## Dev (N = 30, run once to catch bugs; nothing was tuned on it)

| system | accuracy | wrong command | abstain on gold-uncertain | over-abstain | errors | p50 ms |
|---|---|---|---|---|---|---|
| drex | 29/30 | 0 | 6/6 | 1/24 | 0 | 291 |
| qwen_probs | 24/30 | 2 | 5/6 | 4/24 | 0 | 2638 |
| qwen_label | 29/30 | 1 | 5/6 | 0/24 | 0 | 754 |

## Every held-out miss

| system | id | input | gold | pred | kind | reason |
|---|---|---|---|---|---|---|
| qwen_probs | v2h13 | dont open the email, open the attachment | open_this | uncertain | abstained | model chose uncertain |
| qwen_probs | v2h22 | sube un poco | scroll_up | uncertain | abstained | model chose uncertain |
| qwen_probs | v2h25 | don't go down, I need the bit above | scroll_up | cancel | wrong command | ok |
| qwen_probs | v2h26 | one sec, phone's ringing | pause | uncertain | abstained | model chose uncertain |
| drex | v2h29 | un momento | pause | uncertain | abstained | top p 0.46 < 0.5 |
| drex | v2h35 | go ohn / con tin you / coffin you | continue | uncertain | abstained | top p 0.47 < 0.5 |
| qwen_label | v2h35 | go ohn / con tin you / coffin you | continue | uncertain | abstained | model chose uncertain |
| qwen_probs | v2h35 | go ohn / con tin you / coffin you | continue | uncertain | abstained | model chose uncertain |
| qwen_label | v2h46 | mvoe it hree | put_this_here | uncertain | abstained | model chose uncertain |
| drex | v2h50 | not there, here | put_this_here | uncertain | abstained | top p 0.48 < 0.5 |
| qwen_label | v2h50 | not there, here | put_this_here | uncertain | abstained | model chose uncertain |
| qwen_probs | v2h50 | not there, here | put_this_here | uncertain | abstained | model chose uncertain |
| qwen_label | v2h53 | cansul | cancel | uncertain | abstained | model chose uncertain |
| qwen_label | v2h62 | ok continue to the next slide | next | uncertain | abstained | model chose uncertain |
| qwen_label | v2h80 | wait | uncertain | pause | acted on uncertain | ok |
| qwen_probs | v2h80 | wait | uncertain | pause | acted on uncertain | ok |
| qwen_label | v2h86 | no don't open it | uncertain | cancel | acted on uncertain | ok |
| qwen_probs | v2h86 | no don't open it | uncertain | cancel | acted on uncertain | ok |

## Verdict (written by hand after the held-out run)
- **Drex Max had the best held-out score, but on 90 items the lead is not statistically established.** Drex 87/90
  (96.7%, Wilson 90.6 to 98.9%), qwen_label 83/90 (92.2%, 84.8 to 96.2%), qwen_probs 82/90 (91.1%, 83.4 to 95.4%).
  The intervals overlap. On paired items Drex was right where Qwen was wrong 6 times versus 1 the other way
  (sign test p = 0.125 vs qwen_probs), and 5 versus 1 against qwen_label (p = 0.22). That is a lead, not a win.
- **Safety is the clearest difference.** Drex executed no wrong command (0/90) and abstained on all 19 gold-`uncertain`
  items. Both Qwen variants acted on two gold-`uncertain` items: "wait" became `pause`, and "no don't open it" became
  `cancel`; qwen_probs also routed "don't go down, I need the bit above" to `cancel` (gold `scroll_up`). The two
  gold-`uncertain` labels are judgement calls. "wait" is labelled ambiguous between pause and cancel, and "no don't
  open it" is labelled a negation with no positive instruction, so a reader could reasonably score Qwen's answers there
  as acceptable. Every Drex miss is an abstention (top p 0.46 to 0.48, just under the 0.5 threshold), which is the
  cheap failure.
- **Over-abstention:** Drex 3/71 command items (4.2%), each Qwen variant 5/71 (7.0%). All three abstained on
  v2h35 (three-guess lip list for "continue") and on v2h50 ("not there, here", which gives no held item).
- **Latency (client wall-clock, sequential, this machine):** Drex p50 265 ms / p95 405 ms; qwen_label p50 544 ms /
  p95 1113 ms; qwen_probs p50 2391 ms / p95 5115 ms. The one-label format removes most of the gap v1 reported (it cut
  Qwen output from 10,105 to 697 tokens on held-out), but Drex is still about 2x faster at p50 and 2.7x at p95. The
  one-label format did not cost accuracy (83 vs 82).
- **Tokens / cost:** Drex held-out 43,384 in, 31,888 out (reported in tokens only; no price in the response).
  Qwen at DeepInfra list price on held-out: qwen_probs $0.0334, qwen_label $0.0096 (about $0.00011 per call). Total
  spend for this benchmark, dev + held-out, Qwen list price: $0.0574.
- **This set is harder than v1 but not hard enough to rank the models.** The best model is at 96.7%. Of the planned
  stressors, negation (Drex 7/8, qwen_probs 4/8, qwen_label 6/8) and lip lists with the answer missing separated the
  models most. Next steps are real lip-reader and dictation output and more ambiguous or negated items, which would
  need roughly 300+ items to separate a 4-point difference.

## Caveats

- One person wrote the label descriptions, the instructions and all 120 items, and labelled them, in the same
  session. The frozen prompt (commit `9588d1f`) was written before the items. A test checks that no content-word bigram
  is shared between the prompt and any item, and that no v2 input matches a v1 input exactly and no v2 input shares a
  content-word bigram with the v1 descriptions. That guards against copying; it does not prove independence.
- Lip-reading items are simulated hypothesis lists; dictated items are typed strings standing in for speech-to-text.
- The instructions state the gold policy: the final intent wins on self-correction, two different commands mean
  `uncertain`, and a lip list whose guesses split go to `uncertain`. Multi-intent accuracy measures following that
  policy.
- qwen_probs probabilities are self-reported and uncalibrated. qwen_label has no probabilities, so only the "model
  chose uncertain" part of the abstain rule applies to it; threshold and margin cannot. Drex probabilities come from
  its choice head.
- The prompt, threshold, margin and gold labels were not changed after the dev run (dev: drex 29/30, qwen_probs
  24/30, qwen_label 29/30, zero errors). Held-out ran exactly once.
- The production router (`reach route`) still uses the v1 label descriptions in `commands.py`; v2's frozen
  descriptions are used only by `python -m reach.bench_v2`.
