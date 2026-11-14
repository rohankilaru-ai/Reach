"""Measure the addressed check on fixtures/addressed/utterances.jsonl: `python -m reach.addressed_eval dev|held_out`.

Sequential real Drex calls (honest latency), each bounded by addressed.TIMEOUT_S like the server. Writes
docs/evidence/addressed_<split>.json. Tune on dev only.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys

from . import addressed
from .paths import REPO_ROOT


def run(split: str) -> dict:
    rows = [json.loads(x) for x in (REPO_ROOT / "fixtures/addressed/utterances.jsonl").read_text().splitlines() if x]
    rows = [r for r in rows if r["split"] == split]
    out = []
    for r in rows:
        res = asyncio.run(addressed.check_async(r["text"]))
        out.append({**r, "pred": res.addressed, "p": res.p_addressed, "wake": res.wake_matched,
                    "command": res.command, "latency_ms": res.latency_ms, "error": res.error,
                    "tokens": res.input_tokens})
    tp = sum(o["pred"] and o["gold_addressed"] for o in out)
    fp = sum(o["pred"] and not o["gold_addressed"] for o in out)
    fn = sum(not o["pred"] and o["gold_addressed"] for o in out)
    neg = sum(not o["gold_addressed"] for o in out)
    lat = sorted(o["latency_ms"] for o in out)
    summary = {
        "split": split, "n": len(out), "tp": tp, "fp": fp, "fn": fn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "false_trigger_rate": round(fp / neg, 3) if neg else None,
        "errors": sum(bool(o["error"]) for o in out),
        "p50_ms": statistics.median(lat) if lat else None, "p90_ms": lat[int(0.9 * (len(lat) - 1))] if lat else None,
        "input_tokens": sum(o["tokens"] for o in out),
        "by_group": {g: {"n": sum(o["group"] == g for o in out),
                         "correct": sum(o["group"] == g and o["pred"] == o["gold_addressed"] for o in out)}
                     for g in ("command", "side", "hard")},
    }
    (REPO_ROOT / f"docs/evidence/addressed_{split}.json").write_text(json.dumps({"summary": summary, "rows": out},
                                                                                indent=1))
    return summary


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else "dev"), indent=1))
