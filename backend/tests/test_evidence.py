"""Key-free tests for the evidence store: cache by hash, search, citations, shelf, ledger. NDI is faked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reach import evidence
from reach.bridge import evidence_cards, prompt_for
from reach.contracts import Ask, Evidence, outbound_adapter
from reach.ledger import append_ndi, count_ndi, format_log_line

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "docs" / "ndi_parse_small.json"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    pdf = tmp_path / "small.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes for hashing only")
    calls = []

    def fake_parse(path: Path) -> dict:
        calls.append(path)
        return {"job_id": "job-1", "file_id": "f1", "workspace_id": "w1", "result": json.loads(FIXTURE.read_text())}

    return pdf, fake_parse, calls


def test_add_caches_by_sha256(store):
    pdf, parse, calls = store
    first = evidence.add_document(pdf, parse)
    again = evidence.add_document(pdf, parse)
    assert len(calls) == 1
    assert not first["cached"] and again["cached"]
    assert first["passages"] == again["passages"] and first["passages"][0] == "E1"
    pdf.write_bytes(b"%PDF-1.4 different bytes")
    evidence.add_document(pdf, parse)
    assert len(calls) == 2


def test_pages_come_from_ndi_page_markers(store):
    pdf, parse, _ = store
    evidence.add_document(pdf, parse)
    pages = [evidence.get_passage(f"E{i}")["page"] for i in (1, 2)]
    assert pages == [1, 2]


def test_search_ranks_the_matching_passage(store):
    pdf, parse, _ = store
    evidence.add_document(pdf, parse)
    hits = evidence.search("generator tested weekly kerosene")
    assert hits and hits[0]["id"] == "E2" and hits[0]["page"] == 2
    assert evidence.search("zebra quantum") == []


def test_citations_become_level1_cards_with_verbatim_quotes(store):
    pdf, parse, _ = store
    evidence.add_document(pdf, parse)
    answer = "The generator is tested weekly [E2]. A clean lens doubles the range [E1, E2]. Bogus [E99]."
    assert evidence.cited_ids(answer) == ["E2", "E1", "E99"]
    cards = evidence_cards("t1", answer, do_ground=False)
    assert [c.id for c in cards] == ["E2", "E1"]  # unknown ids dropped
    md = json.loads(FIXTURE.read_text())["document"]["markdown"]
    for c in cards:
        assert isinstance(outbound_adapter.validate_json(c.model_dump_json()), Evidence)
        assert evidence.normalize_for_match(c.quote) in evidence.normalize_for_match(md)
        assert "|" not in c.quote and c.level == 2  # page known from the marker, no ground
    assert "generator" in cards[0].quote


def test_shelf_pins_and_offers_to_next_ask(store):
    pdf, parse, _ = store
    evidence.add_document(pdf, parse)
    assert evidence.pin("E1") and not evidence.pin("E42")
    items = evidence.shelf()
    assert [i["id"] for i in items] == ["E1"]
    prompt = prompt_for(Ask(text="what about the lens?"))
    assert "[E1]" in prompt and "evidence_search" in prompt
    assert evidence.unpin("E1") and evidence.shelf() == []


def test_ndi_calls_are_logged_and_counted(tmp_path):
    log = tmp_path / "drex.jsonl"
    append_ndi(log, "t1", "parse", file="a.pdf", pages=3, units=3)
    append_ndi(log, "t1", "ground", targets=2, found=2, units=1)
    append_ndi(log, "t2", "ground", targets=1, ok=False, error="NaceJobTimeoutError")
    assert count_ndi(log, "t1") == 2 and count_ndi(log) == 3
    lines = [format_log_line(json.loads(x)) for x in log.read_text().splitlines()]
    assert "ndi" in lines[0] and "a.pdf" in lines[0] and "found 2/2" in lines[1] and "FAILED" in lines[2]
