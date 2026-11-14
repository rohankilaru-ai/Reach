"""Reach's Intern extension: custom tools registered into every Reach session."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from intern.hooks import Extension
from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field


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


class EvidenceSearchArgs(BaseModel):
    query: str = Field(description="Keywords to look for in the user's added documents")
    k: int = Field(default=5, ge=1, le=10, description="How many passages to return")


class EvidenceSearch(Tool):
    """Keyword (BM25) search over passages of documents the user added with `reach add` (parsed by NDI)."""

    name: ClassVar[str] = "evidence_search"
    description: ClassVar[str] = (
        "Search the documents the user added (PDFs parsed by NDI) and return the best passages with ids like E3. "
        "Use it for any question about the user's documents. Cite every claim you take from a passage with its id "
        "in square brackets, e.g. [E3]. Only cite ids this tool returned."
    )
    Args: ClassVar[type[BaseModel]] = EvidenceSearchArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        from . import evidence

        hits = evidence.search(args.query, args.k)
        if not hits:
            return ToolOutput(content="No passages matched. Try other keywords, or say the documents do not cover it.")
        parts = []
        for h in hits:
            page = f", page {h['page']}" if h["page"] else ""
            parts.append(f"[{h['id']}] ({h['file']}{page})\n{h['text']}")
        parts.append("Cite the passages you use by id in square brackets, e.g. [" + hits[0]["id"] + "].")
        return ToolOutput(content="\n\n".join(parts))


class ReachTools(Extension):
    name: ClassVar[str] = "reach"

    def tools(self) -> list[Tool]:
        return [ReachNow(), EvidenceSearch()]
