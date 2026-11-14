"""Finder tools: list a folder (always available, read-only), open or reveal a path (Do mode, REACH_DO_MODE=1).

Safety, enforced in code before anything runs:
- the path must resolve (realpath, symlinks followed) inside the user's home directory;
- secret-bearing paths are refused with the same patterns guard_policy uses (.env*, .ssh, .aws, .intern-home,
  .reach/run, keychains, key files);
- nothing launchable is opened: executables, .app bundles, scripts and installers (.command, .sh, .py, .scpt, .pkg,
  .dmg ...). Folders open in Finder, documents in their default app, with /usr/bin/open (reveal = open -R).

Readback: the path exists as the claimed type and `open` exited 0; for folders Finder's front window target is read
back through Finder's scripting dictionary when permitted (verified), else the outcome is unverified.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, ClassVar

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import append_action

MAX_LIST = 100
TIMEOUT_S = 10
LAUNCHABLE = frozenset({
    ".app", ".command", ".sh", ".bash", ".zsh", ".csh", ".fish", ".py", ".pyc", ".rb", ".pl", ".php", ".js", ".mjs",
    ".scpt", ".scptd", ".applescript", ".workflow", ".action", ".pkg", ".mpkg", ".dmg", ".terminal", ".tool",
    ".jar", ".bin", ".exe", ".run", ".prefpane", ".kext", ".mobileconfig", ".webloc", ".inetloc", ".url",
})


def home() -> Path:
    return Path.home().resolve()


def _secret(text: str) -> str | None:
    from ..guard_policy import _KEYWORD_WHAT, _SECRET_RE

    for rx, what in _SECRET_RE:
        if what != _KEYWORD_WHAT and rx.search(text):
            return what
    return None


def check_path(raw: str, *, root: Path | None = None) -> Path | str:
    """The resolved path, or why it is refused."""
    root = (root or home()).resolve()
    if not raw or not raw.strip():
        return "refused: no path"
    p = Path(os.path.expanduser(raw.strip()))
    if not p.is_absolute():
        p = root / p
    if what := _secret(str(p)):
        return f"refused: {what} is never opened or listed"
    real = Path(os.path.realpath(p))
    if what := _secret(str(real)):
        return f"refused: {what} is never opened or listed"
    if real != root and not real.is_relative_to(root):
        return "refused: only paths inside your home folder"
    if not real.exists():
        return f"no such file or folder: {raw}"
    return real


def launchable(p: Path) -> bool:
    if any(part.lower().endswith(".app") for part in p.parts):
        return True
    if p.suffix.lower() in LAUNCHABLE:
        return True
    return p.is_file() and os.access(p, os.X_OK)


def list_dir(raw: str, *, root: Path | None = None) -> str:
    p = check_path(raw, root=root)
    if isinstance(p, str):
        return p
    if not p.is_dir():
        return f"{p} is a file, not a folder"
    try:
        names = sorted(n for n in os.listdir(p) if not n.startswith("."))
    except OSError as e:
        return f"cannot list {p} ({type(e).__name__})"
    shown = [n + ("/" if (p / n).is_dir() else "") for n in names[:MAX_LIST] if not _secret(n)]
    more = f"\n... and {len(names) - MAX_LIST} more" if len(names) > MAX_LIST else ""
    return f"{p} ({len(names)} entries)\n" + "\n".join(shown) + more


FINDER_FRONT = """tell application "Finder"
  if (count of Finder windows) is 0 then return ""
  return POSIX path of (target of front Finder window as alias)
end tell"""


def _run(argv: list[str], script: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, input=script, capture_output=True, text=True, timeout=TIMEOUT_S)
    except (subprocess.SubprocessError, OSError):
        return -1, ""
    return r.returncode, r.stdout.strip()


def open_path(raw: str, *, reveal: bool = False, root: Path | None = None, run=_run) -> tuple[str, str, Path | None]:
    """(outcome, detail, path)."""
    p = check_path(raw, root=root)
    if isinstance(p, str):
        return "failed", p, None
    if not reveal and launchable(p):
        return "failed", f"refused: {p.name} is an app, script or installer; Reach never launches those", p
    is_dir = p.is_dir()
    code, _ = run(["/usr/bin/open", *(["-R"] if reveal else []), str(p)])
    if code != 0:
        return "failed", "open did not succeed", p
    if not (p.is_dir() if is_dir else p.is_file()):
        return "failed", "the path changed type after opening", p
    if reveal or is_dir:
        want = p.parent if reveal and not is_dir else (p.parent if reveal else p)
        code, front = run(["osascript", "-"], FINDER_FRONT)
        if code == 0 and front:
            ok = Path(os.path.realpath(front)) == want
            return ("verified" if ok else "unverified",
                    f"Finder's front window shows {front}" + ("" if ok else f", not {want}"), p)
        return "unverified", "opened; could not read Finder's front window back (Automation permission?)", p
    return "verified", f"opened {p.name} in its default app (open exited 0)", p


def _output(tool: str, outcome: str, detail: str, p: Path | None) -> ToolOutput:
    dest = (p.name if p else "-").replace(" ", "_")[:60]
    append_action(tool=tool, destination=dest, chars=0, outcome=outcome, detail=detail[:200])
    content = f"outcome={outcome} destination={dest} chars=0\n{detail}"
    if outcome != "verified":
        content += "\nTell the user plainly that this was NOT confirmed."
    return ToolOutput(status="ok" if outcome == "verified" else "error", content=content)


class PathArgs(BaseModel):
    path: str = Field(description="A file or folder inside the user's home folder, e.g. ~/Documents/Taxes")


class FinderList(Tool):
    name: ClassVar[str] = "finder_list"
    description: ClassVar[str] = "List the names in a folder inside the user's home folder (max 100). Read-only."
    Args: ClassVar[type[BaseModel]] = PathArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(content=await asyncio.to_thread(list_dir, args.path))


class FinderOpen(Tool):
    name: ClassVar[str] = "finder_open"
    description: ClassVar[str] = (
        "EXPERIMENTAL Do mode. Open a folder in Finder or a document in its default app (inside the home folder; "
        "never apps or scripts). Resolve which path from the screen or the user's words first. First line = outcome."
    )
    Args: ClassVar[type[BaseModel]] = PathArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return _output(self.name, *await asyncio.to_thread(open_path, args.path))


class FinderReveal(Tool):
    name: ClassVar[str] = "finder_reveal"
    description: ClassVar[str] = (
        "EXPERIMENTAL Do mode. Show a file or folder selected in a Finder window (inside the home folder). "
        "First line = outcome."
    )
    Args: ClassVar[type[BaseModel]] = PathArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        return _output(self.name, *await asyncio.to_thread(lambda: open_path(args.path, reveal=True)))


def tools() -> list[Tool]:
    from .ax import enabled

    return [FinderList(), *([FinderOpen(), FinderReveal()] if enabled() else [])]
