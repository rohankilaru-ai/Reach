"""Key-free tests for the S8a hook: an inbound `transcript` with lip-reading hypotheses is routed and broadcast."""

from __future__ import annotations

import asyncio
import json

import websockets

from reach import commands, ipc
from reach.commands import RouteResult
from reach.contracts import Hello, inbound_adapter
from reach.paths import drex_log


class FakeRouter:
    def __init__(self, probs):
        self.probs = probs
        self.states = []

    def classify(self, state):
        self.states.append(state)
        label, reason = commands.decide(self.probs)
        return RouteResult(label, reason, probabilities=dict(self.probs), model="fake")


async def _client(port, token, role):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(Hello(token=token, role=role).model_dump_json())
    await ws.recv()
    return ws


async def _next_transcript(ws):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if m["type"] in ("transcript", "error"):
            return m


def _run(router, hyps, monkeypatch, tmp_path):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))

    async def go():
        srv = ipc.Server(bridge=None)
        srv.router = router
        async with websockets.serve(srv.handle, "127.0.0.1", 0) as s:
            port = s.sockets[0].getsockname()[1]
            worker = await _client(port, srv.token, "perception")
            overlay = await _client(port, srv.token, "overlay")
            await worker.send(json.dumps({"type": "transcript", "hypotheses": hyps, "scores": [-1.0] * len(hyps),
                                          "source": "lipreading", "experimental": True}))
            got = await _next_transcript(worker), await _next_transcript(overlay)
            await worker.close(); await overlay.close()
            return got

    return asyncio.run(go())


def test_hypotheses_are_routed_logged_and_broadcast(monkeypatch, tmp_path):
    router = FakeRouter({"explain_this": 0.9, "open_this": 0.05, "uncertain": 0.05})
    to_worker, to_overlay = _run(router, ["explain this", "explain miss"], monkeypatch, tmp_path)
    assert to_worker == to_overlay
    assert to_worker["command"] == "explain_this" and to_worker["uncertain"] is False
    assert to_worker["probability"] == 0.9 and to_worker["experimental"] is True
    assert to_worker["source"] == "lipreading" and to_worker["role"] == "user"
    assert "1. explain this\n2. explain miss" in router.states[0]
    entry = json.loads(drex_log().read_text().splitlines()[-1])
    assert entry["purpose"] == "command_route"


def test_unclear_hypotheses_come_back_uncertain(monkeypatch, tmp_path):
    router = FakeRouter({"scroll_up": 0.42, "scroll_down": 0.38, "uncertain": 0.2})
    res, _ = _run(router, ["scroll"], monkeypatch, tmp_path)
    assert res["command"] == "uncertain" and res["uncertain"] is True


def test_hypotheses_message_is_validated():
    ok = inbound_adapter.validate_python({"type": "transcript", "hypotheses": ["next"]})
    assert ok.source == "lipreading" and ok.experimental is True
    for bad in ({"type": "transcript", "hypotheses": []}, {"type": "transcript", "hypotheses": ["x" * 201]},
                {"type": "transcript", "hypotheses": ["a"] * 11}):
        try:
            inbound_adapter.validate_python(bad)
        except Exception:
            continue
        raise AssertionError(f"accepted {bad}")
