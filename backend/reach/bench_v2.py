"""Router benchmark v2: a clean, harder held-out set (fixtures/commands/utterances_v2.jsonl).

Why v2: in v1 the label descriptions quoted held-out phrasings and the items were easy, so both models scored 40/40.
Here the label descriptions and instructions below were FROZEN in a commit before any v2 API call, are written in
words that share no content-word bigram with any v2 item (tests/test_bench_v2.py checks this), and are used only by
this benchmark: the production router (`reach route`) still uses the v1 descriptions in commands.py.

Three systems, same label set and the same abstain rule (commands.decide):
- drex: Drex Max `choice` question (probabilities from the choice head);
- qwen_probs: Qwen3.8-27B, thinking off, self-reported 12-way probability JSON (the v1 format);
- qwen_label: Qwen3.8-27B, thinking off, a one-label JSON reply. It has no probabilities, so only the
  "model chose uncertain" part of the abstain rule can apply; threshold and margin cannot.

Run:  uv run python -m reach.bench_v2 run --split dev|held_out
      uv run python -m reach.bench_v2 report <jsonl> [<jsonl> ...]   (renders docs from saved rows; no API calls)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import bench as b
from . import commands as c
from .paths import REPO_ROOT

FIXTURES_V2 = REPO_ROOT / "fixtures" / "commands" / "utterances_v2.jsonl"
EVIDENCE = REPO_ROOT / "docs" / "evidence"

# ---- FROZEN PROMPT (do not edit after the v2 dev/held-out runs; any change needs a v3 set) ----
V2_DESCRIPTIONS: dict[str, str] = {
    "explain_this": "Give an account of what the indicated item means, in simpler language.",
    "open_this": "Activate the indicated link, document or entry so that its contents are shown.",
    "scroll_down": "Shift the visible region toward the end of the document.",
    "scroll_up": "Shift the visible region toward the start of the document.",
    "pause": "Suspend the ongoing narration or playback temporarily, keeping its position for later.",
    "continue": "Restart narration or playback that was suspended, from where it was left.",
    "compare_these": "Set two or more indicated items side by side and report similarities and differences.",
    "put_this_here": "Relocate the held or picked-up item to the location the user indicates.",
    "cancel": "Withdraw the user's latest unfinished request, as though it had not been made; other activity carries on.",
    "next": "Advance to the following entry in a sequence: result, slide, section or step.",
    "stop": "Terminate all ongoing activity (speech, playback, actions) at once, with no intention of resuming.",
}
V2_UNCERTAIN_DESC = (
    "No single command fits: an unsupported request, filler, garbled input, a negation with no positive instruction, "
    "a request for two different commands, or input that fits several commands about equally."
)
V2_INSTRUCTIONS = (
    "The state comes from a user of a screen assistant: typed text, speech-to-text, or a ranked list of lip-reading "
    "guesses. Expect misspellings, dropped letters, homophones, filler words and words from other languages. Rules: "
    "if the user corrects themselves, the final intent counts. If they ask for two different commands, the answer is "
    "'uncertain'. A lip-reading list is ranked best first, but the first guess can be wrong; choose the command the "
    "list as a whole supports, and 'uncertain' if the guesses support different commands about equally. Choose "
    "'uncertain' rather than guess."
)
_LABEL_LINES = "\n".join(f"- {k}: {v}" for k, v in {**V2_DESCRIPTIONS, c.UNCERTAIN: V2_UNCERTAIN_DESC}.items())
QWEN_PROBS_SYSTEM = (
    V2_INSTRUCTIONS + "\nLabels:\n" + _LABEL_LINES
    + "\nReturn only JSON: {\"probabilities\": {<every label>: <probability 0..1>}}, summing to 1."
)
QWEN_LABEL_SYSTEM = V2_INSTRUCTIONS + "\nLabels:\n" + _LABEL_LINES + "\nReturn only JSON: {\"label\": <one label>}."
# ---- END FROZEN PROMPT ----


def prompt_hash() -> str:
    blob = json.dumps([V2_DESCRIPTIONS, V2_UNCERTAIN_DESC, V2_INSTRUCTIONS, QWEN_PROBS_SYSTEM, QWEN_LABEL_SYSTEM])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class DrexV2(c.DrexRouter):
    name = "drex"

    def body(self, state: str) -> dict[str, Any]:
        criteria = {**V2_DESCRIPTIONS, c.UNCERTAIN: V2_UNCERTAIN_DESC}
        return {"model": self.model, "state": state,
                "questions": {"command": {"type": "choice", "instructions": V2_INSTRUCTIONS, "criteria": criteria}}}


class QwenProbsV2(b.QwenRouter):
    name = "qwen_probs"

    def body(self, state: str) -> dict[str, Any]:
        body = super().body(state)
        body["messages"][0]["content"] = QWEN_PROBS_SYSTEM
        return body


def parse_label(content: str) -> str | None:
    """The label from a one-label reply, or None if missing / not in the label set."""
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    m = re.search(r"\{.*\}", content, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    label = data.get("label") if isinstance(data, dict) else None
    return label if label in c.LABELS else None


class QwenLabelV2(b.QwenRouter):
    """One-label output. Only the `model chose uncertain` part of the abstain rule applies (no probabilities)."""

    name = "qwen_label"

    def body(self, state: str) -> dict[str, Any]:
        body = super().body(state)
        body["messages"][0]["content"] = QWEN_LABEL_SYSTEM
        body["max_tokens"] = 30
        return body

    def classify(self, state: str) -> c.RouteResult:
        c.load_keys(("DEEPINFRA_API_KEY",))
        headers = {"Authorization": f"Bearer {os.environ.get('DEEPINFRA_API_KEY', '')}"}
        t0 = time.monotonic()
        try:
            resp = self.post(b.QWEN_URL, headers, self.body(state), c.TIMEOUT_S)
        except Exception as e:
            return c.RouteResult(c.UNCERTAIN, "error", latency_ms=int((time.monotonic() - t0) * 1000), error=str(e)[:200])
        latency = int((time.monotonic() - t0) * 1000)
        usage = resp.get("usage") or {}
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        label = parse_label(content)
        cost = round(b.qwen_cost(tin, tout), 8)
        if label is None:
            return c.RouteResult(c.UNCERTAIN, "error", latency_ms=latency, error="unparseable or off-label reply",
                                 input_tokens=tin, output_tokens=tout, est_cost_usd=cost, model=resp.get("model"))
        reason = "model chose uncertain" if label == c.UNCERTAIN else "ok"
        return c.RouteResult(label, reason, probabilities={label: 1.0}, model_choice=label, latency_ms=latency,
                             input_tokens=tin, output_tokens=tout, est_cost_usd=cost,
                             model=resp.get("model") or self.model)


# ---- metrics ----

def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def sign_test(a_only: int, b_only: int) -> float:
    """Exact two-sided sign test p-value on the discordant pairs (McNemar exact)."""
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def summarize_v2(rows: list[dict[str, Any]], priced: bool = True) -> dict[str, Any]:
    """Metrics over per-item rows {gold, pred, error, latency_ms, input_tokens, output_tokens, est_cost_usd}.

    wrong_command = pred is a command and != gold; split into `confusion` (gold is another command) and
    `acted_on_uncertain` (gold is uncertain). abstain_on_uncertain = share of gold-uncertain items answered
    uncertain (higher is better); over_abstention = share of gold-command items answered uncertain (lower is better)."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    U = c.UNCERTAIN
    correct = sum(r["pred"] == r["gold"] for r in rows)
    confusion = sum(r["pred"] != U and r["gold"] != U and r["pred"] != r["gold"] for r in rows)
    acted = sum(r["pred"] != U and r["gold"] == U for r in rows)
    unc = [r for r in rows if r["gold"] == U]
    cmd = [r for r in rows if r["gold"] != U]
    lat = [r["latency_ms"] for r in rows if not r.get("error")]
    lo, hi = wilson(correct, n)
    out = {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "accuracy_ci95": [round(lo, 4), round(hi, 4)],
        "wrong_command": confusion + acted,
        "wrong_command_rate": round((confusion + acted) / n, 4),
        "wrong_confusion": confusion,
        "wrong_acted_on_uncertain": acted,
        "uncertain_items": len(unc),
        "abstained_on_uncertain": sum(r["pred"] == U for r in unc),
        "abstain_on_uncertain_rate": round(sum(r["pred"] == U for r in unc) / len(unc), 4) if unc else None,
        "command_items": len(cmd),
        "abstained_on_command": sum(r["pred"] == U for r in cmd),
        "over_abstention_rate": round(sum(r["pred"] == U for r in cmd) / len(cmd), 4) if cmd else None,
        "errors": sum(bool(r.get("error")) for r in rows),
        "latency_p50_ms": b.percentile(lat, 50),
        "latency_p95_ms": b.percentile(lat, 95),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
    }
    out["est_cost_usd"] = round(sum(float(r.get("est_cost_usd") or 0) for r in rows), 6) if priced else None
    return out


def paired(rows: list[dict[str, Any]], a: str, bsys: str, split: str = "held_out") -> dict[str, Any]:
    ra = {r["id"]: r["pred"] == r["gold"] for r in rows if r["system"] == a and r["split"] == split}
    rb = {r["id"]: r["pred"] == r["gold"] for r in rows if r["system"] == bsys and r["split"] == split}
    ids = sorted(set(ra) & set(rb))
    a_only = sum(ra[i] and not rb[i] for i in ids)
    b_only = sum(rb[i] and not ra[i] for i in ids)
    return {"a": a, "b": bsys, "n": len(ids), "a_right_b_wrong": a_only, "b_right_a_wrong": b_only,
            "sign_test_p": round(sign_test(a_only, b_only), 4)}


SYSTEMS = ("drex", "qwen_probs", "qwen_label")


def report_v2(rows: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"meta": meta, "results": {}, "paired_held_out": []}
    systems = [s for s in SYSTEMS if any(r["system"] == s for r in rows)]
    for s in systems:
        priced = s != "drex"
        mine = [r for r in rows if r["system"] == s]
        out["results"][s] = {sp: summarize_v2([r for r in mine if r["split"] == sp], priced) for sp in ("dev", "held_out")}
        ho = [r for r in mine if r["split"] == "held_out"]
        out["results"][s]["held_out_by_source"] = {
            src: summarize_v2([r for r in ho if r["source"] == src], priced) for src in sorted({r["source"] for r in ho})}
        out["results"][s]["held_out_by_tag"] = {
            t: summarize_v2([r for r in ho if t in r.get("tags", [])], priced)
            for t in sorted({t for r in ho for t in r.get("tags", [])})}
    for i, a in enumerate(systems):
        for bsys in systems[i + 1:]:
            out["paired_held_out"].append(paired(rows, a, bsys))
    out["items"] = rows
    return out


def _p(v: Any) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def render_md_v2(rep: dict[str, Any], verdict: str = "") -> str:
    meta = rep["meta"]
    L = [
        "# Command router benchmark v2: Drex Max vs Qwen3.8-27B (clean, harder set)",
        "",
        f"Held-out run {meta.get('held_out_date', '-')}; dev run {meta.get('dev_date', '-')}. Fixtures: "
        f"`{meta['fixtures']}` ({meta['n_items']} hand-labelled items, {meta['n_uncertain']} gold `uncertain`; "
        f"split {meta['n_dev']} dev / {meta['n_held_out']} held-out). Prompt frozen in commit `{meta['prompt_commit']}` "
        f"(prompt hash `{meta['prompt_hash']}`) before any v2 API call; held-out ran once. Abstain rule: `uncertain` if "
        f"the top label is `uncertain`, top p < {meta['threshold']}, or top minus runner-up < {meta['margin']} "
        "(qwen_label: only the first clause applies). Experimental (S8a).",
        "",
        "## Held-out (N = 90)",
        "",
        "| system | accuracy | 95% Wilson | wrong command (confusion + acted on uncertain) | abstain on gold-uncertain | "
        "over-abstain on commands | errors | p50 ms | p95 ms | in tok | out tok | list $ |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s, by in rep["results"].items():
        m = by.get("held_out") or {}
        if not m.get("n"):
            continue
        lo, hi = m["accuracy_ci95"]
        cost = "tokens only" if m["est_cost_usd"] is None else f"{m['est_cost_usd']:.5f}"
        L.append(
            f"| {s} | {m['correct']}/{m['n']} ({_p(m['accuracy'])}) | {_p(lo)} to {_p(hi)} | {m['wrong_command']} "
            f"({_p(m['wrong_command_rate'])}): {m['wrong_confusion']} + {m['wrong_acted_on_uncertain']} | "
            f"{m['abstained_on_uncertain']}/{m['uncertain_items']} ({_p(m['abstain_on_uncertain_rate'])}) | "
            f"{m['abstained_on_command']}/{m['command_items']} ({_p(m['over_abstention_rate'])}) | {m['errors']} | "
            f"{m['latency_p50_ms']} | {m['latency_p95_ms']} | {m['input_tokens']} | {m['output_tokens']} | {cost} |")
    L += ["", "Paired on held-out (same items; exact sign test on items one got right and the other wrong):", "",
          "| A | B | A right, B wrong | B right, A wrong | p |", "|---|---|---|---|---|"]
    for p in rep["paired_held_out"]:
        L.append(f"| {p['a']} | {p['b']} | {p['a_right_b_wrong']} | {p['b_right_a_wrong']} | {p['sign_test_p']} |")
    L += ["", "Held-out accuracy by tag (an item can carry several tags):", "",
          "| tag | N | " + " | ".join(rep["results"]) + " |", "|---|---|" + "---|" * len(rep["results"])]
    tags = sorted({t for by in rep["results"].values() for t in by.get("held_out_by_tag", {})})
    for t in tags:
        cells, n = [], 0
        for by in rep["results"].values():
            m = by.get("held_out_by_tag", {}).get(t)
            n = m["n"] if m else n
            cells.append(f"{m['correct']}/{m['n']}" if m else "-")
        L.append(f"| {t} | {n} | " + " | ".join(cells) + " |")
    L += ["", "## Dev (N = 30, run once to catch bugs; nothing was tuned on it)", "",
          "| system | accuracy | wrong command | abstain on gold-uncertain | over-abstain | errors | p50 ms |",
          "|---|---|---|---|---|---|---|"]
    for s, by in rep["results"].items():
        m = by.get("dev") or {}
        if m.get("n"):
            L.append(f"| {s} | {m['correct']}/{m['n']} | {m['wrong_command']} | {m['abstained_on_uncertain']}/"
                     f"{m['uncertain_items']} | {m['abstained_on_command']}/{m['command_items']} | {m['errors']} | "
                     f"{m['latency_p50_ms']} |")
    misses = [r for r in rep["items"] if r["split"] == "held_out" and r["pred"] != r["gold"]]
    L += ["", "## Every held-out miss", "", "| system | id | input | gold | pred | kind | reason |", "|---|---|---|---|---|---|---|"]
    for r in sorted(misses, key=lambda r: (r["id"], r["system"])):
        kind = ("abstained" if r["pred"] == c.UNCERTAIN else
                "acted on uncertain" if r["gold"] == c.UNCERTAIN else "wrong command")
        inp = r.get("input", "").replace("|", "/")
        L.append(f"| {r['system']} | {r['id']} | {inp} | {r['gold']} | {r['pred']} | {kind} | "
                 f"{r.get('error') or r.get('reason')} |")
    if verdict:
        L += ["", verdict.strip()]
    return "\n".join(L) + "\n"


def load_v2(path: Path = FIXTURES_V2) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _input(it: dict[str, Any]) -> str:
    return " / ".join(it["hypotheses"]) if it.get("hypotheses") else it.get("text", "")


def run_v2(routers: list[Any], items: list[dict[str, Any]], out_jsonl: Path) -> list[dict[str, Any]]:
    """Every item through every router, interleaved per item, appended to out_jsonl as each answer arrives."""
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out_jsonl.open("a") as f:
        for it in items:
            state = c.state_for(it.get("text"), it.get("hypotheses"))
            for r in routers:
                res = r.classify(state)
                row = {"system": r.name, "id": it["id"], "split": it["split"], "source": it["source"],
                       "tags": it.get("tags", []), "input": _input(it), "gold": it["gold"], "pred": res.command,
                       **{k: v for k, v in res.to_dict().items() if k != "command"}}
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
                print(f"{r.name:<10} {it['id']} gold={it['gold']:<14} pred={res.command:<14} "
                      f"{'OK ' if res.command == it['gold'] else 'X  '} {res.latency_ms}ms {res.output_tokens}tok "
                      f"{res.error or ''}", flush=True)
    return rows


def main(argv: list[str]) -> int:
    if argv and argv[0] == "run":
        split = argv[argv.index("--split") + 1]
        items = [it for it in load_v2() if it["split"] == split]
        out = REPO_ROOT / ".reach" / "bench" / f"bench_v2-{time.strftime('%Y%m%d-%H%M%S')}-{split}.jsonl"
        rows = run_v2([DrexV2(), QwenProbsV2(), QwenLabelV2()], items, out)
        rep = report_v2(rows, {})
        for s, by in rep["results"].items():
            m = by[split]
            print(f"{s:<10} {split} {m['correct']}/{m['n']} wrong={m['wrong_command']} errors={m['errors']} "
                  f"p50={m['latency_p50_ms']} out_tok={m['output_tokens']} cost={m['est_cost_usd']}")
        print(out)
        return 0
    if argv and argv[0] == "report":
        rows = [json.loads(line) for p in argv[1:] for line in Path(p).read_text().splitlines() if line.strip()]
        items = load_v2()
        meta = json.loads((EVIDENCE / "bench_v2.meta.json").read_text()) if (EVIDENCE / "bench_v2.meta.json").exists() else {}
        meta.update({"fixtures": str(FIXTURES_V2.relative_to(REPO_ROOT)), "n_items": len(items),
                     "n_uncertain": sum(it["gold"] == c.UNCERTAIN for it in items),
                     "n_dev": sum(it["split"] == "dev" for it in items),
                     "n_held_out": sum(it["split"] == "held_out" for it in items),
                     "threshold": c.THRESHOLD, "margin": c.MARGIN, "prompt_hash": prompt_hash(),
                     "drex_model": c.DREX_MODEL, "qwen_model": b.QWEN_MODEL})
        rep = report_v2(rows, meta)
        verdict_path = EVIDENCE / "bench_v2_verdict.md"
        verdict = verdict_path.read_text() if verdict_path.exists() else ""
        (EVIDENCE / "bench_v2.json").write_text(json.dumps(rep, indent=1) + "\n")
        (EVIDENCE / "bench_v2.md").write_text(render_md_v2(rep, verdict))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
