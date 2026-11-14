"""Evaluate Drex task understanding on fixtures/intent/requests.jsonl.

`run`: one real Drex request per item (production timeout), raw probabilities saved to docs/evidence/intent_raw.json.
`score`: offline, from the saved raw file, with the current THRESHOLDS; prints dev and held_out separately.
Errors and timeouts are counted on their own, never scored as `other`.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from typing import Any

from . import intent
from .commands import http_post
from .paths import REPO_ROOT

FIXTURE = REPO_ROOT / "fixtures" / "intent" / "requests.jsonl"
RAW = REPO_ROOT / "docs" / "evidence" / "intent_raw.json"


def items() -> list[dict[str, Any]]:
    return [json.loads(x) for x in FIXTURE.read_text().splitlines() if x.strip()]


def run() -> None:
    out = []
    for it in items():
        raw: dict[str, Any] = {}

        def post(u, h, b, t, raw=raw):
            r = http_post(u, h, b, t)
            raw.update(r)
            return r

        t0 = time.monotonic()
        res = intent.understand(it["text"], it.get("selection"), bool(it.get("docs")), bool(it.get("image")), post=post)
        out.append({"id": it["id"], "latency_ms": int((time.monotonic() - t0) * 1000), "error": res.error,
                    "answers": raw.get("answers"), "usage": raw.get("usage")})
        print(f"{it['id']:>2} {res.kind:<18} {res.action:<15} {res.latency_ms} ms {res.error or ''}", flush=True)
    RAW.write_text(json.dumps(out, indent=1))


def score(split: str) -> dict[str, Any]:
    raw = {r["id"]: r for r in json.loads(RAW.read_text())}
    rows = [it for it in items() if it["split"] == split]
    n = ok = errors = 0
    tp = {"risky": 0, "needs_screen_region": 0}
    fp = dict(tp)
    fn = dict(tp)
    misses, lat = [], []
    amb = [0, 0]
    for it in rows:
        r = raw.get(it["id"])
        if not r or r.get("error") or not r.get("answers"):
            errors += 1
            continue
        lat.append(r["latency_ms"])
        has_sel = bool(it.get("selection") or it.get("image"))
        res = intent.parse({"answers": r["answers"], "usage": r.get("usage")}, r["latency_ms"], has_sel)
        n += 1
        hit = res.kind == it["kind"]
        ok += hit
        if it.get("ambiguous"):
            amb[0] += hit
            amb[1] += 1
        pred = {"risky": res.flag("risky_or_irreversible") >= intent.THRESHOLDS["risky_or_irreversible"],
                "needs_screen_region": res.action == "needs_selection"}
        for k in tp:
            if pred[k] and it[k]:
                tp[k] += 1
            elif pred[k]:
                fp[k] += 1
            elif it[k]:
                fn[k] += 1
        if not hit or any(pred[k] != it[k] for k in tp):
            misses.append({"id": it["id"], "text": it["text"], "label": it["kind"], "got": res.kind,
                           "p": round(res.probs.get(res.kind, 0), 2), "risky": [it["risky"], pred["risky"]],
                           "region": [it["needs_screen_region"], pred["needs_screen_region"]]})

    def pr(k):
        p = tp[k] / (tp[k] + fp[k]) if tp[k] + fp[k] else None
        rc = tp[k] / (tp[k] + fn[k]) if tp[k] + fn[k] else None
        return {"precision": p, "recall": rc, "tp": tp[k], "fp": fp[k], "fn": fn[k]}

    lat.sort()
    return {"split": split, "n": n, "errors": errors, "kind_acc": ok / n if n else None, "kind_ok": ok,
            "ambiguous_acc": f"{amb[0]}/{amb[1]}", "risky": pr("risky"), "needs_screen_region": pr("needs_screen_region"),
            "p50_ms": statistics.median(lat) if lat else None, "p95_ms": lat[int(0.95 * (len(lat) - 1))] if lat else None,
            "misses": misses}


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if argv and argv[0] == "run":
        run()
    for s in ("dev", "held_out"):
        print(json.dumps(score(s), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
