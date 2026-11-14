"""Table to notes: transcribe a table in the selected screen region and write it, verified, into the user's notes.

The image (a PNG under .reach/) goes to the same DeepInfra vision model as `see_region`. The model is told to copy
headers, units, signs and empty cells exactly, write `?` for an unreadable cell and never invent a value. The reply
must parse as one rectangular GitHub Markdown table (a header, a separator row, every row the same column count);
otherwise nothing is written and the outcome is `failed`. A valid table goes through the same write path as
`note_append` (file / textedit / notes) with the same readback and first line.
"""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import append_tool_entry
from . import native, vision

SYSTEM = (
    "You transcribe the table in an image of the user's screen into ONE GitHub Markdown table. Copy every header "
    "and cell exactly as shown: keep units, % signs, minus signs, decimals, currency symbols and capitalisation. "
    "Keep an empty cell empty (nothing between the pipes). If a cell is present but you cannot read it, write ? . "
    "Never guess, compute, fill in or invent a value. Keep the same number of columns in every row. Output only "
    "the table: a header row, a separator row of dashes, then the data rows; no code fences and no other text."
)
SEP_CELL = re.compile(r"^:?-{1,}:?$")
MAX_TOKENS = 1500


def build_request(png: bytes, model: str = vision.MODEL) -> dict[str, Any]:
    body = vision.build_request(png, "Transcribe the table in this image as a GitHub Markdown table.", model)
    body["messages"][0]["content"] = SYSTEM
    body["max_tokens"] = MAX_TOKENS
    body["temperature"] = 0
    return body


def split_row(line: str) -> list[str]:
    """Cells of one Markdown row. Only the outer pipes are removed, so empty cells survive; `\\|` stays in a cell."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def extract_table(text: str) -> str:
    """The first block of pipe rows in the reply, with code fences dropped."""
    lines = [ln for ln in (text or "").splitlines() if not ln.strip().startswith("```")]
    block: list[str] = []
    for ln in lines:
        if ln.strip().startswith("|"):
            block.append(ln.strip())
        elif block:
            break
    return "\n".join(block)


def validate(md: str) -> tuple[list[list[str]] | None, str]:
    """(rows including the header, '') when md is one rectangular Markdown table, else (None, why)."""
    lines = [ln for ln in md.splitlines() if ln.strip()]
    if len(lines) < 3:
        return None, "not a table: needs a header, a separator and at least one row"
    rows = [split_row(ln) for ln in lines]
    sep = rows[1]
    if not all(SEP_CELL.match(c) for c in sep):
        return None, "not a table: second line is not a |---| separator"
    width = len(rows[0])
    if width < 1 or not any(rows[0]):
        return None, "not a table: empty header"
    bad = [i + 1 for i, r in enumerate(rows) if len(r) != width]
    if bad:
        return None, f"not rectangular: lines {bad} do not have {width} columns"
    return [rows[0], *rows[2:]], ""


def to_markdown(rows: list[list[str]]) -> str:
    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    return "\n".join([line(rows[0]), line(["---"] * len(rows[0])), *(line(r) for r in rows[1:])])


def cell_accuracy(truth: list[list[str]], got: list[list[str]] | None) -> tuple[int, int]:
    """(cells matching exactly after whitespace trim, cells in the truth). Missing cells count as wrong."""
    total = sum(len(r) for r in truth)
    if not got:
        return 0, total
    ok = 0
    for i, row in enumerate(truth):
        for j, cell in enumerate(row):
            g = got[i][j] if i < len(got) and j < len(got[i]) else None
            if g is not None and " ".join(g.split()) == " ".join(cell.split()):
                ok += 1
    return ok, total


def transcribe(image_path: str | None, send=vision.call) -> tuple[str, list[list[str]] | None, str]:
    """(table markdown, rows, why-not). Logs the vision call like see_region does."""
    p = vision.resolve_image(image_path)
    body = build_request(p.read_bytes())
    t0 = time.time()
    try:
        data = send(body)
    except Exception as e:
        msg = str(e) if str(e).startswith("HTTP") else type(e).__name__
        append_tool_entry("vision", model=vision.MODEL, ok=False, error=msg[:80], ms=int((time.time() - t0) * 1000))
        raise
    usage = data.get("usage") or {}
    append_tool_entry("vision", model=vision.MODEL, ok=True, ms=int((time.time() - t0) * 1000), image=p.name,
                      input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                      cost_usd=usage.get("estimated_cost"))
    reply = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    rows, why = validate(extract_table(reply))
    if rows is None:
        return "", None, why
    return to_markdown(rows), rows, ""


def table_to_note(image_path: str | None, destination: str, send=vision.call, write=native.note_write
                  ) -> tuple[native.WriteResult, str]:
    md, rows, why = transcribe(image_path, send)
    if rows is None:
        return native.WriteResult(destination=destination, outcome="failed",
                                  detail=f"nothing written: the model's table failed validation ({why})"), ""
    r = write(destination, md)
    unreadable = sum(c == "?" for row in rows for c in row)
    shape = f"{len(rows) - 1} rows x {len(rows[0])} columns, {unreadable} unreadable cell(s) marked ?"
    r.detail = f"{r.detail}\n{shape}".strip()
    return r, md


class TableToNoteArgs(BaseModel):
    destination: native.Destination = Field(default="file", description="file (~/Documents/Reach Notes.md), textedit or notes")
    image_path: str | None = Field(default=None, description="PNG under .reach/ (the selection's image_path); default: newest capture")


class TableToNote(Tool):
    name: ClassVar[str] = "table_to_note"
    description: ClassVar[str] = (
        "Copy a table from the screen region the user selected into their notes as a Markdown table, then read it "
        "back. Use it whenever the user asks to put, save or move a table they selected or see on screen into "
        "their notes. Unreadable cells are written as ?; values are never invented. The first line of the result "
        "gives the outcome (verified, unverified or failed); report it truthfully."
    )
    Args: ClassVar[type[BaseModel]] = TableToNoteArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read", "write", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        try:
            r, md = await asyncio.to_thread(table_to_note, args.image_path, args.destination)
        except (PermissionError, FileNotFoundError, ValueError) as e:
            r, md = native.WriteResult(destination=args.destination, outcome="failed", detail=str(e)), ""
        except Exception as e:
            msg = str(e) if str(e).startswith(("HTTP", "DEEPINFRA")) else type(e).__name__
            r, md = native.WriteResult(destination=args.destination, outcome="failed",
                                       detail=f"vision call failed: {msg}; nothing written"), ""
        out = native.tool_output(r, self.name)
        return ToolOutput(status=out.status, content=f"{out.content}\n\n{md}" if md else out.content)

