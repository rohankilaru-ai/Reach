"""click_on_screen (Do mode): look at the screen, find the thing the user named, show a pointer on it, then click it.

The vision model grounds the target to a box (same path as point_at); Reach shows a pointer for a second so the user can
see what is about to be clicked, aborts if the user moves the mouse in that second, and clicks once with a real mouse
event. It does not verify the result: the model is told to look at the screen afterwards. Opt-in with REACH_DO_MODE=1.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import re
import time
from typing import Any, ClassVar

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..contracts import Pointer
from ..ledger import CURRENT_TASK, append_tool_entry

SHOW_S = 1.0
TAKEOVER_PX = 8.0
MAX_CLICKS_PER_ASK = 3
# Never click things that spend money, delete, or send: the owner does those steps themselves (same list idea as ax.py).
DENY = re.compile(r"\b(pay|purchas|buy|delet|eras|send|transfer|confirm|submit|place order|checkout|sign out|log ?out|"
                  r"password|unsubscribe|post|publish)\w*", re.I)
_clicks: dict[str, int] = {}


class _Pt(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _cg():
    cg = ctypes.CDLL(ctypes.util.find_library("ApplicationServices"))
    cg.CGEventCreate.restype = ctypes.c_void_p
    cg.CGEventCreate.argtypes = [ctypes.c_void_p]
    cg.CGEventGetLocation.restype = _Pt
    cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _Pt, ctypes.c_uint32]
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    return cg


def mouse_pos() -> tuple[float, float]:
    cg = _cg()
    p = cg.CGEventGetLocation(cg.CGEventCreate(None))
    return p.x, p.y


def post_click(x: float, y: float) -> None:
    """Move to (x, y) and click once: mouse-move, left down, left up (global point coordinates)."""
    cg = _cg()
    for kind in (5, 1, 2):  # moved, left down, left up
        ev = cg.CGEventCreateMouseEvent(None, kind, _Pt(x, y), 0)
        cg.CGEventPost(0, ev)
        time.sleep(0.04)


def find_target(target: str) -> tuple[float, float, str] | None:
    """Capture the screen and ask the vision model for the one box that encloses `target`. (x, y, app) or None."""
    from . import guide, screen

    png, meta, _note = screen.capture("display")
    steps = guide.plan(png.read_bytes(), meta["region"], (
        f"Find the single thing on screen the user wants to click: {target}. Return exactly ONE step whose box tightly "
        "encloses that element (a button, link, result or icon). If it is not clearly visible return no steps. "
        "Never guess."), 1)
    return (steps[0]["x"], steps[0]["y"], meta.get("app") or "") if steps else None


def refusal(target: str, task_id: str) -> str | None:
    if not (target or "").strip():
        return "say what to click"
    if DENY.search(target):
        return "that looks like a step that spends, sends, deletes or signs out: Reach leaves it for you to click yourself"
    if _clicks.get(task_id, 0) >= MAX_CLICKS_PER_ASK:
        return f"already clicked {MAX_CLICKS_PER_ASK} times for this request; ask again to continue"
    return None


async def click_target(target: str, task_id: str, find=find_target, pos=mouse_pos, click=post_click,
                       emit=None, sleep=asyncio.sleep) -> tuple[str, str]:
    """(outcome, text). outcome is `clicked`, `refused`, `not_found` or `aborted`."""
    why = refusal(target, task_id)
    if why:
        return "refused", why
    found = await asyncio.to_thread(find, target)
    if not found:
        return "not_found", f"I could not clearly see '{target}' on the screen, so I clicked nothing."
    x, y, app = found
    before = await asyncio.to_thread(pos)
    if emit:
        emit(Pointer(task_id=task_id, x=x, y=y, label=f"Clicking: {target[:24]}").model_dump_json())
    await sleep(SHOW_S)
    after = await asyncio.to_thread(pos)
    if abs(after[0] - before[0]) + abs(after[1] - before[1]) > TAKEOVER_PX:
        return "aborted", "you moved the mouse, so I did not click"
    await asyncio.to_thread(click, x, y)
    _clicks[task_id] = _clicks.get(task_id, 0) + 1
    append_tool_entry("action", tool="click_on_screen", destination=app or "screen", chars=0, outcome="clicked")
    return "clicked", f"clicked '{target}' at ({int(x)}, {int(y)}). Look at the screen to confirm what happened."


class ClickArgs(BaseModel):
    target: str = Field(description="What to click, in plain words, e.g. 'the first search result' or 'the Play button'")


class ClickOnScreen(Tool):
    name: ClassVar[str] = "click_on_screen"
    description: ClassVar[str] = (
        "Click something the user can see on screen (a link, a search result, a button). Reach finds it visually, shows "
        "a pointer on it for a second, then clicks once with the mouse. It cannot click anything that spends money, "
        "sends, deletes or signs out. It does not confirm the result: afterwards call see_screen to check, and tell "
        "the user plainly what you did."
    )
    Args: ClassVar[type[BaseModel]] = ClickArgs
    effects: ClassVar[frozenset[str]] = frozenset({"write"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        from . import guide

        outcome, text = await click_target(args.target, CURRENT_TASK.get(), emit=guide._emit)
        return ToolOutput(content=f"outcome={outcome}\n{text}", is_error=outcome in ("not_found", "refused"))
