"""Router and bench tests. No keys, no network: every client is a fake."""

from __future__ import annotations

import json

import pytest

from reach import bench, commands as c
from reach.ledger import format_log_line


def _probs(**kw: float) -> dict[str, float]:
    p = {k: 0.0 for k in c.LABELS}
    p.update(kw)
    return p


def test_label_set_is_eleven_commands_plus_uncertain():
    assert len(c.LABELS) == 12 and c.LABELS[-1] == "uncertain"
    assert set(c.LABELS) == {"explain_this", "open_this", "scroll_down", "scroll_up", "pause", "continue",
                             "compare_these", "put_this_here", "cancel", "next", "stop", "uncertain"}
    assert set(c.ACTIONS) == set(c.LABELS)
    body = c.DrexRouter(post=None).body("x")
    q = body["questions"]["command"]
    assert q["type"] == "choice" and list(q["criteria"]) == list(c.LABELS)


def test_abstain_rule():
    assert c.decide(_probs(scroll_down=0.9, scroll_up=0.05)) == ("scroll_down", "ok")
    assert c.decide(_probs(uncertain=0.6, stop=0.4))[0] == "uncertain"  # model chose uncertain
    assert c.decide(_probs(stop=0.45, cancel=0.2))[0] == "uncertain"  # below threshold
    assert c.decide(_probs(stop=0.55, cancel=0.45))[0] == "uncertain"  # margin too small
    assert c.decide({})[0] == "uncertain"
    assert c.decide({"not_a_label": 0.99})[0] == "uncertain"


def test_parse_drex_response_and_log(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    resp = {"model": "drex-max-v1.1", "evaluation_time_ms": 120.0, "usage": {"input_tokens": 400, "output_tokens": None},
            "answers": {"command": {"type": "choice", "choice": "next", "probabilities": _probs(next=0.93, stop=0.02)}}}
    router = c.DrexRouter(post=lambda url, h, body, t: resp)
    r = c.route(text="nex", router=router)
    assert r.command == "next" and r.input_tokens == 400 and r.eval_ms == 120.0
    assert r.est_cost_usd == pytest.approx(400 * 0.12e-6)
    entry = json.loads((tmp_path / "logs" / "drex.jsonl").read_text().splitlines()[-1])
    assert entry["type"] == "decision" and entry["purpose"] == "command_route"
    assert entry["answers"]["command"]["choice"] == "next" and entry["tokens"]["input"] == 400
    assert "command_route" in format_log_line(entry) and "p=0.93" in format_log_line(entry)


def test_errors_route_to_uncertain(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))

    def boom(*a):
        raise RuntimeError("HTTP 503: busy")

    r = c.route(text="stop", router=c.DrexRouter(post=boom))
    assert r.command == "uncertain" and r.error.startswith("HTTP 503")
    assert c.parse_drex({"answers": {}}, 5).command == "uncertain"


def test_state_for_hypotheses():
    s = c.state_for(hypotheses=["skrol dow", "scroll dough"], selection="para 2")
    assert "1. skrol dow" in s and "2. scroll dough" in s and "Selected on screen: para 2" in s
    assert c.state_for(text="next") == "The user said: next"


def test_parse_qwen_content():
    assert bench.parse_qwen_content('{"probabilities": {"pause": 0.9, "continue": 0.1}}') == {"pause": 0.9, "continue": 0.1}
    assert bench.parse_qwen_content('<think>hmm</think>```json\n{"pause": 1, "bogus": 2}\n```') == {"pause": 1.0}
    assert bench.parse_qwen_content("no json here") == {}
    assert bench.parse_qwen_content("{broken") == {}


def test_qwen_cost_units():
    # $0.20 per million input, $2.50 per million output
    assert bench.qwen_cost(1_000_000, 0) == pytest.approx(0.20)
    assert bench.qwen_cost(0, 1_000_000) == pytest.approx(2.50)


def test_qwen_router_with_fake_client():
    reply = {"usage": {"prompt_tokens": 300, "completion_tokens": 40},
             "choices": [{"message": {"content": json.dumps({"probabilities": _probs(cancel=0.8, stop=0.1)})}}]}
    seen = {}

    def post(url, headers, body, timeout):
        seen["body"] = body
        return reply

    r = bench.QwenRouter(post=post).classify("The user said: forget it")
    assert r.command == "cancel" and r.input_tokens == 300 and r.output_tokens == 40
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert r.est_cost_usd == pytest.approx(bench.qwen_cost(300, 40))


def test_summarize_metrics():
    rows = [
        {"gold": "next", "pred": "next", "latency_ms": 100},
        {"gold": "stop", "pred": "cancel", "latency_ms": 200},  # wrong command
        {"gold": "pause", "pred": "uncertain", "latency_ms": 300},  # abstain on a real command: not wrong
        {"gold": "uncertain", "pred": "uncertain", "latency_ms": 400},
        {"gold": "uncertain", "pred": "open_this", "latency_ms": 500},  # wrong command on an OOV item
        {"gold": "next", "pred": "uncertain", "latency_ms": 9, "error": "timeout"},  # error: in N, not in latency
    ]
    m = bench.summarize(rows)
    assert m["n"] == 6 and m["correct"] == 2 and m["accuracy"] == pytest.approx(2 / 6, abs=1e-4)
    assert m["wrong_command"] == 2 and m["abstention_rate"] == pytest.approx(3 / 6, abs=1e-4)
    assert m["uncertain_caught"] == 1 and m["uncertain_items"] == 2 and m["errors"] == 1
    assert m["latency_p50_ms"] == 300 and m["latency_p95_ms"] == 500
    assert bench.percentile([], 50) is None


def test_fixtures_split_and_labels():
    items = bench.load_fixtures()
    assert len(items) == 60
    assert sum(i["split"] == "dev" for i in items) == 20 and sum(i["split"] == "held_out" for i in items) == 40
    assert sum(i["gold"] == "uncertain" for i in items) == 8
    assert all(i["gold"] in c.LABELS for i in items)
    assert len({i["id"] for i in items}) == 60


def test_bench_run_and_report_with_fake_router(tmp_path):
    class Fake:
        name = "fake"

        def classify(self, state):
            return c.RouteResult("next" if "next" in state else "uncertain", "ok", latency_ms=10, input_tokens=5)

    items = [{"id": "a", "split": "dev", "source": "typed", "text": "next", "gold": "next"},
             {"id": "b", "split": "held_out", "source": "lip", "hypotheses": ["stp"], "gold": "stop"}]
    rows = bench.run([Fake()], items, tmp_path / "r.jsonl", log_drex=False)
    rep = bench.report(rows, {"date": "x", "threshold": 0.5, "margin": 0.15})
    assert rep["results"]["fake"]["dev"]["accuracy"] == 1.0
    assert rep["results"]["fake"]["held_out"]["accuracy"] == 0.0
    assert len((tmp_path / "r.jsonl").read_text().splitlines()) == 2
    assert "| fake | held_out | 1 |" in bench.render_md(rep)
