# Latency before (origin/main af75ed9)

Harness: `scripts/latency.py` against a real `reach serve` (System 2 deepinfra/Qwen/Qwen3.8-27B, Drex intent on,
see_region on Qwen3-VL-235B-A22B-Instruct). Client clock, seconds, median of N runs. The first server was stopped
from outside after 8 runs (asks a-d, notes N=2); asks e and f were re-run on a fresh server built from origin/main.

| ask | N | first token | Drex intent | total | turns | notes |
| --- | --- | --- | --- | --- | --- | --- |
| a chat "hey, how's it going?" | 3 | 1.6 | 0.4 | 2.1 | 1 | |
| b "what is 17 times 23?" | 3 | 2.5 | 0.4 | 2.7 | 1 | "17 x 23 = **391**" (bold) |
| c document (Transformer d_model) | 3 | 5.5 | 0.4 | 10.8 | 3 (3-5) | every run: first evidence_search refused by Intern memory, then retried |
| d notes write | 2 | 3.5 | 0.5 | 3.9 | 2 | note_append verified |
| e screen (see_region, 1600x900 PNG) | 3 | 9.9 | 0.5 | 10.3 | 2 | see_region 3.5-5.2 s; answer in bold |
| f "say ready" | 3 | 1.6 | 0.3 | 1.8 | 1 | |

Streaming: first token arrives well before the end on every ask (text_delta events are forwarded as emitted), so
nothing buffers the answer; the wait is model time before the first token.

## Raw runs
- a chat 0 {"ttft": 1.7528265000437386, "intent": 0.4124712920165621, "total": 2.1900302920257673, "turns": 1, "tools": [], "final": "Hey! I'm doing well, thanks for asking. Just here and ready to help with whatever you need. What's on your mind?"}
- a chat 1 {"ttft": 1.6458521250169724, "intent": 0.45007045805687085, "total": 2.1114087080350146, "turns": 1, "tools": [], "final": "Hey! I'm doing well, thanks for asking. Ready to help with whatever you need \u2014 what's on your mind?"}
- a chat 2 {"ttft": 1.4788696669857018, "intent": 0.4860977089847438, "total": 1.9632851249771193, "turns": 1, "tools": [], "final": "Hey! I'm doing well, thanks for asking. It's a nice day to be up and running. What can I help you with today?"}
- b math 0 {"ttft": 1.7626370420330204, "intent": 0.3962737090187147, "total": 1.9543392920168117, "turns": 1, "tools": [], "final": "17 \u00d7 23 = **391**"}
- b math 1 {"ttft": 2.4716147090075538, "intent": 0.40428800001973286, "total": 2.7065268750302494, "turns": 1, "tools": [], "final": "17 \u00d7 23 = **391**."}
- b math 2 {"ttft": 2.4936415829579346, "intent": 0.37611154100159183, "total": 2.672189915960189, "turns": 1, "tools": [], "final": "17 times 23 is **391**."}
- c document 0 {"ttft": 5.470882166991942, "intent": 0.4363441669847816, "total": 10.816900666977745, "turns": 3, "tools": ["evidence_search:refused:0.0s", "evidence_search:ok:0.0s"], "final": "In the Transformer paper, the base model has **d_model = 512**. The paper states this in Section 3.1 (Encode
- c document 1 {"ttft": 5.380677750043105, "intent": 0.4826873750425875, "total": 17.32265945803374, "turns": 5, "tools": ["evidence_search:refused:0.0s", "evidence_search:ok:0.0s", "evidence_search:ok:0.0s", "evidence_search:ok:0.0s"], "final": "The base model uses **d_model = 512**.  From Table 3 (s
- c document 2 {"ttft": 6.817908334021922, "intent": 0.3907188749872148, "total": 7.998559708998073, "turns": 3, "tools": ["evidence_search:refused:0.0s", "evidence_search:ok:0.0s"], "final": "In the base model, **d_model = 512**. The paper says:  > \"To facilitate these residual connections, all sub-
- d notes 0 {"ttft": 3.569864375051111, "intent": 0.46594225004082546, "total": 3.972194041009061, "turns": 2, "tools": ["note_append:ok:0.2s"], "final": "Done \u2014 \"latency test, buy oat milk\" is saved in your notes (verified)."}
- d notes 1 {"ttft": 3.4130085420329124, "intent": 0.4684397089877166, "total": 3.814260874991305, "turns": 2, "tools": ["note_append:ok:0.5s"], "final": "Done \u2014 \"latency test, buy oat milk.\" is saved in your notes (Reach Notes.md), verified."}
- e screen #0: total 10.3s, first token 10.0s, turns 2, tools see_region:ok:5.1s, status ok
- e screen #1: total 10.3s, first token 9.9s, turns 2, tools see_region:ok:5.2s, status ok
- e screen #2: total 9.1s, first token 8.8s, turns 2, tools see_region:ok:3.5s, status ok
- f say ready #0: total 2.0s, first token 1.6s, turns 1, tools -, status ok
- f say ready #1: total 1.8s, first token 1.7s, turns 1, tools -, status ok
- f say ready #2: total 1.3s, first token 1.1s, turns 1, tools -, status ok
