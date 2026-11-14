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


# ---- client -> server ----


class Hello(BaseModel):
    type: Literal["hello"] = "hello"
    token: str


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


Inbound = Annotated[Hello | Ask | Abort | Select | AddDoc, Field(discriminator="type")]
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
    write_outcome: str | None = Field(
        default=None,
        description="Computed from readbacks of native writes: verified, unverified or failed; None when nothing was written",
    )
    browser: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One entry per browser_task: outcome, status, kind, reason, final_url, cost_usd, drex_calls",
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


class Transcript(BaseModel):
    """A finished user or assistant turn, for the panel history (declared now)."""

    type: Literal["transcript"] = "transcript"
    task_id: str
    role: Literal["user", "assistant"]
    text: str


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    message: str
    task_id: str | None = None


Outbound = Annotated[
    Ready | Accepted | EventMsg | Receipt | Evidence | Pointer | Transcript | ErrorMsg | Added, Field(discriminator="type")
]
outbound_adapter: TypeAdapter[Outbound] = TypeAdapter(Outbound)

LEDGER_TYPES = frozenset({"decision", "nudge", "tool_guard"})

BROWSER_TOOL = "browser_task"
