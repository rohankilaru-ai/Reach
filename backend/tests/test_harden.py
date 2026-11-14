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
