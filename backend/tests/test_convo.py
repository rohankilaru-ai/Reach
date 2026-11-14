import time

from reach import convo
from reach.intent import Intent


def setup_function(_):
    convo.reset()


def test_empty_memory_never_continues():
    assert convo.decide(Intent(flags={"continues_previous": 0.99})) is False
    assert convo.history_text() is None


def test_follow_up_keeps_history_and_new_topic_clears_it():
    convo.add("what is 17 times 23", "391.", [])
    assert convo.decide(Intent(flags={"continues_previous": 0.9})) is True
    assert "391." in convo.history_text()
    assert convo.decide(Intent(flags={"continues_previous": 0.1})) is False  # Drex: new topic
    assert convo.history_text() is None  # memory was cleared


def test_idle_expiry_and_turn_cap(monkeypatch):
    for i in range(9):
        convo.add(f"q{i}", f"a{i}")
    assert convo.history_text().count("User:") == convo.MAX_TURNS
    convo._turns[-1]["ts"] = time.time() - convo.IDLE_S - 5
    assert convo.has_turns() is False


def test_drex_failure_falls_back_to_recency():
    convo.add("open example.com", "Done.")
    assert convo.decide(Intent(error="timeout")) is True  # just said: keep
    convo._turns[-1]["ts"] = time.time() - convo.FALLBACK_S - 10
    assert convo.decide(Intent(error="timeout")) is False


def test_previous_summary_is_short():
    convo.add("x" * 900, "y" * 2000)
    assert len(convo.previous_summary()) <= 500


def test_prompt_includes_history_only_when_continuing():
    from reach import bridge
    from reach.contracts import Ask

    convo.add("what is the capital of france", "Paris.")
    p = bridge.prompt_for(Ask(text="and its population?"), Intent(flags={"continues_previous": 0.9}))
    assert "Conversation so far" in p and "Paris." in p
    p = bridge.prompt_for(Ask(text="write a haiku"), Intent(flags={"continues_previous": 0.05}))
    assert "Conversation so far" not in p
