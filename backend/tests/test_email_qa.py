import pytest

from reach.tools import email


def test_compose_url_encodes_and_validates():
    u = email.compose_url("a@b.com", "Hi there", "Line one\nLine & two")
    assert u.startswith("https://mail.google.com/mail/?view=cm") and "to=a%40b.com" in u
    assert "Line%20one%0ALine%20%26%20two" in u
    with pytest.raises(ValueError):
        email.compose_url("not-an-address", "x", "y")
    with pytest.raises(ValueError):
        email.compose_url("", "x", "y")
    with pytest.raises(ValueError):
        email.compose_url("a@b.com, bad", "x", "y")


def test_body_is_capped():
    u = email.compose_url("a@b.com", "s", "x" * 5000)
    assert len(u) < 2200


def test_hand_selection_is_one_shot_and_expires(monkeypatch):
    import time

    from reach import relay
    from reach.contracts import Ask, Selection

    r = relay.Relay()
    r.latest, r.latest_at = Selection(text="hi", region=[0, 0, 10, 10]), time.monotonic()
    ask, how = r.attach(Ask(text="explain"))
    assert how == "pinch-latest" and ask.selection is not None
    ask2, how2 = r.attach(Ask(text="again"))
    assert how2 is None  # consumed
    r.latest, r.latest_at = Selection(text="old"), time.monotonic() - relay.LATEST_TTL_S - 1
    assert r.attach(Ask(text="x"))[1] is None
