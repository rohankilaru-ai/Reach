"""Key-free tests for the pinch relay: select/pointer from a perception client reach overlay clients only."""

from __future__ import annotations

import asyncio
import json

import websockets

from reach import ipc, relay
from reach.contracts import Hello, Selection, outbound_adapter


class QuickBridge:
    grounds = False

    async def run(self, task, sink):
        return task.builder.receipt()


async def _client(port, token, role=None):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(Hello(token=token, role=role).model_dump_json())
    assert json.loads(await ws.recv())["type"] == "ready"
    return ws


async def _next(ws, kind, timeout=2.0):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if m["type"] == kind:
            return m


async def _nothing(ws, timeout=0.3) -> bool:
    try:
        await asyncio.wait_for(ws.recv(), timeout)
    except asyncio.TimeoutError:
        return True
    return False


def _run(body, monkeypatch, tmp_path):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    (tmp_path / ".reach").mkdir()

    async def main():
        srv = ipc.Server(QuickBridge())
        async with websockets.serve(srv.handle, "127.0.0.1", 0) as s:
            await body(srv, s.sockets[0].getsockname()[1], tmp_path / ".reach")

    asyncio.run(main())


def test_select_and_pointer_relay_to_overlays_only(monkeypatch, tmp_path):
    async def body(srv, port, rd):
        ov = await _client(port, srv.token, "overlay")
        cli = await _client(port, srv.token)
        cam = await _client(port, srv.token, "perception")
        img = rd / "selection-1.png"
        img.write_bytes(b"png")
        await cam.send(json.dumps({"type": "select", "selection": {
            "text": "line one\nline two", "region": [10, 20, 300, 40], "image_path": str(img)}}))
        m = await _next(ov, "selection")
        assert m["selection"]["text"].startswith("line one") and m["selection"]["source"] == "pinch"
        outbound_adapter.validate_python(m)
        await cam.send(json.dumps({"type": "pointer", "x": 100.5, "y": 200, "state": "pinch"}))
        p = await _next(ov, "pointer")
        assert (p["x"], p["y"], p["state"], p["source"]) == (100.5, 200, "pinch", "hand")
        assert await _nothing(cli)  # non-overlay clients get no relay
        assert await _nothing(cam)  # never echoed to the sender
        for ws in (ov, cli, cam):
            await ws.close()

    _run(body, monkeypatch, tmp_path)


def test_bad_select_and_pointer_are_rejected(monkeypatch, tmp_path):
    async def body(srv, port, rd):
        ov = await _client(port, srv.token, "overlay")
        cam = await _client(port, srv.token, "perception")
        await cam.send(json.dumps({"type": "select", "selection": {"text": "x", "image_path": "/etc/passwd"}}))
        assert "under .reach" in (await _next(cam, "error"))["message"]
        await cam.send(json.dumps({"type": "select", "selection": {"text": "x", "image_path": str(rd / ".." / "a.png")}}))
        assert "under .reach" in (await _next(cam, "error"))["message"]
        await cam.send('{"type": "select", "selection": {"region": [NaN, 0, 1, 1]}}')
        assert (await _next(cam, "error"))["type"] == "error"
        await cam.send('{"type": "pointer", "x": Infinity, "y": 1}')
        assert "bad message" in (await _next(cam, "error"))["message"]
        await cam.send(json.dumps({"type": "select", "selection": {"region": [0, 0, -5, 1]}}))
        assert "width/height" in (await _next(cam, "error"))["message"]
        assert await _nothing(ov)
        # a client without the perception role cannot drive the pointer
        anon = await _client(port, srv.token)
        await anon.send(json.dumps({"type": "pointer", "x": 1, "y": 1}))
        assert "role perception" in (await _next(anon, "error"))["message"]
        assert await _nothing(ov)
        assert srv.relay.latest is None

    _run(body, monkeypatch, tmp_path)


def test_ask_uses_latest_pinch_selection_and_clear(monkeypatch, tmp_path):
    async def body(srv, port, rd):
        cam = await _client(port, srv.token, "perception")
        cli = await _client(port, srv.token, "cli")
        await cam.send(json.dumps({"type": "select", "selection": {"text": "picked by hand"}}))
        await asyncio.sleep(0.1)
        await cli.send(json.dumps({"type": "ask", "text": "explain"}))
        r = await _next(cli, "receipt")
        assert r["selection_source"] == "pinch"
        log = [json.loads(line) for line in (rd / "logs" / "relay.jsonl").read_text().splitlines()]
        asks = [x for x in log if x["kind"] == "ask"]
        assert asks[-1]["selection"] == "pinch-latest" and asks[-1]["chars"] == len("picked by hand")
        # an ask with its own selection keeps it
        await cli.send(json.dumps({"type": "ask", "text": "x", "selection": {"text": "mine"}}))
        assert (await _next(cli, "receipt"))["selection_source"] is None
        # the overlay's clear button: an empty select drops the latest
        ov = await _client(port, srv.token, "overlay")
        await ov.send(json.dumps({"type": "select", "selection": {}}))
        await asyncio.sleep(0.1)
        assert srv.relay.latest is None
        await cli.send(json.dumps({"type": "ask", "text": "again"}))
        assert (await _next(cli, "receipt"))["selection_source"] is None

    _run(body, monkeypatch, tmp_path)


def test_overlay_drag_select_is_not_relayed_or_stored(monkeypatch, tmp_path):
    async def body(srv, port, rd):
        ov1 = await _client(port, srv.token, "overlay")
        ov2 = await _client(port, srv.token, "overlay")
        await ov1.send(json.dumps({"type": "select", "selection": {"region": [0, 0, 10, 10]}}))
        assert await _nothing(ov2)
        assert srv.relay.latest is None

    _run(body, monkeypatch, tmp_path)


def test_latest_expires(monkeypatch):
    r = relay.Relay()
    r.latest, r.latest_at = Selection(text="t", source="pinch"), 0.0  # monotonic 0 is long ago
    from reach.contracts import Ask

    ask, how = r.attach(Ask(text="q"))
    assert ask.selection is None and how is None


def test_hello_role_is_optional():
    assert Hello(token="t").role is None
    assert json.loads(Hello(token="t").model_dump_json())["role"] is None
