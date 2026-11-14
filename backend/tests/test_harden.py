"""Hardening: honest failed-write status, dangling-symlink refusal, `reach stop` kills browser children."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from reach.ledger import ReceiptBuilder, claims_success


def _write_end(outcome: str, status: str = "ok") -> dict:
    return {"type": "tool_call_end", "name": "note_append", "status": status,
            "content": f"outcome={outcome} destination=file chars=20"}


@pytest.mark.parametrize("outcome", ["failed", "unverified"])
def test_failed_or_unverified_write_is_not_ok(outcome):
    b = ReceiptBuilder("w1")
    b.feed(_write_end(outcome))
    b.feed({"type": "run_end", "reason": "completed", "final_text": "Done, saved.", "usage": {}})
    r = b.receipt()
    assert r.status == "failed" and r.write_outcome == outcome
    assert f"note write {outcome}" in r.reason
    assert "claims success" in r.reason  # the model said "saved" while the write did not verify


def test_failed_write_honest_text_has_no_claim_note():
    b = ReceiptBuilder("w2")
    b.feed(_write_end("failed"))
    b.feed({"type": "run_end", "reason": "completed", "final_text": "I could not save it to your notes.", "usage": {}})
    r = b.receipt()
    assert r.status == "failed" and r.reason == "note write failed"


def test_refused_write_is_failed():
    b = ReceiptBuilder("w3")
    b.feed(_write_end("verified", status="refused"))
    b.feed({"type": "run_end", "reason": "completed", "final_text": "ok", "usage": {}})
    r = b.receipt()
    assert r.status == "failed" and r.write_outcome == "failed"


def test_verified_write_stays_ok():
    b = ReceiptBuilder("w4")
    b.feed(_write_end("verified"))
    b.feed({"type": "run_end", "reason": "completed", "final_text": "Saved to your notes.", "usage": {}})
    r = b.receipt()
    assert r.status == "ok" and r.reason == "completed" and r.write_outcome == "verified"


def test_aborted_status_kept_with_failed_write():
    b = ReceiptBuilder("w5")
    b.feed(_write_end("failed"))
    b.feed({"type": "run_end", "reason": "aborted", "final_text": "", "usage": {}})
    assert b.receipt().status == "aborted"


def test_claims_success():
    assert claims_success("Done! It's saved.") and claims_success("I added it to your notes")
    assert not claims_success("The write failed") and not claims_success("I couldn't save it")
    assert not claims_success("Here is the summary.")


# ---- dangling symlink ----

def test_dangling_symlink_notes_file_is_refused(tmp_path):
    from reach.tools import native

    notes = tmp_path / "Documents" / "Reach Notes.md"
    notes.parent.mkdir(parents=True)
    target = tmp_path / "zshenv-not-yet"
    notes.symlink_to(target)  # dangling
    r = native.write_file("hello", allowed=notes)
    assert r.outcome == "failed" and "refused" in r.detail
    assert not target.exists()


def test_plain_notes_file_still_verified(tmp_path):
    from reach.tools import native

    notes = tmp_path / "Documents" / "Reach Notes.md"
    r = native.write_file("hello world", allowed=notes)
    assert r.outcome == "verified" and notes.read_text() == "hello world\n"
    r2 = native.write_file("second", allowed=notes)
    assert r2.outcome == "verified" and notes.read_text() == "hello world\nsecond\n"


# ---- reach stop ends browser children ----

def _gone(pid: int, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_stop_kills_registered_browser_children(tmp_path, monkeypatch):
    from reach import cli
    from reach.tools import browser

    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    run = tmp_path / ".reach" / "run"
    run.mkdir(parents=True)
    pidfile = tmp_path / "grandchild.pid"
    # A fake harness: ignores SIGTERM (forces the SIGKILL step) and has a grandchild like Chromium.
    fake = subprocess.Popen(
        [sys.executable, "-c",
         "import os,signal,subprocess,sys,time\n"
         "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
         "g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
         f"open({str(pidfile)!r},'w').write(str(g.pid))\n"
         "time.sleep(60)\n"],
        start_new_session=True,
    )
    server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        end = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < end:
            time.sleep(0.05)
        grand = int(pidfile.read_text())
        browser.register_child(fake.pid)
        (run / "server.json").write_text(json.dumps({"pid": server.pid}))
        cli.cmd_stop(SimpleNamespace())
        fake.wait(timeout=5)
        server.wait(timeout=5)
        assert _gone(grand)
        assert browser._read_children() == []
    finally:
        for p in (fake, server):
            if p.poll() is None:
                p.kill()


def test_kill_children_skips_own_group_and_non_leaders(tmp_path, monkeypatch):
    from reach.tools import browser

    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # same group as pytest
    try:
        assert browser.kill_children([os.getpid(), child.pid], grace_s=0.1) == []
        assert child.poll() is None
    finally:
        child.kill()


def test_child_env_drops_secrets_but_keeps_harness_keys():
    from reach.tools import browser

    env = browser.child_env({"PATH": "/bin", "HOME": "/h", "NACE_API_KEY": "sk-FAKE-1", "OPENAI_API_KEY": "sk-FAKE-2",
                             "GITHUB_TOKEN": "x", "REACH_DIR": "/r", "AWS_SECRET_ACCESS_KEY": "y"})
    assert env == {"PATH": "/bin", "HOME": "/h", "NACE_API_KEY": "sk-FAKE-1", "REACH_DIR": "/r"}
