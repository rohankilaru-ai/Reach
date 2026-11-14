"""Speak without a key: is a spoken utterance addressed to the assistant? (Drex Max, one request.)

A client that listens all the time sends `utterance {text, source, wake_matched?}`. ONE Drex Max request asks two
`noul` questions (`addressed_to_assistant`, `is_command_or_question`) plus the 12-command `choice` from commands.py.
The server replies `addressed {text, p_addressed, addressed, command?, reason}` to that client only. It never runs an
agent task and never acts on its own; the client decides what to do with `addressed: true`.

The decision rule lives in `decide` and nowhere else:
  addressed = (wake word at the start AND p >= WAKE_BAR) OR p >= BAR
Unsure, an error, a missing key or a timeout (TIMEOUT_S, total) => not addressed. Never act on ambiguous speech.
Every check is logged to the Drex log as a `decision` with purpose `addressed`. Contract: docs/evidence/addressed.md.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import commands
from .commands import DREX_URL, DREX_USD_PER_INPUT_TOKEN, http_post, load_keys
from .ledger import append_drex
from .paths import drex_log

MODEL = os.environ.get("REACH_ADDRESSED_MODEL", "drex-max-v1.1")
PURPOSE = "addressed"
TIMEOUT_S = 1.2
BAR = 0.85  # no wake word: Drex must be sure
WAKE_BAR = 0.5  # wake word at the start: a lower bar, so "Reach" inside a sentence about the product does not fire
COMMAND_BAR = 0.5  # is_command_or_question at or above this before a routed command is surfaced

WAKE_RE = re.compile(r"^\s*(?:hey|hi|ok|okay)?[\s,]*reach\b[\s,.!?:;-]*", re.I)

INSTRUCTIONS = (
    "The state is one utterance picked up by an always-on microphone on a desktop computer that runs a voice "
    "assistant called Reach. The microphone also hears the user talking to other people, thinking aloud, reading "
    "text aloud, meetings, lectures, videos and TV. Judge only from the words; when unsure, answer no."
)
Q_ADDRESSED = (
    INSTRUCTIONS + " Is the speaker talking TO the AI assistant on this computer, asking it for help or giving it "
    "a command, as opposed to talking to another person, thinking aloud, reading text aloud, or audio from a TV, "
    "video, lecture or meeting? The word 'reach' can also be an ordinary verb ('reach over', 'reach out')."
)
Q_COMMAND = INSTRUCTIONS + " Is the utterance a command or a question (rather than a statement or remark)?"


def wake_matched(text: str) -> bool:
    """The wake phrase ('Reach' or 'Hey Reach') at the very start of the utterance."""
    return bool(WAKE_RE.match(text or ""))


@dataclass
class Result:
    text: str
    addressed: bool = False
    p_addressed: float | None = None
    p_command: float | None = None
    wake_matched: bool = False
    command: str | None = None
    command_probs: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(p: float | None, wake: bool, error: str | None = None) -> tuple[bool, str]:
    """THE rule. Returns (addressed, reason)."""
    if error:
        return False, f"not addressed: {error}"
    if p is None:
        return False, "not addressed: no probability"
    if p >= BAR:
        return True, f"p_addressed {p:.2f} >= {BAR}"
    if wake and p >= WAKE_BAR:
        return True, f"wake word and p_addressed {p:.2f} >= {WAKE_BAR}"
    return False, f"p_addressed {p:.2f} below {WAKE_BAR if wake else BAR}" + (" (wake word)" if wake else "")


def state_for(text: str, wake: bool) -> str:
    s = f"Heard: {text}"
    if wake:
        s += "\nThe utterance starts with the assistant's name, Reach."
    return s


def body(state: str, model: str = MODEL) -> dict[str, Any]:
    criteria = {**commands.COMMANDS, commands.UNCERTAIN: commands.UNCERTAIN_DESC}
    return {"model": model, "state": state, "questions": {
        "addressed_to_assistant": {"type": "noul", "instructions": Q_ADDRESSED},
        "is_command_or_question": {"type": "noul", "instructions": Q_COMMAND},
        "command": {"type": "choice", "instructions": commands.INSTRUCTIONS, "criteria": criteria},
    }}


def _noul(a: Any) -> float | None:
    from .intent import _noul as n

    v = n(a)
    return commands._prob(v) if v is not None else None


def parse(resp: dict[str, Any], text: str, wake: bool, latency_ms: int) -> Result:
    answers = resp.get("answers") or {}
    usage = resp.get("usage") or {}
    tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    r = Result(text=text, wake_matched=wake, latency_ms=latency_ms, input_tokens=tin, output_tokens=tout,
               cost_usd=round(tin * DREX_USD_PER_INPUT_TOKEN, 8), model=resp.get("model"))
    r.p_addressed = _noul(answers.get("addressed_to_assistant"))
    r.p_command = _noul(answers.get("is_command_or_question"))
    raw = ((answers.get("command") or {}).get("probabilities")) or {}
    if isinstance(raw, dict):
        r.command_probs = {str(k): float(v) for k, v in raw.items() if commands._prob(v) is not None}
    if r.p_addressed is None:
        r.error = "no addressed_to_assistant answer"
    r.addressed, r.reason = decide(r.p_addressed, wake, r.error)
    if r.addressed and (r.p_command or 0) >= COMMAND_BAR and r.command_probs:
        label, _why = commands.decide(r.command_probs)
        r.command = None if label == commands.UNCERTAIN else label
    return r


def check(text: str, wake: bool | None = None, post=http_post, timeout: float = TIMEOUT_S) -> Result:
    """One Drex Max request. Never raises; an error is `addressed: False` with `error` set."""
    wake = wake_matched(text) if wake is None else bool(wake)
    t0 = time.monotonic()
    load_keys(("NACE_API_KEY",))
    key = os.environ.get("NACE_API_KEY") or os.environ.get("DREX_API_KEY")
    if not key and post is http_post:
        r = Result(text=text, wake_matched=wake, error="no Drex key")
        r.addressed, r.reason = decide(None, wake, r.error)
        return r
    headers = {"Authorization": f"Bearer {key or ''}", "X-Nace-Client": "reach/1"}
    try:
        resp = post(DREX_URL, headers, body(state_for(text, wake)), timeout)
    except Exception as e:
        r = Result(text=text, wake_matched=wake, error=f"{type(e).__name__}: {e}"[:200],
                   latency_ms=int((time.monotonic() - t0) * 1000))
        r.addressed, r.reason = decide(None, wake, r.error)
        return r
    return parse(resp, text, wake, int((time.monotonic() - t0) * 1000))


async def check_async(text: str, wake: bool | None = None, post=http_post, timeout: float = TIMEOUT_S) -> Result:
    """`check` in a thread, bounded to `timeout` in total: a late answer is discarded and counts as not addressed."""
    t0 = time.monotonic()
    w = wake_matched(text) if wake is None else bool(wake)
    fut = asyncio.ensure_future(asyncio.to_thread(check, text, w, post, timeout))
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.TimeoutError:
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        r = Result(text=text, wake_matched=w, error="timeout", latency_ms=int((time.monotonic() - t0) * 1000))
        r.addressed, r.reason = decide(None, w, r.error)
        return r


def log_event(r: Result) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    if r.p_addressed is not None:
        answers["addressed_to_assistant"] = {"noul": round(r.p_addressed, 4)}
    if r.p_command is not None:
        answers["is_command_or_question"] = {"noul": round(r.p_command, 4)}
    if r.command_probs:
        answers["command"] = {"choice": r.command, "probabilities": {k: round(v, 4) for k, v in r.command_probs.items()}}
    return {"type": "decision", "purpose": PURPOSE, "timestamp": int(time.time() * 1000), "ok": r.error is None,
            "error": r.error, "model": r.model, "state": r.text[:300], "answers": answers,
            "action": "addressed" if r.addressed else "ignored", "reason": r.reason, "wake_matched": r.wake_matched,
            "latency_ms": r.latency_ms, "tokens": {"input": r.input_tokens, "output": r.output_tokens},
            "cost_usd": r.cost_usd}


def log(r: Result, path: Path | None = None, task_id: str = "addressed") -> dict[str, Any]:
    ev = log_event(r)
    append_drex(path or drex_log(), ev, task_id)
    return ev


def reply(r: Result) -> dict[str, Any]:
    """The `addressed` message fields (contracts.Addressed)."""
    return {"text": r.text, "p_addressed": None if r.p_addressed is None else round(r.p_addressed, 4),
            "addressed": r.addressed, "command": r.command, "reason": r.reason, "wake_matched": r.wake_matched,
            "latency_ms": r.latency_ms}
