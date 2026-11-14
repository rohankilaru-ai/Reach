"""Do mode (EXPERIMENTAL, opt-in with REACH_DO_MODE=1): a few UI actions in other Mac apps through the Accessibility API.

Only semantic Accessibility calls are used: read attributes, `AXPress`, and setting `AXValue` on a text field. Reach
never sends keystrokes or synthesized mouse events, never drags or scrolls.

Safety, enforced in code:
- Accessibility trust is checked with the prompt option OFF; without it every tool returns a "needs you" message.
- Only the frontmost app's focused window (and, when asked, its menu bar items) is described or acted on.
- Deny-listed apps (System Settings, Keychain Access, Passwords, security agents, terminals) are refused outright.
- Secure text fields are never listed or touched; text fields labelled like passwords, PINs or card numbers are refused.
- Elements whose title reads like paying, buying, deleting, erasing, transferring or submitting an order are refused:
  the owner does that step.
- Every action shows the overlay ghost pointer on the element ("About to press: <title>") 1.0 s before acting, then
  re-resolves the element; a changed frontmost app, window or element is refused as stale.
- Kill switch: any mouse or keyboard input from the user after the action started aborts it and every later action in
  the same ask ("user took over"). Esc is handled by the overlay's abort.
- At most 8 actions per ask.

Every action reads back and reports `outcome=verified|unverified|failed` on the first line (same receipt semantics as
native note writes) and is logged to the Drex log as type `action`.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import CURRENT_OUT, CURRENT_TASK, append_action

ENV_FLAG = "REACH_DO_MODE"
MAX_ELEMENTS = 80
MAX_NODES = 3000
MAX_DEPTH = 14
ACTION_BUDGET = 8
SHOW_BEFORE_S = 1.0
READBACK_S = 1.5
POLL_S = 0.1

PRESS_ROLES = frozenset({"AXButton", "AXCheckBox", "AXRadioButton", "AXMenuItem", "AXDisclosureTriangle", "AXLink"})
TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox"})
SECURE = "AXSecureTextField"

DENY_BUNDLES = frozenset({
    "com.apple.systempreferences", "com.apple.SystemSettings", "com.apple.keychainaccess", "com.apple.Passwords",
    "com.apple.SecurityAgent", "com.apple.loginwindow", "com.apple.LocalAuthentication.UIAgent",
    "com.apple.coreservices.uiagent", "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable",
    "net.kovidgoyal.kitty", "com.mitchellh.ghostty", "io.alacritty", "com.github.wez.wezterm",
})
DANGER_RE = re.compile(
    r"\b(pay|payment|purchase|buy|delete|erase|send money|transfer|confirm purchase|submit order|place order|"
    r"checkout|check out|wipe|uninstall|empty trash)\b", re.I)
SENSITIVE_FIELD_RE = re.compile(
    r"\b(password|passcode|passphrase|pin|cvv|cvc|card number|security code|social security|ssn|api key|token|"
    r"secret)\b", re.I)
OWNER_STEP = "needs the owner to do this step"


# ---- the Accessibility layer (real one lazy-imports pyobjc; tests pass a fake) ----


class AXBackend(Protocol):
    def trusted(self) -> bool: ...
    def frontmost(self) -> tuple[Any, int, str, str] | None: ...  # (app element, pid, bundle id, name)
    def window(self, app: Any) -> Any | None: ...
    def menubar(self, app: Any) -> Any | None: ...
    def attr(self, el: Any, name: str) -> Any: ...
    def children(self, el: Any) -> list[Any]: ...
    def actions(self, el: Any) -> list[str]: ...
    def settable(self, el: Any, name: str) -> bool: ...
    def set_attr(self, el: Any, name: str, value: Any) -> bool: ...
    def perform(self, el: Any, action: str) -> bool: ...
    def frame(self, el: Any) -> tuple[float, float, float, float] | None: ...
    def idle_seconds(self) -> float: ...


class MacAX:
    """pyobjc-backed Accessibility calls. Needs the `do` extra (pyobjc-framework-ApplicationServices, -Quartz)."""

    def __init__(self) -> None:
        import ApplicationServices as AS  # noqa: N814
        import Quartz

        self.AS, self.Q = AS, Quartz
        AS.AXUIElementSetMessagingTimeout(AS.AXUIElementCreateSystemWide(), 2.0)  # a hung app cannot hang Reach

    def trusted(self) -> bool:
        AS = self.AS
        return bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: False}))

    def attr(self, el: Any, name: str) -> Any:
        err, v = self.AS.AXUIElementCopyAttributeValue(el, name, None)
        return v if err == 0 else None

    def frontmost(self) -> tuple[Any, int, str, str] | None:
        from AppKit import NSRunningApplication

        app = self.attr(self.AS.AXUIElementCreateSystemWide(), "AXFocusedApplication")
        pid = None
        if app is not None:
            err, pid = self.AS.AXUIElementGetPid(app, None)
            pid = pid if err == 0 else None
        if pid is None:  # the system-wide element often answers kAXErrorCannotComplete; ask Launch Services (fresh)
            pid = _lsappinfo_front_pid()
            if pid is None:
                return None
            app = self.AS.AXUIElementCreateApplication(pid)
        ra = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        bundle = str(ra.bundleIdentifier() or "") if ra else ""
        name = str(self.attr(app, "AXTitle") or (ra.localizedName() if ra else "") or "")
        return app, int(pid), bundle, name

    def window(self, app: Any) -> Any | None:
        return self.attr(app, "AXFocusedWindow") or self.attr(app, "AXMainWindow")

    def menubar(self, app: Any) -> Any | None:
        return self.attr(app, "AXMenuBar")

    def children(self, el: Any) -> list[Any]:
        return list(self.attr(el, "AXChildren") or [])

    def actions(self, el: Any) -> list[str]:
        err, names = self.AS.AXUIElementCopyActionNames(el, None)
        return [str(n) for n in (names or [])] if err == 0 else []

    def settable(self, el: Any, name: str) -> bool:
        err, ok = self.AS.AXUIElementIsAttributeSettable(el, name, None)
        return err == 0 and bool(ok)

    def set_attr(self, el: Any, name: str, value: Any) -> bool:
        return self.AS.AXUIElementSetAttributeValue(el, name, value) == 0

    def perform(self, el: Any, action: str) -> bool:
        return self.AS.AXUIElementPerformAction(el, action) == 0

    def frame(self, el: Any) -> tuple[float, float, float, float] | None:
        AS = self.AS
        pos, size = self.attr(el, "AXPosition"), self.attr(el, "AXSize")
        if pos is None or size is None:
            return None
        ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        if not (ok1 and ok2):
            return None
        return float(p.x), float(p.y), float(s.width), float(s.height)

    def idle_seconds(self) -> float:
        # Real user input only: kCGAnyInputEventType also resets every few seconds with nobody at the machine.
        Q = self.Q
        kinds = (Q.kCGEventMouseMoved, Q.kCGEventLeftMouseDown, Q.kCGEventRightMouseDown, Q.kCGEventOtherMouseDown,
                 Q.kCGEventLeftMouseDragged, Q.kCGEventRightMouseDragged, Q.kCGEventScrollWheel, Q.kCGEventKeyDown,
                 Q.kCGEventFlagsChanged)
        return min(float(Q.CGEventSourceSecondsSinceLastEventType(Q.kCGEventSourceStateHIDSystemState, k))
                   for k in kinds)


def _lsappinfo_front_pid() -> int | None:
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=3).stdout.strip()
        out = subprocess.run(["lsappinfo", "info", "-only", "pid", asn], capture_output=True, text=True,
                             timeout=3).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    m = re.search(r"pid\"?\s*=\s*(\d+)", out)
    return int(m.group(1)) if m else None


def host_app() -> str:
    """The .app this process runs under (the one that needs the Accessibility grant), found by walking up `ps`."""
    pid = os.getpid()
    for _ in range(12):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=3).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            break
        if not out:
            break
        ppid, _, comm = out.partition(" ")
        m = re.search(r"/([^/]+)\.app/", comm)
        if m:
            return m.group(1)
        pid = int(ppid)
        if pid <= 1:
            break
    return "the app that runs Reach"


def needs_accessibility() -> str:
    return (f"needs you: allow Accessibility for {host_app()} in System Settings > Privacy & Security > "
            "Accessibility, then ask again")


# ---- describe ----


@dataclass
class Elem:
    index: int
    role: str
    subrole: str
    title: str
    enabled: bool
    kind: str  # press | text
    frame: tuple[float, float, float, float] | None
    handle: Any = field(default=None, repr=False)

    def line(self) -> str:
        on = "" if self.enabled else " (disabled)"
        return f"[{self.index}] {self.role} \"{self.title}\"{on}"


@dataclass
class Snapshot:
    pid: int
    bundle: str
    app: str
    window_title: str
    query: str | None
    elems: list[Elem]


def _label(ax: AXBackend, el: Any) -> str:
    for name in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp"):
        v = ax.attr(el, name)
        if v:
            return re.sub(r"\s+", " ", str(v)).strip()[:80]
    return ""


def is_secure(role: str, subrole: str) -> bool:
    return SECURE in (role, subrole)


def _collect(ax: AXBackend, root: Any, out: list[Elem], query: str | None, prefix: str = "", menus: bool = False) -> None:
    """Breadth-first over the tree; stable order, so the same window gives the same indexes."""
    queue: list[tuple[Any, int, str]] = [(root, 0, prefix)]
    seen = 0
    while queue and len(out) < MAX_ELEMENTS and seen < MAX_NODES:
        el, depth, path = queue.pop(0)
        seen += 1
        role = str(ax.attr(el, "AXRole") or "")
        subrole = str(ax.attr(el, "AXSubrole") or "")
        if is_secure(role, subrole):
            continue  # never listed, never descended into
        title = _label(ax, el)
        kind = "press" if role in PRESS_ROLES else ("text" if role in TEXT_ROLES else "")
        shown = f"{path} > {title}" if (menus and path and title) else title
        if kind and el is not root and (query is None or query.lower() in shown.lower()):
            if not (menus and role == "AXMenuItem" and not title):  # menu separators
                out.append(Elem(len(out), role, subrole, shown, ax.attr(el, "AXEnabled") is not False, kind,
                                ax.frame(el), el))
        if depth < MAX_DEPTH:
            sub = f"{path} > {title}" if (menus and path and title) else (title if menus and role == "AXMenuBarItem" else path)
            queue.extend((c, depth + 1, sub) for c in ax.children(el))


def describe(ax: AXBackend, app: str | None = None, query: str | None = None) -> Snapshot | str:
    """The interactive elements of the frontmost app's focused window, or a refusal string."""
    if not ax.trusted():
        return needs_accessibility()
    front = ax.frontmost()
    if front is None:
        return "No frontmost app found."
    app_el, pid, bundle, name = front
    if app and app.strip().lower() not in (name.lower(), bundle.lower()):
        return f"refused: {app} is not the frontmost app ({name}); ask the user to bring it to the front"
    if bundle in DENY_BUNDLES:
        return f"refused: Reach does not act in {name or bundle}; {OWNER_STEP}"
    win = ax.window(app_el)
    if win is None:
        return f"{name} has no focused window."
    elems: list[Elem] = []
    _collect(ax, win, elems, query or None)
    if query:
        bar = ax.menubar(app_el)
        if bar is not None:
            _collect(ax, bar, elems, query, menus=True)
    return Snapshot(pid, bundle, name, str(ax.attr(win, "AXTitle") or ""), query or None, elems[:MAX_ELEMENTS])


def render_snapshot(s: Snapshot) -> str:
    head = f"{s.app} ({s.bundle}) window \"{s.window_title}\": {len(s.elems)} interactive elements"
    lines = [e.line() for e in s.elems]
    tail = ("Use element_ref = the number in brackets with ax_press (buttons, checkboxes, menu items) or ax_set_text "
            "(text fields). Pass query to also search menu bar items, e.g. query='Plain Text'.")
    return "\n".join([head, *lines, tail])


# ---- acting ----


@dataclass
class TaskState:
    actions: int = 0
    took_over: bool = False
    snapshot: Snapshot | None = None


_STATE: dict[str, TaskState] = {}


def state() -> TaskState:
    tid = CURRENT_TASK.get()
    if len(_STATE) > 64:
        _STATE.clear()
    return _STATE.setdefault(tid, TaskState())


@dataclass
class ActResult:
    outcome: str  # verified | unverified | failed
    destination: str
    chars: int
    detail: str


def _ref_index(ref: str) -> int | None:
    m = re.match(r"\s*\[?(\d+)\]?", ref or "")
    return int(m.group(1)) if m else None


def refuse_reason(e: Elem, bundle: str, *, kind: str) -> str | None:
    if bundle in DENY_BUNDLES:
        return f"refused: Reach does not act in this app; {OWNER_STEP}"
    if is_secure(e.role, e.subrole):
        return "refused: a password field is never touched"
    if DANGER_RE.search(e.title):
        return f"refused: \"{e.title}\" looks like a payment, delete or submit step; {OWNER_STEP}"
    if kind == "text" and SENSITIVE_FIELD_RE.search(e.title):
        return f"refused: \"{e.title}\" looks like a password, PIN or card field; {OWNER_STEP}"
    if e.kind != kind:
        want = "a text field" if kind == "text" else "a button, checkbox or menu item"
        return f"refused: [{e.index}] is a {e.role}, not {want}"
    if not e.enabled:
        return f"refused: [{e.index}] \"{e.title}\" is disabled"
    return None


def _signature(ax: AXBackend, el: Any) -> tuple:
    """What a press can visibly change: the element's value/title, the focused window, its frame, the tree size."""
    front = ax.frontmost()
    if front is None:
        return ("none",)
    app_el = front[0]
    win = ax.window(app_el)
    n = 0
    if win is not None:
        tmp: list[Elem] = []
        _collect(ax, win, tmp, None)
        n = len(tmp)
    return (str(ax.attr(el, "AXValue")), str(ax.attr(el, "AXTitle")), front[1],
            str(ax.attr(win, "AXTitle")) if win is not None else "", ax.frame(win) if win is not None else None,
            len(ax.attr(app_el, "AXWindows") or []), n)


Emit = Any  # Callable[[float, float, str], None]


async def act(ax: AXBackend, kind: str, app: str, ref: str, text: str | None = None, *,
              emit: Emit | None = None, show_s: float = SHOW_BEFORE_S) -> ActResult:
    st = state()
    dest = f"{app}/{ref}".replace(" ", "_")[:60]
    if st.took_over:
        return ActResult("failed", dest, 0, "user took over: stopped all pending actions")
    if not ax.trusted():
        return ActResult("failed", dest, 0, needs_accessibility())
    snap = st.snapshot
    idx = _ref_index(ref)
    if snap is None or idx is None or not (0 <= idx < len(snap.elems)):
        return ActResult("failed", dest, 0, "refused: unknown element_ref; call ax_describe first")
    target = snap.elems[idx]
    dest = f"{snap.app}/{target.role}#{idx}".replace(" ", "_")
    if app.strip().lower() not in (snap.app.lower(), snap.bundle.lower()):
        return ActResult("failed", dest, 0, f"refused as stale: the last describe was of {snap.app}, not {app}")
    if why := refuse_reason(target, snap.bundle, kind=kind):
        return ActResult("failed", dest, 0, why)
    if st.actions >= ACTION_BUDGET:
        return ActResult("failed", dest, 0, f"refused: the action budget of {ACTION_BUDGET} for this ask is used up")
    st.actions += 1

    started = time.monotonic()
    verb = "press" if kind == "press" else "type into"
    if emit is not None and target.frame is not None:
        x, y, w, h = target.frame
        emit(x + w / 2, y + h / 2, f"About to {verb}: {target.title or target.role}"[:80])
    waited = 0.0
    while waited < show_s:  # show the user first; any input from them stops it
        await asyncio.sleep(POLL_S)
        waited = time.monotonic() - started
        if (idle := ax.idle_seconds()) + 0.05 < waited:
            st.took_over = True
            return ActResult("failed", dest, 0, f"user took over: input {idle:.2f}s ago; stopped before acting")

    fresh = await asyncio.to_thread(describe, ax, None, snap.query)
    if isinstance(fresh, str):
        return ActResult("failed", dest, 0, f"refused as stale: {fresh}")
    if (fresh.pid, fresh.window_title) != (snap.pid, snap.window_title) or idx >= len(fresh.elems):
        return ActResult("failed", dest, 0, "refused as stale: the frontmost app or window changed since describe")
    now = fresh.elems[idx]
    if (now.role, now.title) != (target.role, target.title):
        return ActResult("failed", dest, 0, "refused as stale: the element changed since describe")
    if why := refuse_reason(now, fresh.bundle, kind=kind):
        return ActResult("failed", dest, 0, why)
    if ax.idle_seconds() + 0.05 < time.monotonic() - started:
        st.took_over = True
        return ActResult("failed", dest, 0, "user took over: stopped before acting")

    if kind == "text":
        assert text is not None
        if not ax.settable(now.handle, "AXValue"):
            return ActResult("failed", dest, 0, f"[{idx}] \"{now.title}\" does not accept text through Accessibility")
        if not ax.set_attr(now.handle, "AXValue", text):
            return ActResult("failed", dest, 0, "the app refused the new value")
        after = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < READBACK_S:
            after = ax.attr(now.handle, "AXValue")
            if after is not None and _norm(str(after)) == _norm(text):
                return ActResult("verified", dest, len(text), "Read back the field: it holds exactly the text.")
            await asyncio.sleep(POLL_S)
        if after is None:
            return ActResult("unverified", dest, len(text), "Could not read the field back.")
        return ActResult("failed", dest, len(text), "Read back the field: it does NOT hold the text.")

    if "AXPress" not in ax.actions(now.handle):
        return ActResult("failed", dest, 0, f"[{idx}] \"{now.title}\" cannot be pressed through Accessibility")
    before = await asyncio.to_thread(_signature, ax, now.handle)
    if not ax.perform(now.handle, "AXPress"):
        return ActResult("failed", dest, 0, "the app refused the press")
    t0 = time.monotonic()
    while time.monotonic() - t0 < READBACK_S:
        await asyncio.sleep(POLL_S * 2)
        if await asyncio.to_thread(_signature, ax, now.handle) != before:
            return ActResult("verified", dest, 0, f"Pressed \"{now.title}\"; its state or the window changed.")
    return ActResult("unverified", dest, 0, f"Pressed \"{now.title}\" but saw no change, so this is NOT confirmed.")


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def result_output(r: ActResult, tool: str) -> ToolOutput:
    append_action(tool=tool, destination=r.destination, chars=r.chars, outcome=r.outcome, detail=r.detail[:200])
    content = f"outcome={r.outcome} destination={r.destination} chars={r.chars}\n{r.detail}"
    if r.outcome != "verified":
        content += "\nTell the user plainly that this was NOT confirmed done."
    return ToolOutput(status="ok" if r.outcome == "verified" else "error", content=content)


def overlay_pointer(x: float, y: float, label: str) -> None:
    """The overlay ghost pointer (global screen points, top-left origin, as Accessibility reports them)."""
    from ..contracts import Pointer

    put = CURRENT_OUT.get()
    if put is None:
        return
    try:
        put(Pointer(task_id=CURRENT_TASK.get(), x=x, y=y, label=label).model_dump_json())
    except Exception:
        pass


_AX: AXBackend | None = None


def backend() -> AXBackend:
    global _AX
    if _AX is None:
        _AX = MacAX()
    return _AX


def _backend_or_error() -> AXBackend | str:
    try:
        return backend()
    except ImportError:
        return "Do mode needs the optional Accessibility bindings: cd backend && uv sync --extra do"


# ---- tools ----


class DescribeArgs(BaseModel):
    app: str | None = Field(default=None, description="App name or bundle id; must be the frontmost app")
    query: str | None = Field(default=None, description="Optional filter; also searches the app's menu bar items")


class AXDescribe(Tool):
    name: ClassVar[str] = "ax_describe"
    description: ClassVar[str] = (
        "EXPERIMENTAL Do mode. List the buttons, checkboxes, menu items and text fields of the frontmost app's "
        "focused window, each with an index like [3]. Read-only. Call it before ax_press or ax_set_text."
    )
    Args: ClassVar[type[BaseModel]] = DescribeArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        ax = _backend_or_error()
        if isinstance(ax, str):
            return ToolOutput(status="error", content=ax)
        s = await asyncio.to_thread(describe, ax, args.app, args.query)
        if isinstance(s, str):
            return ToolOutput(status="error", content=s)
        state().snapshot = s
        return ToolOutput(content=render_snapshot(s))


class PressArgs(BaseModel):
    app: str = Field(description="The app name from ax_describe")
    element_ref: str = Field(description="The element index from ax_describe, e.g. '3'")


class AXPress(Tool):
    name: ClassVar[str] = "ax_press"
    description: ClassVar[str] = (
        "EXPERIMENTAL Do mode. Press one button, checkbox or menu item listed by ax_describe, after showing the user "
        "where. The first line gives the outcome (verified, unverified, failed); report it truthfully. It never "
        "pays, deletes, sends or submits: those return 'needs the owner to do this step'."
    )
    Args: ClassVar[type[BaseModel]] = PressArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        ax = _backend_or_error()
        if isinstance(ax, str):
            return ToolOutput(status="error", content=ax)
        r = await act(ax, "press", args.app, args.element_ref, emit=overlay_pointer)
        return result_output(r, self.name)


class SetTextArgs(BaseModel):
    app: str = Field(description="The app name from ax_describe")
    element_ref: str = Field(description="The text field index from ax_describe, e.g. '2'")
    text: str = Field(description="The full text the field should hold (replaces its contents)")


class AXSetText(Tool):
    name: ClassVar[str] = "ax_set_text"
    description: ClassVar[str] = (
        "EXPERIMENTAL Do mode. Replace the contents of one text field listed by ax_describe with text, after showing "
        "the user where, then read it back. Never for passwords. The first line gives the outcome."
    )
    Args: ClassVar[type[BaseModel]] = SetTextArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        ax = _backend_or_error()
        if isinstance(ax, str):
            return ToolOutput(status="error", content=ax)
        r = await act(ax, "text", args.app, args.element_ref, args.text, emit=overlay_pointer)
        return result_output(r, self.name)


def enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1"


def tools() -> list[Tool]:
    """The Do mode tools, only when REACH_DO_MODE=1."""
    return [AXDescribe(), AXPress(), AXSetText()] if enabled() else []
