"""point_at (guide on screen): key-free tests with a fake vision client and a fake overlay emitter."""

import asyncio
import json
import os
import struct
import time
import zlib

import pytest

from reach.contracts import Ask, Selection
from reach.tools import guide


def png(w: int, h: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def reply(steps) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"steps": steps})}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "estimated_cost": 0.0001}}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    (tmp_path / "captures").mkdir()
    return tmp_path


@pytest.fixture
def sent():
    out: list[dict] = []
    guide.set_emitter(lambda m: out.append(json.loads(m)))
    yield out
    guide.cancel()
    guide.set_emitter(None)


def test_box_to_screen_with_retina_scale():
    # 2x capture: region 400x300 points at (100, 200) -> image 800x600 px. Box centre (500, 500) normalised.
    assert guide.to_screen([400, 400, 600, 600], [100, 200, 400, 300], 800, 600) == (300.0, 350.0)
    # top-left quarter centre
    assert guide.to_screen([0, 0, 500, 500], [100, 200, 400, 300], 800, 600) == (200.0, 275.0)
    # 1x capture gives the same screen point (normalised boxes do not depend on the pixel size)
    assert guide.to_screen([400, 400, 600, 600], [100, 200, 400, 300], 400, 300) == (300.0, 350.0)


def test_outliers_dropped_or_clamped():
    region = [0, 0, 100, 100]
    assert guide.to_screen([1500, 10, 1600, 20], region, 200, 200) is None  # far outside: dropped
    assert guide.to_screen([-300, 10, -100, 20], region, 200, 200) is None
    assert guide.to_screen([600, 600, 400, 700], region, 200, 200) is None  # inverted
    assert guide.to_screen([float("nan"), 0, 1, 1], region, 200, 200) is None
    assert guide.to_screen([990, 990, 1010, 1010], region, 200, 200) == (99.5, 99.5)  # slightly out: clamped to 1000


def test_plan_orders_and_drops(root):
    steps = [
        {"label": "Type your email here", "target": "Email", "box": [100, 100, 300, 150]},
        {"label": "Ghost field", "target": "?", "box": [2000, 2000, 2100, 2100]},
        {"label": "Click Submit", "target": "Submit", "box": [700, 800, 900, 900]},
    ]
    out = guide.plan(png(80, 60), [10, 20, 40, 30], "fill this in", 6, send=lambda b: reply(steps))
    assert [s["n"] for s in out] == [1, 2]
    assert [s["label"] for s in out] == ["Type your email here", "Click Submit"]
    assert out[0]["x"] == pytest.approx(10 + 0.2 * 40) and out[0]["y"] == pytest.approx(20 + 0.125 * 30, abs=0.06)
    entry = json.loads((root / "logs" / "drex.jsonl").read_text().splitlines()[-1])
    assert entry["purpose"] == "point_at" and entry["steps"] == 2 and entry["dropped"] == 1


def test_plan_handles_garbage_and_empty(root):
    for text in ["", "sorry, I can't", '{"steps": []}', '{"steps": [{"label": "x"}]}', "```json\n{oops"]:
        data = {"choices": [{"message": {"content": text}}]}
        assert guide.plan(png(10, 10), [0, 0, 10, 10], "q", 6, send=lambda b, d=data: d) == []


def test_request_asks_strict_json_and_no_guessing():
    body = guide.build_request(png(4, 4), "how do I fill this in", 4)
    sys_msg = body["messages"][0]["content"]
    assert "STRICT JSON" in sys_msg and "never invent" in sys_msg and "0-1000" in sys_msg
    assert "at most 4 steps" in body["messages"][1]["content"][1]["text"]


def test_steps_emitted_in_order_with_spacing(sent):
    gaps: list[float] = []

    async def fake_sleep(s):
        gaps.append(s)

    steps = [{"n": i + 1, "label": f"step {i + 1}", "x": 10.0 * i, "y": 5.0} for i in range(3)]

    async def go():
        await guide.start("t1", steps, gap_s=3.5, sleep=fake_sleep)

    asyncio.run(go())
    assert [m["label"] for m in sent] == ["1. step 1", "2. step 2", "3. step 3"]
    assert all(m["type"] == "pointer" and m["task_id"] == "t1" for m in sent)
    assert [m["x"] for m in sent] == [0.0, 10.0, 20.0]
    assert gaps == [3.5, 3.5]


def test_abort_cancels_pending_pointers(sent):
    steps = [{"n": i + 1, "label": f"s{i}", "x": 1.0, "y": 1.0} for i in range(4)]

    async def go():
        t = guide.start("t2", steps, gap_s=0.05)
        await asyncio.sleep(0.01)
        assert guide.cancel("t2") == 1
        await asyncio.sleep(0.2)
        assert t.cancelled()

    asyncio.run(go())
    assert len(sent) == 1


def test_new_guide_replaces_old(sent):
    steps = [{"n": i + 1, "label": f"s{i}", "x": 1.0, "y": 1.0} for i in range(4)]

    async def go():
        old = guide.start("a", steps, gap_s=0.05)
        await asyncio.sleep(0.01)
        new = guide.start("b", steps[:1], gap_s=0.05)
        await asyncio.sleep(0.2)
        assert old.cancelled() and new.done()

    asyncio.run(go())
    assert [m["task_id"] for m in sent] == ["a", "b"]


def test_bridge_task_abort_cancels_its_guide(sent):
    from reach.bridge import Task

    task = Task(Ask(text="show me"))
    steps = [{"n": i + 1, "label": "s", "x": 1.0, "y": 1.0} for i in range(3)]

    async def go():
        t = guide.start(task.id, steps, gap_s=0.05)
        await asyncio.sleep(0.01)
        task.abort()
        await asyncio.sleep(0.2)
        assert t.cancelled()

    asyncio.run(go())
    assert len(sent) == 1


def run_tool(sel, send=None, monkeypatch=None):
    from reach.ledger import CURRENT_TASK

    if send is not None:
        monkeypatch.setattr(guide.vision, "call", send)
        monkeypatch.setattr(guide, "plan", lambda *a, **k: guide.__dict__["_real_plan"](*a, **k, send=send))

    async def go():
        CURRENT_TASK.set("tid")
        guide.CURRENT_SELECTION.set(sel)
        out = await guide.PointAt().run(guide.PointAtArgs(question="how do I fill this in"), None)
        await asyncio.sleep(0.05)
        return out

    return asyncio.run(go())


guide._real_plan = guide.plan


def test_no_selection_asks_to_circle(sent):
    out = run_tool(None)
    assert out.status == "error" and "circle" in out.content and not sent
    out = run_tool(Selection(text="just text"))
    assert out.status == "error" and "circle" in out.content and not sent


def test_stale_capture_refused(root, sent, monkeypatch):
    p = root / "captures" / "old.png"
    p.write_bytes(png(20, 20))
    old = time.time() - 600
    os.utime(p, (old, old))
    called = []
    out = run_tool(Selection(region=[0, 0, 10, 10], image_path=str(p)),
                   send=lambda b: called.append(1) or reply([]), monkeypatch=monkeypatch)
    assert out.status == "error" and "old" in out.content and "circle" in out.content
    assert not called and not sent


def test_tool_points_and_reports_steps(root, sent, monkeypatch):
    p = root / "captures" / "form.png"
    p.write_bytes(png(200, 100))  # 2x capture of a 100x50-point region at (500, 300)
    steps = [{"label": "Type your email here", "target": "Email", "box": [0, 0, 200, 200]}]
    out = run_tool(Selection(region=[500, 300, 100, 50], image_path=str(p)),
                   send=lambda b: reply(steps), monkeypatch=monkeypatch)
    assert out.status == "ok" and "1. Type your email here" in out.content and "did not click" in out.content
    assert sent == [{"type": "pointer", "task_id": "tid", "x": 510.0, "y": 305.0, "label": "1. Type your email here",
                     "screen": None, "state": None, "source": None}]


def test_no_pointer_when_model_returns_nothing(root, sent, monkeypatch):
    p = root / "captures" / "form.png"
    p.write_bytes(png(20, 20))
    out = run_tool(Selection(region=[0, 0, 10, 10], image_path=str(p)),
                   send=lambda b: reply([]), monkeypatch=monkeypatch)
    assert out.status == "ok" and "nothing" in out.content and not sent


def test_registered_and_hinted():
    from reach.bridge import prompt_for
    from reach.intent import TOOL_FAMILY
    from reach.intern_ext import ReachTools

    assert "point_at" in [t.name for t in ReachTools().tools()]
    assert "point_at" in TOOL_FAMILY["screen_question"] and "see_region" in TOOL_FAMILY["screen_question"]
    p = prompt_for(Ask(text="show me how", selection=Selection(image_path=".reach/captures/f.png", region=[0, 0, 1, 1])))
    assert "point_at" in p and "see_region" in p


def test_relay_broadcast_reaches_every_overlay():
    from reach.relay import Relay

    r = Relay()
    a, b, c = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
    r.join(1, "overlay", a)
    r.join(2, "overlay", b)
    r.join(3, None, c)
    assert r.broadcast("x") == 2 and a.get_nowait() == "x" and b.get_nowait() == "x" and c.empty()
