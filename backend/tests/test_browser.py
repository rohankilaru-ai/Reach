"""browser_task without keys: outcome mapping from fixture JSON, abort kills the child tree, receipt fields."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from reach.ledger import ReceiptBuilder
from reach.tools import browser

SUCCEEDED = {
    "status": "succeeded", "answer_text": "The first Travel book is 'It's Only the Himalayas'.",
    "final_url": "https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
    "outcome": {"kind": "completed", "reason": "", "evidence": []},
    "usage": {"drex_calls": 3, "llm_calls": 4, "cost_usd": 0.0061},
    "artifacts": [{"kind": "screenshot", "step": None, "path": "/x/final.png"}],
}
PARTIAL_RISKY = {
    "status": "partial", "answer_text": "Filled the form but did not submit.", "final_url": "https://httpbin.org/forms/post",
    "outcome": {"kind": "risky_action_denied", "reason": "Submitting the order needs the user's approval."},
    "usage": {"drex_calls": 2, "llm_calls": 3, "cost_usd": 0.004},
}


@pytest.mark.parametrize(
    "result,done,kind",
    [
        (SUCCEEDED, True, "completed"),
        (PARTIAL_RISKY, False, "risky_action_denied"),
        ({**PARTIAL_RISKY, "status": "partial", "outcome": {"kind": "blocked_login", "reason": "login"}}, False,
         "blocked_login"),
        ({"status": "timed_out", "outcome": {"kind": "limit_reached", "reason": "150 s"}}, False, "limit_reached"),
        ({"status": "failed", "outcome": {"kind": "internal_error", "reason": "x"}}, False, "internal_error"),
        ({"error": {"code": "run_refused", "message": "unknown model"}}, False, "run_refused"),
        (None, False, "no_result"),
        ({**SUCCEEDED, "answer_text": "Not confirmed on screen: the Travel link was not activated."}, False,
         "not_confirmed_on_screen"),
    ],
)
def test_outcome_mapping_only_succeeded_is_done(result, done, kind):
    s = browser.summarize(result, exit_code=1)
    assert s["done"] is done
    assert s["kind"] == kind
    assert s["outcome"] == ("done" if done else "not_done")
    text = browser.render(s)
    assert ("DONE (harness status succeeded)" in text) is done
    if not done:
        assert "NOT DONE" in text


def test_marker_round_trip_carries_cost_and_drex_calls():
    m = browser.parse_marker(browser.render(browser.summarize(SUCCEEDED, exit_code=0)))
    assert m["outcome"] == "done" and m["drex_calls"] == 3 and m["cost_usd"] == pytest.approx(0.0061)
    assert m["final_url"].endswith("travel_2/index.html")


def _end(content: str, status: str = "ok") -> dict:
    return {"type": "tool_call_end", "name": "browser_task", "status": status, "content": content, "call_id": "c1",
            "turn": 1, "duration_ms": 10}


def test_receipt_done_when_browser_succeeded():
    b = ReceiptBuilder("t1")
    b.feed(_end(browser.render(browser.summarize(SUCCEEDED, exit_code=0))))
    b.feed({"type": "run_end", "reason": "completed", "final_text": "It's Only the Himalayas", "usage": {}})
    r = b.receipt()
    assert r.status == "ok" and r.outcome == "done" and r.tools == ["browser_task"]
    assert r.browser[0]["drex_calls"] == 3 and r.browser[0]["cost_usd"] == pytest.approx(0.0061)


def test_receipt_not_ok_when_browser_did_not_finish_even_if_model_says_done():
    b = ReceiptBuilder("t2")
    b.feed(_end(browser.render(browser.summarize(PARTIAL_RISKY, exit_code=1)), status="error"))
    b.feed({"type": "run_end", "reason": "completed", "final_text": "Done! I submitted the order.", "usage": {}})
    r = b.receipt()
    assert r.status == "failed" and r.outcome == "not_done"
    assert r.browser[0]["kind"] == "risky_action_denied"


FAKE = textwrap.dedent(
    """
    import os, subprocess, sys, time
    pidfile = sys.argv[1]
    # a grandchild in its own session, like a detached Chromium
    g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    open(pidfile, "w").write(f"{os.getpid()} {g.pid}")
    time.sleep(60)
    """
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill 0; ps shows its state
    import subprocess

    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def test_abort_kills_child_and_detached_grandchild(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    script = tmp_path / "fake.py"
    script.write_text(FAKE)
    pidfile = tmp_path / "pids"

    async def go():
        abort = asyncio.Event()
        run = asyncio.create_task(browser.run_task("t", "https://x", abort=abort,
                                                   cmd=[sys.executable, str(script), str(pidfile)]))
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        t0 = time.monotonic()
        abort.set()
        s = await asyncio.wait_for(run, 5)
        return s, time.monotonic() - t0

    s, took = asyncio.run(go())
    child, grandchild = map(int, pidfile.read_text().split())
    assert s["outcome"] == "aborted" and not s["done"]
    assert took < 3.0
    time.sleep(0.2)
    assert not _alive(child)
    assert not _alive(grandchild)


def test_fake_harness_json_is_read_from_stdout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    script = tmp_path / "ok.py"
    script.write_text(f"print({json.dumps(json.dumps(SUCCEEDED))})")
    s = asyncio.run(browser.run_task("t", "https://x", cmd=[sys.executable, str(script)]))
    assert s["done"] and s["drex_calls"] == 3


def test_browser_outcome_and_write_outcome_coexist():
    b = ReceiptBuilder("t3")
    b.feed(_end(browser.render(browser.summarize(SUCCEEDED, exit_code=0))))
    b.feed({"type": "tool_call_end", "name": "note_append", "status": "ok",
            "content": "outcome=verified destination=file chars=20"})
    b.feed({"type": "run_end", "reason": "completed", "final_text": "saved", "usage": {}})
    r = b.receipt()
    assert r.status == "ok" and r.outcome == "done" and r.write_outcome == "verified"
    assert r.tools == ["browser_task", "note_append"]
