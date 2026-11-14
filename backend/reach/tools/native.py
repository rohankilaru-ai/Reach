"""Native note actions with verified completion: append to an allowlisted destination, then read it back.

Only the target app's own scripting dictionary (TextEdit, Notes via osascript) or plain file APIs are used. Reach
never sends keystrokes or mouse events. Text from documents is passed to AppleScript as `argv`, never pasted into
the script source.

Every write ends with a readback and an `outcome` computed in code: `verified` (the destination holds one more copy
of the text than before the write), `unverified` (it could not be read back), `failed` (the readback does not hold it,
or the write failed). The first line of the tool result is `outcome=<verdict> destination=<d> chars=<n>`, which the
receipt builder parses.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Literal

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import WRITE_TOOLS, append_action, parse_outcome, worst  # noqa: F401 (re-exported)

Destination = Literal["file", "textedit", "notes"]
DESTINATIONS: tuple[str, ...] = ("file", "textedit", "notes")
NOTES_FILE = Path.home() / "Documents" / "Reach Notes.md"
OSASCRIPT_TIMEOUT_S = 10

Outcome = Literal["verified", "unverified", "failed"]


class WriteResult(BaseModel):
    destination: str
    chars: int = 0
    outcome: Outcome
    detail: str = ""


# ---- verdict logic (pure) ----


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def verdict(text: str, before: str | None, after: str | None) -> Outcome:
    """Did this write land? Count copies of the text before and after, so an old copy cannot verify a new write."""
    if after is None:
        return "unverified"
    needle = _norm(text)
    if not needle:
        return "failed"
    had = _norm(before or "").count(needle)
    return "verified" if _norm(after).count(needle) > had else "failed"


def first_line(r: WriteResult) -> str:
    return f"outcome={r.outcome} destination={r.destination} chars={r.chars}"


# ---- destinations ----


def check_notes_path(path: Path, allowed: Path | None = None) -> Path:
    """The file destination is one fixed file. Anything else (another path, a symlink elsewhere) is refused."""
    allowed = (allowed or NOTES_FILE).expanduser()
    p = path.expanduser()
    # lstat-based: a symlink is refused even when it dangles (resolve() would follow it to its target).
    if p.is_symlink() or p.parent.resolve() / p.name != allowed.parent.resolve() / allowed.name:
        raise PermissionError(f"refused: file notes go only to {allowed}")
    return p


def write_file(text: str, path: Path | None = None, allowed: Path | None = None) -> WriteResult:
    try:
        p = check_notes_path(path or (allowed or NOTES_FILE), allowed)
    except PermissionError as e:
        return WriteResult(destination="file", outcome="failed", detail=str(e))
    try:
        before = p.read_text(encoding="utf-8") if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        chunk = ("" if not before or before.endswith("\n") else "\n") + text.rstrip("\n") + "\n"
        # O_NOFOLLOW: a symlink planted between the check and the open is refused, not followed.
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(chunk)
    except OSError as e:
        return WriteResult(destination="file", outcome="failed", detail=f"write error {type(e).__name__}")
    try:
        after = p.read_text(encoding="utf-8")
    except OSError:
        after = None
    return WriteResult(destination="file", chars=len(chunk), outcome=verdict(text, before, after), detail=str(p))


Runner = Callable[[str, list[str]], str | None]
"""(AppleScript source, argv) -> stdout, or None when osascript failed or timed out."""


def osascript(script: str, argv: list[str]) -> str | None:
    try:
        r = subprocess.run(
            ["osascript", "-", *argv], input=script, capture_output=True, text=True, timeout=OSASCRIPT_TIMEOUT_S
        )
    except (subprocess.TimeoutExpired, OSError):
        return None  # a timeout is most often the Automation permission prompt waiting for the owner
    return r.stdout if r.returncode == 0 else None


TEXTEDIT_WRITE = """on run argv
tell application "TextEdit"
  set d to make new document with properties {text:(item 1 of argv)}
  return name of d
end tell
end run"""
TEXTEDIT_READ = """on run argv
tell application "TextEdit" to return text of document (item 1 of argv)
end run"""
NOTES_WRITE = """on run argv
tell application "Notes"
  set n to make new note with properties {name:(item 2 of argv), body:(item 1 of argv)}
  return id of n
end tell
end run"""
NOTES_READ = """on run argv
tell application "Notes" to return plaintext of note id (item 1 of argv)
end run"""


def write_app(destination: str, text: str, run: Runner = osascript) -> WriteResult:
    """A new TextEdit document or Apple Note holding the text, then a readback of that same document or note."""
    if destination == "textedit":
        ref = run(TEXTEDIT_WRITE, [text])
        read = TEXTEDIT_READ
    else:
        ref = run(NOTES_WRITE, [text, "Reach note"])
        read = NOTES_READ
    if ref is None or not ref.strip():
        return WriteResult(destination=destination, outcome="unverified",
                           detail="osascript gave no result (Automation permission may be pending)")
    after = run(read, [ref.strip()])
    out = verdict(text, "", after)
    return WriteResult(destination=destination, chars=len(text), outcome=out, detail=f"document {ref.strip()}")


def note_write(destination: str, text: str, *, run: Runner = osascript, notes_file: Path | None = None) -> WriteResult:
    if destination not in DESTINATIONS:
        return WriteResult(destination=destination, outcome="failed",
                           detail=f"refused: destination must be one of {', '.join(DESTINATIONS)}")
    if not text.strip():
        return WriteResult(destination=destination, outcome="failed", detail="nothing to write")
    if destination == "file":
        return write_file(text, allowed=notes_file)
    return write_app(destination, text, run)


def tool_output(r: WriteResult, tool: str) -> ToolOutput:
    append_action(tool=tool, destination=r.destination, chars=r.chars, outcome=r.outcome)
    msg = {
        "verified": "Read back from the destination: the text is there.",
        "unverified": "Could not read the destination back, so this is NOT confirmed. Tell the user.",
        "failed": "The text is NOT in the destination. Tell the user it did not work.",
    }[r.outcome]
    content = f"{first_line(r)}\n{msg}\n{r.detail}".strip()
    return ToolOutput(status="ok" if r.outcome == "verified" else "error", content=content)


# ---- citations ----


def citation_block(item: dict[str, Any]) -> str:
    page = f", p. {item['page']}" if item.get("page") else ""
    return f"> {item['quote']}\n> -- {item['file']}{page} [{item['id']}]"


def shelf_items(item_ids: list[str]) -> list[dict[str, Any]]:
    from .. import evidence

    pinned = {i["id"]: i for i in evidence.shelf()}
    out = []
    for pid in item_ids:
        if pid in pinned:
            out.append(pinned[pid])
            continue
        p = evidence.get_passage(pid)
        if p is not None:
            out.append({**p, "quote": evidence.best_quote(p["text"], "")})
    return out


# ---- tools ----


class NoteAppendArgs(BaseModel):
    text: str = Field(description="The exact text to add, e.g. a cited quote")
    destination: Destination = Field(default="file", description="file (~/Documents/Reach Notes.md), textedit or notes")
    source: str | None = Field(default=None, description="Citation for the text, e.g. 'paper.pdf, p. 3 [E4]'")


class NoteAppend(Tool):
    name: ClassVar[str] = "note_append"
    description: ClassVar[str] = (
        "Put text in the user's notes and read it back to confirm. destination: 'file' (default, appends to "
        "~/Documents/Reach Notes.md), 'textedit' (new TextEdit document) or 'notes' (new Apple Note). Use this tool, "
        "and only this tool, whenever the user asks to save or put something in their notes. The result's first "
        "line gives the outcome: verified, unverified or failed; report it truthfully."
    )
    Args: ClassVar[type[BaseModel]] = NoteAppendArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        text = args.text if not args.source else f"{args.text}\n-- {args.source}"
        r = await asyncio.to_thread(note_write, args.destination, text)
        return tool_output(r, self.name)


class ShelfToNoteArgs(BaseModel):
    item_ids: list[str] = Field(description="Evidence ids such as E3 (pinned on the shelf or returned by evidence_search)")
    destination: Destination = Field(default="file", description="file, textedit or notes")


class ShelfToNote(Tool):
    name: ClassVar[str] = "shelf_to_note"
    description: ClassVar[str] = (
        "Write evidence items (quote, file and page citation) into the user's notes, then read them back. Use it to "
        "put cited quotes or pinned shelf items in the notes. The first line of the result gives the outcome."
    )
    Args: ClassVar[type[BaseModel]] = ShelfToNoteArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        items = shelf_items(args.item_ids)
        if not items:
            r = WriteResult(destination=args.destination, outcome="failed", detail="no such evidence ids")
            return tool_output(r, self.name)
        text = "\n\n".join(citation_block(i) for i in items)
        r = await asyncio.to_thread(note_write, args.destination, text)
        return tool_output(r, self.name)


class OpenSourceArgs(BaseModel):
    item_id: str = Field(description="Evidence id such as E3")


class OpenSource(Tool):
    """Opens the cited PDF (path from the evidence store, never from the model). Reads only; changes nothing."""

    name: ClassVar[str] = "open_source"
    description: ClassVar[str] = "Open the PDF an evidence id cites, so the user can see the passage. Says which page."
    Args: ClassVar[type[BaseModel]] = OpenSourceArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        from .. import evidence

        p = evidence.get_passage(args.item_id)
        if p is None or not p.get("path") or not Path(p["path"]).is_file():
            return ToolOutput(status="error", content=f"No source file for {args.item_id}.")
        try:
            subprocess.run(["open", p["path"]], timeout=OSASCRIPT_TIMEOUT_S, check=True, capture_output=True)
        except (subprocess.SubprocessError, OSError) as e:
            return ToolOutput(status="error", content=f"Could not open the file ({type(e).__name__}).")
        page = f"; the passage is on page {p['page']} (the viewer opens at its first page)" if p.get("page") else ""
        return ToolOutput(content=f"Opened {p['file']}{page}.")
