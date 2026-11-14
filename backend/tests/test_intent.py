"""Drex task understanding: key-free, with a fake Drex client."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from reach import intent
from reach.contracts import Ask, Selection
from reach.ledger import format_log_line


def resp(kind: str, p: float, **flags: float) -> dict:
    rest = (1 - p) / (len(intent.KINDS) - 1)
    probs = {k: (p if k == kind else rest) for k in intent.KINDS}
    answers = {"kind": {"type": "choice", "choice": kind, "probabilities": probs}}
    for n in intent.FLAGS:
        answers[n] = {"type": "noul", "noul": flags.get(n, 0.01)}
    return {"model": "drex-max-v1.1", "answers": answers, "usage": {"input_tokens": 700, "output_tokens": 400}}


def fake(r: dict, seen: list | None = None):
    def post(url, headers, body, timeout):
        if seen is not None:
            seen.append(body)
        return r
    return post


def test_one_request_with_all_typed_questions():
    seen: list = []
    intent.understand("open spotify", post=fake(resp("mac_control", 0.9), seen))
    assert len(seen) == 1
    qs = seen[0]["questions"]
    assert qs["kind"]["type"] == "choice" and set(qs["kind"]["criteria"]) == set(intent.KINDS)
    assert {n: q["type"] for n, q in qs.items() if n != "kind"} == {n: "noul" for n in intent.FLAGS}
    assert "Selection present: none" in seen[0]["state"]


@pytest.mark.parametrize("kind", [k for k in intent.KINDS if k != "other"])
def test_kind_mapping_and_hint(kind):
    it = intent.understand("x", "some selected text", post=fake(resp(kind, 0.93)))
    assert it.kind == kind and it.action == "hint"
    add = intent.prompt_addendum(it)
    assert add.startswith("Hint: Drex") and "p=0.93" in add and "not an order" in add
    assert it.hint_used


def test_hint_names_the_tool_family():
    it = intent.understand("find flights", post=fake(resp("browser_task", 0.93)))
    assert "use browser_task" in intent.prompt_addendum(it)
    mac = intent.understand("open spotify", post=fake(resp("mac_control", 0.9)))
    assert "no tool for controlling the Mac" in intent.prompt_addendum(mac)


def test_low_confidence_abstains_to_other_without_hint():
    it = intent.understand("hmm", post=fake(resp("chat", intent.THRESHOLDS["kind"] - 0.01)))
    assert it.kind == "other" and it.action == "none"
    assert intent.prompt_addendum(it) == "" and not it.hint_used


def test_kind_above_abstain_but_below_hint_threshold_gives_no_hint():
    p = (intent.THRESHOLDS["kind"] + intent.THRESHOLDS["hint"]) / 2
    it = intent.understand("x", post=fake(resp("chat", p)))
    assert it.kind == "chat" and it.action == "none"


def test_needs_selection_only_when_nothing_selected(monkeypatch):
    monkeypatch.setenv("REACH_AUTO_SCREEN", "0")  # the old circle-it short-circuit, kept behind the switch
    r = resp("screen_question", 0.5, needs_screen_region=0.95)
    assert intent.understand("what is this?", post=fake(r)).action == "needs_selection"
    assert intent.understand("what is this?", "a selected word", post=fake(r)).action != "needs_selection"
    assert intent.understand("what is this?", None, has_image=True, post=fake(r)).action != "needs_selection"
    low = resp("screen_question", 0.5, needs_screen_region=intent.THRESHOLDS["needs_screen_region"] - 0.01)
    assert intent.understand("what is this?", post=fake(low)).action != "needs_selection"


def test_risky_adds_required_confirmation():
    it = intent.understand("delete all my files", post=fake(resp("mac_control", 0.99, risky_or_irreversible=0.99)))
    assert it.action == "confirm"
    add = intent.prompt_addendum(it)
    assert add.startswith("REQUIRED") and "Do NOT act" in add and "confirm" in add and "p=0.99" in add


def test_errors_and_missing_key_skip_silently(monkeypatch):
    def boom(*a):
        raise TimeoutError("timed out")
    it = intent.understand("x", post=boom)
    assert it.error and it.kind == "other" and it.action == "none"
    assert intent.prompt_addendum(it) == ""
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    monkeypatch.delenv("DREX_API_KEY", raising=False)
    monkeypatch.setenv("REACH_ENV_FILE", "/nonexistent")
    monkeypatch.setattr(intent, "load_keys", lambda names: None)
    assert intent.understand("x").error == "no Drex key"


def test_timeout_fallback_discards_late_result(monkeypatch):
    monkeypatch.setattr(intent, "TIMEOUT_S", 0.1)

    async def go():
        def slow():
            time.sleep(0.5)
            return intent.Intent(kind="chat", action="hint")
        fut = asyncio.ensure_future(asyncio.to_thread(slow))
        return await intent.finish(fut, time.monotonic())

    it = asyncio.run(go())
    assert it.error == "timeout" and it.action == "none"


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("REACH_INTENT", "0")
    assert intent.start(Ask(text="x")) is None


def test_log_format(tmp_path):
    it = intent.understand("delete", post=fake(resp("mac_control", 0.99, risky_or_irreversible=0.98)))
    path = tmp_path / "drex.jsonl"
    intent.log(it, "delete", "t1", path)
    e = json.loads(path.read_text())
    assert e["type"] == "decision" and e["purpose"] == "intent" and e["task_id"] == "t1"
    assert e["answers"]["kind"]["choice"] == "mac_control"
    assert e["answers"]["risky_or_irreversible"] == {"noul": 0.98}
    assert e["action"] == "confirm" and e["tokens"] == {"input": 700, "output": 400} and "latency_ms" in e
    line = format_log_line(e)
    assert "intent" in line and "kind=mac_control" in line and "p=0.99" in line
    err = intent.Intent(error="timeout")
    intent.log(err, "x", "t2", path)
    e2 = json.loads(path.read_text().splitlines()[1])
    assert e2["ok"] is False and "FAILED timeout" in format_log_line(e2)


def test_receipt_dict_and_prompt_hook():
    from reach.bridge import prompt_for

    it = intent.understand("find flights", post=fake(resp("browser_task", 0.93)))
    p = prompt_for(Ask(text="find flights", selection=Selection(text="x")), it)
    assert "Drex (a fast classifier) judged this a browser task (p=0.93); use browser_task." in p
    d = it.receipt_dict()
    assert d["kind"] == "browser_task" and d["hint_used"] and d["action"] == "hint"
    assert d["probs"]["browser_task"] == 0.93 and "risky_or_irreversible" in d["probs"]
    assert prompt_for(Ask(text="hi")) == prompt_for(Ask(text="hi"), None)


def test_bridge_short_circuits_needs_selection(monkeypatch, tmp_path):
    from reach import bridge

    monkeypatch.setattr("reach.paths.drex_log", lambda: tmp_path / "drex.jsonl")
    monkeypatch.setattr(bridge, "drex_log", lambda: tmp_path / "drex.jsonl")
    monkeypatch.setattr(intent, "drex_log", lambda: tmp_path / "drex.jsonl")
    sel = intent.Intent(kind="other", flags={"needs_screen_region": 0.93}, probs={"other": 0.4}, action="needs_selection")

    async def done(): return sel
    monkeypatch.setattr(intent, "start", lambda ask: asyncio.ensure_future(done()))

    class Sess:
        started = False
        async def start(self, prompt): Sess.started = True
        async def aclose(self): pass

    class Boot:
        async def session(self, emit): return Sess()

    b = bridge.InternBridge.__new__(bridge.InternBridge)
    b.bootstrap = Boot()
    events: list = []
    task = bridge.Task(Ask(text="what is this?"))
    r = asyncio.run(b.run(task, lambda tid, e: events.append(e)))
    assert not Sess.started and r.status == "ok" and r.reason == "needs_selection" and r.tools == []
    assert r.final_text.startswith("Which thing do you mean?") and r.intent["action"] == "needs_selection"
    assert any(e.get("purpose") == "intent" for e in events) and r.system1_asks == 1
