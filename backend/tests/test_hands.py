"""reach hands on|off|status and the `hands` message: a harmless stand-in child (never real hands mode)."""

from __future__ import annotations


import pytest

from reach import hands, ipc


@pytest.fixture
def fake_child(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    monkeypatch.setenv("REACH_HANDS_CMD", "sleep 30")
    monkeypatch.setattr(hands, "stop_perception", lambda: None)   # never pkill anything real in tests
    yield
    hands.stop()
    hands.set_emitter(None)


def test_on_status_off(fake_child, tmp_path):
    assert hands.status() == {"on": False, "pid": None}
    st = hands.start(preview=False)
    assert st["on"] and (tmp_path / "run" / "hands.pid").exists()
    assert hands.start()["pid"] == st["pid"]          # idempotent: one child only
    assert hands.status()["on"]
    assert hands.stop() == {"on": False, "pid": None}
    assert not (tmp_path / "run" / "hands.pid").exists() and not hands.status()["on"]


def test_stale_pidfile_is_off(fake_child, tmp_path):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "hands.pid").write_text("999999")
    assert hands.status() == {"on": False, "pid": None}


def test_default_command_is_hands_mode_with_control(monkeypatch):
    monkeypatch.delenv("REACH_HANDS_CMD", raising=False)
    cmd = hands.command()
    assert cmd[-3:] == ["-m", "hands_mode.run", "--control"]


def test_ipc_hands_message_parsing():
    assert ipc._hands_msg('{"type":"hands","on":true}') is True
    assert ipc._hands_msg('{"type":"hands","on":false}') is False
    assert ipc._hands_msg('{"type":"hands","on":"yes"}') is None
    assert ipc._hands_msg('{"type":"ask","text":"hands"}') is None
    assert ipc._hands_msg("not json") is None


def test_watcher_broadcasts_when_child_exits_on_its_own(fake_child, tmp_path, monkeypatch):
    """A fake child that exits by itself (like an open-palm exit) -> off is announced and the pidfile is gone."""
    import threading

    monkeypatch.setenv("REACH_HANDS_CMD", "sleep 0.3")
    seen: list[dict] = []
    done = threading.Event()

    def emit(st):
        seen.append(st)
        if not st["on"]:
            done.set()

    hands.set_emitter(emit)
    st = hands.start(preview=False)
    assert st["on"] and seen[0]["on"] and seen[0]["reason"] == "started"
    assert done.wait(5), "watcher never announced the exit"
    assert seen[-1]["on"] is False and seen[-1]["reason"].startswith("exited")
    assert not (tmp_path / "run" / "hands.pid").exists() and hands.status()["on"] is False


def test_requested_stop_is_announced_once_as_stopped(fake_child, monkeypatch):
    import time

    seen: list[dict] = []
    hands.set_emitter(seen.append)
    hands.start(preview=False)
    hands.stop()
    time.sleep(0.3)   # the watcher must not add a second "exited"
    offs = [s for s in seen if not s["on"]]
    assert offs == [{"on": False, "pid": None, "reason": "stopped"}]


def test_start_stops_old_perception_worker_and_twice_is_noop(fake_child, monkeypatch):
    calls = []
    monkeypatch.setattr(hands, "stop_perception", lambda: calls.append(1))
    a = hands.start(preview=False)
    b = hands.start(preview=False)
    assert a["pid"] == b["pid"] and calls == [1]


def test_server_emitter_reaches_overlays(fake_child):
    import asyncio
    import json as _json

    async def run():
        srv = ipc.Server(bridge=None)
        q: asyncio.Queue = asyncio.Queue()
        srv.relay.join(1, "overlay", q)
        srv.loop = asyncio.get_running_loop()
        await asyncio.to_thread(srv._hands_emit, {"on": False, "pid": None, "reason": "exited"})
        return _json.loads(await asyncio.wait_for(q.get(), 2))

    msg = asyncio.run(run())
    assert msg == {"type": "hands_state", "on": False, "pid": None, "reason": "exited"}


def test_state_line_parsing_and_phase_mapping():
    assert hands.parse_state_line("[hands] dragging\n") == "dragging"
    assert hands.parse_state_line("Hands mode ON: your hand...") is None
    cases = {
        "waiting for hand": "no_hand",
        "point to move · pinch to click": "pointing",
        "clutch · point to move": "pointing",
        "scrolling": "pointing",
        "pinch": "pinching",
        "pinch · click": "pinching",
        "double click": "pinching",
        "dragging": "dragging",
        "paused · you moved the mouse": "paused",
        "open palm · hold to leave": "leaving",
        "hand leaving · pinch ignored": "leaving",
        "camera stalled": None,
    }
    for state, phase in cases.items():
        assert hands.phase_of(state) == phase, state


def test_watcher_broadcasts_phases_from_child_stdout(fake_child, monkeypatch):
    """A fake child printing hands_mode state lines -> phases broadcast in order, exit reason 'open palm'."""
    import threading

    script = "import time\nfor s in ['waiting for hand','point to move','dragging','open palm · hold to leave']:\n" \
             "    print('[hands] '+s, flush=True); time.sleep(0.02)\n"
    monkeypatch.setattr(hands, "command", lambda: [__import__("sys").executable, "-c", script])
    seen: list[dict] = []
    done = threading.Event()

    def emit(st):
        seen.append(st)
        if not st["on"]:
            done.set()

    hands.set_emitter(emit)
    hands.start()
    assert done.wait(10)
    phases = [s["phase"] for s in seen if s.get("phase")]
    assert phases == ["no_hand", "pointing", "dragging", "leaving"]
    assert seen[-1]["reason"] == "open palm"


# ---------------------------------------------------------------- custom gestures (EXPERIMENTAL)

import asyncio  # noqa: E402
import json  # noqa: E402

from test_relay import _client, _next, _nothing, _run  # noqa: E402


def test_request_record_starts_hands_and_writes_request(fake_child, tmp_path):
    st = hands.request_record("swipe", {"kind": "top", "value": ""})
    assert st["on"]
    req = json.loads((tmp_path / "run" / "hands_request.json").read_text())
    assert req["op"] == "record" and req["name"] == "swipe" and req["action"] == {"kind": "top", "value": ""}
    assert len(req["id"]) >= 16
    for bad_name, bad_action in [("", {"kind": "top"}), ("x" * 41, {"kind": "top"}), ("ok", {"kind": "rm"}),
                                 ("ok", {"kind": "keys", "value": ""}), ("ok", "top")]:
        with pytest.raises(ValueError):
            hands.request_record(bad_name, bad_action)


def test_forget_removes_only_that_gesture(fake_child, tmp_path):
    hands.gestures_path().write_text(json.dumps({"gestures": [{"name": "a", "x": 1}, {"name": "b", "x": 2}]}))
    hands.forget("a")
    assert [g["name"] for g in json.loads(hands.gestures_path().read_text())["gestures"]] == ["b"]
    hands.forget("missing")   # no-op
    assert not list(tmp_path.glob("*.tmp"))


def test_gesture_event_only_command_and_ask():
    assert json.loads(hands.gesture_event({"name": "g", "action": {"kind": "ask", "value": "summarise"}})) == {
        "type": "hands_gesture", "name": "g", "action": {"kind": "ask", "value": "summarise"}}
    assert hands.gesture_event({"name": "g", "action": {"kind": "top", "value": ""}}) is None
    assert hands.gesture_event({"name": "", "action": {"kind": "command", "value": "next"}}) is None


def test_ipc_record_forget_and_gesture_relay(monkeypatch, tmp_path):
    monkeypatch.setenv("REACH_HANDS_CMD", "sleep 30")

    async def body(srv, port, rd):
        ov = await _client(port, srv.token, "overlay")
        cam = await _client(port, srv.token, "perception")
        await ov.send(json.dumps({"type": "hands_record", "name": "wave", "action": {"kind": "command",
                                                                                     "value": "next"}}))
        st = await _next(ov, "hands_state")
        for _ in range(100):   # the "started" broadcast can arrive before the request file is written
            if (rd / "run" / "hands_request.json").exists():
                break
            await asyncio.sleep(0.02)
        assert st["on"] and json.loads((rd / "run" / "hands_request.json").read_text())["name"] == "wave"
        await ov.send(json.dumps({"type": "hands_record", "name": "", "action": {"kind": "top"}}))
        assert "name" in (await _next(ov, "error"))["message"]
        (rd / "gestures.json").write_text(json.dumps({"gestures": [{"name": "wave"}]}))
        await ov.send(json.dumps({"type": "hands_forget", "name": "wave"}))
        for _ in range(100):   # other hands_state broadcasts (start, phases) can arrive first: wait for the file
            if json.loads((rd / "gestures.json").read_text()) == {"gestures": []}:
                break
            await asyncio.sleep(0.02)
        assert json.loads((rd / "gestures.json").read_text()) == {"gestures": []}
        await cam.send(json.dumps({"type": "hands_gesture", "name": "wave",
                                   "action": {"kind": "command", "value": "next"}}))
        m = await _next(ov, "hands_gesture")
        assert m == {"type": "hands_gesture", "name": "wave", "action": {"kind": "command", "value": "next"}}
        assert await _nothing(cam)                       # never echoed to the sender
        await ov.send(json.dumps({"type": "hands_gesture", "name": "x", "action": {"kind": "ask", "value": "y"}}))
        await _next(ov, "error")                        # overlays cannot inject gestures
        for ws in (ov, cam):
            await ws.close()
        await asyncio.to_thread(hands.stop)

    _run(body, monkeypatch, tmp_path)
