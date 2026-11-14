"""Clipboard: put text on the user's clipboard directly (no Cmd-C / Cmd-V key events), then read it back to confirm.

Reach never reads the clipboard (it may hold passwords); it only writes what the user asked to copy, and checks the write.
"""
from __future__ import annotations

import subprocess
from typing import Any, ClassVar

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

MAX_CHARS = 8000


def copy_text(text: str, run=subprocess.run) -> tuple[str, str]:
    """(outcome, detail): verified when the clipboard holds exactly the text afterwards."""
    text = text or ""
    if not text.strip():
        return "failed", "nothing to copy"
    if len(text) > MAX_CHARS:
        return "failed", f"too long to copy ({len(text)} characters; the limit is {MAX_CHARS})"
    try:
        run(["/usr/bin/pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
        back = run(["/usr/bin/pbpaste"], capture_output=True, check=True, timeout=5).stdout.decode("utf-8", "replace")
    except Exception as e:
        return "unverified", f"could not confirm the copy: {type(e).__name__}"
    if back == text:
        return "verified", f"{len(text)} characters are on the clipboard"
    return "unverified", "the clipboard changed before it could be checked (another app wrote to it)"


class CopyArgs(BaseModel):
    text: str = Field(description="The exact text to put on the clipboard")


class ClipboardCopy(Tool):
    name: ClassVar[str] = "clipboard_copy"
    description: ClassVar[str] = (
        "Put text on the user's clipboard so they can paste it anywhere. Use it for 'copy this', 'put that on my "
        "clipboard'. It does not paste for them; tell them it is ready to paste."
    )
    Args: ClassVar[type[BaseModel]] = CopyArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        outcome, detail = await asyncio.to_thread(copy_text, args.text)
        first = f"outcome={outcome} destination=clipboard chars={len(args.text or '')}"
        return ToolOutput(content=f"{first}\n{detail}", is_error=outcome == "failed")
