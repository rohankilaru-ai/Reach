"""see_screen and Drex-checked hands-free speech: key-free (fake screencapture runner, fake VL client, fake Drex)."""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
from PIL import Image

from reach import addressed, intent
from reach.ledger import ReceiptBuilder
from reach.tools import screen

INFO = {"screen": [1512, 982], "scale": 2, "front": "Terminal",
        "window": {"id": 42, "owner": "Terminal", "b": [100, 50, 800, 600]}}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    return tmp_path


def runner(size=(3024, 1964), blank=False, rc=0, seen=None):
    def run(cmd, timeout):
        if seen is not None:
            seen.append(cmd)
        if rc != 0:
            return rc
        im = Image.new("RGB", size, (0, 0, 0))
        if not blank:
            for x in range(0, size[0], 50):
                for y in range(0, size[1], 7):
                    im.putpixel((x, y), (255, 255, 255))
            im.paste((200, 30, 30), (10, 10, size[0] // 2, size[1] // 3))
        im.save(cmd[-1], "PNG")
        return 0
    return run


def fake_vl(seen=None):
    def send(body):
        if seen is not None:
            seen.append(body)
        return {"choices": [{"message": {"content": "A terminal window."}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 8, "estimated_cost": 0.0002}}
    return send


def test_blank_image_is_detected(tmp_path):
    p = tmp_path / "b.png"
    Image.new("RGB", (400, 300), (0, 0, 0)).save(p)
    assert screen.is_blank(p)
    runner()(["x", str(tmp_path / "ok.png")], 1)
    assert not screen.is_blank(tmp_path / "ok.png")


@pytest.mark.parametrize("kw", [{"blank": True}, {"rc": 1}])
def test_blank_or_failed_capture_says_permission_and_leaves_nothing(root, monkeypatch, kw):
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    with pytest.raises(screen.NeedsPermission) as e:
        screen.capture("display", run=runner(**kw), info=lambda: INFO)
    assert str(e.value).startswith("needs you: allow Screen Recording for Terminal")
    assert list((root / "captures").iterdir()) == []


def test_tool_returns_permission_message_not_crash(root, monkeypatch):
    monkeypatch.setattr(screen, "see_screen", lambda q, t: (_ for _ in ()).throw(screen.NeedsPermission("needs you: x")))
    out = asyncio.run(screen.SeeScreen().run(screen.SeeScreenArgs(question="what?"), None))
    assert out.status == "error" and out.content.startswith("needs you")


def test_downsizes_and_writes_sidecar(root):
    seen: list = []
    png, meta, note = screen.capture("display", run=runner(seen=seen), info=lambda: INFO)
    assert seen[0][:3] == ["/usr/sbin/screencapture", "-x", "-m"]
    with Image.open(png) as im:
        assert max(im.size) == screen.MAX_SIDE and im.size == (1568, round(1964 * 1568 / 3024))
    side = json.loads(png.with_suffix(".json").read_text())
    assert side["region"] == [0, 0, 1512, 982] and side["display"] == "main" and side["target"] == "display"
    assert side["scale"] == pytest.approx(1568 / 1512, rel=1e-3) and abs(side["created"] - time.time()) < 5
    assert screen.read_sidecar(png) == side and note == ""
    assert screen.to_points(side, 1568, 0) == pytest.approx((1512, 0), abs=0.01)


def test_window_target_uses_window_id_and_region(root):
    seen: list = []
    png, meta, _ = screen.capture("window", run=runner(size=(1200, 900), seen=seen), info=lambda: INFO)
    assert "-l" in seen[0] and seen[0][seen[0].index("-l") + 1] == "42"
    assert meta["region"] == [100, 50, 800, 600] and meta["display"] == "window:42" and meta["scale"] == 1.5
    assert screen.to_points(meta, 0, 0) == (100, 50)


def test_window_target_falls_back_to_display_and_says_so(root):
    _, meta, note = screen.capture("window", run=runner(), info=lambda: {**INFO, "window": None})
    assert meta["target"] == "display" and "whole display" in note


def test_see_screen_sends_capture_to_vision_and_logs(root):
    seen: list = []
    answer, png = screen.see_screen("what is on screen?", run=runner(), info=lambda: INFO, send=fake_vl(seen))
    assert answer == "A terminal window." and png.parent == root / "captures"
    assert seen[0]["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    log = [json.loads(x) for x in (root / "logs" / "drex.jsonl").read_text().splitlines()]
    assert log[-1]["type"] == "vision" and log[-1]["ok"] and log[-1]["cost_usd"] == 0.0002


def test_read_sidecar_rejects_bad_or_missing(tmp_path):
    p = tmp_path / "a.png"
    assert screen.read_sidecar(p) is None
    p.with_suffix(".json").write_text('{"region": [1, 2], "scale": 2}')
    assert screen.read_sidecar(p) is None


def test_retention_deletes_only_old_captures(root):
    d = root / "captures"
    d.mkdir()
    old, new, other = d / "screen-1.png", d / "screen-2.png", d / "keep.txt"
    for p in (old, old.with_suffix(".json"), new, other):
        p.write_text("x")
    t = time.time()
    os.utime(old, (t - 7200, t - 7200))
    os.utime(old.with_suffix(".json"), (t - 7200, t - 7200))
    os.utime(other, (t - 7200, t - 7200))
    assert screen.prune_captures() == 2
    assert sorted(p.name for p in d.iterdir()) == ["keep.txt", "screen-2.png"]


def test_receipt_screen_captured_only_on_real_capture():
    b = ReceiptBuilder("t")
    b.feed({"type": "tool_call_end", "name": "see_screen", "status": "error", "content": "needs you: allow"})
    assert b.receipt().screen_captured is False
    b.feed({"type": "tool_call_end", "name": "see_screen", "status": "ok", "content": "screen_captured=true image=x"})
    assert b.receipt().screen_captured is True


# ---- intent: no circle needed ----


def _resp(kind, p, **flags):
    rest = (1 - p) / (len(intent.KINDS) - 1)
    answers = {"kind": {"probabilities": {k: (p if k == kind else rest) for k in intent.KINDS}}}
    for n in intent.FLAGS:
        answers[n] = {"noul": flags.get(n, 0.01)}
    return {"answers": answers, "usage": {"input_tokens": 700}}


def _post(r):
    return lambda url, headers, body, timeout: r


def test_screen_question_without_selection_hints_see_screen(monkeypatch):
    monkeypatch.delenv("REACH_AUTO_SCREEN", raising=False)
    it = intent.understand("what is on my screen?", post=_post(_resp("screen_question", 0.9)))
    assert it.action == "see_screen"
    add = intent.prompt_addendum(it)
    assert "see_screen" in add and it.hint_used
    it2 = intent.understand("what is this?", post=_post(_resp("other", 0.4, needs_screen_region=0.95)))
    assert it2.action == "see_screen"


def test_selection_present_never_see_screen():
    it = intent.understand("what is this?", "selected words", post=_post(_resp("screen_question", 0.9,
                                                                             needs_screen_region=0.95)))
    assert it.action == "hint" and "see_region" in intent.prompt_addendum(it)


def test_risky_still_wins_over_see_screen():
    it = intent.understand("delete this", post=_post(_resp("mac_control", 0.9, needs_screen_region=0.95,
                                                           risky_or_irreversible=0.9)))
    assert it.action == "confirm"


def test_auto_screen_off_keeps_circle_short_circuit(monkeypatch):
    monkeypatch.setenv("REACH_AUTO_SCREEN", "0")
    it = intent.understand("what is this?", post=_post(_resp("other", 0.4, needs_screen_region=0.95)))
    assert it.action == "needs_selection"


# ---- addressed ----


def _addr(p, pc=0.9, cmd="scroll_down", cp=0.9):
    probs = {k: (cp if k == cmd else (1 - cp) / 11) for k in (*addressed.commands.COMMANDS, "uncertain")}
    return {"answers": {"addressed_to_assistant": {"noul": p}, "is_command_or_question": {"noul": pc},
                        "command": {"probabilities": probs}}, "usage": {"input_tokens": 300}, "model": "drex-max-v1.1"}


def test_wake_word_only_at_start():
    assert addressed.wake_matched("Reach, scroll down")
    assert addressed.wake_matched("hey reach what time is it")
    assert not addressed.wake_matched("I need to reach out to Sam")
    assert not addressed.wake_matched("the Reach app is neat")
    assert not addressed.wake_matched("reaching for it")


def test_decision_rule():
    assert addressed.decide(0.86, False) == (True, "p_addressed 0.86 >= 0.85")
    assert addressed.decide(0.84, False)[0] is False
    assert addressed.decide(0.55, True)[0] is True
    assert addressed.decide(0.45, True)[0] is False
    assert addressed.decide(0.99, True, "timeout")[0] is False
    assert addressed.decide(None, True)[0] is False


def test_one_request_three_questions_and_command():
    seen = []

    def post(url, headers, body, timeout):
        seen.append(body)
        return _addr(0.95)
    r = addressed.check("scroll down please", post=post)
    assert set(seen[0]["questions"]) == {"addressed_to_assistant", "is_command_or_question", "command"}
    assert r.addressed and r.command == "scroll_down" and r.p_addressed == 0.95


def test_not_addressed_has_no_command_and_errors_are_not_addressed():
    r = addressed.check("so I told him to scroll down", post=_post(_addr(0.2)))
    assert not r.addressed and r.command is None

    def boom(*a):
        raise RuntimeError("HTTP 500")
    r = addressed.check("Reach scroll down", post=boom)
    assert not r.addressed and r.error and r.wake_matched


def test_timeout_is_not_addressed():
    def slow(*a):
        time.sleep(0.5)
        return _addr(0.99)
    r = asyncio.run(addressed.check_async("scroll down", False, post=slow, timeout=0.1))
    assert not r.addressed and r.error == "timeout"


def test_log_entry_purpose_addressed(tmp_path):
    p = tmp_path / "drex.jsonl"
    addressed.log(addressed.check("Reach, next", post=_post(_addr(0.7, cmd="next"))), path=p)
    ev = json.loads(p.read_text())
    assert ev["type"] == "decision" and ev["purpose"] == "addressed" and ev["action"] == "addressed"
    assert ev["wake_matched"] is True


def test_server_answers_utterance(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    import websockets

    from reach.ipc import Server

    srv = Server(bridge=None)
    srv.addressed_post = _post(_addr(0.97, cmd="stop"))

    async def go():
        async with websockets.serve(srv.handle, "127.0.0.1", 0) as s:
            port = s.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "hello", "token": srv.token}))
                await ws.recv()
                await ws.send(json.dumps({"type": "utterance", "text": "stop", "source": "always_listening"}))
                return json.loads(await asyncio.wait_for(ws.recv(), 5))
    m = asyncio.run(go())
    assert m["type"] == "addressed" and m["addressed"] is True and m["command"] == "stop" and m["p_addressed"] == 0.97
