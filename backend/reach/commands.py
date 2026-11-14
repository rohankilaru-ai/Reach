"""The 12-command router (S8a part 1, experimental).

A user utterance (typed text, macOS dictation, or a list of lip-reading hypotheses) is mapped to one of 11 commands
or `uncertain` with ONE Drex Max `choice` question. Drex returns a probability per label; the router abstains
(`uncertain`) when the top label is `uncertain`, its probability is below THRESHOLD, or it beats the runner-up by less
than MARGIN. It never guesses: executing the wrong command is worse than asking again.

Keys are loaded inside the process (see `load_keys`) and never printed. Every decision is appended to the Drex log
(`.reach/logs/drex.jsonl`) as a `decision` entry with purpose `command_route`, so `reach log` shows it.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .ledger import append_drex
from .paths import REPO_ROOT, drex_log

COMMANDS: dict[str, str] = {
    "explain_this": "Explain, define or summarise the selected or pointed-at thing ('what is this', 'break this down').",
    "open_this": "Open, launch or click into the pointed-at link, file or item.",
    "scroll_down": "Scroll or page down, move further down.",
    "scroll_up": "Scroll or page up, go back up.",
    "pause": "Pause or wait for a moment; resumable later.",
    "continue": "Resume, continue, keep going, carry on.",
    "compare_these": "Compare or contrast the selected items.",
    "put_this_here": "Move, drop or place the held item at the pointed location.",
    "cancel": "Cancel or undo the pending request; never mind, forget it.",
    "next": "Go to the next item, result, page or step.",
    "stop": "Stop everything that is running, stop talking, halt.",
}
UNCERTAIN = "uncertain"
UNCERTAIN_DESC = (
    "None of the commands above: unrelated requests, filler, single words that name no command, or garbled input "
    "that could be several commands."
)
LABELS: tuple[str, ...] = (*COMMANDS, UNCERTAIN)

# What each command does in Reach. The overlay and `reach ask --route` read this; nothing else executes it here.
ACTIONS: dict[str, tuple[str, str]] = {
    "explain_this": ("ask", "Explain the selected text."),
    "open_this": ("open", "Open the pointed-at item."),
    "scroll_down": ("scroll", "down"),
    "scroll_up": ("scroll", "up"),
    "pause": ("playback", "pause"),
    "continue": ("playback", "continue"),
    "compare_these": ("ask", "Compare the selected items."),
    "put_this_here": ("shelf", "place"),
    "cancel": ("abort", "cancel the pending request"),
    "next": ("navigate", "next"),
    "stop": ("abort", "stop everything"),
    UNCERTAIN: ("none", "did nothing: not sure which command was meant"),
}

# Tuned on the `dev` split only (fixtures/commands/utterances.jsonl); never on held_out.
THRESHOLD = 0.5
MARGIN = 0.15

DREX_URL = "https://console.nace.ai/v1/systemone"
DREX_MODEL = os.environ.get("REACH_ROUTER_MODEL", "drex-max-v1.1")
# Drex responses carry tokens, not a price. Docs list Drex Max input at $0.12 per million; output is not billed.
DREX_USD_PER_INPUT_TOKEN = 0.12e-6
TIMEOUT_S = 30

INSTRUCTIONS = (
    "The state is something a user said, typed or mouthed to a screen assistant. Which command did they mean? "
    "Typos, missing letters and sound-alike words are expected, especially in lip-reading hypotheses. Pick "
    "'uncertain' if it is not one of the commands."
)

Post = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def load_keys(names: tuple[str, ...] = ("NACE_API_KEY", "DEEPINFRA_API_KEY")) -> None:
    """Load the named keys into os.environ from a .env file, in-process, without printing anything.

    The file is $REACH_ENV_FILE if set, else the repo `.env`, else `.intern-home/.env`. Existing values win."""
    if all(os.environ.get(n) for n in names):
        return
    candidates = [os.environ.get("REACH_ENV_FILE"), REPO_ROOT / ".env", REPO_ROOT / ".intern-home" / ".env"]
    for c in candidates:
        if not c:
            continue
        try:
            lines = Path(c).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in names:
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return


def http_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST JSON, return JSON. Errors carry the status and a truncated body only, never the headers."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from None


def state_for(text: str | None = None, hypotheses: list[str] | None = None, selection: str | None = None) -> str:
    """The Drex state: what the user said, or the ranked lip-reading hypotheses."""
    if hypotheses:
        lines = [f"{i}. {h}" for i, h in enumerate(hypotheses, 1)]
        s = "Lip-reading hypotheses for one mouthed command, best first (often missing letters):\n" + "\n".join(lines)
    else:
        s = f"The user said: {text or ''}"
    if selection:
        s += f"\nSelected on screen: {selection[:300]}"
    return s


def _prob(v: Any) -> float | None:
    """A probability in [0, 1], or None for anything else (null, bool, text, NaN, inf, out of range)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and 0.0 <= f <= 1.0 else None


def decide(probs: dict[str, float], threshold: float = THRESHOLD, margin: float = MARGIN) -> tuple[str, str]:
    """The abstain rule. Returns (label, reason); label is `uncertain` unless one command clearly wins."""
    if any(_prob(v) is None for v in probs.values()):
        return UNCERTAIN, "invalid probabilities"
    clean = {k: float(v) for k, v in probs.items() if k in LABELS}
    if not clean:
        return UNCERTAIN, "no probabilities"
    ranked = sorted(clean.items(), key=lambda kv: kv[1], reverse=True)
    top, p = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top == UNCERTAIN:
        return UNCERTAIN, "model chose uncertain"
    if p < threshold:
        return UNCERTAIN, f"top p {p:.2f} < {threshold}"
    if p - second < margin:
        return UNCERTAIN, f"margin {p - second:.2f} < {margin}"
    return top, "ok"


@dataclass
class RouteResult:
    command: str
    reason: str
    probabilities: dict[str, float] = field(default_factory=dict)
    model_choice: str | None = None
    latency_ms: int = 0
    eval_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0
    error: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DrexRouter:
    """One Drex Max `choice` question per utterance."""

    name = "drex"

    def __init__(self, post: Post = http_post, model: str = DREX_MODEL) -> None:
        self.post = post
        self.model = model

    def body(self, state: str) -> dict[str, Any]:
        criteria = {**COMMANDS, UNCERTAIN: UNCERTAIN_DESC}
        return {
            "model": self.model,
            "state": state,
            "questions": {"command": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}},
        }

    def classify(self, state: str) -> RouteResult:
        load_keys(("NACE_API_KEY",))
        headers = {"Authorization": f"Bearer {os.environ.get('NACE_API_KEY', '')}", "X-Nace-Client": "reach/1"}
        t0 = time.monotonic()
        try:
            resp = self.post(DREX_URL, headers, self.body(state), TIMEOUT_S)
        except Exception as e:
            return RouteResult(UNCERTAIN, "error", latency_ms=int((time.monotonic() - t0) * 1000), error=str(e)[:200])
        latency = int((time.monotonic() - t0) * 1000)
        return parse_drex(resp, latency)


def parse_drex(resp: dict[str, Any], latency_ms: int) -> RouteResult:
    try:
        ans = (resp.get("answers") or {}).get("command") or {}
        raw = ans.get("probabilities") or {}
        usage = resp.get("usage") or {}
        tin = int(usage.get("input_tokens") or 0)
        tout = int(usage.get("output_tokens") or 0)
        if not isinstance(raw, dict):
            raise TypeError("probabilities is not an object")
    except (AttributeError, TypeError, ValueError) as e:
        return RouteResult(UNCERTAIN, "error", latency_ms=latency_ms, error=f"malformed response: {type(e).__name__}")
    probs = {str(k): _prob(v) for k, v in raw.items()}
    if any(v is None for v in probs.values()):
        return RouteResult(UNCERTAIN, "error", latency_ms=latency_ms, error="invalid probability in response",
                           input_tokens=tin, output_tokens=tout, model=resp.get("model"))
    if not probs:
        return RouteResult(UNCERTAIN, "error", latency_ms=latency_ms, error="no probabilities in response",
                           input_tokens=tin, model=resp.get("model"))
    label, reason = decide(probs)
    return RouteResult(
        label, reason, probabilities=probs, model_choice=ans.get("choice"), latency_ms=latency_ms,
        eval_ms=resp.get("evaluation_time_ms"), input_tokens=tin, output_tokens=tout,
        est_cost_usd=round(tin * DREX_USD_PER_INPUT_TOKEN, 8), model=resp.get("model"),
    )


def log_route(result: RouteResult, state: str, path: Path | None = None, task_id: str = "route") -> None:
    """Append the decision to the Drex log as a `decision` event with purpose `command_route`."""
    event = {
        "type": "decision",
        "purpose": "command_route",
        "timestamp": int(time.time() * 1000),
        "ok": result.error is None,
        "error": result.error,
        "model": result.model,
        "state": state[:300],
        "answers": {"command": {"choice": result.command, "model_choice": result.model_choice,
                                "probabilities": result.probabilities}},
        "reason": result.reason,
        "latency_ms": result.latency_ms,
        "evaluation_time_ms": result.eval_ms,
        "tokens": {"input": result.input_tokens, "output": result.output_tokens},
        "cost_usd": result.est_cost_usd,
    }
    append_drex(path or drex_log(), event, task_id)


def route(text: str | None = None, hypotheses: list[str] | None = None, selection: str | None = None,
          router: DrexRouter | None = None, log: bool = True) -> RouteResult:
    state = state_for(text, hypotheses, selection)
    result = (router or DrexRouter()).classify(state)
    if log:
        log_route(result, state)
    return result
