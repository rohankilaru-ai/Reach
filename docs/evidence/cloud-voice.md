# Cloud voice evidence (2026-11-14)

Endpoints that worked (DeepInfra, OpenAI-compatible, key loaded in the backend process only):
- STT: `POST https://api.deepinfra.com/v1/openai/audio/transcriptions`, multipart `file` (WAV) + `model=openai/whisper-large-v3-turbo` + `response_format=verbose_json` (returns text, language, duration, segments; `json` returns text only).
- TTS: `POST https://api.deepinfra.com/v1/openai/audio/speech`, JSON `{model, input, voice, response_format: "wav"}`; returns `audio/wav`. Kokoro-82M takes `voice` (`af_heart`); Qwen3-TTS returned HTTP 500 when sent `af_heart`, and worked with no voice.

Gate: `backend/scripts/cloud_voice_gate.py` (TTS -> STT loop, 3 sentences with numbers), then the Swift
`--cloud-voice-selftest` through the real backend `stt`/`tts` messages (3 sentences, Kokoro, on-device comparison
on the same audio). Times are wall clock from this Mac.

## TTS: time to first audio byte (ms)
| Model | run 1 (3 sentences) | p50 | run 2 | p50 |
| --- | --- | --- | --- | --- |
| hexgrad/Kokoro-82M (af_heart) | 1972 (cold), 877, 1020 | 1020 | 1123, 3102, 3720 | 3102 |
| Qwen/Qwen3-TTS (default voice) | 4409, 4319, 2932 | 4319 | | |

Swift selftest (Kokoro, full server time): 5284, 5009, 1041 ms. Kokoro is the default: faster in every paired run,
but its latency on DeepInfra is very uneven (0.9 to 5.3 s). With the 3 s fallback, a good share of replies will be
spoken on-device today. Responses are a whole WAV (the endpoint did not stream audio progressively: first byte to last
byte is ~100-200 ms), so splitting into sentences would be the next latency win; not built.

## STT (Whisper large-v3-turbo) vs Apple on-device, same generated audio
| Sentence | Whisper (ms) | Whisper text | Apple on-device (ms) | Apple text |
| --- | --- | --- | --- | --- |
| The meeting starts at 3:45 on Tuesday, room 212. | 1275 | The meeting starts at 3:00. 45 on Tuesday, room 212. | 380 | The meeting starts at 3:45 on Tuesday room 212 |
| Add 17 apples and 25 oranges, then divide by four. | 1555 | Add 17 apples and 25 oranges, then divide by 4. | 194 | Add 17 apples and 25 oranges then divide by four |
| Open the quarterly report and highlight the second chart. | 1515 | Open the quarterly report and highlight the second chart. | 183 | Open the quarterly report and highlight the second chart |

Python gate STT p50: 639 ms (run 1), 821 ms (run 2); selftest round trip p50 1518 ms.
Informal word error: Whisper 1 error in 3 sentences ("3:45" heard as "3:00. 45"; Kokoro reads "3:45" with a pause,
and Qwen3-TTS audio was transcribed as "3:45" correctly), plus "17" -> "7" once on Qwen audio. Apple on-device: no
word errors, with punctuation missing.

Honest verdict: on clean synthetic speech Apple on-device was as accurate as Whisper (better on "3:45") and 4-8x
faster (~0.2-0.4 s vs ~0.6-1.6 s). This test does not show Whisper's expected advantage (real mics, accents, noise,
domain words); that needs a by-hand pass with a real voice. The 2.5 s wait keeps Apple's text whenever Whisper is late.

Cost of this slice's probes and gates: well under $0.01 (about 20 short TTS calls and 20 STT calls).
