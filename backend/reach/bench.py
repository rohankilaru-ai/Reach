"""`reach bench`: the command router on Drex Max vs the same task on Qwen3.8-27B (DeepInfra), same labels and abstain rule.

Per-item results are appended to a JSONL file as they arrive, so a crash never forces a second held-out run. The
summary (accuracy per split, abstention, wrong-command rate, latency percentiles, tokens, est cost) is written to
docs/evidence/bench.json and bench.md.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from . import commands as c
from .paths import REPO_ROOT

FIXTURES = REPO_ROOT / "fixtures" / "commands" / "utterances.jsonl"
EVIDENCE = REPO_ROOT / "docs" / "evidence"

QWEN_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
QWEN_MODEL = "Qwen/Qwen3.8-27B"
# DeepInfra /models/list for Qwen/Qwen3.8-27B: input 2e-05 cents/token, output 2.5e-04 cents/token (list price).
QWEN_USD_PER_INPUT_TOKEN = 2e-05 / 100  # $0.20 per million
QWEN_USD_PER_OUTPUT_TOKEN = 2.5e-04 / 100  # $2.50 per million

QWEN_SYSTEM = (
    "You route what a user said, typed or mouthed to a screen assistant to one command. Typos, missing letters and "
    "sound-alike words are expected, especially in lip-reading hypotheses. Labels:\n"
    + "\n".join(f"- {k}: {v}" for k, v in {**c.COMMANDS, c.UNCERTAIN: c.UNCERTAIN_DESC}.items())
    + "\nReturn only JSON: {\"probabilities\": {<every label>: <probability 0..1>}}, summing to 1."
)


def qwen_cost(tin: int, tout: int) -> float:
    return tin * QWEN_USD_PER_INPUT_TOKEN + tout * QWEN_USD_PER_OUTPUT_TOKEN


def parse_qwen_content(content: str) -> dict[str, float]:
    """Probabilities from Qwen's reply; tolerant of think blocks and code fences. Empty dict if unparseable."""
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    m = re.search(r"\{.*\}", content, flags=re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    probs = data.get("probabilities", data) if isinstance(data, dict) else {}
    if not isinstance(probs, dict):
        return {}
    return {k: float(v) for k, v in probs.items() if k in c.LABELS and isinstance(v, (int, float))}


class QwenRouter:
    """The same task as one chat completion with thinking disabled and JSON output. Probabilities are self-reported."""

    name = "qwen"

    def __init__(self, post: c.Post = c.http_post, model: str = QWEN_MODEL) -> None:
        self.post = post
        self.model = model

    def body(self, state: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": QWEN_SYSTEM}, {"role": "user", "content": state}],
            "max_tokens": 300,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }

    def classify(self, state: str) -> c.RouteResult:
        c.load_keys(("DEEPINFRA_API_KEY",))
        headers = {"Authorization": f"Bearer {os.environ.get('DEEPINFRA_API_KEY', '')}"}
        t0 = time.monotonic()
        try:
            resp = self.post(QWEN_URL, headers, self.body(state), c.TIMEOUT_S)
        except Exception as e:
            return c.RouteResult(c.UNCERTAIN, "error", latency_ms=int((time.monotonic() - t0) * 1000), error=str(e)[:200])
        latency = int((time.monotonic() - t0) * 1000)
        usage = resp.get("usage") or {}
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        probs = parse_qwen_content(content)
        if not probs:
            return c.RouteResult(c.UNCERTAIN, "error", latency_ms=latency, error="unparseable reply",
                                 input_tokens=tin, output_tokens=tout, est_cost_usd=qwen_cost(tin, tout),
                                 model=resp.get("model"))
        label, reason = c.decide(probs)
        top = max(probs, key=probs.get)
        return c.RouteResult(label, reason, probabilities=probs, model_choice=top, latency_ms=latency,
                             input_tokens=tin, output_tokens=tout, est_cost_usd=round(qwen_cost(tin, tout), 8),
                             model=resp.get("model") or self.model)


def load_fixtures(path: Path = FIXTURES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def percentile(xs: list[float], q: float) -> float | None:
    """Nearest-rank percentile."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics over per-item rows {gold, pred, error, latency_ms, input_tokens, output_tokens, est_cost_usd}.

    correct: pred == gold. abstained: pred is uncertain. wrong_command: pred is a command and differs from gold
    (gold-uncertain items included). Errors count in N, are routed to uncertain, and are also reported apart."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    correct = sum(r["pred"] == r["gold"] for r in rows)
    abstained = sum(r["pred"] == c.UNCERTAIN for r in rows)
    wrong = sum(r["pred"] != c.UNCERTAIN and r["pred"] != r["gold"] for r in rows)
    errors = sum(bool(r.get("error")) for r in rows)
    cmd_rows = [r for r in rows if r["gold"] != c.UNCERTAIN]
    unc_rows = [r for r in rows if r["gold"] == c.UNCERTAIN]
    lat = [r["latency_ms"] for r in rows if not r.get("error")]
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "abstention_rate": round(abstained / n, 4),
        "wrong_command": wrong,
        "wrong_command_rate": round(wrong / n, 4),
        "command_items": len(cmd_rows),
        "command_recall": round(sum(r["pred"] == r["gold"] for r in cmd_rows) / len(cmd_rows), 4) if cmd_rows else None,
        "uncertain_items": len(unc_rows),
        "uncertain_caught": sum(r["pred"] == c.UNCERTAIN for r in unc_rows),
        "errors": errors,
        "latency_p50_ms": percentile(lat, 50),
        "latency_p95_ms": percentile(lat, 95),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "est_cost_usd": round(sum(float(r.get("est_cost_usd") or 0) for r in rows), 6),
    }


def run(routers: list[Any], items: list[dict[str, Any]], out_jsonl: Path, log_drex: bool = True) -> list[dict[str, Any]]:
    """Every item through every router, sequentially (same HTTP path), appended to out_jsonl as it arrives."""
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out_jsonl.open("a") as f:
        for it in items:
            state = c.state_for(it.get("text"), it.get("hypotheses"))
            for r in routers:
                res = r.classify(state)
                if log_drex and r.name == "drex":
                    c.log_route(res, state, task_id="bench")
                row = {"system": r.name, "id": it["id"], "split": it["split"], "source": it["source"],
                       "gold": it["gold"], "pred": res.command, **{k: v for k, v in res.to_dict().items() if k != "command"}}
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
                print(f"{r.name:<5} {it['id']} gold={it['gold']:<14} pred={res.command:<14} "
                      f"{'OK ' if res.command == it['gold'] else 'X  '} {res.latency_ms}ms {res.error or ''}", flush=True)
    return rows


def report(rows: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    systems = sorted({r["system"] for r in rows})
    splits = sorted({r["split"] for r in rows})
    out: dict[str, Any] = {"meta": meta, "results": {}}
    for s in systems:
        out["results"][s] = {sp: summarize([r for r in rows if r["system"] == s and r["split"] == sp]) for sp in splits}
        out["results"][s]["all"] = summarize([r for r in rows if r["system"] == s])
        # per-input-source accuracy on held_out (typed / dictated / lip)
        ho = [r for r in rows if r["system"] == s and r["split"] == "held_out"]
        out["results"][s]["held_out_by_source"] = {
            src: summarize([r for r in ho if r["source"] == src]) for src in sorted({r["source"] for r in ho})
        }
    out["items"] = rows
    return out


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.1f}%" if pct else str(v)


def render_md(rep: dict[str, Any], notes: str = "") -> str:
    meta = rep["meta"]
    lines = [
        "# Command router benchmark: Drex Max vs Qwen3.8-27B",
        "",
        f"Run {meta['date']}. Fixtures: `fixtures/commands/utterances.jsonl` (60 hand-written utterances, 8 gold "
        f"`uncertain`). Abstain rule for both: `uncertain` if the top label is `uncertain`, top p < {meta['threshold']}, "
        f"or top minus runner-up < {meta['margin']}. Experimental (S8a).",
        "",
        "| system | split | N | accuracy | abstain | wrong command | uncertain caught | errors | p50 ms | p95 ms | in tok | out tok | est $ |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s, by in rep["results"].items():
        for sp in ("held_out", "dev", "all"):
            m = by.get(sp)
            if not m or not m.get("n"):
                continue
            lines.append(
                f"| {s} | {sp} | {m['n']} | {_fmt(m['accuracy'], True)} | {_fmt(m['abstention_rate'], True)} | "
                f"{m['wrong_command']} ({_fmt(m['wrong_command_rate'], True)}) | {m['uncertain_caught']}/{m['uncertain_items']} | "
                f"{m['errors']} | {_fmt(m['latency_p50_ms'])} | {_fmt(m['latency_p95_ms'])} | {m['input_tokens']} | "
                f"{m['output_tokens']} | {m['est_cost_usd']:.5f} |"
            )
    lines += ["", "Held-out accuracy by input source:", "", "| system | source | N | accuracy | wrong command |", "|---|---|---|---|---|"]
    for s, by in rep["results"].items():
        for src, m in by.get("held_out_by_source", {}).items():
            lines.append(f"| {s} | {src} | {m['n']} | {_fmt(m['accuracy'], True)} | {m['wrong_command']} |")
    misses = [r for r in rep["items"] if r["pred"] != r["gold"]]
    if misses:
        lines += ["", "Misses (all splits):", "", "| system | id | split | gold | pred | reason |", "|---|---|---|---|---|---|"]
        for r in misses:
            lines.append(f"| {r['system']} | {r['id']} | {r['split']} | {r['gold']} | {r['pred']} | {r.get('error') or r['reason']} |")
    if notes:
        lines += ["", notes.strip()]
    return "\n".join(lines) + "\n"


CAVEATS = """## Caveats

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
"""


def main_bench(split: str = "all", systems: tuple[str, ...] = ("drex", "qwen"), write_docs: bool = True,
               out_dir: Path | None = None) -> dict[str, Any]:
    items = [it for it in load_fixtures() if split == "all" or it["split"] == split]
    routers = [r for r in (c.DrexRouter(), QwenRouter()) if r.name in systems]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = out_dir or (REPO_ROOT / ".reach" / "bench")
    rows = run(routers, items, out_dir / f"bench-{stamp}-{split}.jsonl")
    meta = {"date": time.strftime("%Y-%m-%d %H:%M"), "split": split, "threshold": c.THRESHOLD, "margin": c.MARGIN,
            "drex_model": c.DREX_MODEL, "qwen_model": QWEN_MODEL, "fixtures": str(FIXTURES.relative_to(REPO_ROOT))}
    rep = report(rows, meta)
    if write_docs:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "bench.json").write_text(json.dumps(rep, indent=1) + "\n")
        (EVIDENCE / "bench.md").write_text(render_md(rep, CAVEATS))
    return rep
