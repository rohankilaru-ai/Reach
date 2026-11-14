"""Cloud voice gate: TTS -> STT round trip on DeepInfra for a few sentences (no microphone).

    REACH_ENV_FILE=<path to .env> python scripts/cloud_voice_gate.py [tts_model ...]

Prints per-sentence TTS time to first audio, STT latency and the round-trip transcript. Keeps the WAVs under
.reach/audio/gate/ only with --keep (for the on-device comparison); otherwise deletes them.
"""

from __future__ import annotations

import re
import statistics
import sys
from pathlib import Path

from reach import speech_cloud as sc

SENTENCES = [
    "The meeting starts at 3:45 on Tuesday, room 212.",
    "Add 17 apples and 25 oranges, then divide by four.",
    "Open the quarterly report and highlight the second chart.",
]
VOICES = {"hexgrad/Kokoro-82M": "af_heart", "Qwen/Qwen3-TTS": None}


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower().replace(":", " "))


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = [[i + j if i * j == 0 else 0 for j in range(len(h) + 1)] for i in range(len(r) + 1)]
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r)][len(h)] / max(1, len(r))


def main() -> None:
    keep = "--keep" in sys.argv
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or list(VOICES)
    for model in models:
        firsts, stts = [], []
        for i, s in enumerate(SENTENCES):
            t = sc.synthesize(s, voice=VOICES.get(model), model=model)
            p = Path(t["path"])
            r = sc.transcribe(p)
            firsts.append(t["first_ms"]); stts.append(r["ms"])
            print(f"{model} #{i+1} tts first {t['first_ms']} ms total {t['ms']} ms | stt {r['ms']} ms | "
                  f"WER {wer(s, r['text']):.2f} | heard: {r['text']}")
            if keep:
                dest = sc.audio_dir() / "gate" / f"{model.split('/')[-1]}-{i+1}.wav"
                dest.parent.mkdir(exist_ok=True)
                p.replace(dest)
            else:
                p.unlink(missing_ok=True)
        print(f"{model}: TTS first-audio p50 {statistics.median(firsts):.0f} ms, STT p50 {statistics.median(stts):.0f} ms")


if __name__ == "__main__":
    main()
