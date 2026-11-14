"""Do mode (EXPERIMENTAL) with a fake Accessibility layer, and the Finder tools. No keys, no real UI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from reach.ledger import CURRENT_TASK, WRITE_TOOLS, ReceiptBuilder
from reach.tools import ax, finder


class Node:
    def __init__(self, role, title="", *, subrole="", value=None, enabled=True, children=(), on_press=None,
                 settable=True, frame=(10.0, 20.0, 100.0, 40.0)):
        self.a = {"AXRole": role, "AXTitle": title, "AXSubrole": subrole, "AXValue": value, "AXEnabled": enabled}
        self.kids = list(children)
        self.on_press = on_press
        self.can_set = settable
        self.frame = frame


class FakeAX:
    def __init__(self, win: Node, *, bundle="com.apple.TextEdit", name="TextEdit", trusted=True, menubar=None):
        self.win, self.bundle, self.name, self.is_trusted, self.bar = win, bundle, name, trusted, menubar
        self.pid = 42
        self.app = Node("AXApplication", name)
        self.idle = 100.0
        self.pressed: list[str] = []
        self.on_idle = None

    def trusted(self):
        return self.is_trusted

    def frontmost(self):
        return self.app, self.pid, self.bundle, self.name

    def window(self, app):
        return self.win

    def menubar(self, app):
        return self.bar

    def attr(self, el, name):
        if name == "AXWindows":
            return [self.win]
        return el.a.get(name)

    def children(self, el):
        return el.kids

    def actions(self, el):
        return ["AXPress"] if el.a["AXRole"] in ax.PRESS_ROLES else []

    def settable(self, el, name):
        return el.can_set

    def set_attr(self, el, name, value):
        el.a[name] = value
        return True

    def perform(self, el, action):
        self.pressed.append(el.a["AXTitle"])
        if el.on_press:
            el.on_press(el)
        return True

    def frame(self, el):
        return el.frame

    def idle_seconds(self):
        if self.on_idle:
            return self.on_idle()
        return self.idle


def window(*kids, title="Doc"):
    return Node("AXWindow", title, children=kids)


def flip(el):
    el.a["AXValue"] = 0 if el.a["AXValue"] else 1


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(ax, "SHOW_BEFORE_S", 0.15)
    monkeypatch.setattr(ax, "READBACK_S", 0.3)
    monkeypatch.setattr(ax, "needs_accessibility", lambda: "needs you: allow Accessibility for Terminal in System "
                        "Settings > Privacy & Security > Accessibility")
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    ax._STATE.clear()
    from reach.ledger import CURRENT_OUT

    out_tok = CURRENT_OUT.set(None)
    tok = CURRENT_TASK.set(f"t-{os.urandom(3).hex()}")
    yield
    CURRENT_TASK.reset(tok)
    CURRENT_OUT.reset(out_tok)


def run(fake, kind, ref, text=None, emit=None, app="TextEdit"):
    s = ax.describe(fake, app)
    assert not isinstance(s, str), s
    ax.state().snapshot = s
    return asyncio.run(ax.act(fake, kind, app, ref, text, emit=emit, show_s=0.15))


def test_describe_lists_interactive_skips_secure_and_caps():
    kids = [Node("AXButton", f"b{i}") for i in range(120)]
    fake = FakeAX(window(Node("AXSecureTextField", "Password"), Node("AXTextField", "pw", subrole="AXSecureTextField"),
                         Node("AXStaticText", "hello"), *kids))
    s = ax.describe(fake)
    assert len(s.elems) == ax.MAX_ELEMENTS
    assert all(e.role == "AXButton" for e in s.elems)
    assert "Password" not in ax.render_snapshot(s)


def test_untrusted_says_needs_you_and_never_acts():
    fake = FakeAX(window(Node("AXButton", "OK")), trusted=False)
    s = ax.describe(fake)
    assert isinstance(s, str) and s.startswith("needs you: allow Accessibility for Terminal")


def test_set_text_verified_and_pointer_first():
    area = Node("AXTextArea", "", value="")
    fake = FakeAX(window(area))
    shown = []
    r = run(fake, "text", "0", "Hello there.", emit=lambda x, y, label: shown.append((x, y, label)))
    assert r.outcome == "verified" and area.a["AXValue"] == "Hello there."
    assert shown == [(60.0, 40.0, "About to type into: AXTextArea")]


def test_set_text_readback_failed_when_app_ignores_value():
    area = Node("AXTextField", "Name", value="old")
    fake = FakeAX(window(area))
    fake.set_attr = lambda el, n, v: True  # claims success, changes nothing
    r = run(fake, "text", "0", "new")
    assert r.outcome == "failed" and "does NOT hold" in r.detail


def test_secure_and_sensitive_fields_refused():
    fake = FakeAX(window(Node("AXTextField", "Card number")))
    r = run(fake, "text", "0", "4111")
    assert r.outcome == "failed" and "owner" in r.detail
    e = ax.Elem(0, "AXTextField", "AXSecureTextField", "x", True, "text", None)
    assert "password" in ax.refuse_reason(e, "com.x", kind="text")


def test_press_verified_when_state_changes_unverified_when_not():
    box = Node("AXCheckBox", "Bold", value=0, on_press=flip)
    fake = FakeAX(window(box, Node("AXButton", "Nothing")))
    assert run(fake, "press", "0").outcome == "verified"
    r = run(fake, "press", "1")
    assert r.outcome == "unverified" and fake.pressed == ["Bold", "Nothing"]


@pytest.mark.parametrize("title", ["Buy now", "Delete", "Confirm Purchase", "Submit order", "Pay", "Transfer",
                                   "Payments", "Deleting...", "Purchases", "Transferring", "Erase Disk"])
def test_danger_titles_refused(title):
    fake = FakeAX(window(Node("AXButton", title)))
    r = run(fake, "press", "0")
    assert r.outcome == "failed" and ax.OWNER_STEP in r.detail and fake.pressed == []


def test_denied_apps():
    fake = FakeAX(window(Node("AXButton", "OK")), bundle="com.apple.systempreferences", name="System Settings")
    assert "does not act" in ax.describe(fake)
    fake = FakeAX(window(Node("AXButton", "OK")), bundle="com.apple.Terminal", name="Terminal")
    assert "does not act" in ax.describe(fake)


def test_stale_when_element_or_app_changes():
    win = window(Node("AXButton", "OK"))
    fake = FakeAX(win)
    s = ax.describe(fake)
    ax.state().snapshot = s
    win.kids[0].a["AXTitle"] = "Something else"
    r = asyncio.run(ax.act(fake, "press", "TextEdit", "0", show_s=0.15))
    assert "stale" in r.detail and fake.pressed == []
    fake2 = FakeAX(window(Node("AXButton", "OK")))
    ax.state().snapshot = ax.describe(fake2)
    fake2.pid = 99  # another app came to the front
    r = asyncio.run(ax.act(fake2, "press", "TextEdit", "0", show_s=0.15))
    assert "stale" in r.detail and fake2.pressed == []


def test_user_takeover_aborts_and_latches():
    fake = FakeAX(window(Node("AXButton", "OK"), Node("AXButton", "Two")))
    fake.on_idle = lambda: 0.0  # the user is moving the mouse
    r = run(fake, "press", "0")
    assert r.detail.startswith("user took over") and fake.pressed == []
    fake.on_idle = None
    r = run(fake, "press", "1")
    assert r.detail.startswith("user took over") and fake.pressed == []


def test_budget_of_eight_per_ask():
    fake = FakeAX(window(Node("AXCheckBox", "Bold", value=0, on_press=flip)))
    outs = [run(fake, "press", "0").outcome for _ in range(9)]
    assert outs[:8] == ["verified"] * 8 and outs[8] == "failed"
    assert len(fake.pressed) == 8


def test_actions_logged_and_receipt_semantics(tmp_path, monkeypatch):
    log = tmp_path / "drex.jsonl"
    monkeypatch.setattr("reach.paths.drex_log", lambda: log)
    out = ax.result_output(ax.ActResult("unverified", "TextEdit/AXButton#0", 0, "saw no change"), "ax_press")
    assert out.content.startswith("outcome=unverified")
    assert json.loads(log.read_text().splitlines()[-1])["type"] == "action"
    assert {"ax_press", "ax_set_text", "finder_open", "finder_reveal"} <= WRITE_TOOLS
    b = ReceiptBuilder("t")
    b.feed({"type": "tool_call_end", "name": "ax_press", "status": "ok", "content": out.content})
    b.feed({"type": "run_end", "reason": "completed", "final_text": "Pressed it."})
    assert b.receipt().status == "failed"


def test_tools_absent_unless_flag(monkeypatch):
    monkeypatch.delenv("REACH_DO_MODE", raising=False)
    assert ax.tools() == [] and [t.name for t in finder.tools()] == ["finder_list"]
    monkeypatch.setenv("REACH_DO_MODE", "1")
    assert [t.name for t in ax.tools()] == ["ax_describe", "ax_press", "ax_set_text"]
    assert {t.name for t in finder.tools()} == {"finder_list", "finder_open", "finder_reveal"}
    effects = {t.name: t.effects for t in ax.tools() + finder.tools()}
    assert effects["ax_press"] == effects["ax_set_text"] == effects["finder_open"] == frozenset({"write"})
    assert effects["ax_describe"] == effects["finder_list"] == frozenset({"read"})


# ---- Finder ----


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "Docs").mkdir(parents=True)
    (h / "Docs" / "a.txt").write_text("x")
    return h


def test_finder_escape_and_symlink_refused(home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assert "only paths inside" in finder.check_path(str(home / "Docs" / ".." / ".." / "outside"), root=home)
    (home / "link").symlink_to(outside)
    assert "only paths inside" in finder.check_path(str(home / "link"), root=home)
    assert finder.check_path("Docs", root=home) == (home / "Docs").resolve()


@pytest.mark.parametrize("name", [".ssh", ".aws", ".env", ".env.local", ".intern-home", "login.keychain-db"])
def test_finder_secret_refused(home, name):
    p = home / name
    p.mkdir()
    assert "never opened" in finder.check_path(str(p), root=home)


def test_finder_secret_behind_symlink_refused(home):
    (home / ".ssh").mkdir()
    (home / "innocent").symlink_to(home / ".ssh")
    assert "never opened" in finder.check_path(str(home / "innocent"), root=home)


@pytest.mark.parametrize("name", ["run.command", "x.sh", "s.py", "a.scpt", "i.pkg", "d.dmg", "Foo.app"])
def test_finder_launchable_refused(home, name):
    p = home / name
    p.mkdir() if name.endswith(".app") else p.write_text("x")
    calls = []
    out, detail, _ = finder.open_path(str(p), root=home, run=lambda *a: calls.append(a) or (0, ""))
    assert out == "failed" and "never launches" in detail and calls == []


def test_finder_executable_bit_refused(home):
    p = home / "tool"
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    assert finder.launchable(p)


def test_finder_open_folder_readback(home):
    target = (home / "Docs").resolve()
    out, _, _ = finder.open_path(str(target), root=home, run=lambda argv, s=None: (0, str(target) + "/"))
    assert out == "verified"
    out, _, _ = finder.open_path(str(target), root=home, run=lambda argv, s=None: (0, "/elsewhere/"))
    assert out == "unverified"
    out, _, _ = finder.open_path(str(target), root=home, run=lambda argv, s=None: (1, ""))
    assert out == "failed"


def test_finder_list_cap(home):
    big = home / "Big"
    big.mkdir()
    for i in range(130):
        (big / f"f{i:03}").write_text("")
    out = finder.list_dir(str(big), root=home)
    assert out.count("\nf") == finder.MAX_LIST and "and 30 more" in out
    assert "a.txt" in finder.list_dir("Docs", root=home)


# ---- the real call path: tool.run -> overlay pointer on the asking client's queue ----


def test_tool_run_sends_top_level_pointer_with_task_id(monkeypatch):
    from reach.ledger import CURRENT_OUT

    area = Node("AXTextArea", "", value="", frame=None)  # no element frame: falls back to the window's centre
    fake = FakeAX(window(area))
    fake.frame = lambda el: (0.0, 0.0, 200.0, 100.0) if el is fake.win else el.frame
    monkeypatch.setattr(ax, "_AX", fake)
    monkeypatch.setattr("reach.paths.drex_log", lambda: Path(os.environ["REACH_DIR"]) / "drex.jsonl")
    sent: list[str] = []
    CURRENT_OUT.set(sent.append)

    async def go():
        d = await ax.AXDescribe().run(ax.DescribeArgs(app="TextEdit"), None)
        assert "[0] AXTextArea" in d.content
        return await ax.AXSetText().run(ax.SetTextArgs(app="TextEdit", element_ref="0", text="Hi."), None)

    out = asyncio.run(go())
    assert out.content.startswith("outcome=verified")
    msg = json.loads(sent[0])
    assert msg["type"] == "pointer" and msg["task_id"] == CURRENT_TASK.get()
    assert (msg["x"], msg["y"], msg["label"]) == (100.0, 50.0, "About to type into: AXTextArea")


def test_no_overlay_channel_is_said_in_the_result(monkeypatch):
    from reach.ledger import CURRENT_OUT

    CURRENT_OUT.set(None)
    fake = FakeAX(window(Node("AXCheckBox", "Bold", value=0, on_press=flip)))
    s = ax.describe(fake)
    ax.state().snapshot = s
    r = asyncio.run(ax.act(fake, "press", "TextEdit", "0", emit=ax.overlay_pointer, show_s=0.15))
    assert r.outcome == "verified" and "pointer not shown" in r.detail


def test_server_run_gives_tools_the_client_queue():
    from reach.contracts import Ask
    from reach.ipc import Server
    from reach.ledger import CURRENT_OUT

    class FakeBridge:
        grounds = False

        async def run(self, task, sink):
            CURRENT_OUT.get()('{"type":"pointer","task_id":"%s","x":1,"y":2}' % task.id)
            return task.builder.receipt()

    async def go():
        srv = Server(FakeBridge())
        out: asyncio.Queue = asyncio.Queue()
        task = srv.Task(Ask(text="press bold"))
        await srv._run(task, out)
        return task, [json.loads(out.get_nowait()) for _ in range(out.qsize())]

    task, msgs = asyncio.run(go())
    assert msgs[0] == {"type": "pointer", "task_id": task.id, "x": 1, "y": 2}


def test_guard_policy_lets_finder_paths_through_but_not_secrets(tmp_path):
    from reach import guard_policy

    assert guard_policy.check("finder_open", {"path": "~/Documents/Taxes"}, tmp_path) is None
    assert guard_policy.check("finder_list", {"path": "~/.ssh"}, tmp_path) is not None
    assert guard_policy.check("ax_set_text", {"app": "TextEdit", "element_ref": "3",
                                              "text": "the token count was high"}, tmp_path) is None
