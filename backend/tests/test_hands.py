"""reach hands on|off|status and the `hands` message: a harmless stand-in child (never real hands mode)."""

from __future__ import annotations


import pytest

from reach import hands, ipc


@pytest.fixture
def fake_child(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    monkeypatch.setenv("REACH_HANDS_CMD", "sleep 30")
    yield
    hands.stop()


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
        assert st["on"] and json.loads((rd / "run" / "hands_request.json").read_text())["name"] == "wave"
        await ov.send(json.dumps({"type": "hands_record", "name": "", "action": {"kind": "top"}}))
        assert "name" in (await _next(ov, "error"))["message"]
        (rd / "gestures.json").write_text(json.dumps({"gestures": [{"name": "wave"}]}))
        await ov.send(json.dumps({"type": "hands_forget", "name": "wave"}))
        await _next(ov, "hands_state")
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
