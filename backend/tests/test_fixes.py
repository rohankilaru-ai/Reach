"""Key-free tests for the Greptile findings: abort scoping, task cleanup, start lock, refused count, quotes."""

from __future__ import annotations

import asyncio
import json

import websockets

from reach import cli, evidence, ipc
from reach.contracts import Abort, Ask, Hello
from reach.ledger import ReceiptBuilder


class HoldBridge:
    """Runs until aborted; grounds off so no evidence calls are made."""

    grounds = False

    async def run(self, task, sink):
        while not task.aborted:
            await asyncio.sleep(0.01)
        return task.builder.receipt(aborted=True)


async def _client(port, token):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(Hello(token=token).model_dump_json())
    await ws.recv()
    return ws


async def _accepted(ws):
    while True:
        m = json.loads(await ws.recv())
        if m["type"] == "accepted":
            return m["task_id"]


async def _with_server(fn, monkeypatch, tmp_path):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    srv = ipc.Server(HoldBridge())
    async with websockets.serve(srv.handle, "127.0.0.1", 0) as s:
        await fn(srv, s.sockets[0].getsockname()[1])


def test_abort_without_id_only_cancels_own_tasks(monkeypatch, tmp_path):
    async def body(srv, port):
        a, b = await _client(port, srv.token), await _client(port, srv.token)
        await a.send(Ask(text="a").model_dump_json())
        await b.send(Ask(text="b").model_dump_json())
        ida, idb = await _accepted(a), await _accepted(b)
        ta, tb = srv.tasks[ida], srv.tasks[idb]
        await a.send(Abort().model_dump_json())
        await asyncio.sleep(0.2)
        assert ta.aborted and not tb.aborted
        assert ida not in srv.tasks  # finished tasks are removed
        # an unknown id (or another client's id) cancels nothing
        await a.send(Abort(task_id="nope").model_dump_json())
        await a.send(Abort(task_id=idb).model_dump_json())
        await asyncio.sleep(0.2)
        assert not tb.aborted
        await b.send(Abort(task_id=idb).model_dump_json())
        await asyncio.sleep(0.2)
        assert tb.aborted
        await a.close(); await b.close()

    asyncio.run(_with_server(body, monkeypatch, tmp_path))


def test_finished_task_leaves_mine(monkeypatch, tmp_path):
    async def body(srv, port):
        mine = {}
        task = srv.Task(Ask(text="x"))
        mine[task.id] = task
        srv.tasks[task.id] = task
        task.abort()
        await srv._run(task, asyncio.Queue(), mine)
        assert task.id not in mine and task.id not in srv.tasks

    asyncio.run(_with_server(body, monkeypatch, tmp_path))


def test_refused_counted_once():
    b = ReceiptBuilder("t")
    b.feed({"type": "tool_guard", "name": "bash", "rule": "r"})
    b.feed({"type": "tool_call_end", "name": "bash", "status": "refused"})
    assert b.receipt().refused == ["bash"]


def test_concurrent_first_starts_spawn_one_server(monkeypatch, tmp_path):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    spawned = []

    class FakeProc:
        def __init__(self, *a, **k):
            spawned.append(1)

    state = {"up": False}

    async def fake_connect():
        return object() if state["up"] else None

    real_popen = cli.subprocess.Popen

    def popen(*a, **k):
        FakeProc()
        state["up"] = True
        return None

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "_connect", fake_connect)

    async def go():
        return await asyncio.gather(cli._ensure_server(), cli._ensure_server(), cli._ensure_server())

    res = asyncio.run(go())
    assert len(spawned) == 1 and all(r is not None for r in res)
    cli.subprocess.Popen = real_popen


PASSAGE = (
    "Batch Normalization achieves the same accuracy with 14 times fewer training steps. "
    "We also thank the reviewers for their helpful comments on this manuscript.\n\n"
    "Our ensemble reaches $4.9\\%$ top-5 validation error, and $4.82\\%$ test error, exceeding the accuracy of human raters. "
    "The model is trained on many GPUs with synchronous updates across the cluster."
)


def test_quote_tolerates_inline_math_and_prefers_numbers():
    q = evidence.best_quote(PASSAGE, "The ensemble reached 4.82% test error, beating human raters.")
    assert "4.82" in q and "$" not in q
    q2 = evidence.best_quote(PASSAGE, "It needs 14x fewer steps.")
    assert "14 times fewer" in q2
