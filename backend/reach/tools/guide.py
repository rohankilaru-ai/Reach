"""Guide on screen: point at things inside the user's circled screen region, step by step.

`point_at` sends the selection's PNG to the same DeepInfra vision model as `see_region`, asks for an ordered list of
steps (short imperative label + the target's bounding box) as strict JSON, converts each box to a screen point and
shows the steps with the overlay's existing ghost pointer (`pointer` messages), one every few seconds, in a background
task. It only draws guidance: it never clicks, types or sends any input event.

Box convention (measured on this endpoint, see docs/evidence/guide.md): Qwen3-VL returns [x1, y1, x2, y2] normalised
to 0-1000 on each axis of the image it was sent, x first, whatever the image's pixel size.
Coordinates: a selection's `region` is [x, y, w, h] in screen points with a top-left origin (as the overlay builds it);
the capture PNG is at the screen's backing scale (e.g. 2x on retina), so scale = image pixel width / region width.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import struct
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..contracts import Pointer, Selection
from ..ledger import CURRENT_TASK, append_tool_entry
from . import vision

CURRENT_SELECTION: ContextVar[Selection | None] = ContextVar("reach_selection", default=None)
"""Set by the bridge for each run: the ask's selection (image_path + region travel together)."""

STEP_GAP_S = 3.5
BOX_SCALE = 1000.0  # Qwen3-VL's normalised box range on this endpoint
MAX_STEPS = 8
MAX_LABEL = 60
SLACK = 0.02  # boxes up to 2% outside the image are clamped; further out they are dropped

CIRCLE_FIRST = (
    "Nothing usable is circled: ask the user to circle the form or tool on screen (or circle it again if it was a "
    "while ago), then ask again. No pointer was shown."
)

Emitter = Callable[[str], Any]
_emit: Emitter | None = None
_pending: dict[str, asyncio.Task] = {}


def set_emitter(fn: Emitter | None) -> None:
    """The server hands in a function that sends one JSON message to every overlay client."""
    global _emit
    _emit = fn


def cancel(task_id: str | None = None) -> int:
    """Stop pending pointer steps: those of one ask, or all of them. Returns how many sequences were stopped."""
    keys = [task_id] if task_id is not None else list(_pending)
    n = 0
    for k in keys:
        t = _pending.pop(k, None)
        if t is not None and not t.done():
            t.cancel()
            n += 1
    return n


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    w, h = struct.unpack(">II", data[16:24])
    if w <= 0 or h <= 0:
        raise ValueError("empty image")
    return w, h


SYSTEM = (
    "You guide a user through the screen region shown in the image (a form, a tool, a table or an app). "
    "Answer with STRICT JSON only, no prose, no code fence: "
    '{"steps": [{"label": "...", "target": "...", "box": [x1, y1, x2, y2]}]}. '
    "Steps are in the order the user should do them. label is a short imperative instruction (at most 8 words), "
    "e.g. 'Type your email here' or 'Click Save'. target is the exact visible text of (or next to) the thing to "
    "point at. box is the bounding box of the thing to click or type into (the input box itself, not its caption), "
    "as integers normalised to 0-1000 over the image width (x) and height (y). "
    "Only use elements you can actually see in the image; never invent fields or buttons. If you are unsure, return "
    'fewer steps; if nothing fits, return {"steps": []}.'
)


def build_request(png: bytes, question: str, max_steps: int, model: str = vision.MODEL) -> dict[str, Any]:
    body = vision.build_request(png, f"{question}\n\nGive at most {max_steps} steps.", model)
    body["messages"][0]["content"] = SYSTEM
    body["max_tokens"] = 900
    body["temperature"] = 0.1
    return body


def parse_steps(text: str) -> list[dict[str, Any]]:
    """The model's JSON steps; [] when it returned nothing usable."""
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        label = " ".join(str(s.get("label") or "").split())[:MAX_LABEL]
        box = s.get("box")
        if not label or not isinstance(box, list) or len(box) != 4:
            continue
        try:
            box = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        out.append({"label": label, "target": str(s.get("target") or "")[:80], "box": box})
    return out


def to_screen(box: list[float], region: list[float], img_w: int, img_h: int) -> tuple[float, float] | None:
    """Box (0-1000 normalised) -> the screen point at its centre, or None when it is not inside the region.

    normalised -> image pixels (img_w, img_h) -> screen points (divide by the capture scale, add the region origin)."""
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        return None
    lo, hi = -SLACK * BOX_SCALE, (1 + SLACK) * BOX_SCALE
    if any(v < lo or v > hi for v in box):
        return None
    x1, y1, x2, y2 = (min(max(v, 0.0), BOX_SCALE) for v in box)
    if x2 < x1 or y2 < y1:
        return None
    rx, ry, rw, rh = region
    if rw <= 0 or rh <= 0:
        return None
    sx, sy = img_w / rw, img_h / rh  # capture scale (2.0 on retina)
    px = (x1 + x2) / 2 / BOX_SCALE * img_w
    py = (y1 + y2) / 2 / BOX_SCALE * img_h
    x, y = rx + px / sx, ry + py / sy
    if not (rx <= x <= rx + rw and ry <= y <= ry + rh):
        return None
    return round(x, 1), round(y, 1)


def plan(png: bytes, region: list[float], question: str, max_steps: int, send=vision.call) -> list[dict[str, Any]]:
    """Ask the vision model, keep the steps whose boxes land inside the region, numbered in order."""
    w, h = png_size(png)
    t0 = time.time()
    try:
        data = send(build_request(png, question, max_steps))
    except Exception as e:
        msg = str(e) if str(e).startswith("HTTP") else type(e).__name__
        append_tool_entry("vision", model=vision.MODEL, purpose="point_at", ok=False, error=msg[:80],
                          ms=int((time.time() - t0) * 1000))
        raise
    usage = data.get("usage") or {}
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    raw = parse_steps(text)
    steps = []
    for s in raw[:max_steps]:
        pt = to_screen(s["box"], region, w, h)
        if pt is None:
            continue
        steps.append({**s, "x": pt[0], "y": pt[1], "n": len(steps) + 1})
    append_tool_entry(
        "vision", model=vision.MODEL, purpose="point_at", ok=True, ms=int((time.time() - t0) * 1000),
        input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
        cost_usd=usage.get("estimated_cost"), steps=len(steps), dropped=len(raw[:max_steps]) - len(steps),
    )
    return steps


async def show(task_id: str, steps: list[dict[str, Any]], gap_s: float = STEP_GAP_S,
               sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
    """Emit one pointer per step, in order, `gap_s` apart. Cancelled by abort or a new ask."""
    for i, s in enumerate(steps):
        if i:
            await sleep(gap_s)
        if _emit is None:
            return
        _emit(Pointer(task_id=task_id, x=s["x"], y=s["y"], label=f"{s['n']}. {s['label']}").model_dump_json())


def start(task_id: str, steps: list[dict[str, Any]], gap_s: float = STEP_GAP_S, sleep=asyncio.sleep) -> asyncio.Task:
    cancel()  # one guide at a time: a new one replaces any still running
    t = asyncio.get_running_loop().create_task(show(task_id, steps, gap_s, sleep))
    _pending[task_id] = t

    def done(f: asyncio.Task) -> None:
        if _pending.get(task_id) is f:
            _pending.pop(task_id, None)
        if not f.cancelled():
            f.exception()  # never "exception was never retrieved"

    t.add_done_callback(done)
    return t


def selection_image(sel: Selection | None) -> tuple[Path, list[float]]:
    """The selection's own PNG and region (never the newest capture paired with some other region)."""
    if sel is None or not sel.image_path or not sel.region or len(sel.region) != 4:
        raise FileNotFoundError(CIRCLE_FIRST)
    p = vision.resolve_image(sel.image_path)
    age = time.time() - p.stat().st_mtime
    if age > vision.MAX_DEFAULT_AGE_S:
        raise FileNotFoundError(f"The circled region is {int(age)} s old. " + CIRCLE_FIRST)
    return p, list(sel.region)


def render(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return ("The vision model found nothing it could point at for that in the circled region, so no pointer was "
                "shown. Say so, and ask the user to circle the part of the screen with the form or tool.")
    lines = [f"{s['n']}. {s['label']}" + (f" (at '{s['target']}')" if s.get("target") else "") for s in steps]
    return (
        f"The on-screen pointer is now showing these {len(steps)} step(s) one after another, about "
        f"{STEP_GAP_S:g} s apart:\n" + "\n".join(lines) + "\n\nExplain the steps to the user in words and say the "
        "pointer shows where each one is. You did not click or type anything; the user does each step themselves."
    )


class PointAtArgs(BaseModel):
    question: str = Field(description="What the user wants to do in the circled region, e.g. 'how do I fill this in'")
    max_steps: int = Field(default=6, ge=1, le=MAX_STEPS, description="Most steps to show")


class PointAt(Tool):
    name: ClassVar[str] = "point_at"
    description: ClassVar[str] = (
        "Show the user WHERE to click or type in the screen region they circled: finds the targets with a vision "
        "model and moves an on-screen pointer to each one, step by step, with a short label. Use it for 'where do I "
        "click', 'how do I fill this in', 'show me how'. It only points; it never clicks or types. Afterwards, "
        "explain the steps in words."
    )
    Args: ClassVar[type[BaseModel]] = PointAtArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        try:
            p, region = selection_image(CURRENT_SELECTION.get())
            steps = await asyncio.to_thread(plan, p.read_bytes(), region, args.question, args.max_steps)
        except (PermissionError, FileNotFoundError, ValueError) as e:
            return ToolOutput(status="error", content=str(e))
        except Exception as e:
            msg = str(e) if str(e).startswith(("HTTP", "DEEPINFRA")) else type(e).__name__
            return ToolOutput(status="error", content=f"vision call failed: {msg}")
        if steps:
            start(CURRENT_TASK.get(), steps)
        return ToolOutput(content=render(steps))
