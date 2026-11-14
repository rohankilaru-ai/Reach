"""Native note actions and screen vision, key-free: no osascript, no network, no real ~/Documents."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from reach.contracts import Ask, Selection
from reach.ledger import ReceiptBuilder
from reach.tools import native, vision


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    monkeypatch.setattr(native, "NOTES_FILE", tmp_path / "Documents" / "Reach Notes.md")


def test_destination_allowlist_refuses_unknown():
    r = native.note_write("slack", "hello")
    assert r.outcome == "failed" and "refused" in r.detail


def test_file_write_verified_and_creates_file(tmp_path):
    notes = tmp_path / "Documents" / "Reach Notes.md"
    r = native.note_write("file", "Layer norm normalizes across features.", notes_file=notes)
    assert r.outcome == "verified" and r.chars > 0
    assert "Layer norm normalizes" in notes.read_text()


def test_file_path_outside_notes_is_refused(tmp_path):
    notes = tmp_path / "Documents" / "Reach Notes.md"
    r = native.write_file("x", path=tmp_path / "elsewhere.md", allowed=notes)
    assert r.outcome == "failed" and "refused" in r.detail
    assert not (tmp_path / "elsewhere.md").exists()


def test_symlinked_notes_file_is_refused(tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("")
    notes = tmp_path / "Documents" / "Reach Notes.md"
    notes.parent.mkdir(parents=True)
    notes.symlink_to(target)
    with pytest.raises(PermissionError):
        native.check_notes_path(notes, tmp_path / "Documents" / "real.md")


def test_verdict_needs_a_new_copy():
    assert native.verdict("abc", None, "abc") == "verified"
    assert native.verdict("abc", "abc", "abc") == "failed"  # an old copy cannot verify a new write
    assert native.verdict("abc", "abc", "abc\nabc") == "verified"
    assert native.verdict("abc", "", None) == "unverified"
    assert native.verdict("a  b\nc", "", "a b c") == "verified"  # whitespace normalized (Notes plaintext)


def test_app_write_with_fake_runner_destination_failure():
    """The destination accepts the write but the readback lacks the text: never success."""
    calls = []

    def fake(script, argv):
        calls.append(argv)
        return "Untitled 3" if "make new document" in script else "something else entirely"

    r = native.write_app("textedit", "put this in my notes", run=fake)
    assert r.outcome == "failed"
    assert calls[0] == ["put this in my notes"]  # text passed as argv, not spliced into the script


def test_app_write_unreadable_is_unverified():
    r = native.write_app("notes", "hello", run=lambda s, a: None)
    assert r.outcome == "unverified"
    ok = native.write_app("notes", "hello", run=lambda s, a: "x-coredata://1" if "make new note" in s else "Reach note hello")
    assert ok.outcome == "verified"


def test_tool_output_first_line_and_status():
    out = native.tool_output(native.WriteResult(destination="file", chars=12, outcome="failed"), "note_append")
    assert out.content.splitlines()[0] == "outcome=failed destination=file chars=12"
    assert out.status == "error"
    entry = json.loads((Path(os.environ["REACH_DIR"]) / "logs" / "drex.jsonl").read_text().splitlines()[-1])
    assert entry["type"] == "action" and entry["outcome"] == "failed"


def _end(name, content, status="ok"):
    return {"type": "tool_call_end", "name": name, "status": status, "content": content}


def test_receipt_outcome_mapping_worst_wins():
    b = ReceiptBuilder("t")
    b.feed(_end("evidence_search", "[E1] ..."))
    assert b.receipt().write_outcome is None  # no write, no outcome
    b.feed(_end("note_append", "outcome=verified destination=file chars=10"))
    r = b.receipt()
    assert r.write_outcome == "verified" and r.status == "failed"  # status untouched (no run_end)
    b.feed(_end("shelf_to_note", "outcome=unverified destination=notes chars=0", status="error"))
    assert b.receipt().write_outcome == "unverified"
    b.feed(_end("note_append", "refused by guard", status="refused"))
    assert b.receipt().write_outcome == "failed"


def test_receipt_outcome_unparseable_is_not_verified():
    b = ReceiptBuilder("t")
    b.feed(_end("note_append", "I wrote it, trust me"))
    assert b.receipt().write_outcome == "unverified"


def test_vision_request_building_without_sending():
    body = vision.build_request(b"\x89PNG fake", "what is the best move?")
    parts = body["messages"][1]["content"]
    assert body["model"] == vision.MODEL
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()
    assert parts[1] == {"type": "text", "text": "what is the best move?"}


def test_vision_reads_only_under_reach_dir(tmp_path):
    root = tmp_path / ".reach"
    (root / "captures").mkdir(parents=True)
    outside = tmp_path / "x.png"
    outside.write_bytes(b"\x89PNG")
    with pytest.raises(PermissionError):
        vision.resolve_image(str(outside), root)
    with pytest.raises(PermissionError):
        vision.resolve_image("../x.png", root)
    with pytest.raises(FileNotFoundError):
        vision.resolve_image(None, root)
    cap = root / "captures" / "sel.png"
    cap.write_bytes(b"\x89PNG")
    assert vision.resolve_image(None, root) == cap.resolve()


def test_see_logs_latency_and_uses_fake_sender(tmp_path):
    root = Path(os.environ["REACH_DIR"])
    (root / "captures").mkdir(parents=True)
    (root / "captures" / "a.png").write_bytes(b"\x89PNG")
    sent = []

    def fake(body):
        sent.append(body)
        return {"choices": [{"message": {"content": "Qh7#"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3}}

    assert vision.see(None, "best move?", send=fake) == "Qh7#"
    entry = json.loads((root / "logs" / "drex.jsonl").read_text().splitlines()[-1])
    assert entry["type"] == "vision" and entry["ok"] and "ms" in entry and entry["input_tokens"] == 9


def test_prompt_mentions_see_region_for_image_selection():
    from reach.bridge import prompt_for

    p = prompt_for(Ask(text="best move?", selection=Selection(image_path=".reach/captures/board.png")))
    assert "see_region" in p and ".reach/captures/board.png" in p
    assert "note_append" in p
