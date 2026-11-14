"""The wire contract between the Reach backend and its clients (CLI, overlay).

Transport: WebSocket on 127.0.0.1, port and pid in `.reach/run/server.json`, token in `.reach/run/token` (mode 0600).
The client's first message must be `{"type": "hello", "token": "<token>"}`; the server answers `{"type": "ready"}` or
closes with code 4401. Every message is one JSON object with a `type` field.

Frozen after S1: additive edits only.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1


class Selection(BaseModel):
    """What the user pointed at: selected text and/or a screen region."""

    text: str | None = None
    app: str | None = None
    region: list[float] | None = Field(default=None, description="[x, y, w, h] in screen points")
    image_path: str | None = None
    source: str | None = Field(default=None, description="Who made it: 'pinch' (camera worker) or None (overlay/CLI)")
    path: list[list[float]] | None = Field(
        default=None, description="Circle selection: the loop as [[x, y], ...] in screen points, top-left origin (<= ~64)"
    )


# ---- client -> server ----


class Hello(BaseModel):
    type: Literal["hello"] = "hello"
    token: str
    role: Literal["overlay", "perception", "cli"] | None = Field(
        default=None, description="Optional. 'overlay' clients receive relayed selection/pointer messages"
    )


class Ask(BaseModel):
    type: Literal["ask"] = "ask"
    text: str
    selection: Selection | None = None


class Abort(BaseModel):
    type: Literal["abort"] = "abort"
    task_id: str | None = None


class Select(BaseModel):
    """The user changed the selection without asking yet (declared now; used from S2)."""

    type: Literal["select"] = "select"
    selection: Selection


class AddDoc(BaseModel):
    """Ingest a document with NDI into the evidence store (outside any agent run)."""

    type: Literal["add"] = "add"
    path: str


class PointerIn(BaseModel):
    """Hand pointer from the perception worker (EXPERIMENTAL). Relayed to overlay clients as `pointer`."""

    type: Literal["pointer"] = "pointer"
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    label: str | None = None
    state: Literal["hover", "pinch"] | None = None


class Hypotheses(BaseModel):
    """EXPERIMENTAL (S8a): N-best transcript hypotheses for one utterance, best first, e.g. from the lip-reading
    worker (perception/silent). The server routes them with the 12-command router and broadcasts a `transcript`
    result. It never runs an agent task."""

    type: Literal["transcript"] = "transcript"
    hypotheses: list[Annotated[str, Field(max_length=200)]] = Field(min_length=1, max_length=10)
    scores: list[float] | None = Field(default=None, description="log-probabilities, same order as hypotheses")
    source: str = Field(default="lipreading", max_length=40)
    experimental: bool = True


class Utterance(BaseModel):
    """Hands-free speech (always listening): the server asks Drex whether it is addressed to the assistant and answers
    the sender with `addressed`. It never runs an agent task. Contract: docs/evidence/addressed.md."""

    type: Literal["utterance"] = "utterance"
    text: str = Field(max_length=500)
    source: str = Field(default="always_listening", max_length=40)
    wake_matched: bool | None = None


Inbound = Annotated[Hello | Ask | Abort | Select | AddDoc | PointerIn | Hypotheses | Utterance, Field(discriminator="type")]
inbound_adapter: TypeAdapter[Inbound] = TypeAdapter(Inbound)


# ---- server -> client ----


class Ready(BaseModel):
    type: Literal["ready"] = "ready"
    protocol: int = PROTOCOL_VERSION


class Accepted(BaseModel):
    type: Literal["accepted"] = "accepted"
    task_id: str


class EventMsg(BaseModel):
    """One Intern event, passed through unchanged as `event` (its own `type` is inside)."""

    type: Literal["event"] = "event"
    task_id: str
    event: dict[str, Any]


Status = Literal["ok", "failed", "aborted"]


class Receipt(BaseModel):
    type: Literal["receipt"] = "receipt"
    task_id: str
    status: Status
    reason: str = Field(description="Intern's run_end reason (completed, aborted, ...) or error")
    tools: list[str] = Field(default_factory=list, description="Tool names that ran, in order")
    refused: list[str] = Field(default_factory=list, description="Tool calls refused by the guard or a hook")
    system1_asks: int = Field(description="decision events observed for this task")
    tokens: int = Field(description="System 2 input + output tokens")
    est_cost_usd: float
    duration_ms: int
    final_text: str = ""
    ndi_calls: int = Field(default=0, description="NDI calls (parse/ground) made for this task")
    outcome: str | None = Field(
        default=None, description="Computed, never model-written: done | not_done | aborted, from the last browser task"
    )
    selection_source: str | None = Field(
        default=None, description="'pinch' when the ask used a hand selection (sent by the overlay or attached by the server)"
    )
    write_outcome: str | None = Field(
        default=None,
        description="Computed from readbacks of native writes: verified, unverified or failed; None when nothing was written",
    )
    browser: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One entry per browser_task: outcome, status, kind, reason, final_url, cost_usd, drex_calls",
    )
    intent: dict[str, Any] | None = Field(
        default=None, description="Drex task understanding before Intern ran: kind, probs, hint_used, action"
    )
    screen_captured: bool = Field(
        default=False, description="Privacy: True when see_screen captured the screen for this task (sent only to the vision model)"
    )


class Evidence(BaseModel):
    """A passage the answer cites. `source` is the file name; `locator` is NDI ground's best match (page, polygons)."""

    type: Literal["evidence"] = "evidence"
    task_id: str
    source: str
    quote: str | None = None
    locator: dict[str, Any] | None = None
    id: str | None = Field(default=None, description="Passage id cited by the answer, e.g. E3")
    file: str | None = Field(default=None, description="Absolute path of the source PDF, for opening it")
    page: int | None = None
    level: int = Field(default=1, description="1 quote+id, 2 page, 3 page region (polygons)")


class Added(BaseModel):
    type: Literal["added"] = "added"
    name: str
    sha256: str
    cached: bool
    passages: int


class Pointer(BaseModel):
    """Where on screen to point (declared now; filled from S4)."""

    type: Literal["pointer"] = "pointer"
    task_id: str
    x: float
    y: float
    label: str | None = None
    screen: int | None = None
    state: str | None = Field(default=None, description="Relayed hand pointer: hover | pinch")
    source: str | None = Field(default=None, description="'hand' when relayed from the perception worker")


class Transcript(BaseModel):
    """A finished user or assistant turn, for the panel history (declared now)."""

    type: Literal["transcript"] = "transcript"
    task_id: str
    role: Literal["user", "assistant"]
    text: str
    # S8a additive fields: set only on a routed `transcript` (hypotheses) result. `uncertain` results never act.
    command: str | None = None
    probability: float | None = None
    uncertain: bool | None = None
    reason: str | None = None
    source: str | None = None
    experimental: bool | None = None


class SelectionMsg(BaseModel):
    """A selection made outside the overlay (the pinch worker), relayed to overlay clients. Does not start an ask."""

    type: Literal["selection"] = "selection"
    selection: Selection


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    message: str
    task_id: str | None = None


class Addressed(BaseModel):
    """Reply to `utterance`: whether the speech is addressed to the assistant. Unsure or timed out => addressed False."""

    type: Literal["addressed"] = "addressed"
    text: str
    p_addressed: float | None = None
    addressed: bool = False
    command: str | None = None
    reason: str = ""
    wake_matched: bool = False
    latency_ms: int = 0


Outbound = Annotated[
    Ready | Accepted | EventMsg | Receipt | Evidence | Pointer | Transcript | ErrorMsg | Added | SelectionMsg | Addressed, Field(discriminator="type")
]
outbound_adapter: TypeAdapter[Outbound] = TypeAdapter(Outbound)

LEDGER_TYPES = frozenset({"decision", "nudge", "tool_guard"})

BROWSER_TOOL = "browser_task"
