"""Conversation memory: Reach remembers the last few exchanges, and Drex decides whether a new message continues them.

Each ask opens a fresh Intern session, so without this Reach forgets what was just said. After every finished ask the
user's words and Reach's reply are stored. On the next ask Drex Max answers one extra yes/no question
(`continues_previous`): does this message refer to the previous exchange? If yes the recent turns are put in front of
the model; if no the memory is cleared and a new conversation starts. Ten idle minutes also start a new one.
"""
from __future__ import annotations

import threading
import time
from typing import Any

IDLE_S = 20 * 60
MAX_TURNS = 6
CONTINUE_AT = 0.6  # Drex probability at or above which the message continues the conversation
FALLBACK_S = 3 * 60  # when Drex did not answer: continue only if the last turn was this recent
_lock = threading.Lock()
_turns: list[dict[str, Any]] = []


def _expire(now: float | None = None) -> None:
    now = now or time.time()
    if _turns and now - _turns[-1]["ts"] > IDLE_S:
        _turns.clear()


def has_turns() -> bool:
    with _lock:
        _expire()
        return bool(_turns)


def reset() -> None:
    with _lock:
        _turns.clear()


def add(user: str, assistant: str, tools: list[str] | None = None) -> None:
    user, assistant = (user or "").strip(), (assistant or "").strip()
    if not user or not assistant:
        return
    with _lock:
        _expire()
        _turns.append({"user": user[:600], "assistant": assistant[:900], "tools": list(tools or [])[:6],
                       "ts": time.time()})
        del _turns[:-MAX_TURNS]


def previous_summary(max_chars: int = 500) -> str | None:
    """The last exchange, short, for Drex to judge a follow-up against."""
    with _lock:
        _expire()
        if not _turns:
            return None
        t = _turns[-1]
    return f"user: {t['user'][:200]} | Reach: {t['assistant'][:max_chars - 220]}"


def history_text() -> str | None:
    with _lock:
        _expire()
        turns = list(_turns)
    if not turns:
        return None
    lines = []
    for t in turns:
        lines.append(f"User: {t['user']}")
        used = f" (used: {', '.join(t['tools'])})" if t["tools"] else ""
        lines.append(f"Reach{used}: {t['assistant']}")
    return "\n".join(lines)


def decide(intent: Any) -> bool:
    """True when the new message continues the conversation. Clears memory when it does not (Drex said so)."""
    with _lock:
        _expire()
        if not _turns:
            return False
        age = time.time() - _turns[-1]["ts"]
    p = None if intent is None or getattr(intent, "error", None) else intent.flags.get("continues_previous")
    if p is None:  # Drex did not answer: be conservative, keep only a very recent conversation
        return age <= FALLBACK_S
    if p >= CONTINUE_AT:
        return True
    reset()
    return False
