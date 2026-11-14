"""Reach's Intern extension: custom tools registered into every Reach session."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from intern.hooks import Extension
from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel


class NowArgs(BaseModel):
    pass


class ReachNow(Tool):
    """A trivial read-only tool that proves custom tool registration."""

    name: ClassVar[str] = "reach_now"
    description: ClassVar[str] = "Return the current local date and time on the user's machine (ISO 8601)."
    Args: ClassVar[type[BaseModel]] = NowArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(content=datetime.now().astimezone().isoformat(timespec="seconds"))


class ReachTools(Extension):
    name: ClassVar[str] = "reach"

    def tools(self) -> list[Tool]:
        return [ReachNow()]
