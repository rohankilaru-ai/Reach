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


class BrowserTaskArgs(BaseModel):
    task: str = Field(description="What to do in the browser, in plain words, e.g. 'open the Travel category and report the first book title'")
    start_url: str = Field(description="The public web page to start from (https://...)")


class BrowserTask(Tool):
    """Runs one bounded web task in a real browser (Nace browser harness, Drex Max clicks). Judged by the guard."""

    name: ClassVar[str] = "browser_task"
    description: ClassVar[str] = (
        "Open a real web browser at start_url and carry out one bounded task (navigate, click, read) on public pages, "
        "then return what it found. Use it whenever the user asks you to use the browser or look something up on a "
        "website. It reports DONE only when the task finished; otherwise report to the user that it was NOT done and "
        "why. It never pays, sends, or submits on its own."
    )
    Args: ClassVar[type[BaseModel]] = BrowserTaskArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read", "write", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        from .tools import browser

        s = await browser.run_task(args.task, args.start_url, abort=ctx.abort)
        content = browser.render(s)
        return ToolOutput(status="ok" if s["done"] else "error", content=content)


class ReachTools(Extension):
    name: ClassVar[str] = "reach"

    def tools(self) -> list[Tool]:
        from intern.tools.files import ReadTool

        from .tools import chrome
        from .tools.native import NoteAppend, OpenSource, ShelfToNote
        from .tools.tables import TableToNote
        from .tools.vision import SeeRegion

        mac_tools: list[Tool] = [*chrome.tools(), TableToNote()]
        from .tools import ax, finder

        mac_tools += ax.tools() + finder.tools()  # finder_list always; Do mode tools only with REACH_DO_MODE=1
        # The bridge drops Intern's base pack (shell, write, edit); read comes back here, fenced by guard_policy.
        return mac_tools + [
            ReachNow(), EvidenceSearch(), BrowserTask(), NoteAppend(), ShelfToNote(), OpenSource(), SeeRegion(),
            ReadTool(),
        ]
