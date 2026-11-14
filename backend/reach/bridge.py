"""One Intern Bootstrap per server, one Intern Session per ask. Events go out through a sync callback."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import Ask, Receipt
from .ledger import ReceiptBuilder, append_drex
from .paths import REPO_ROOT, drex_log

SYSTEM2 = os.environ.get("REACH_SYSTEM2", "deepinfra/Qwen/Qwen3.8-27B")
SYSTEM1 = os.environ.get("REACH_SYSTEM1", "drex-max-v1.1")
EXTENSION = "reach.intern_ext:ReachTools"

Sink = Callable[[str, dict[str, Any]], None]
"""(task_id, event dict) -> None; must not block."""


def prompt_for(ask: Ask) -> str:
    if ask.selection and ask.selection.text:
        return f"{ask.text}\n\nSelected text:\n<<<\n{ask.selection.text}\n>>>"
    return ask.text


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
            sink(task.id, data)

        error = None
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
