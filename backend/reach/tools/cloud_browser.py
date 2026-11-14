"""browser_cloud: Nace's own hosted browser agent ("Browser runs"), with Drex Max driving a cloud browser.

It browses only the origins allowed for the run, in a sandbox, with no logins and no side effects on the user's Mac. Billed
per run to the user's Nace credits and capped. Banking, payment, identity, large social and government sites are refused
by Nace. For pages the user is signed in to, Reach uses their own Chrome instead.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar
from urllib.parse import urlparse

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import append_tool_entry

MAX_COST_USD = 0.10
MAX_SECONDS = 90
HARD_CAP_S = 110


def origin_of(url: str) -> str | None:
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")


def summarize(run: Any) -> dict[str, Any]:
    """Nace's run object -> the fields Reach shows. `done` only for a succeeded run with a completed outcome."""
    status = str(getattr(run, "status", "") or "")
    outcome = getattr(run, "outcome", None)
    kind = str(getattr(outcome, "kind", "") or "")
    reason = str(getattr(outcome, "reason", "") or "")
    usage = getattr(run, "usage", None)
    answer = getattr(run, "answer_text", None) or getattr(run, "answer", None) or ""
    return {
        "done": status == "succeeded" and kind in ("", "completed"),
        "status": status, "kind": kind, "reason": reason[:200],
        "answer": str(answer)[:1500], "final_url": getattr(run, "final_url", None),
        "cost_usd": float(getattr(usage, "cost_usd", 0) or 0) if usage else 0.0,
    }


def run_task(task: str, start_url: str, allow: list[str] | None = None, client_factory=None) -> dict[str, Any]:
    from nace_sdk import BrowserRunLimits, NaceClient

    from ..commands import load_keys

    load_keys(("NACE_API_KEY",))
    origin = origin_of(start_url)
    if origin is None:
        raise ValueError("start_url must be a public http or https address")
    origins = [origin] + [o for o in (origin_of(x) for x in (allow or [])) if o and o != origin][:19]
    with (client_factory or NaceClient)() as client:
        return summarize(client.browser_runs.run(
            task=task[:4000], start_url=start_url, allowed_origins=origins,
            limits=BrowserRunLimits(max_cost_usd=MAX_COST_USD, max_seconds=MAX_SECONDS)))


class CloudArgs(BaseModel):
    task: str = Field(description="What to find or do, in plain language (up to 4000 characters)")
    start_url: str = Field(description="The public page to open first, e.g. https://news.ycombinator.com/")
    allow: list[str] = Field(default_factory=list, description="Other origins the browser may load (optional)")


class CloudBrowser(Tool):
    name: ClassVar[str] = "browser_cloud"
    description: ClassVar[str] = (
        "Use Nace's hosted cloud browser (Drex Max drives it) to research or act on PUBLIC web pages and return the "
        "answer. Fast and sandboxed, no logins, never sends messages or pays. Give a start_url. Not for the user's own "
        "signed-in pages (use their Chrome for those) and not for banking, payment, identity, large social or "
        "government sites, which Nace refuses. Report the result faithfully: if it did not complete, say so."
    )
    Args: ClassVar[type[BaseModel]] = CloudArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(asyncio.to_thread(run_task, args.task, args.start_url, args.allow), HARD_CAP_S)
        except asyncio.TimeoutError:
            return ToolOutput(status="error", content="outcome=not_done\nThe cloud browser did not finish in time; nothing was changed.")
        except ValueError as e:
            return ToolOutput(status="error", content=f"outcome=not_done\n{e}")
        except Exception as e:  # SDK errors: origin_blocked, rate limits, credit: never include secrets
            name = type(e).__name__
            msg = str(e)[:200]
            return ToolOutput(status="error", content=f"outcome=not_done\nNace's cloud browser refused or failed ({name}): {msg}")
        append_tool_entry("action", tool="browser_cloud", destination=urlparse(args.start_url).hostname or "web",
                          chars=len(res["answer"]), outcome="done" if res["done"] else "not_done",
                          ms=int((time.time() - t0) * 1000), cost_usd=res["cost_usd"])
        head = "outcome=done" if res["done"] else f"outcome=not_done ({res['kind'] or res['status']}: {res['reason']})"
        return ToolOutput(status="ok" if res["done"] else "error",
                          content=f"{head}\n{res['answer']}\n(final page: {res['final_url']}; cost ${res['cost_usd']:.4f})")
