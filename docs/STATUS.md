# Status (updated 2026-11-14 11:40 PT)

| Slice | State | Evidence |
| --- | --- | --- |
| S0 Intern + Drex Max + Qwen | PASS | `intern -p --json --system1 drex-max-v1.1 --system2 deepinfra/Qwen/Qwen3.8-27B` completed with 4 `decision` events |
| S0 NDI parse | PASS | `smoke/ndi_smoke.py` returned 47,662 chars of Markdown plus blocks and chunks for a 15-page PDF |
| S0 ground response shape | not yet recorded | S3 |
| S0 browser harness | cloned and `npm ci` done; build and DeepInfra helper pending | slice B |
| S1 to S9 | not started | |

Facts found in S0
- Drex Max id on the hosted API is `drex-max-v1.1` (`drex-max` resolves to it). It also reads images.
- `INTERN_HOME` is `.intern-home/` (gitignored), with `.env` linked to the repo `.env` so only Intern reads it.
- System 2 is `deepinfra/Qwen/Qwen3.8-27B` (DeepInfra is a partner tool).
