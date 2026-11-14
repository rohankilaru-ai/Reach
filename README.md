# Reach
Reach — hackathon project

Experimental: `reach route "<text>"` maps an utterance to one of 12 commands with one Drex Max `choice` question
(abstains as `uncertain`); `reach bench` compares it with Qwen3.8-27B on hand-written fixtures. Results:
`docs/evidence/bench.md`.
Benchmark v2 (clean prompt, harder 120-item set, plus a one-label Qwen variant): `docs/evidence/bench_v2.md`.
