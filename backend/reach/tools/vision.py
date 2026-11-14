"""Screen vision: send a selected screen region (PNG under .reach/) to a vision-language model and return its answer.

The image goes only to DeepInfra's OpenAI-compatible chat endpoint. The key is loaded inside the process (only
`DEEPINFRA_API_KEY`, via setdefault) from the repo `.env` or `$INTERN_HOME/.env`, and is never printed. Latency,
tokens and the cost DeepInfra reports (if any) are logged to the Drex log as a `{"type": "vision"}` entry.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, ClassVar

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..ledger import append_tool_entry
from ..paths import REPO_ROOT, reach_dir

MODEL = os.environ.get("REACH_VISION_MODEL", "Qwen/Qwen3-VL-235B-A22B-Instruct")
ENDPOINT = "https://api.deepinfra.com/v1/openai/chat/completions"
TIMEOUT_S = 90
MAX_BYTES = 8 * 2**20
KEY = "DEEPINFRA_API_KEY"


def load_key() -> None:
    if os.environ.get(KEY):
        return
    candidates = [REPO_ROOT / ".env"]
    if os.environ.get("INTERN_HOME"):
        candidates.append(Path(os.environ["INTERN_HOME"]) / ".env")
    for env in candidates:
        try:
            lines = env.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == KEY:
                os.environ.setdefault(KEY, v.strip().strip('"').strip("'"))
                return


def newest_captures(root: Path) -> list[Path]:
    """Capture PNGs under `root`, oldest first: overlay captures (captures/*.png) and pinch selections
    (selection-*.png, written by perception/reach_perception/capture.py)."""
    found = [p for p in (root / "captures").glob("*.png") if p.is_file()] if (root / "captures").is_dir() else []
    found += [p for p in root.glob("selection-*.png") if p.is_file()]
    return sorted(found, key=lambda x: x.stat().st_mtime)


MAX_DEFAULT_AGE_S = 120
"""A capture older than this is never used by default; the user must select again."""


def resolve_image(image_path: str | None, root: Path | None = None) -> Path:
    """The PNG to look at: the given path, or the newest capture. Only files under .reach/ are readable."""
    root = (root or reach_dir()).resolve()
    if image_path:
        p = Path(image_path).expanduser()
        if not p.is_absolute():
            p = root / p
    else:
        caps = newest_captures(root)
        if not caps:
            raise FileNotFoundError("no screen capture yet; select a region first")
        p = caps[-1]
        import time

        age = time.time() - p.stat().st_mtime
        if age > MAX_DEFAULT_AGE_S:
            raise FileNotFoundError(
                f"the newest capture is {int(age)} s old, so it is probably not what you mean; "
                "ask the user to circle the thing on screen first"
            )
    p = p.resolve()
    if not p.is_relative_to(root):
        raise PermissionError("refused: see_region reads only images under .reach/")
    if p.suffix.lower() != ".png" or not p.is_file():
        raise FileNotFoundError("not a PNG file")
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("image too large")
    return p


def build_request(png: bytes, question: str, model: str = MODEL) -> dict[str, Any]:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {
        "model": model,
        "max_tokens": 700,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You look at a region of the user's screen and answer their question about "
             "it. Describe only what is visible; say so when you cannot read something."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": question},
            ]},
        ],
    }


def call(body: dict[str, Any]) -> dict[str, Any]:
    import httpx

    load_key()
    key = os.environ.get(KEY)
    if not key:
        raise RuntimeError("DEEPINFRA_API_KEY is not set")
    r = httpx.post(ENDPOINT, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def see(image_path: str | None, question: str, send=call) -> str:
    p = resolve_image(image_path)
    body = build_request(p.read_bytes(), question)
    t0 = time.time()
    try:
        data = send(body)
    except Exception as e:
        msg = str(e) if str(e).startswith("HTTP") else type(e).__name__
        append_tool_entry("vision", model=MODEL, ok=False, error=msg[:80], ms=int((time.time() - t0) * 1000))
        raise
    usage = data.get("usage") or {}
    append_tool_entry(
        "vision", model=MODEL, ok=True, ms=int((time.time() - t0) * 1000), image=p.name,
        input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
        cost_usd=usage.get("estimated_cost"),
    )
    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


class SeeRegionArgs(BaseModel):
    question: str = Field(description="What to find out about the selected screen region")
    image_path: str | None = Field(default=None, description="PNG under .reach/ (the selection's image_path); default: newest capture")


class SeeRegion(Tool):
    name: ClassVar[str] = "see_region"
    description: ClassVar[str] = (
        "Look at the screen region the user selected (a PNG under .reach/) with a vision model and answer a question "
        "about it, e.g. a diagram, a chart, a chess position. Use it whenever the user's question is about what is "
        "on screen in their selection."
    )
    Args: ClassVar[type[BaseModel]] = SeeRegionArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        try:
            answer = await asyncio.to_thread(see, args.image_path, args.question)
        except (PermissionError, FileNotFoundError, ValueError) as e:
            return ToolOutput(status="error", content=str(e))
        except Exception as e:
            msg = str(e) if str(e).startswith(("HTTP", "DEEPINFRA")) else type(e).__name__
            return ToolOutput(status="error", content=f"vision call failed: {msg}")
        return ToolOutput(content=f"Vision model ({MODEL}) says:\n{answer}")
