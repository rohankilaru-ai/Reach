"""Chrome tab control and table-to-notes, key-free: a fake osascript runner and a fake vision call."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from reach.ledger import WRITE_TOOLS, ReceiptBuilder, parse_outcome
from reach.tools import chrome, native, tables
from reach.tools.chrome import US, RS, RunResult

PID = 4242


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    monkeypatch.setattr(native, "NOTES_FILE", tmp_path / "Documents" / "Reach Notes.md")


class FakeChrome:
    """A tiny Chrome: windows (id -> list of tab dicts). Records every (script, argv) call."""

    def __init__(self, loads=True):
        self.windows = {1: [{"id": 11, "title": "User mail", "url": "https://mail.example.org/", "loading": False}]}
        self.active = {1: 11}
        self.next_id = 100
        self.calls: list[tuple[str, list[str]]] = []
        self.loads = loads
        self.denied = False

    def __call__(self, script, argv):
        self.calls.append((script, list(argv)))
        if self.denied:
            return RunResult(ok=False, err="execution error: Not authorized to send Apple events to Google Chrome. (-1743)")
        if script == chrome.LIST:
            recs = []
            for wi, (wid, tabs) in enumerate(self.windows.items(), 1):
                for ti, t in enumerate(tabs, 1):
                    act = "true" if self.active[wid] == t["id"] else "false"
                    recs.append(US.join([str(wi), str(wid), str(ti), str(t["id"]), t["title"], t["url"], act]))
            return RunResult(ok=True, out=RS.join(recs) + RS)
        if script == chrome.OPEN:
            url, target = argv
            if target == "new":
                wid = max(self.windows) + 1
                self.windows[wid] = []
            elif target == "front":
                wid = next(iter(self.windows))
            else:
                wid = int(target)
            self.next_id += 1
            self.windows[wid].append({"id": self.next_id, "title": "Example", "loading": False,
                                      "url": url + "/" if self.loads else "chrome-error://x"})
            self.active[wid] = self.next_id
            return RunResult(ok=True, out=f"{self.next_id}{US}{wid}")
        if script == chrome.TAB_STATE:
            tid = int(argv[0])
            for wid, tabs in self.windows.items():
                for t in tabs:
                    if t["id"] == tid:
                        return RunResult(ok=True, out=US.join(["1", str(t["loading"]).lower(), t["url"], str(wid)]))
            return RunResult(ok=True, out="0")
        if script == chrome.CLOSE:
            tid = int(argv[0])
            for tabs in self.windows.values():
                for t in tabs:
                    if t["id"] == tid:
                        tabs.remove(t)
                        return RunResult(ok=True, out="closed")
            return RunResult(ok=True, out="missing")
        if script == chrome.ACTIVATE:
            wi, ti = int(argv[0]), int(argv[1])
            wid = list(self.windows)[wi - 1]
            tid = self.windows[wid][ti - 1]["id"]
            self.active[wid] = tid
            self.windows = {wid: self.windows[wid], **{k: v for k, v in self.windows.items() if k != wid}}
            return RunResult(ok=True, out=str(tid))
        raise AssertionError("unknown script")


def pid():
    return PID


def no_sleep(_):
    pass


# ---- Chrome ----


def test_close_refuses_user_tab_without_running_any_script():
    fake = FakeChrome()
    r = chrome.close_tab(11, run=fake, pid_fn=pid)
    assert r.outcome == "failed" and "refused" in r.detail
    assert fake.calls == []
    assert any(t["id"] == 11 for t in fake.windows[1])


def test_move_refuses_user_tab_without_running_any_script():
    fake = FakeChrome()
    r = chrome.move_tab(11, 2, run=fake, pid_fn=pid)
    assert r.outcome == "failed" and "refused" in r.detail and fake.calls == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "about:blank",
                                 "https://ex ample.com", "", "data:text/html,hi", "https:///nohost"])
def test_url_scheme_allowlist_refuses(url):
    fake = FakeChrome()
    r = chrome.open_url(url, "new", run=fake, pid_fn=pid)
    assert r.outcome == "failed" and "refused" in r.detail and fake.calls == []


def test_open_records_ownership_and_verifies_load():
    fake = FakeChrome()
    r = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    assert r.outcome == "verified" and r.tab == 101
    assert chrome.is_owned(101, PID) and not chrome.is_owned(11, PID)


def test_url_goes_as_argv_not_spliced_into_script():
    fake = FakeChrome()
    evil = 'https://example.com/"; do shell script "rm -rf ~'
    # the space makes this one refused; use a quote-only variant that passes the allowlist
    evil = 'https://example.com/a"&(do_shell_script)'
    chrome.open_url(evil, "front", run=fake, pid_fn=pid, sleep=no_sleep)
    script, argv = fake.calls[0]
    assert script == chrome.OPEN and argv[0] == evil and evil not in script


def test_scripts_use_no_system_events_or_keystrokes():
    src = "\n".join([chrome.LIST, chrome.OPEN, chrome.TAB_STATE, chrome.CLOSE, chrome.ACTIVATE])
    assert "osascript" not in inspect.getsource(tables)
    for banned in ("System Events", "keystroke", "key code", "click", "reload", "do shell script", "execute"):
        assert banned not in src


def test_unverified_when_tab_never_reaches_url():
    fake = FakeChrome(loads=False)
    r = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, wait_s=0, sleep=no_sleep)
    assert r.outcome == "unverified" and chrome.is_owned(r.tab, PID)


def test_close_owned_tab_verified_and_forgotten():
    fake = FakeChrome()
    r = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    c = chrome.close_tab(r.tab, run=fake, pid_fn=pid)
    assert c.outcome == "verified" and not chrome.is_owned(r.tab, PID)
    assert any(t["id"] == 11 for t in fake.windows[1])


def test_ownership_dropped_when_chrome_restarts():
    fake = FakeChrome()
    r = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    fake.calls.clear()
    c = chrome.close_tab(r.tab, run=fake, pid_fn=lambda: PID + 1)  # same id, new Chrome process
    assert c.outcome == "failed" and "refused" in c.detail and fake.calls == []


def test_move_opens_in_target_then_closes_original():
    fake = FakeChrome()
    a = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    b = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    target_win = [t["window"] for t in chrome.list_tabs(fake, PID) if t["tab_id"] == b.tab][0]
    m = chrome.move_tab(a.tab, target_win, run=fake, pid_fn=pid, sleep=no_sleep)
    assert m.outcome == "verified"
    assert chrome.tab_state(a.tab, fake) is None
    assert chrome.tab_state(m.tab, fake)["window_id"] == chrome.tab_state(b.tab, fake)["window_id"]
    assert chrome.is_owned(m.tab, PID) and not chrome.is_owned(a.tab, PID)


def test_move_keeps_original_when_copy_does_not_load():
    fake = FakeChrome()
    a = chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    chrome.open_url("https://example.com", "new", run=fake, pid_fn=pid, sleep=no_sleep)
    fake.loads = False
    m = chrome.move_tab(a.tab, 3, run=fake, pid_fn=pid, wait_s=0, sleep=no_sleep)
    assert m.outcome == "unverified" and "left open" in m.detail
    assert chrome.tab_state(a.tab, fake) is not None


def test_activate_user_tab_verified():
    fake = FakeChrome()
    chrome.open_url("https://example.com", "front", run=fake, pid_fn=pid, sleep=no_sleep)
    r = chrome.activate_tab(1, 1, run=fake)
    assert r.outcome == "verified" and r.tab == 11


def test_permission_denied_gives_clear_message():
    fake = FakeChrome()
    fake.denied = True
    with pytest.raises(chrome.PermissionNeeded) as e:
        chrome.list_tabs(fake, PID)
    assert "Privacy & Security > Automation" in str(e.value)
    assert chrome.needs_permission(RunResult(ok=False, timed_out=True))


def test_list_tabs_parses_titles_with_pipes_and_tabs():
    fake = FakeChrome()
    fake.windows[1][0]["title"] = "a | b\tc, d"
    t = chrome.list_tabs(fake, PID)[0]
    assert t["title"] == "a | b\tc, d" and t["active"] and not t["reach_owned"]


def test_chrome_and_table_tools_count_as_writes():
    assert {"table_to_note", "chrome_open_url", "chrome_close_tab", "chrome_move_tab"} <= WRITE_TOOLS
    out = chrome.output(chrome.Result("unverified", "x", 5), "chrome_close_tab")
    assert parse_outcome(out.content) == "unverified"
    rb = ReceiptBuilder("t")
    rb.feed({"type": "tool_call_end", "name": "chrome_close_tab", "status": "ok", "content": out.content})
    assert rb.receipt().write_outcome == "unverified"


# ---- tables ----

TRUTH = [["Region", "Q1 sales", "Change", "Note"],
         ["North", "1,204", "-3.5%", ""],
         ["South", "980", "12%", "new"]]


def reply(md):
    return lambda body: {"choices": [{"message": {"content": md}}], "usage": {}}


def png(tmp_path):
    caps = Path(tmp_path / ".reach" / "captures")
    caps.mkdir(parents=True, exist_ok=True)
    p = caps / "t.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    return p


def test_split_row_keeps_empty_cells_and_escaped_pipes():
    assert tables.split_row("| a |  | c\\|d |") == ["a", "", "c\\|d"]


def test_validate_accepts_rectangular_and_rejects_ragged():
    rows, why = tables.validate(tables.to_markdown(TRUTH))
    assert rows == TRUTH and why == ""
    rows, why = tables.validate("| a | b |\n|---|---|\n| 1 | 2 | 3 |")
    assert rows is None and "rectangular" in why
    rows, why = tables.validate("| a | b |\n| 1 | 2 |")
    assert rows is None and "separator" in why
    assert tables.validate("no table here")[0] is None


def test_extract_strips_code_fences_and_prose():
    text = "Here it is:\n```markdown\n| a | b |\n|:--|--:|\n| 1 |  |\n```\nDone."
    rows, _ = tables.validate(tables.extract_table(text))
    assert rows == [["a", "b"], ["1", ""]]


def test_prompt_forbids_invention_and_marks_unreadable():
    body = tables.build_request(b"png")
    sys = body["messages"][0]["content"]
    assert "?" in sys and "invent" in sys and "empty" in sys.lower() and body["temperature"] == 0


def test_invalid_table_writes_nothing(tmp_path):
    p = png(tmp_path)
    notes = native.NOTES_FILE
    r, md = tables.table_to_note(str(p), "file", send=reply("| a | b |\n|---|---|\n| 1 |"))
    assert r.outcome == "failed" and md == "" and not notes.exists()


def test_valid_table_written_and_verified_with_unknown_marker(tmp_path):
    p = png(tmp_path)
    got = "| Region | Q1 sales | Change | Note |\n|---|---|---|---|\n| North | 1,204 | -3.5% |  |\n| South | ? | 12% | new |"
    r, md = tables.table_to_note(str(p), "file", send=reply(got))
    assert r.outcome == "verified" and "1 unreadable" in r.detail
    assert md in native.NOTES_FILE.read_text()
    rows, _ = tables.validate(md)
    assert tables.cell_accuracy(TRUTH, rows) == (11, 12)


def test_cell_accuracy_counts_missing_as_wrong():
    assert tables.cell_accuracy(TRUTH, None) == (0, 12)
    assert tables.cell_accuracy(TRUTH, TRUTH[:2]) == (8, 12)


def test_image_outside_reach_dir_refused(tmp_path):
    outside = tmp_path / "x.png"
    outside.write_bytes(b"\x89PNG")
    with pytest.raises(PermissionError):
        tables.table_to_note(str(outside), "file", send=reply("| a |\n|---|\n| 1 |"))


def test_tools_registered():
    from reach.intern_ext import ReachTools

    names = {t.name for t in ReachTools().tools()}
    assert {"chrome_list_tabs", "chrome_open_url", "chrome_activate_tab", "chrome_close_tab", "chrome_move_tab",
            "table_to_note", "note_append"} <= names
    assert json.dumps(sorted(names))
