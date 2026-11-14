"""Pinch relay (EXPERIMENTAL): selection and pointer from the camera worker to overlay clients.

A client that says `role: "perception"` in its hello may send `select` and `pointer`. The server checks them and relays
each to every connected `role: "overlay"` client (never back to the sender) as `selection` / `pointer`. It never starts
an ask by itself. It keeps the latest hand selection for 60 s; an `ask` without its own selection uses it.
An all-empty `select` (no text, region or image) from any roled client clears it (the overlay's clear button).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

from .contracts import Ask, PointerIn, Pointer, Select, Selection, SelectionMsg
from .paths import reach_dir

LATEST_TTL_S = 60.0
MAX_TEXT = 20_000
MAX_PATH = 256


def check_selection(sel: Selection) -> str | None:
    """None if fine, else why not. Regions are finite [x, y, w, h] with w, h >= 0; images live under .reach/."""
    if sel.region is not None:
        if len(sel.region) != 4 or not all(math.isfinite(v) for v in sel.region):
            return "region must be 4 finite numbers"
        if sel.region[2] < 0 or sel.region[3] < 0:
            return "region width/height must be >= 0"
    if sel.path is not None:
        if len(sel.path) > MAX_PATH or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in sel.path):
            return f"path must be at most {MAX_PATH} finite [x, y] points"
    if sel.text is not None and len(sel.text) > MAX_TEXT:
        return f"text longer than {MAX_TEXT} chars"
    if sel.image_path is not None:
        try:
            p = Path(sel.image_path).resolve()
            root = reach_dir().resolve()
        except (OSError, RuntimeError):
            return "bad image_path"
        if not p.is_relative_to(root):
            return "image_path must be under .reach/"
    return None


def is_empty(sel: Selection) -> bool:
    return not (sel.text or sel.region or sel.image_path)


class Relay:
    def __init__(self) -> None:
        self.overlays: dict[int, asyncio.Queue] = {}
        self.latest: Selection | None = None
        self.latest_at = 0.0

    def join(self, key: int, role: str | None, out: asyncio.Queue) -> None:
        if role == "overlay":
            self.overlays[key] = out

    def leave(self, key: int) -> None:
        self.overlays.pop(key, None)

    def _broadcast(self, sender: int, msg: str) -> int:
        n = 0
        for key, q in list(self.overlays.items()):
            if key != sender:
                q.put_nowait(msg)
                n += 1
        return n

    def select(self, sender: int, role: str | None, msg: Select) -> str | None:
        """Returns an error string, or None when handled."""
        sel = msg.selection
        if is_empty(sel):
            if role is not None:
                self.latest = None
            return None
        if role != "perception":
            return None  # the overlay's own drag-select travels with its ask; nothing to relay
        why = check_selection(sel)
        if why:
            return f"select rejected: {why}"
        sel = sel.model_copy(update={"source": "pinch"})
        self.latest, self.latest_at = sel, time.monotonic()
        n = self._broadcast(sender, SelectionMsg(selection=sel).model_dump_json())
        log_line({"kind": "select", "from": role, "relayed_to": n, "chars": len(sel.text or ""),
                  "image": bool(sel.image_path), "region": sel.region, "path_points": len(sel.path or [])})
        return None

    def pointer(self, sender: int, role: str | None, msg: PointerIn) -> str | None:
        if role != "perception":
            return "pointer needs role perception in hello"
        out = Pointer(task_id="", x=msg.x, y=msg.y, label=msg.label, state=msg.state, source="hand")
        self._broadcast(sender, out.model_dump_json())
        return None

    def attach(self, ask: Ask) -> tuple[Ask, str | None]:
        """(ask to run, how its selection got there). Uses the latest hand selection if the ask has none."""
        if ask.selection is not None:
            return ask, ("pinch" if ask.selection.source == "pinch" else "client")
        if self.latest is not None and time.monotonic() - self.latest_at < LATEST_TTL_S:
            return ask.model_copy(update={"selection": self.latest}), "pinch-latest"
        return ask, None


def log_line(obj: dict) -> None:
    p = reach_dir() / "logs" / "relay.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **obj}) + "\n")
    except OSError:
        pass
