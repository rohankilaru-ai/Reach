"""Bench v2 tests. No keys, no network: fixtures validator, Wilson interval, sign test, metrics, one-label parser."""

from __future__ import annotations

import json
import re

import pytest

from reach import bench_v2 as v2, commands as c
from reach.bench import FIXTURES as V1_FIXTURES

STOP = set("""a an the this that these those it its is are was were be been do does did done to of in on at by for
from with and or but not no so if then than as into over up down out about me my i you your we our they them he she
him her his what which who whom when where how why can could would should will shall may might must just only all any
some each every both one two other more most less very too there here ok okay um uh pls please let lets""".split())


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-zà-ÿ]+", s.lower()) if w not in STOP]


def _bigrams(s: str) -> set[tuple[str, str]]:
    w = _words(s)
    return set(zip(w, w[1:]))


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-zà-ÿ0-9]+", s.lower()))


def _inputs(it: dict) -> list[str]:
    return it.get("hypotheses") or [it["text"]]


ITEMS = v2.load_v2()


def test_fixture_shape_and_counts():
    assert len(ITEMS) == 120 and len({it["id"] for it in ITEMS}) == 120
    dev = [it for it in ITEMS if it["split"] == "dev"]
    ho = [it for it in ITEMS if it["split"] == "held_out"]
    assert len(dev) == 30 and len(ho) == 90
    unc = [it for it in ITEMS if it["gold"] == c.UNCERTAIN]
    assert len(unc) == 25
    assert sum(it["gold"] == c.UNCERTAIN for it in dev) == 6 and sum(it["gold"] == c.UNCERTAIN for it in ho) == 19
    for it in ITEMS:
        assert it["gold"] in c.LABELS
        assert it["why"].strip()
        assert ("text" in it) != ("hypotheses" in it)
        if "hypotheses" in it:
            assert 1 <= len(it["hypotheses"]) <= 3
    # every command appears in held-out
    assert {it["gold"] for it in ho} == set(c.LABELS)


def test_no_v1_overlap():
    v1 = [json.loads(line) for line in V1_FIXTURES.read_text().splitlines() if line.strip()]
    v1_inputs = {_norm(s) for it in v1 for s in _inputs(it)}
    for it in ITEMS:
        for s in _inputs(it):
            assert _norm(s) not in v1_inputs, (it["id"], s)
    v1_desc = set().union(*(_bigrams(d) for d in [*c.COMMANDS.values(), c.UNCERTAIN_DESC, c.INSTRUCTIONS]))
    for it in ITEMS:
        for s in _inputs(it):
            assert not (_bigrams(s) & v1_desc), (it["id"], s, _bigrams(s) & v1_desc)


def test_frozen_prompt_shares_no_bigram_with_v2_items():
    prompt = set().union(*(_bigrams(d) for d in [*v2.V2_DESCRIPTIONS.values(), v2.V2_UNCERTAIN_DESC,
                                                   v2.V2_INSTRUCTIONS]))
    for it in ITEMS:
        for s in _inputs(it):
            assert not (_bigrams(s) & prompt), (it["id"], s, _bigrams(s) & prompt)
    assert list(v2.V2_DESCRIPTIONS) == list(c.COMMANDS)  # same label set as the production router
    body = v2.DrexV2(post=None).body("x")
    assert list(body["questions"]["command"]["criteria"]) == list(c.LABELS)


def test_wilson_interval():
    lo, hi = v2.wilson(90, 90)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.9591, abs=1e-3)
    lo, hi = v2.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    lo, hi = v2.wilson(45, 90)
    assert lo == pytest.approx(0.3984, abs=1e-3) and hi == pytest.approx(0.6016, abs=1e-3)
    lo, hi = v2.wilson(81, 90)
    assert lo < 0.9 < hi and lo == pytest.approx(0.8211, abs=1e-3)
    assert v2.wilson(0, 0) == (0.0, 1.0)


def test_sign_test():
    assert v2.sign_test(0, 0) == 1.0
    assert v2.sign_test(5, 0) == pytest.approx(0.0625)
    assert v2.sign_test(3, 3) == 1.0
    assert v2.sign_test(10, 1) == pytest.approx(0.01172, abs=1e-4)


def _row(gold, pred, **kw):
    r = {"system": "s", "id": kw.pop("id", "x"), "split": "held_out", "source": "typed", "gold": gold, "pred": pred,
         "latency_ms": 100, "input_tokens": 10, "output_tokens": 2, "est_cost_usd": 0.001, "error": None}
    r.update(kw)
    return r


def test_summarize_v2_metrics():
    U = c.UNCERTAIN
    rows = [
        _row("stop", "stop"),
        _row("stop", "cancel"),            # confusion
        _row("pause", U),                  # over-abstention
        _row(U, U),                        # caught
        _row(U, "next"),                   # acted on uncertain
        _row("next", U, error="boom", latency_ms=9999),
    ]
    m = v2.summarize_v2(rows)
    assert m["n"] == 6 and m["correct"] == 2
    assert m["wrong_command"] == 2 and m["wrong_confusion"] == 1 and m["wrong_acted_on_uncertain"] == 1
    assert m["uncertain_items"] == 2 and m["abstained_on_uncertain"] == 1 and m["abstain_on_uncertain_rate"] == 0.5
    assert m["command_items"] == 4 and m["abstained_on_command"] == 2 and m["over_abstention_rate"] == 0.5
    assert m["errors"] == 1 and m["latency_p95_ms"] == 100  # errored call excluded from latency
    assert m["accuracy_ci95"][0] < m["accuracy"] < m["accuracy_ci95"][1]
    assert m["est_cost_usd"] == 0.006 and v2.summarize_v2(rows, priced=False)["est_cost_usd"] is None


def test_paired_counts():
    rows = [_row("stop", "stop", id="a", system="x"), _row("stop", "cancel", id="a", system="y"),
            _row("next", "next", id="b", system="x"), _row("next", "next", id="b", system="y"),
            _row("pause", c.UNCERTAIN, id="c", system="x"), _row("pause", "pause", id="c", system="y")]
    p = v2.paired(rows, "x", "y")
    assert p["n"] == 3 and p["a_right_b_wrong"] == 1 and p["b_right_a_wrong"] == 1 and p["sign_test_p"] == 1.0


def test_parse_label_and_qwen_label_router(monkeypatch):
    assert v2.parse_label('{"label": "next"}') == "next"
    assert v2.parse_label('<think>x</think>```json\n{"label": "uncertain"}\n```') == "uncertain"
    assert v2.parse_label('{"label": "jump"}') is None
    assert v2.parse_label("next") is None

    def post(url, headers, body, timeout):
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "one label" in body["messages"][0]["content"]
        return {"model": "m", "usage": {"prompt_tokens": 400, "completion_tokens": 6},
                "choices": [{"message": {"content": '{"label": "stop"}'}}]}

    monkeypatch.setenv("DEEPINFRA_API_KEY", "test")
    r = v2.QwenLabelV2(post=post).classify("The user said: x")
    assert r.command == "stop" and r.output_tokens == 6 and r.est_cost_usd > 0

    def bad(url, headers, body, timeout):
        return {"usage": {}, "choices": [{"message": {"content": '{"label": "jump"}'}}]}

    r = v2.QwenLabelV2(post=bad).classify("x")
    assert r.command == c.UNCERTAIN and r.error


def test_qwen_probs_uses_frozen_prompt():
    body = v2.QwenProbsV2(post=None).body("x")
    assert body["messages"][0]["content"] == v2.QWEN_PROBS_SYSTEM
    assert "probabilities" in v2.QWEN_PROBS_SYSTEM
