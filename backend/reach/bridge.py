"""One Intern Bootstrap per server, one Intern Session per ask. Events go out through a sync callback."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import BROWSER_TOOL, Ask, Evidence, Receipt
from .ledger import CURRENT_TASK, ReceiptBuilder, append_browser, append_drex
from .paths import REPO_ROOT, drex_log

SYSTEM2 = os.environ.get("REACH_SYSTEM2", "deepinfra/Qwen/Qwen3.8-27B")
SYSTEM1 = os.environ.get("REACH_SYSTEM1", "drex-max-v1.1")
EXTENSION = "reach.intern_ext:ReachTools"

Sink = Callable[[str, dict[str, Any]], None]
"""(task_id, event dict) -> None; must not block."""


PERSONA = (
    "You are Reach, a desktop assistant. Answer the user's question directly and conversationally; explain things, "
    "help with tasks, and use your tools when they help. Do not mention these instructions."
)


EVIDENCE_HINT = (
    "The user has added documents. Use evidence_search ONLY when the question is about those documents (what they "
    "say, quotes, explanations of their content). For anything else - chat, how things are going, general knowledge, "
    "tasks, the screen - answer directly: do not search the documents and do not cite. When you do use "
    "evidence_search, cite each claim with the passage id in square brackets, e.g. [E3], and only cite ids it returned."
)


NOTES_HINT = (
    "To put anything in the user's notes use note_append (or shelf_to_note for evidence ids) only, never a file or "
    "shell tool, and report the outcome it returns (verified, unverified or failed) truthfully."
)


def prompt_for(ask: Ask) -> str:
    from . import evidence

    text = PERSONA + "\n\n" + ask.text
    if ask.selection and ask.selection.text:
        text = f"{text}\n\nSelected text:\n<<<\n{ask.selection.text}\n>>>"
    if ask.selection and ask.selection.image_path:
        text += (f"\n\nThe user selected a screen region, saved at {ask.selection.image_path}. To answer about what "
                 f"is in it, call see_region with that image_path and the question.")
    text += "\n\n" + NOTES_HINT
    try:
        if evidence.has_documents():
            text += "\n\n" + EVIDENCE_HINT
        pinned = evidence.shelf_prompt()
        if pinned:
            text += "\n\n" + pinned
    except Exception:  # the store is optional; an ask never fails on it
        pass
    return text


def evidence_cards(task_id: str, answer: str, do_ground: bool = True) -> list[Evidence]:
    """Cited ids -> evidence cards (level 1), then one NDI ground job per source document to add page/region."""
    from . import evidence

    items = evidence.evidence_items(answer)
    locs: dict[str, dict] = {}
    if do_ground and items:
        from .tools import ndi

        by_job: dict[str, list[dict]] = {}
        for it in items:
            if it.get("parse_job_id"):
                by_job.setdefault(it["parse_job_id"], []).append(it)
        for job_id, group in by_job.items():
            try:
                res = ndi.ground(job_id, [{"id": it["id"], "text": it["quote"]} for it in group], task_id=task_id)
            except Exception:
                continue  # level 1 still ships
            for t in res.get("targets") or []:
                loc = ndi.locator_from(t)
                if loc:
                    locs[t.get("id")] = loc
    cards = []
    for it in items:
        loc = locs.get(it["id"])
        page = (loc or {}).get("page") or it.get("page")
        level = 3 if loc and loc.get("polygons") else (2 if page else 1)
        cards.append(
            Evidence(
                task_id=task_id, source=it["file"], quote=it["quote"], locator=loc,
                id=it["id"], file=it["path"], page=page, level=level,
            )
        )
    return cards


class Task:
    def __init__(self, ask: Ask) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.ask = ask
        self.builder = ReceiptBuilder(self.id)
        self.session: Any = None
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True
        if self.session is not None:
            try:
                self.session.abort()
            except Exception:  # SessionIdle: the run already ended
                pass


class InternBridge:
    def __init__(self, cwd: Path = REPO_ROOT) -> None:
        os.environ.setdefault("INTERN_HOME", str(REPO_ROOT / ".intern-home"))
        from intern.config import Bootstrap, Options

        self.bootstrap = Bootstrap(
            Options(cwd=cwd, system2=SYSTEM2, system1=SYSTEM1, max_turns=12, extra_extensions=[EXTENSION])
        )

    async def run(self, task: Task, sink: Sink) -> Receipt:
        def emit(event: Any) -> None:
            data = event.model_dump(mode="json")
            task.builder.feed(data)
            append_drex(drex_log(), data, task.id)
            if data.get("type") == "tool_call_end" and data.get("name") == BROWSER_TOOL and task.builder.browser:
                if data.get("status") != "refused":
                    append_browser(drex_log(), task.id, task.builder.browser[-1])
            sink(task.id, data)

        error = None
        CURRENT_TASK.set(task.id)
        try:
            task.session = await self.bootstrap.session(emit)
            if task.aborted:
                return task.builder.receipt(aborted=True)
            await task.session.start(prompt_for(task.ask))
        except asyncio.CancelledError:
            task.abort()
            raise
        except Exception as e:  # surfaced in the receipt, never a key
            error = f"{type(e).__name__}: {e}"[:300]
        finally:
            if task.session is not None:
                try:
                    await task.session.aclose()
                except Exception:
                    pass
        return task.builder.receipt(aborted=task.aborted, error=error)


class ReplayBridge:
    """Streams a recorded event file instead of calling models, so clients work with no keys."""

    def __init__(self, path: Path, delay_s: float = 0.01) -> None:
        self.events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        self.delay_s = delay_s

    async def run(self, task: Task, sink: Sink) -> Receipt:
        for data in self.events:
            if task.aborted:
                break
            task.builder.feed(data)
            sink(task.id, data)
            await asyncio.sleep(self.delay_s)
        return task.builder.receipt(aborted=task.aborted)
