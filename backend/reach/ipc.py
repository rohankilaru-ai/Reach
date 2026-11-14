"""Localhost WebSocket server speaking the contracts.py protocol."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from pathlib import Path

import websockets
from pydantic import ValidationError

from .contracts import (
    Abort, Accepted, AddDoc, Added, Ask, ErrorMsg, EventMsg, Hello, PointerIn, Ready, Select, Transcript,
    inbound_adapter,
)
from .bridge import evidence_cards
from .ledger import count_ndi
from .relay import Relay, log_line
from .paths import drex_log, reach_dir, run_dir


GROUND_DEADLINE_S = 150


def log_evidence(card) -> None:
    p = reach_dir() / "logs" / "evidence.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(card.model_dump_json() + "\n")


def write_run_files(port: int, token: str) -> None:
    d = run_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tok = d / "token"
    fd = os.open(tok, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(tok, 0o600)
    (d / "server.json").write_text(json.dumps({"host": "127.0.0.1", "port": port, "pid": os.getpid()}))


def clear_run_files() -> None:
    for name in ("server.json", "token"):
        (run_dir() / name).unlink(missing_ok=True)


def read_run_files() -> tuple[int, str] | None:
    d = run_dir()
    try:
        info = json.loads((d / "server.json").read_text())
        return int(info["port"]), (d / "token").read_text().strip()
    except (OSError, ValueError, KeyError):
        return None


class Server:
    def __init__(self, bridge) -> None:
        from .bridge import Task

        self.bridge = bridge
        self.Task = Task
        self.token = secrets.token_urlsafe(32)
        self.tasks: dict[str, Task] = {}
        self.relay = Relay()

    async def handle(self, ws) -> None:
        try:
            first = inbound_adapter.validate_json(await asyncio.wait_for(ws.recv(), 10))
        except (ValidationError, asyncio.TimeoutError, websockets.ConnectionClosed):
            await ws.close(4400, "expected hello")
            return
        if not isinstance(first, Hello) or not secrets.compare_digest(first.token, self.token):
            await ws.close(4401, "bad token")
            return
        await ws.send(Ready().model_dump_json())

        out: asyncio.Queue[str | None] = asyncio.Queue()

        async def pump() -> None:
            while (msg := await out.get()) is not None:
                try:
                    await ws.send(msg)
                except websockets.ConnectionClosed:
                    return

        pump_task = asyncio.create_task(pump())
        role, key = first.role, id(ws)
        self.relay.join(key, role, out)
        mine: dict[str, object] = {}
        running: set[asyncio.Task] = set()
        try:
            async for raw in ws:
                try:
                    msg = inbound_adapter.validate_json(raw)
                except ValidationError as e:
                    out.put_nowait(ErrorMsg(message=f"bad message: {e.errors()[0]['msg']}").model_dump_json())
                    continue
                if isinstance(msg, Ask):
                    ask, sel_from = self.relay.attach(msg)
                    task = self.Task(ask)
                    task.selection_source = "pinch" if sel_from and sel_from.startswith("pinch") else None
                    log_line({"kind": "ask", "task_id": task.id, "from": role, "selection": sel_from,
                              "chars": len((ask.selection.text or "") if ask.selection else "")})
                    self.tasks[task.id] = task
                    mine[task.id] = task
                    out.put_nowait(Accepted(task_id=task.id).model_dump_json())
                    out.put_nowait(Transcript(task_id=task.id, role="user", text=msg.text).model_dump_json())
                    t = asyncio.create_task(self._run(task, out, mine))
                    running.add(t)
                    t.add_done_callback(running.discard)
                elif isinstance(msg, Abort):
                    if msg.task_id:
                        targets = [mine[msg.task_id]] if msg.task_id in mine else []
                    else:  # no id: only this client's own tasks
                        targets = list(mine.values())
                    for task in targets:
                        task.abort()
                elif isinstance(msg, (Select, PointerIn)):
                    fn = self.relay.select if isinstance(msg, Select) else self.relay.pointer
                    if err := fn(key, role, msg):
                        out.put_nowait(ErrorMsg(message=err).model_dump_json())
                elif isinstance(msg, AddDoc):
                    t = asyncio.create_task(self._add(msg, out))
                    running.add(t)
                    t.add_done_callback(running.discard)
        finally:
            self.relay.leave(key)
            for task in list(mine.values()):  # a client that goes away takes its running tasks with it
                if task.id in self.tasks:
                    task.abort()
            if running:
                await asyncio.wait(running, timeout=15)
            out.put_nowait(None)
            await pump_task

    async def _run(self, task, out: asyncio.Queue, mine: dict | None = None) -> None:
        def sink(task_id: str, event: dict) -> None:
            out.put_nowait(EventMsg(task_id=task_id, event=event).model_dump_json())

        try:
            receipt = await self.bridge.run(task, sink)
            if receipt.final_text and getattr(self.bridge, "grounds", True) and not task.aborted:
                try:
                    cards = await self._ground(task, receipt.final_text)
                except Exception:  # the evidence store is optional; the receipt always ships
                    cards = []
                if task.aborted:  # stopped while grounding: no cards, and never the earlier success
                    cards = []
                    update = {"status": "aborted", "reason": "aborted"}
                    if receipt.outcome is not None:
                        update["outcome"] = "aborted"
                    receipt = receipt.model_copy(update=update)
                for card in cards:
                    try:
                        log_evidence(card)
                    except Exception:  # an unwritable log must not lose the card or the receipt
                        pass
                    out.put_nowait(card.model_dump_json())
                try:
                    receipt.ndi_calls = count_ndi(drex_log(), task.id)
                except Exception:
                    pass
            receipt.selection_source = getattr(task, "selection_source", None)
            if receipt.final_text and receipt.status != "aborted":
                out.put_nowait(
                    Transcript(task_id=task.id, role="assistant", text=receipt.final_text).model_dump_json()
                )
            out.put_nowait(receipt.model_dump_json())
        finally:
            self.tasks.pop(task.id, None)
            if mine is not None:
                mine.pop(task.id, None)

    async def _ground(self, task, answer: str) -> list:
        """Evidence cards with NDI grounding. Stops starting ground jobs once the ask is aborted or the deadline
        passes, and returns at once on abort (an in-flight ground call finishes in its thread and starts no other)."""
        stop = threading.Event()
        deadline = time.monotonic() + GROUND_DEADLINE_S

        def should_stop() -> bool:
            return stop.is_set() or task.aborted or time.monotonic() > deadline

        fut = asyncio.ensure_future(asyncio.to_thread(evidence_cards, task.id, answer, True, should_stop))
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())  # never "exception was never retrieved"
        cards = None
        try:
            while not fut.done() and not task.aborted and time.monotonic() <= deadline:
                await asyncio.wait({fut}, timeout=0.2)
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                cards = fut.result()
        finally:
            stop.set()
        if task.aborted:
            return []
        if cards is None:  # ground slow or down: level-1/2 cards still ship
            cards = evidence_cards(task.id, answer, do_ground=False)
        return cards

    async def _add(self, msg: AddDoc, out: asyncio.Queue) -> None:
        from . import evidence
        from .tools import ndi

        try:
            r = await asyncio.to_thread(evidence.add_document, Path(msg.path), ndi.parse_pdf)
            out.put_nowait(
                Added(name=r["name"], sha256=r["sha256"], cached=r["cached"], passages=len(r["passages"])).model_dump_json()
            )
        except Exception as e:
            out.put_nowait(ErrorMsg(message=f"add failed: {type(e).__name__}").model_dump_json())

    async def serve(self, port: int = 0) -> None:
        async with websockets.serve(self.handle, "127.0.0.1", port, max_size=8 * 2**20) as server:
            bound = server.sockets[0].getsockname()[1]
            write_run_files(bound, self.token)
            try:
                await asyncio.Future()
            finally:
                clear_run_files()
