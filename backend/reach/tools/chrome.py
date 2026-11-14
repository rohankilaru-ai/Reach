"""Bounded Google Chrome control through Chrome's own AppleScript dictionary, with verified readback.

Safety rules, enforced in code before any script runs:
- Listing tabs and switching to a tab (activate) are allowed for every tab.
- Closing and moving are allowed ONLY for tabs Reach opened itself. Their Chrome tab ids are kept in
  `.reach/chrome_owned.json` together with Chrome's pid; when Chrome restarts (new pid) the set is dropped, because
  Chrome reuses tab ids across restarts.
- A tab's URL is only ever set on a tab created in that same script. A user's tab is never navigated, reloaded or
  modified.
- Only http/https URLs open. Every value (URL, ids) goes to osascript as `argv`, never into the script source.
- No keystrokes, no mouse events, no System Events.

Every action reads Chrome back afterwards and reports `outcome=verified|unverified|failed` on the first line.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import append_tool_entry
from ..paths import reach_dir

OSASCRIPT_TIMEOUT_S = 10
LOAD_WAIT_S = 15.0
POLL_S = 0.5
US, RS = "\x1f", "\x1e"  # unit / record separators: titles can contain anything else
PERMISSION_MSG = "needs you: allow Terminal to control Google Chrome in Privacy & Security > Automation"
Outcome = Literal["verified", "unverified", "failed"]


@dataclass
class RunResult:
    ok: bool
    out: str = ""
    err: str = ""
    timed_out: bool = False


Runner = Callable[[str, list[str]], RunResult]
"""(AppleScript source, argv) -> RunResult."""


def run_osascript(script: str, argv: list[str]) -> RunResult:
    try:
        r = subprocess.run(["osascript", "-", *argv], input=script, capture_output=True, text=True,
                           timeout=OSASCRIPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return RunResult(ok=False, timed_out=True)
    except OSError as e:
        return RunResult(ok=False, err=type(e).__name__)
    return RunResult(ok=r.returncode == 0, out=r.stdout.rstrip("\n"), err=r.stderr.strip())


def needs_permission(r: RunResult) -> bool:
    """Automation denied (-1743) or the macOS prompt still waiting for the owner (osascript times out)."""
    e = r.err.lower()
    return r.timed_out or "-1743" in e or "not authorized" in e or "not allowed to send apple events" in e


def chrome_pid() -> int | None:
    try:
        r = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return None
    pids = [int(x) for x in r.stdout.split() if x.isdigit()]
    return min(pids) if pids else None


# ---- pure checks ----


def check_url(url: str) -> str:
    """Only http/https with a host; no whitespace or control characters."""
    u = (url or "").strip()
    if not u or len(u) > 2048 or any(c.isspace() or ord(c) < 32 for c in u):
        raise ValueError("refused: not a plain URL")
    parts = urlsplit(u)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError("refused: only http and https URLs open")
    return u


def norm_url(u: str) -> str:
    p = urlsplit((u or "").strip())
    path = p.path.rstrip("/")
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path}{q}"


# ---- ownership state ----


def state_path() -> Path:
    return reach_dir() / "chrome_owned.json"


def load_owned(pid: int | None) -> dict[str, dict[str, Any]]:
    """Reach-owned tabs for the running Chrome. A different (or no) pid means Chrome restarted: nothing is owned."""
    try:
        s = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    if pid is None or s.get("pid") != pid:
        return {}
    return dict(s.get("tabs") or {})


def save_owned(pid: int | None, tabs: dict[str, dict[str, Any]]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": pid, "tabs": tabs}, indent=1))


def is_owned(tab_id: int, pid: int | None) -> bool:
    return str(tab_id) in load_owned(pid)


# ---- AppleScript (values only via argv) ----

LIST = """on run argv
set US to character id 31
set RS to character id 30
set out to ""
tell application "Google Chrome"
  set wi to 0
  repeat with w in windows
    set wi to wi + 1
    set aid to id of active tab of w
    set ti to 0
    repeat with t in tabs of w
      set ti to ti + 1
      set out to out & wi & US & (id of w) & US & ti & US & (id of t) & US & (title of t) & US & (URL of t) & US & ((id of t) = aid) & RS
    end repeat
  end repeat
end tell
return out
end run"""

OPEN = """on run argv
set u to item 1 of argv
set target to item 2 of argv
tell application "Google Chrome"
  if target is "new" or (count of windows) = 0 then
    set w to make new window
    set t to active tab of w
    set URL of t to u
  else
    if target is "front" then
      set w to window 1
    else
      set w to window id (target as integer)
    end if
    set t to make new tab at end of tabs of w with properties {URL:u}
  end if
  return ((id of t) as text) & (character id 31) & ((id of w) as text)
end tell
end run"""

TAB_STATE = """on run argv
set tid to (item 1 of argv) as integer
tell application "Google Chrome"
  repeat with w in windows
    repeat with i from 1 to (count of tabs of w)
      set t to tab i of w
      if (id of t) = tid then return "1" & (character id 31) & ((loading of t) as text) & (character id 31) & (URL of t) & (character id 31) & ((id of w) as text)
    end repeat
  end repeat
end tell
return "0"
end run"""

CLOSE = """on run argv
set tid to (item 1 of argv) as integer
tell application "Google Chrome"
  repeat with w in windows
    repeat with i from 1 to (count of tabs of w)
      if (id of tab i of w) = tid then
        close tab i of w
        return "closed"
      end if
    end repeat
  end repeat
end tell
return "missing"
end run"""

ACTIVATE = """on run argv
set wi to (item 1 of argv) as integer
set ti to (item 2 of argv) as integer
tell application "Google Chrome"
  set w to window wi
  set tid to id of tab ti of w
  set active tab index of w to ti
  set index of w to 1
  activate
  return tid as text
end tell
end run"""


# ---- operations ----


@dataclass
class Result:
    outcome: Outcome
    detail: str = ""
    tab: int | None = None


class PermissionNeeded(Exception):
    pass


def _call(run: Runner, script: str, argv: list[str]) -> RunResult:
    r = run(script, argv)
    if not r.ok and needs_permission(r):
        raise PermissionNeeded(PERMISSION_MSG)
    return r


def list_tabs(run: Runner = run_osascript, pid: int | None = None) -> list[dict[str, Any]]:
    r = _call(run, LIST, [])
    if not r.ok:
        raise RuntimeError(f"Chrome did not answer: {r.err[:120]}")
    owned = load_owned(pid)
    tabs = []
    for rec in r.out.split(RS):
        f = rec.split(US)
        if len(f) != 7:
            continue
        tabs.append({"window": int(f[0]), "window_id": int(f[1]), "index": int(f[2]), "tab_id": int(f[3]),
                     "title": f[4], "url": f[5], "active": f[6] == "true", "reach_owned": f[3] in owned})
    return tabs


def tab_state(tab_id: int, run: Runner) -> dict[str, Any] | None:
    r = _call(run, TAB_STATE, [str(tab_id)])
    if not r.ok:
        raise RuntimeError(f"Chrome did not answer: {r.err[:120]}")
    f = r.out.split(US)
    if f[0] != "1" or len(f) != 4:
        return None
    return {"loading": f[1] == "true", "url": f[2], "window_id": int(f[3])}


def wait_loaded(tab_id: int, url: str, run: Runner, wait_s: float = LOAD_WAIT_S, sleep=time.sleep) -> tuple[Outcome, str]:
    """Poll the tab until it stops loading at the requested URL (trailing slash and host case ignored)."""
    deadline = time.monotonic() + wait_s
    st = None
    while True:
        st = tab_state(tab_id, run)
        if st is None:
            return "failed", "the new tab is gone"
        if not st["loading"] and norm_url(st["url"]) == norm_url(url):
            return "verified", f"tab {tab_id} loaded {st['url']}"
        if time.monotonic() >= deadline:
            break
        sleep(POLL_S)
    why = "still loading" if st["loading"] else f"shows {st['url']!r}, not the requested URL"
    return "unverified", f"tab {tab_id} {why}"


def open_url(url: str, target: str = "front", run: Runner = run_osascript, pid_fn=chrome_pid,
             wait_s: float = LOAD_WAIT_S, sleep=time.sleep) -> Result:
    try:
        url = check_url(url)
    except ValueError as e:
        return Result("failed", str(e))
    r = _call(run, OPEN, [url, target])
    f = r.out.split(US)
    if not r.ok or len(f) != 2 or not f[0].isdigit():
        return Result("failed", f"Chrome did not open the tab: {r.err[:120]}")
    tab_id, win_id = int(f[0]), int(f[1])
    pid = pid_fn()
    owned = load_owned(pid)
    owned[str(tab_id)] = {"url": url, "window_id": win_id, "opened": int(time.time())}
    save_owned(pid, owned)  # recorded before readback, so even an unverified tab can be closed later
    outcome, detail = wait_loaded(tab_id, url, run, wait_s, sleep)
    return Result(outcome, detail, tab_id)


def close_tab(tab_id: int, run: Runner = run_osascript, pid_fn=chrome_pid) -> Result:
    pid = pid_fn()
    owned = load_owned(pid)
    if str(tab_id) not in owned:
        return Result("failed", f"refused: tab {tab_id} is not a tab Reach opened; Reach closes only its own tabs",
                      tab_id)
    r = _call(run, CLOSE, [str(tab_id)])
    if not r.ok:
        return Result("failed", f"Chrome did not close the tab: {r.err[:120]}", tab_id)
    still = tab_state(tab_id, run)
    if still is None:
        owned.pop(str(tab_id), None)
        save_owned(pid, owned)
        note = "was already closed" if r.out.strip() == "missing" else "closed; readback: tab is gone"
        return Result("verified", f"tab {tab_id} {note}", tab_id)
    return Result("failed", f"tab {tab_id} is still open after close", tab_id)


def move_tab(tab_id: int, to_window: int, run: Runner = run_osascript, pid_fn=chrome_pid,
             wait_s: float = LOAD_WAIT_S, sleep=time.sleep) -> Result:
    """Chrome's dictionary cannot move tabs between windows: open the same URL in the target window, verify it
    loaded, and only then close the original."""
    pid = pid_fn()
    if str(tab_id) not in load_owned(pid):
        return Result("failed", f"refused: tab {tab_id} is not a tab Reach opened; Reach moves only its own tabs",
                      tab_id)
    src = tab_state(tab_id, run)
    if src is None:
        return Result("failed", f"tab {tab_id} is not open", tab_id)
    windows = {t["window"]: t["window_id"] for t in list_tabs(run, pid)}
    if to_window not in windows:
        return Result("failed", f"no window {to_window}; chrome_list_tabs shows the window numbers", tab_id)
    if windows[to_window] == src["window_id"]:
        return Result("failed", "the tab is already in that window", tab_id)
    new = open_url(src["url"], str(windows[to_window]), run, pid_fn, wait_s, sleep)
    if new.outcome != "verified":
        return Result(new.outcome if new.tab else "failed",
                      f"moved copy not confirmed ({new.detail}); original tab {tab_id} left open", new.tab)
    closed = close_tab(tab_id, run, pid_fn)
    after = tab_state(new.tab, run) if new.tab is not None else None
    if closed.outcome == "verified" and after is not None and after["window_id"] == windows[to_window]:
        return Result("verified", f"tab now {new.tab} in window {to_window}; original {tab_id} closed", new.tab)
    return Result("unverified", f"new tab {new.tab}: {new.detail}; original: {closed.detail}", new.tab)


def activate_tab(window: int, index: int, run: Runner = run_osascript) -> Result:
    if window < 1 or index < 1:
        return Result("failed", "window and index start at 1")
    r = _call(run, ACTIVATE, [str(int(window)), str(int(index))])
    if not r.ok or not r.out.strip().isdigit():
        return Result("failed", f"no tab {index} in window {window}: {r.err[:120]}")
    tid = int(r.out.strip())
    tabs = list_tabs(run)
    hit = [t for t in tabs if t["tab_id"] == tid]
    if hit and hit[0]["active"] and hit[0]["window"] == 1:
        return Result("verified", f"tab {tid} is the active tab of the front window", tid)
    if hit and hit[0]["active"]:
        return Result("unverified", f"tab {tid} is active but its window is not in front", tid)
    return Result("failed", f"tab {tid} is not active after switching", tid)


def first_line(r: Result) -> str:
    return f"outcome={r.outcome} destination=chrome tab={r.tab if r.tab is not None else '-'}"


def output(r: Result, tool: str) -> ToolOutput:
    append_tool_entry("action", tool=tool, destination="chrome", chars=0, outcome=r.outcome, tab=r.tab)
    msg = {
        "verified": "Read back from Chrome: done.",
        "unverified": "Could not confirm this in Chrome. Tell the user it is NOT confirmed.",
        "failed": "It did NOT happen. Tell the user.",
    }[r.outcome]
    return ToolOutput(status="ok" if r.outcome == "verified" else "error", content=f"{first_line(r)}\n{msg}\n{r.detail}")


def permission_output(tool: str) -> ToolOutput:
    append_tool_entry("action", tool=tool, destination="chrome", chars=0, outcome="failed", error="automation")
    return ToolOutput(status="error", content=f"outcome=failed destination=chrome tab=-\n{PERMISSION_MSG}")


async def _guarded(tool: str, fn, *a) -> ToolOutput:
    import asyncio

    try:
        return output(await asyncio.to_thread(fn, *a), tool)
    except PermissionNeeded:
        return permission_output(tool)
    except RuntimeError as e:
        return output(Result("failed", str(e)), tool)


# ---- tools ----


class ListArgs(BaseModel):
    pass


class ChromeListTabs(Tool):
    name: ClassVar[str] = "chrome_list_tabs"
    description: ClassVar[str] = (
        "List the open Google Chrome tabs: window number, tab index, tab id, title, URL, whether it is active and "
        "whether Reach opened it. Read-only."
    )
    Args: ClassVar[type[BaseModel]] = ListArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        if chrome_pid() is None:
            return ToolOutput(content="Google Chrome is not running.")
        try:
            tabs = await asyncio.to_thread(list_tabs, run_osascript, chrome_pid())
        except PermissionNeeded:
            return ToolOutput(status="error", content=PERMISSION_MSG)
        except RuntimeError as e:
            return ToolOutput(status="error", content=str(e))
        lines = [f"window {t['window']} tab {t['index']} id={t['tab_id']}{' ACTIVE' if t['active'] else ''}"
                 f"{' reach-owned' if t['reach_owned'] else ''}: {t['title'][:80]} <{t['url'][:200]}>" for t in tabs]
        return ToolOutput(content=f"{len(tabs)} tabs\n" + "\n".join(lines))


class OpenArgs(BaseModel):
    url: str = Field(description="http or https URL to open")
    new_window: bool = Field(default=False, description="Open in a new window instead of a new tab in the front window")


class ChromeOpenUrl(Tool):
    name: ClassVar[str] = "chrome_open_url"
    description: ClassVar[str] = (
        "Open an http/https URL in a NEW Google Chrome tab (or a new window) and confirm it loaded. The tab is "
        "recorded as Reach-owned, so Reach may later close or move it. Never changes the user's existing tabs. "
        "The first line of the result gives the outcome; report it truthfully."
    )
    Args: ClassVar[type[BaseModel]] = OpenArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return await _guarded(self.name, open_url, args.url, "new" if args.new_window else "front")


class ActivateArgs(BaseModel):
    window: int = Field(description="Window number from chrome_list_tabs (1 = front)")
    index: int = Field(description="Tab index in that window from chrome_list_tabs")


class ChromeActivateTab(Tool):
    name: ClassVar[str] = "chrome_activate_tab"
    description: ClassVar[str] = (
        "Switch Google Chrome to a tab (any tab, including the user's) and bring its window to the front. It does "
        "not reload or change the tab. Get the window and index from chrome_list_tabs first."
    )
    Args: ClassVar[type[BaseModel]] = ActivateArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return await _guarded(self.name, activate_tab, args.window, args.index)


class CloseArgs(BaseModel):
    tab_ref: int = Field(description="Tab id from chrome_list_tabs or chrome_open_url (must be Reach-owned)")


class ChromeCloseTab(Tool):
    name: ClassVar[str] = "chrome_close_tab"
    description: ClassVar[str] = (
        "Close a Google Chrome tab that Reach itself opened (by tab id). Tabs the user opened are refused. "
        "Reads Chrome back to confirm the tab is gone."
    )
    Args: ClassVar[type[BaseModel]] = CloseArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return await _guarded(self.name, close_tab, args.tab_ref)


class MoveArgs(BaseModel):
    tab_ref: int = Field(description="Tab id of a Reach-owned tab")
    to_window: int = Field(description="Target window number from chrome_list_tabs")


class ChromeMoveTab(Tool):
    name: ClassVar[str] = "chrome_move_tab"
    description: ClassVar[str] = (
        "Move a Reach-owned Google Chrome tab to another window: opens the same URL there, confirms it loaded, then "
        "closes the original. The moved tab gets a new tab id. Tabs the user opened are refused."
    )
    Args: ClassVar[type[BaseModel]] = MoveArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return await _guarded(self.name, move_tab, args.tab_ref, args.to_window)


CHROME_WRITE_TOOLS = frozenset({"chrome_open_url", "chrome_activate_tab", "chrome_close_tab", "chrome_move_tab"})


def tools() -> list[Tool]:
    return [ChromeListTabs(), ChromeOpenUrl(), ChromeActivateTab(), ChromeCloseTab(), ChromeMoveTab()]

