"""reach hands on|off|status and the `hands` message: a harmless stand-in child (never real hands mode)."""

from __future__ import annotations


import pytest

from reach import hands, ipc


@pytest.fixture
def fake_child(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    monkeypatch.setenv("REACH_HANDS_CMD", "sleep 30")
    yield
    hands.stop()


def test_on_status_off(fake_child, tmp_path):
    assert hands.status() == {"on": False, "pid": None}
    st = hands.start(preview=False)
    assert st["on"] and (tmp_path / "run" / "hands.pid").exists()
    assert hands.start()["pid"] == st["pid"]          # idempotent: one child only
    assert hands.status()["on"]
    assert hands.stop() == {"on": False, "pid": None}
    assert not (tmp_path / "run" / "hands.pid").exists() and not hands.status()["on"]


def test_stale_pidfile_is_off(fake_child, tmp_path):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "hands.pid").write_text("999999")
    assert hands.status() == {"on": False, "pid": None}


def test_default_command_is_hands_mode_with_control(monkeypatch):
    monkeypatch.delenv("REACH_HANDS_CMD", raising=False)
    cmd = hands.command()
    assert cmd[-3:] == ["-m", "hands_mode.run", "--control"]


def test_ipc_hands_message_parsing():
    assert ipc._hands_msg('{"type":"hands","on":true}') is True
    assert ipc._hands_msg('{"type":"hands","on":false}') is False
    assert ipc._hands_msg('{"type":"hands","on":"yes"}') is None
    assert ipc._hands_msg('{"type":"ask","text":"hands"}') is None
    assert ipc._hands_msg("not json") is None
