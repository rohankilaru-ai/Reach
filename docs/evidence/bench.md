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
- Held-out is NOT clean: the label descriptions in the prompt were written after the fixtures and quote held-out
  phrasings almost verbatim ("what is this" h01, "click into" h06, "page down"/"move further down" h08/h10, "wait for
  a moment" h15, "resume"/"carry on" h17/h18, "forget it" h27, "stop talking"/"halt" h33/h35). Both models saw them.
  Nothing was changed after the dev run and held-out ran once, but held-out accuracy is not a generalisation number;
  a rerun with descriptions that do not echo held-out items is the first follow-up.
- Drex probabilities come from the model's choice head. Qwen's probabilities are self-reported in JSON and are not
  calibrated; the same abstain rule is applied to both.
- Latency is client wall-clock from this machine, sequential, same HTTP code path. Drex's own `evaluation_time_ms` is
  in bench.json per item.
- Cost: Drex responses carry tokens but no price, so Drex is reported in tokens. The Drex `est $` column is only
  conditional: input tokens x $0.12/M, the Drex Max input price in the docs' browser-run price table (the direct-API
  price page is a placeholder in the docs we have); output tokens are not billed. Qwen is DeepInfra list price ($0.20/M in, $2.50/M out) before any
  discount. Qwen emits a 12-way probability JSON (~114 output tokens per item), which dominates its latency and cost;
  a one-label reply (7 output tokens, ~0.7 s in a probe) would narrow the gap. Thinking was disabled for Qwen (`chat_template_kwargs.enable_thinking=false`).

## Verdict (written by hand after the run)

- **Accuracy is a tie and this set cannot separate the two models.** Both got 40/40 held-out and 20/20 dev, with no
  wrong commands and all 8 out-of-vocabulary items abstained. The prompt's label descriptions echo 10 held-out items
  (see caveats), and the fixtures are easy; a clean, harder set (real lip-reader output, real dictation, confusable
  pairs like pause/stop/cancel) is needed to rank accuracy.
- **Latency: Drex p50 260 ms vs Qwen 2414 ms on held-out (p95 389 vs 4188)**, but much of Qwen's time is its 12-way JSON
  output; with a one-label reply the gap would shrink (a one-off probe took ~0.7 s). Drex `evaluation_time_ms` p50 was
  156 ms.
- **Tokens:** Drex 21,662 input for 60 items (output not billed); Qwen 18,842 in + 6,847 out = $0.0209 at list price.
  Drex has no price in its response; at the docs' browser-run Max price it would be ~$0.0026. Not a cost finding.
- Confidence on held-out command items: Drex mean top p 0.947 (min 0.595, 3 below 0.9); Qwen self-reported mean 0.960
  (min 0.700). Qwen's numbers are not calibrated, so this is not like-for-like.
- No prompt, threshold or margin change was made after the dev run (first draft scored 20/20 on dev for both).
