"""Key-free, camera-free, model-free tests for the silent speech worker."""

from __future__ import annotations

import json

import pytest

from reach_silent import commands as C
from reach_silent import worker


def test_vocabulary_is_the_12_backend_labels():
    assert len(C.VOCAB) == 12 and C.UNCERTAIN in C.VOCAB and len(C.PHRASES) == 11


@pytest.mark.parametrize("text,label", [
    ("explain this", "explain_this"), ("explain miss", "explain_this"), ("scroll dow", "scroll_down"),
    ("scrollup", "scroll_up"), ("put this hear", "put_this_here"), ("STOP!", "stop"),
    ("we're going to talk about the weather", C.UNCERTAIN), ("", C.UNCERTAIN),
])
def test_nearest_command(text, label):
    assert C.nearest_command(text)[0] == label


def test_rescore_puts_command_like_hypotheses_first_and_keeps_order():
    nbest = [("we're going to talk", -10.0), ("open miss", -11.0), ("explain this", -12.0), ("open miss", -13.0)]
    ranked = C.rescore(nbest)
    assert [r["text"] for r in ranked] == ["open miss", "explain this", "we're going to talk"]  # deduped
    assert ranked[0]["nearest"] == "open_this" and ranked[-1]["nearest"] == C.UNCERTAIN


def test_closed_set_winner_breaks_ties():
    nbest = [("open miss", -11.0), ("explain this", -12.0)]
    closed = [("explain this", -2.0), ("open this", -6.0), ("next", -9.0)]
    assert C.rescore(nbest, closed)[0]["text"] == "explain this"


def test_closed_set_probs_and_local_uncertain():
    probs = C.closed_set_probs([("scroll up", -1.0), ("scroll down", -1.05), ("next", -8.0)])
    assert abs(sum(probs.values()) - 1) < 1e-3
    assert C.local_decide(probs)[2] is True  # two near-equal: abstain
    sure = C.closed_set_probs([("pause", -0.1), ("stop", -6.0)])
    assert C.local_decide(sure)[:1] == ("pause",) and C.local_decide(sure)[2] is False
    assert C.local_decide({}) == (C.UNCERTAIN, 0.0, True)


def test_message_format_matches_backend_contract():
    msg = C.transcript_message(C.rescore([("explain this", -1.234567), ("explain miss", -2.0)]))
    assert msg == {"type": "transcript", "hypotheses": ["explain this", "explain miss"], "source": "lipreading",
                   "experimental": True, "scores": [-1.235, -2.0]}
    typed = C.transcript_message(C.rescore([("Next!", None)]))
    assert "scores" not in typed and typed["hypotheses"] == ["next"]
    long = C.rescore([("x" * 500, None)] + [(f"h{i}", None) for i in range(30)])
    assert len(long) == C.MAX_HYPS and all(len(r["text"]) <= C.MAX_CHARS for r in long)
    with pytest.raises(ValueError):
        C.transcript_message([])


def test_uncertain_result_never_reads_as_an_action():
    local = {"command": "pause", "probability": 0.9, "uncertain": False}
    assert "did nothing" in worker.describe({"command": "uncertain", "probability": 0.4, "uncertain": True}, local)
    assert "did nothing" in worker.describe(None, local)  # no backend: never acts on the local guess
    assert "did nothing" in worker.describe({"error": "x"}, local)
    assert worker.describe({"command": "pause", "probability": 0.9, "uncertain": False}, local).endswith("pause p=0.90")


@pytest.fixture
def no_camera(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("camera opened")

    monkeypatch.setattr(worker, "open_camera", boom)


def test_no_camera_without_the_flag(no_camera, capsys):
    assert worker.main([]) == 0
    assert "OFF" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["denied", "not_determined", "restricted", "unknown"])
def test_camera_not_opened_without_permission(no_camera, monkeypatch, status, capsys):
    monkeypatch.setattr(worker, "camera_status", lambda: status)
    assert worker.main(["--camera-check"]) == 2
    assert worker.main(["--on"]) == 2
    assert "NOT opened" in capsys.readouterr().out


def test_typed_hyp_path_without_backend(no_camera, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    assert worker.main(["--hyp", "explain this", "explain miss"]) == 0
    out = capsys.readouterr().out
    msg = json.loads(out.split("message: ", 1)[1].splitlines()[0])
    assert msg["hypotheses"] == ["explain this", "explain miss"] and msg["source"] == "lipreading"
    assert "not routed (no backend running)" in out and "did nothing" in out


def test_typed_hyp_path_with_backend_result(no_camera, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(worker, "send_hypotheses", lambda m: sent.append(m) or
                        {"type": "transcript", "command": "explain_this", "probability": 0.93, "uncertain": False,
                         "reason": "ok", "source": "lipreading"})
    assert worker.main(["--hyp", "explain miss"]) == 0
    assert sent[0]["experimental"] is True
    assert "explain_this p=0.93" in capsys.readouterr().out
