"""Pure functions over Intern event dicts: the Drex log and the receipt. Shared by the bridge, replay and tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .contracts import LEDGER_TYPES, Receipt


def append_drex(path: Path, event: dict[str, Any], task_id: str) -> bool:
    """Append a decision / nudge / tool_guard event to the Drex log. Returns True if written."""
    if event.get("type") not in LEDGER_TYPES:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"task_id": task_id, **event}) + "\n")
    return True


def append_ndi(path: Path, task_id: str, op: str, **fields: Any) -> None:
    """Log one NDI call (parse / ground) next to the Drex decisions. Timestamp in ms like Intern events."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"task_id": task_id, "type": "ndi", "op": op, "timestamp": int(time.time() * 1000), **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def count_ndi(path: Path, task_id: str | None = None) -> int:
    """NDI calls in the log, for one task or all."""
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "ndi" and (task_id is None or e.get("task_id") == task_id):
            n += 1
    return n


def _answer(answers: dict[str, Any] | None) -> tuple[str, float | None]:
    """The compact answer of a decision: 'q=choice' (or 'q=noul') and its probability."""
    if not answers:
        return "-", None
    parts, prob = [], None
    for q, a in answers.items():
        if "choice" in a:
            p = (a.get("probabilities") or {}).get(a["choice"])
            parts.append(f"{q}={a['choice']}")
        elif "noul" in a:
            p = a["noul"]
            parts.append(f"{q}=yes" if p >= 0.5 else f"{q}=no")
        else:
            p = None
            parts.append(f"{q}={json.dumps(a, separators=(',', ':'))[:40]}")
        if prob is None and isinstance(p, (int, float)):
            prob = float(p)
    return " ".join(parts), prob


def format_log_line(entry: dict[str, Any]) -> str:
    """One line per ledger entry: time, kind, purpose, answer, probability, cost."""
    ts = time.strftime("%H:%M:%S", time.localtime(entry.get("timestamp", 0) / 1000))
    kind = entry.get("type")
    if kind == "decision":
        answer, prob = _answer(entry.get("answers"))
        if not entry.get("ok", True):
            answer = f"FAILED {entry.get('error') or ''}".strip()
        p = f"p={prob:.2f}" if prob is not None else "p=-"
        cached = " cached" if entry.get("cached") else ""
        return f"{ts} decision  {entry.get('purpose', '?'):<14} {answer:<32} {p:<7} ${entry.get('cost_usd', 0):.4f}{cached}"
    if kind == "tool_guard":
        risk = entry.get("risk")
        p = f"p={risk:.2f}" if isinstance(risk, (int, float)) else "p=-"
        return f"{ts} guard     {entry.get('name', '?'):<14} refused:{entry.get('rule', '')[:24]:<23} {p:<7} $0.0000"
    if kind == "nudge":
        return f"{ts} nudge     {entry.get('reason', '?'):<14} {entry.get('text', '')[:32]:<32} p=-     $0.0000"
    if kind == "ndi":
        ok = "FAILED " + str(entry.get("error")) if entry.get("ok") is False else "ok"
        if entry.get("op") == "parse":
            what = f"{entry.get('file', '?')[:20]} {entry.get('pages', '?')}p"
        else:
            what = f"found {entry.get('found', '-')}/{entry.get('targets', '?')}"
        return f"{ts} ndi       {entry.get('op', '?'):<14} {what:<32} {ok:<7} units={entry.get('units', '-')}"
    return f"{ts} {kind}"


class ReceiptBuilder:
    """Folds a task's event stream into its receipt."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.started = time.monotonic()
        self.tools: list[str] = []
        self.refused: list[str] = []
        self.decisions = 0
        self.system1_cost = 0.0
        self.run_end: dict[str, Any] | None = None
        self.text: list[str] = []

    def feed(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_call_end":
            (self.refused if event.get("status") == "refused" else self.tools).append(event.get("name", "?"))
        # tool_guard is not counted: the refused call also ends with tool_call_end(status=refused)
        elif kind == "decision":
            self.decisions += 1
            self.system1_cost += float(event.get("cost_usd") or 0)
        elif kind == "text_delta":
            self.text.append(event.get("text", ""))
        elif kind == "run_end":
            self.run_end = event

    def receipt(self, *, aborted: bool = False, error: str | None = None) -> Receipt:
        end = self.run_end or {}
        usage = end.get("usage") or {}
        reason = end.get("reason") or ("aborted" if aborted else (f"error: {error}" if error else "no_run_end"))
        status = "ok" if reason == "completed" else ("aborted" if reason == "aborted" else "failed")
        duration = end.get("duration_ms")
        if duration is None:
            duration = int((time.monotonic() - self.started) * 1000)
        return Receipt(
            task_id=self.task_id,
            status=status,
            reason=reason,
            tools=self.tools,
            refused=self.refused,
            system1_asks=self.decisions,
            tokens=int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
            est_cost_usd=round(float(usage.get("cost_usd", 0.0)) + self.system1_cost, 6),
            duration_ms=int(duration),
            final_text=end.get("final_text") or "".join(self.text),
        )
