# Status (updated 2026-11-14 11:50 PT)

| Slice | State | Evidence |
| --- | --- | --- |
| S0 Intern + Drex Max + Qwen | PASS | `intern -p --json --system1 drex-max-v1.1 --system2 deepinfra/Qwen/Qwen3.8-27B` completed with 4 `decision` events |
| S0 NDI parse | PASS | `smoke/ndi_smoke.py` returned 47,662 chars of Markdown plus blocks and chunks for a 15-page PDF |
| S0 ground response shape | recorded | `docs/GROUND_SHAPE.md`: page + normalized polygons (level 3) |
| S3 NDI evidence + shelf | PASS (gate) | `reach add` new arXiv PDF, `reach ask`: 5 cards, all level 3, `scripts/check_evidence.py` 5/5 verbatim |
| S0 browser harness | cloned and `npm ci` done; build and DeepInfra helper pending | slice B |
| S1 to S9 | not started | |

Facts found in S0
- Drex Max id on the hosted API is `drex-max-v1.1` (`drex-max` resolves to it). It also reads images.
- `INTERN_HOME` is `.intern-home/` (gitignored), with `.env` linked to the repo `.env` so only Intern reads it.
- System 2 is `deepinfra/Qwen/Qwen3.8-27B` (DeepInfra is a partner tool).

## Slice board (11:50 PT)
| Slice | State |
| --- | --- |
| S1 Intern bridge, receipts, Drex log | merged (PR #1, #3) |
| S3 NDI evidence tool, ground, shelf | merged (PR #2); real two-PDF gate passed |
| B DeepInfra browser helper | merged; direct browser run passed (19 s, $0.0012) |
| S2 overlay | building |
| S4 browser task as Intern tool | building |
| S8a part 1: command router + bench (experimental) | PR open (`rohan/router`): `reach route`, `reach ask --route`, `reach bench`; held-out 40/40 for both (not clean: prompt echoes 10 held-out phrasings), Drex p50 260 ms vs Qwen 2414 ms (Qwen emits 12-way JSON); see `docs/evidence/bench.md`. Bench v2 (clean prompt, 120 harder items; PR `rohan/bench-v2`): held-out Drex 87/90 (0 wrong commands), Qwen one-label 83/90, Qwen 12-way 82/90 (2-3 wrong commands each); lead not significant (sign test p 0.125-0.22); p50 265 / 544 / 2391 ms; see `docs/evidence/bench_v2.md` |
| S5 verified completion, S6 pinch, S8a silent path | not started |
