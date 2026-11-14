"""Speed hook: turn System 2's reasoning off for simple asks and cap the output, before each provider request.

Qwen3.8-27B on DeepInfra can disable reasoning with `chat_template_kwargs: {"enable_thinking": false}` (measured:
a short answer dropped from ~1.2 s to ~0.6 s; a screen ask spends most of its turns thinking). Reasoning stays on
for multi-step tool work (document questions, browser tasks, Mac control, unclear asks) and everywhere when
REACH_THINK=1. The bridge records each task's Drex intent kind in KINDS; the hook reads it via CURRENT_TASK.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from intern.hooks import Extension

from .ledger import CURRENT_TASK

THINK_KINDS = frozenset({"document_question", "browser_task", "mac_control", "other"})
"""Intent kinds that keep reasoning on. Anything else (chat, notes, screen, or no intent) answers without it."""
NO_THINK_CAP = 1024
"""Output cap when reasoning is off: room for a short answer or a tool call's arguments, not an essay."""
NO_THINK_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

KINDS: dict[str, str | None] = {}
"""task id -> Drex intent kind (None when the intent call failed). The bridge sets and clears it."""


def think_for(kind: str | None) -> bool:
    if os.environ.get("REACH_THINK") == "1":
        return True
    return kind in THINK_KINDS


def shape(request: Any, kind: str | None) -> Any | None:
    """The request with reasoning off and the cap lowered, or None to leave it unchanged."""
    if think_for(kind):
        return None
    update: dict[str, Any] = {}
    if "qwen" in request.model.lower():  # the switch is Qwen's chat template; other endpoints may reject the field
        update["compat"] = request.compat.model_copy(
            update={"extra_body": {**request.compat.extra_body, **NO_THINK_BODY}})
    return request.model_copy(update={
        **update,
        "reasoning_effort": None,
        "max_output_tokens": min(request.max_output_tokens, NO_THINK_CAP),
    })


class SpeedHook(Extension):
    name: ClassVar[str] = "reach_speed"

    async def before_provider_request(self, request: Any, ctx: Any) -> Any | None:
        task = CURRENT_TASK.get(None)
        if not task or task not in KINDS:
            return None  # not a bridge ask (e.g. a test session): leave it alone
        return shape(request, KINDS[task])
