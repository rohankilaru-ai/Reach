# Command router benchmark: Drex Max vs Qwen3.8-27B

Run 2026-11-14 11:56. Fixtures: `fixtures/commands/utterances.jsonl` (60 hand-written utterances, 8 gold `uncertain`). Abstain rule for both: `uncertain` if the top label is `uncertain`, top p < 0.5, or top minus runner-up < 0.15. Experimental (S8a).

| system | split | N | accuracy | abstain | wrong command | uncertain caught | errors | p50 ms | p95 ms | in tok | out tok | est $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| drex | held_out | 40 | 100.0% | 12.5% | 0 (0.0%) | 5/5 | 0 | 260 | 389 | 14453 | 14214 | 0.00173 |
| drex | dev | 20 | 100.0% | 15.0% | 0 (0.0%) | 3/3 | 0 | 316 | 432 | 7209 | 7132 | 0.00086 |
| drex | all | 60 | 100.0% | 13.3% | 0 (0.0%) | 8/8 | 0 | 284 | 407 | 21662 | 21346 | 0.00260 |
| qwen | held_out | 40 | 100.0% | 12.5% | 0 (0.0%) | 5/5 | 0 | 2414 | 4188 | 12573 | 4564 | 0.01392 |
| qwen | dev | 20 | 100.0% | 15.0% | 0 (0.0%) | 3/3 | 0 | 2708 | 4256 | 6269 | 2283 | 0.00696 |
| qwen | all | 60 | 100.0% | 13.3% | 0 (0.0%) | 8/8 | 0 | 2584 | 4256 | 18842 | 6847 | 0.02089 |

Held-out accuracy by input source:

| system | source | N | accuracy | wrong command |
|---|---|---|---|---|
| drex | dictated | 14 | 100.0% | 0 |
| drex | lip | 10 | 100.0% | 0 |
| drex | typed | 16 | 100.0% | 0 |
| qwen | dictated | 14 | 100.0% | 0 |
| qwen | lip | 10 | 100.0% | 0 |
| qwen | typed | 16 | 100.0% | 0 |

## Caveats

- N is small (40 held-out, 20 dev); one item is 2.5 points of held-out accuracy, and p95 over 40 calls is the 38th value.
- The utterances are hand-written by the builder, not recorded from users; lip-reading items are simulated hypothesis
  lists, not output of a real lip reader.
- The prompt, threshold and margin were tuned on `dev` only; `held_out` was run once with them frozen.
- Drex probabilities come from the model's choice head. Qwen's probabilities are self-reported in JSON and are not
  calibrated; the same abstain rule is applied to both.
- Latency is client wall-clock from this machine, sequential, same HTTP code path. Drex's own `evaluation_time_ms` is
  in bench.json per item.
- Cost: Drex responses carry tokens but no price. The Drex column is input tokens x $0.12/M, the Drex Max input price
  in the Nace docs (output tokens are not billed). Qwen is DeepInfra list price ($0.20/M in, $2.50/M out) before any
  discount. Thinking was disabled for Qwen (`chat_template_kwargs.enable_thinking=false`).

## Verdict (written by hand after the run)

- **Accuracy is a tie, and this set cannot separate the two models.** Both got 40/40 held-out and 20/20 dev, with no
  wrong commands and all 8 out-of-vocabulary items abstained. The fixtures are too easy to rank accuracy; a harder set
  (real lip-reader output, real dictation errors, more confusable pairs such as pause/stop/cancel) is needed for that.
- **Drex Max is faster and cheaper on the same task.** Held-out p50 260 ms vs 2414 ms (about 9x), p95 389 ms vs 4188 ms.
  Drex's server-side `evaluation_time_ms` p50 was 156 ms. Estimated cost for all 60 items: $0.0026 Drex vs $0.0209
  Qwen (Drex from the docs' input price, Qwen at list price).
- Confidence on held-out command items: Drex mean top p 0.947 (min 0.595, 3 below 0.9); Qwen self-reported mean 0.960
  (min 0.700). Qwen's numbers are not calibrated probabilities, so this is not a like-for-like comparison.
- No prompt, threshold or margin change was needed: the first draft scored 20/20 on dev for both, so the frozen
  settings are the first draft. Held-out was run once.
