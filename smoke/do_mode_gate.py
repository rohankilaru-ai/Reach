"""Do mode gate (EXPERIMENTAL): real Accessibility actions on a throwaway TextEdit document Reach creates itself.

  cd backend && uv run --extra do python ../smoke/do_mode_gate.py

1. makes a new TextEdit document through TextEdit's scripting dictionary and brings TextEdit to the front;
2. ax_describe finds the document window's text area;
3. ax_set_text puts a sentence in it, read back;
4. ax_press presses a harmless control (a format-bar checkbox, else Format > Make Plain Text from the menu bar);
5. closes the document WITHOUT saving (always, in a finally).
No keystrokes or mouse events are sent. Needs Accessibility granted to the app that runs it.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

from reach.tools import ax

SENTENCE = "Reach typed this sentence through the Accessibility API."


def osa(script: str, *argv: str) -> str:
    r = subprocess.run(["osascript", "-", *argv], input=script, capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else f"ERR {r.stderr.strip()[:200]}"


def main() -> int:
    mac = ax.MacAX()
    print("accessibility trusted:", mac.trusted(), "| host app:", ax.host_app())
    if not mac.trusted():
        print(ax.needs_accessibility())
        return 2
    import time

    t0 = time.monotonic()  # the kill switch aborts on any user input, so start in a quiet moment (max 120 s)
    while mac.idle_seconds() < 5 and time.monotonic() - t0 < 120:
        time.sleep(0.5)
    print(f"waited {time.monotonic() - t0:.0f}s for 5s without user input (idle now {mac.idle_seconds():.1f}s)")
    name = osa('tell application "TextEdit"\nset d to make new document\nreturn name of d\nend tell')
    print("created document:", name)
    osa('tell application "TextEdit" to activate')
    shown: list[tuple[float, float, str]] = []
    try:
        asyncio.run(asyncio.sleep(0.8))
        snap = ax.describe(mac, "TextEdit")
        if isinstance(snap, str):
            print("describe refused:", snap)
            return 1
        print(f"describe: window \"{snap.window_title}\", {len(snap.elems)} elements")
        if snap.window_title != name:
            print("front window is not the test document; stopping")
            return 1
        ax.state().snapshot = snap
        area = next((e for e in snap.elems if e.role == "AXTextArea"), None)
        print("text area:", area.line() if area else None)
        if area is None:
            return 1
        emit = lambda x, y, label: shown.append((round(x), round(y), label))  # noqa: E731

        def attempt(kind: str, ref: str, text: str | None = None):
            """Up to 3 tries; a 'user took over' abort means someone used the mouse or keys, so wait for quiet."""
            from reach.ledger import CURRENT_TASK

            for n in range(3):
                CURRENT_TASK.set(f"gate-{kind}-{n}")  # a fresh ask: fresh budget and takeover latch
                ax.state().snapshot = snap
                r = asyncio.run(ax.act(mac, kind, "TextEdit", ref, text, emit=emit))
                print(f"  try {n + 1}: outcome={r.outcome} | {r.detail}")
                if not r.detail.startswith("user took over"):
                    return r
                t1 = time.monotonic()
                while mac.idle_seconds() < 5 and time.monotonic() - t1 < 60:
                    time.sleep(0.5)
            return r

        r = attempt("text", str(area.index), SENTENCE)
        print(f"ax_set_text: outcome={r.outcome} | {r.detail}")
        readback = osa('on run argv\ntell application "TextEdit" to return text of document (item 1 of argv)\nend run',
                       name)
        print("TextEdit's own readback matches:", readback.strip() == SENTENCE)

        box = next((e for e in snap.elems if e.role == "AXCheckBox" and e.enabled), None)
        if box is None:
            snap = ax.describe(mac, "TextEdit", "Make Plain Text")
            assert not isinstance(snap, str)
            box = next((e for e in snap.elems if e.role == "AXMenuItem"), None)
        print("press target:", box.line() if box else None)
        if box is not None:
            r2 = attempt("press", str(box.index))
            print(f"ax_press: outcome={r2.outcome} | {r2.detail}")
        print("pointer shown first:", shown)
        return 0
    finally:
        print("close without saving:",
              osa('on run argv\ntell application "TextEdit" to close document (item 1 of argv) saving no\nend run', name)
              or "ok")


if __name__ == "__main__":
    sys.exit(main())
