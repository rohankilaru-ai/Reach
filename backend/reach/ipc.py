"""Localhost WebSocket server speaking the contracts.py protocol."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

import websockets
from pydantic import ValidationError

from .contracts import Abort, Accepted, Ask, ErrorMsg, EventMsg, Hello, Ready, Select, Transcript, inbound_adapter
from .paths import run_dir


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
        mine: list = []
        running: set[asyncio.Task] = set()
        try:
            async for raw in ws:
                try:
                    msg = inbound_adapter.validate_json(raw)
                except ValidationError as e:
                    out.put_nowait(ErrorMsg(message=f"bad message: {e.errors()[0]['msg']}").model_dump_json())
                    continue
                if isinstance(msg, Ask):
                    task = self.Task(msg)
                    self.tasks[task.id] = task
                    mine.append(task)
                    out.put_nowait(Accepted(task_id=task.id).model_dump_json())
                    out.put_nowait(Transcript(task_id=task.id, role="user", text=msg.text).model_dump_json())
                    t = asyncio.create_task(self._run(task, out))
                    running.add(t)
                    t.add_done_callback(running.discard)
                elif isinstance(msg, Abort):
                    targets = [self.tasks[msg.task_id]] if msg.task_id in self.tasks else list(self.tasks.values())
                    for task in targets:
                        task.abort()
                elif isinstance(msg, Select):
                    pass  # S2 consumes this; accepted and ignored for now
        finally:
            for task in mine:  # a client that goes away takes its running tasks with it
                if task.id in self.tasks:
                    task.abort()
            if running:
                await asyncio.wait(running, timeout=15)
            out.put_nowait(None)
            await pump_task

    async def _run(self, task, out: asyncio.Queue) -> None:
        def sink(task_id: str, event: dict) -> None:
            out.put_nowait(EventMsg(task_id=task_id, event=event).model_dump_json())

        try:
            receipt = await self.bridge.run(task, sink)
            if receipt.final_text:
                out.put_nowait(
                    Transcript(task_id=task.id, role="assistant", text=receipt.final_text).model_dump_json()
                )
            out.put_nowait(receipt.model_dump_json())
        finally:
            self.tasks.pop(task.id, None)

    async def serve(self, port: int = 0) -> None:
        async with websockets.serve(self.handle, "127.0.0.1", port, max_size=8 * 2**20) as server:
            bound = server.sockets[0].getsockname()[1]
            write_run_files(bound, self.token)
            try:
                await asyncio.Future()
            finally:
                clear_run_files()
