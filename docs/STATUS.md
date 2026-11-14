# Status (2026-11-14, updated at each merge)

| Slice | State |
| --- | --- |
| Intern bridge, receipts, Drex log (S1) | merged; real gate passed |
| Overlay: hotkeys, streamed answers, evidence cards (S2) | merged; self-tests pass; not every path tried by hand |
| NDI evidence tool, ground, shelf (S3) | merged; two real PDFs, 5/5 cited quotes verbatim |
| Browser helper on DeepInfra (B) | merged; real run passed |
| Browser task as an Intern tool (S4) | merged; real click run, guard decisions logged, abort verified |
| Verified native note actions and screen vision (S5) | merged; real gates passed |
| Voice: push-to-talk with on-device speech | merged; audio-file self-test passed, live mic untested |
| 12-command router and benchmark | v1 merged; v2 (clean, harder, 90 held-out) results in `docs/evidence/bench_v2.md` |
| Pinch worker (MediaPipe) | merged; synthetic tests only; integration with the overlay in review |
| Top-anchored draggable panel | in progress |
| Secret-path policy, honest failed-write status | in review |
| Lip reading | planned, not built |

Facts worth knowing
- Drex Max id on the hosted API is `drex-max-v1.1`; it reads images too.
- System 2 is `deepinfra/Qwen/Qwen3.8-27B`. DeepInfra is a partner tool.
- `INTERN_HOME` is `.intern-home/` (gitignored), with `.env` linked into it so Intern reads the keys itself. The NDI tool, the router and the browser wrapper also load `.env` inside their own processes.
