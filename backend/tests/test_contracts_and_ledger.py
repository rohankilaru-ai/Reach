import json
import os
import stat
from pathlib import Path

import pytest

from reach.contracts import Abort, Ask, Receipt, inbound_adapter, outbound_adapter
from reach.ledger import ReceiptBuilder, append_drex, format_log_line

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "events" / "ask.jsonl"


def fixture_events():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def test_inbound_contract_parses_ask_and_abort():
    msg = inbound_adapter.validate_json('{"type":"ask","text":"hi","selection":{"text":"sel"}}')
    assert isinstance(msg, Ask) and msg.selection.text == "sel"
    assert isinstance(inbound_adapter.validate_json('{"type":"abort"}'), Abort)
    with pytest.raises(Exception):
        inbound_adapter.validate_json('{"type":"nope"}')


def test_receipt_shape_round_trips_through_outbound_union():
    r = Receipt(task_id="t", status="ok", reason="completed", system1_asks=2, tokens=10, est_cost_usd=0.0, duration_ms=5)
    parsed = outbound_adapter.validate_json(r.model_dump_json())
    assert isinstance(parsed, Receipt)
    for key in ("task_id", "status", "reason", "tools", "refused", "system1_asks", "tokens", "est_cost_usd", "duration_ms"):
        assert key in r.model_dump()


def test_receipt_from_recorded_stream():
    b = ReceiptBuilder("fx")
    for e in fixture_events():
        b.feed(e)
    r = b.receipt()
    assert r.reason == "completed" and r.status == "ok"
    assert r.system1_asks == sum(1 for e in fixture_events() if e["type"] == "decision") > 0
    assert r.tools == ["reach_now"]  # the custom extension tool ran
    assert "391" in r.final_text
    assert r.tokens > 0


def test_aborted_stream_without_run_end_reports_aborted():
    b = ReceiptBuilder("ab")
    b.feed({"type": "text_delta", "turn": 1, "text": "par"})
    r = b.receipt(aborted=True)
    assert (r.status, r.reason) == ("aborted", "aborted")


def test_ledger_writes_only_drex_events_and_formats_them(tmp_path):
    log = tmp_path / "logs" / "drex.jsonl"
    written = [append_drex(log, e, "fx") for e in fixture_events()]
    assert sum(written) == 3
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert {e["type"] for e in entries} == {"decision"} and all(e["task_id"] == "fx" for e in entries)
    lines = [format_log_line(e) for e in entries]
    assert any("supervise" in line and "mode=advancing" in line and "p=0.49" in line for line in lines)
    guard = {"type": "tool_guard", "timestamp": 0, "turn": 1, "call_id": "c", "name": "bash", "layer": "system1",
             "rule": "deletes files", "reason": "r", "risk": 0.9}
    assert append_drex(log, guard, "fx") and "p=0.90" in format_log_line(guard)


def test_run_files_token_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    from reach.ipc import read_run_files, write_run_files

    write_run_files(4321, "secret-token")
    mode = stat.S_IMODE(os.stat(tmp_path / ".reach" / "run" / "token").st_mode)
    assert mode == 0o600
    assert read_run_files() == (4321, "secret-token")
