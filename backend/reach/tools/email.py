"""Email: open a prefilled Gmail draft in the user's Chrome. It NEVER sends: the user reads the draft and presses Send.

Gmail is where the owner's mail is, and their Chrome is already signed in, so a compose URL needs no extra login or
API access. The draft opens in a new Reach-owned tab through the same verified Chrome path as `chrome_open_url`.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import quote

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from . import chrome

MAX_BODY = 1800  # compose URLs have a practical length limit
_ADDR = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def compose_url(to: str, subject: str, body: str, cc: str = "") -> str:
    """The Gmail compose URL, validated. Raises ValueError for an address that is not a plain email address."""
    def addrs(s: str) -> list[str]:
        out = [a.strip() for a in re.split(r"[,;]", s or "") if a.strip()]
        for a in out:
            if not _ADDR.match(a):
                raise ValueError(f"not an email address: {a!r}")
        return out

    to_l, cc_l = addrs(to), addrs(cc)
    if not to_l:
        raise ValueError("a recipient is required")
    q = f"view=cm&fs=1&to={quote(','.join(to_l))}&su={quote((subject or '')[:200])}&body={quote((body or '')[:MAX_BODY])}"
    if cc_l:
        q += f"&cc={quote(','.join(cc_l))}"
    return "https://mail.google.com/mail/?" + q


class DraftArgs(BaseModel):
    to: str = Field(description="Recipient email address (several separated by commas)")
    subject: str = Field(default="", description="Subject line")
    body: str = Field(default="", description="Message text, plain, under about 1500 characters")
    cc: str = Field(default="", description="Optional cc addresses")


class EmailDraft(Tool):
    name: ClassVar[str] = "email_draft"
    description: ClassVar[str] = (
        "Open a prefilled Gmail compose window in the user's Chrome. It NEVER sends the email: tell the user the draft "
        "is ready for them to read and send. Use it for 'email X about Y'. Write the subject and body yourself from "
        "the request; do not invent facts, recipients or attachments."
    )
    Args: ClassVar[type[BaseModel]] = DraftArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        try:
            url = compose_url(args.to, args.subject, args.body, args.cc)
        except ValueError as e:
            return chrome.output(chrome.Result("failed", f"No draft opened: {e}"), self.name)
        return await chrome._guarded(self.name, chrome.open_url, url, "new")
