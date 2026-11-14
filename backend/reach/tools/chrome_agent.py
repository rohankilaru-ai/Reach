"""browser_in_chrome: runs one task in the owner's own signed-in Chrome through the Drex extension's pairing server.

The extension setting 'Let coding agents use Drex' shows a pairing code. With REACH_PAIRING_CODE set in the backend
environment, this tool spawns the harness's stdio MCP server (`node <harness>/mcp/server.mjs`) with
DREX_PAIRING_CODE, speaks a minimal MCP JSON-RPC client over stdin/stdout (initialize, tools/list, tools/call
`drex_run`), and maps the run's status: only `done` counts as done. The child is held for abort and killed on the time
cap. The pairing code is never logged and is scrubbed from every string this module returns.
Progress: the pairing server sends no progress notifications, so none are forwarded.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..paths import REPO_ROOT
from . import browser

DEFAULT_TIMEOUT_S = 120.0
TOOL = "drex_run"
MARKER = "[browser_in_chrome receipt]"
SETUP = (
    "To let Reach use your own Chrome (see docs/EXTENSION.md):\n"
    "1. In Chrome, open the Drex side panel (Alt+Shift+D) and open settings (the gear).\n"
    "2. Turn on 'Let coding agents use Drex'; it shows a pairing code.\n"
    "3. Put REACH_PAIRING_CODE=<that code> in .reach/server.env.\n"
    "4. Restart Reach."
)


def pairing_code() -> str:
    return os.environ.get("REACH_PAIRING_CODE", "").strip()


def server_path() -> Path:
    harness = os.environ.get("REACH_BROWSER_HARNESS_DIR") or str(REPO_ROOT / "vendor" / "drex-browser-harness")
    return Path(harness) / "mcp" / "server.mjs"


def command() -> list[str]:
    return [os.environ.get("REACH_NODE", "node"), str(server_path())]


def _scrub(text: str, code: str) -> str:
    text = str(text or "")
    return text.replace(code, "[pairing code]") if code else text


def needs_you(reason: str) -> dict[str, Any]:
    return {"outcome": "needs_you", "done": False, "status": "needs_you", "reason": reason, "answer": "",
            "final_url": None, "cost_usd": 0.0}


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """tools/call result -> Reach fields. Only extension status `done` is done."""
    texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
    text = "\n".join(texts)
    if result.get("isError"):
        return needs_you(text[:500] or "the Drex extension did not take the task")
    try:
        r = json.loads(text)
    except ValueError:
        r = None
    if not isinstance(r, dict):
        return {"outcome": "not_done", "done": False, "status": "no_result", "reason": "unreadable result from the "
                "extension", "answer": text[:500], "final_url": None, "cost_usd": 0.0}
    status = str(r.get("status") or "unknown")
    done = status == "done"
    page = r.get("end_page") or {}
    return {"outcome": "done" if done else "not_done", "done": done, "status": status,
            "reason": str(r.get("message") or "")[:300], "answer": str(r.get("answer") or "")[:2000],
            "final_url": page.get("url") if isinstance(page, dict) else None,
            "cost_usd": float(r.get("cost_usd") or 0.0)}


def render(s: dict[str, Any], code: str = "") -> str:
    if s["done"]:
        head = f"Chrome task DONE (extension status done).\nAnswer: {s['answer']}"
    elif s["outcome"] == "aborted":
        head = ("Chrome task ABORTED by the user; the link to Chrome was closed (the run in Chrome may still be "
                "finishing its current move). Do not retry.")
    elif s["outcome"] == "needs_you":
        head = (f"Chrome task NOT DONE: needs you. {s['reason']}\n{SETUP}\n"
                "Tell the user plainly what to do; do not claim it was done.")
    else:
        head = (f"Chrome task NOT DONE: extension status {s['status']}. Reason: {s['reason'] or '-'}\n"
                f"What it saw: {s['answer'] or '-'}\n"
                "Tell the user plainly that the task was not completed and why. Do not claim it was done, do not retry.")
    marker = {k: s[k] for k in ("outcome", "status", "reason", "final_url", "cost_usd")}
    return _scrub(f"{head}\n{MARKER} {json.dumps(marker, separators=(',', ':'))}", code)


class _Client:
    """Newline-delimited JSON-RPC over the child's stdio; replies matched by id, everything else ignored."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc, self.next_id = proc, 1

    async def send(self, msg: dict[str, Any]) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())  # type: ignore[union-attr]
        await self.proc.stdin.drain()  # type: ignore[union-attr]

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        rid, self.next_id = self.next_id, self.next_id + 1
        await self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            line = await self.proc.stdout.readline()  # type: ignore[union-attr]
            if not line:
                raise ConnectionError("the pairing server exited")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid:
                if "error" in msg:
                    raise ConnectionError(str((msg["error"] or {}).get("message", "MCP error")))
                return msg.get("result") or {}


async def _session(client: _Client, task: str, start_url: str | None) -> dict[str, Any]:
    await client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "reach", "version": "0.1"}})
    await client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools = await client.request("tools/list", {})
    if TOOL not in {t.get("name") for t in tools.get("tools") or []}:
        return needs_you(f"the pairing server has no {TOOL} tool (harness out of date?)")
    args: dict[str, Any] = {"task": task}
    if start_url:
        args["url"] = start_url
    return summarize(await client.request("tools/call", {"name": TOOL, "arguments": args}))


_lock = asyncio.Lock()  # one pairing server at a time: two would collide on the bridge port


async def run_task(task: str, start_url: str | None = None, abort: asyncio.Event | None = None,
                   cmd: list[str] | None = None, timeout_s: float | None = None) -> dict[str, Any]:
    code = pairing_code()
    if not code:
        return needs_you("REACH_PAIRING_CODE is not set, so Reach is not paired with the extension.")
    argv = cmd or command()
    if cmd is None and not server_path().exists():
        return needs_you("the Drex browser harness (mcp/server.mjs) was not found on this machine.")
    timeout_s = float(os.environ.get("REACH_CHROME_TIMEOUT_S", DEFAULT_TIMEOUT_S)) if timeout_s is None else timeout_s
    env = {k: v for k, v in browser.child_env().items() if k != "REACH_PAIRING_CODE"}
    env["DREX_PAIRING_CODE"] = code
    async with _lock:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True, env=env, limit=64 * 1024 * 1024,
            )
        except OSError:
            return needs_you("could not start node for the pairing server (Node 22.18 or later is needed).")
        browser.register_child(proc.pid)
        work = asyncio.create_task(_session(_Client(proc), task, start_url))
        waiters: set[asyncio.Task[Any]] = {work}
        stopper = asyncio.create_task(abort.wait()) if abort is not None else None
        if stopper:
            waiters.add(stopper)
        try:
            done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                try:
                    s = work.result()
                except (ConnectionError, OSError, ValueError) as e:
                    s = needs_you(f"the pairing server stopped before answering ({e}).")
            elif stopper is not None and stopper in done:
                work.cancel()
                s = {"outcome": "aborted", "done": False, "status": "cancelled", "reason": "the user aborted the task",
                     "answer": "", "final_url": None, "cost_usd": 0.0}
            else:
                work.cancel()
                s = {"outcome": "not_done", "done": False, "status": "timeout",
                     "reason": f"no result within {int(timeout_s)} s; the link to Chrome was closed (the run in "
                               "Chrome may still be finishing)", "answer": "", "final_url": None, "cost_usd": 0.0}
            return {k: _scrub(v, code) if isinstance(v, str) else v for k, v in s.items()}
        except asyncio.CancelledError:
            work.cancel()
            await browser.kill_tree(proc)
            raise
        finally:
            if stopper:
                stopper.cancel()
            if proc.returncode is None:
                await browser.kill_tree(proc)
            browser.unregister_child(proc.pid)


def python_fake_cmd(script: Path) -> list[str]:
    """For tests: run a fake MCP server script with this interpreter."""
    return [sys.executable, str(script)]
