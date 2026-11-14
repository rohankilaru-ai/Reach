"""browser_in_chrome without a real extension: a fake stdio MCP server stands in for the Drex pairing server."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from reach.tools import chrome_agent

FAKE = Path(__file__).with_name("fake_drex_mcp.py")
CMD = chrome_agent.python_fake_cmd(FAKE)
CODE = "PAIR-9f3k-SECRET"


@pytest.fixture(autouse=True)
def paired(monkeypatch, tmp_path):
    monkeypatch.setenv("REACH_PAIRING_CODE", CODE)
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))


def run(mode, monkeypatch, **kw):
    monkeypatch.setenv("FAKE_MODE", mode)
    return asyncio.run(chrome_agent.run_task("check my YouTube views", "https://studio.youtube.com", cmd=CMD, **kw))


def test_handshake_and_completed(monkeypatch):
    s = run("done", monkeypatch)  # the reply carries a 200 KB screenshot line: the stream limit must hold it
    assert s["done"] and s["outcome"] == "done" and "1,234 views" in s["answer"]
    assert s["final_url"] == "https://studio.youtube.com/"
    text = chrome_agent.render(s, CODE)
    assert "DONE" in text and "AAAA" not in text and CODE not in text


@pytest.mark.parametrize("mode", ["partial", "needs_user"])
def test_partial_and_handoff_not_done(monkeypatch, mode):
    s = run(mode, monkeypatch)
    assert not s["done"] and s["status"] == mode and "sign-in" in s["reason"]
    assert "NOT DONE" in chrome_agent.render(s, CODE)


def test_extension_not_connected_needs_you(monkeypatch):
    s = run("not_connected", monkeypatch)
    text = chrome_agent.render(s, CODE)
    assert not s["done"] and s["outcome"] == "needs_you"
    assert "isn't connected" in text and "Let coding agents use Drex" in text and "docs/EXTENSION.md" in text


def test_timeout_kills_child(monkeypatch, tmp_path):
    pidf = tmp_path / "pid"
    monkeypatch.setenv("FAKE_PID_FILE", str(pidf))
    t0 = time.monotonic()
    s = run("hang", monkeypatch, timeout_s=1.5)
    assert time.monotonic() - t0 < 8
    assert not s["done"] and s["status"] == "timeout"
    _assert_dead(int(pidf.read_text()))


def test_abort_kills_child(monkeypatch, tmp_path):
    pidf = tmp_path / "pid"
    monkeypatch.setenv("FAKE_PID_FILE", str(pidf))
    monkeypatch.setenv("FAKE_MODE", "hang")

    async def go():
        ev = asyncio.Event()
        task = asyncio.create_task(chrome_agent.run_task("x", None, abort=ev, cmd=CMD, timeout_s=30))
        for _ in range(100):
            if pidf.exists() and pidf.read_text():
                break
            await asyncio.sleep(0.05)
        ev.set()
        return await task

    t0 = time.monotonic()
    s = asyncio.run(go())
    assert time.monotonic() - t0 < 8
    assert s["outcome"] == "aborted" and not s["done"]
    _assert_dead(int(pidf.read_text()))


def _assert_dead(pid):
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("fake server still alive")


def test_missing_code_gives_setup_steps(monkeypatch):
    monkeypatch.delenv("REACH_PAIRING_CODE")
    s = asyncio.run(chrome_agent.run_task("x", None, cmd=CMD))
    text = chrome_agent.render(s)
    assert s["outcome"] == "needs_you" and "REACH_PAIRING_CODE" in text and "4. Restart Reach" in text


def test_pairing_code_never_in_output(monkeypatch):
    s = run("echo_code", monkeypatch)  # the fake echoes its DREX_PAIRING_CODE into an error
    text = chrome_agent.render(s, CODE)
    assert CODE not in text and CODE not in repr(s) and "[pairing code]" in text


def test_child_env_carries_code_only_as_drex_var(monkeypatch):
    seen = {}

    async def fake_exec(*argv, env=None, **kw):
        seen.update(env)
        raise OSError("stop here")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(chrome_agent.run_task("x", None, cmd=CMD))
    assert seen["DREX_PAIRING_CODE"] == CODE and "REACH_PAIRING_CODE" not in seen


def test_registered_only_when_paired(monkeypatch):
    from reach.intern_ext import ReachTools

    assert "browser_in_chrome" in {t.name for t in ReachTools().tools()}
    monkeypatch.delenv("REACH_PAIRING_CODE")
    assert "browser_in_chrome" not in {t.name for t in ReachTools().tools()}
