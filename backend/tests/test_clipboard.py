from types import SimpleNamespace

from reach.tools import clipboard


def fake_run(store):
    def run(cmd, input=None, capture_output=False, check=False, timeout=None):
        if cmd[0].endswith("pbcopy"):
            store["v"] = input.decode()
            return SimpleNamespace(stdout=b"")
        return SimpleNamespace(stdout=store["v"].encode())
    return run


def test_copy_verified():
    s = {}
    assert clipboard.copy_text("hello", fake_run(s))[0] == "verified" and s["v"] == "hello"


def test_empty_and_too_long_fail():
    assert clipboard.copy_text("  ")[0] == "failed"
    assert clipboard.copy_text("x" * 9000)[0] == "failed"


def test_changed_clipboard_is_unverified():
    s = {}
    def run(cmd, input=None, capture_output=False, check=False, timeout=None):
        if cmd[0].endswith("pbcopy"):
            return SimpleNamespace(stdout=b"")
        return SimpleNamespace(stdout=b"someone else")
    assert clipboard.copy_text("hello", run)[0] == "unverified"
