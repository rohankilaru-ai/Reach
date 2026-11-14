"""Browser task: hands one bounded web task to the Nace browser harness and reports exactly what came back.

The harness runs as a child process held here (its own process group), so an abort kills it and the Chromium it
started. Keys never pass through Python: `scripts/nace-browser.mjs` loads the repo `.env` inside its own process.
Only harness status `succeeded` counts as done; everything else (partial, failed, timed_out, cancelled, a refused
start, unreadable output) is reported as NOT done with the harness's own reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ..paths import REPO_ROOT, reach_dir

DEFAULT_MODEL = "Qwen/Qwen3-VL-235B-A22B-Instruct"
MAX_COST_USD = "0.10"
MAX_SECONDS = 150
KILL_GRACE_S = 1.0
MARKER = "[browser_task receipt]"


def script_path() -> Path:
    return Path(os.environ.get("REACH_BROWSER_SCRIPT", REPO_ROOT / "scripts" / "nace-browser.mjs"))


def command(task: str, start_url: str, out_dir: Path) -> list[str]:
    model = os.environ.get("REACH_BROWSER_HELPER_MODEL", DEFAULT_MODEL)
    return [
        os.environ.get("REACH_NODE", "node"), str(script_path()), "run", task, "--url", start_url,
        "--json", "--temp-profile", "--mode", os.environ.get("REACH_BROWSER_MODE", "loop"), "--model", model,
        "--max-cost", MAX_COST_USD, "--max-seconds", str(MAX_SECONDS), "--out", str(out_dir),
    ]  # --allow-risky is never passed


def summarize(result: dict[str, Any] | None, *, exit_code: int | None, aborted: bool = False) -> dict[str, Any]:
    """Harness JSON -> the fields Reach shows. `done` is True only for status `succeeded`."""
    if aborted:
        return {"outcome": "aborted", "done": False, "status": "cancelled", "kind": "aborted",
                "reason": "the user aborted the task; the browser was closed", "answer": "", "final_url": None,
                "cost_usd": 0.0, "drex_calls": 0, "llm_calls": 0, "screenshot": None}
    if not isinstance(result, dict):
        return {"outcome": "not_done", "done": False, "status": "no_result", "kind": "no_result",
                "reason": f"the browser harness printed no readable result (exit {exit_code})", "answer": "",
                "final_url": None, "cost_usd": 0.0, "drex_calls": 0, "llm_calls": 0, "screenshot": None}
    if isinstance(result.get("error"), dict) and not result.get("status"):
        err = result["error"]
        return {"outcome": "not_done", "done": False, "status": "run_refused", "kind": str(err.get("code", "error")),
                "reason": str(err.get("message", ""))[:300], "answer": "", "final_url": None,
                "cost_usd": 0.0, "drex_calls": 0, "llm_calls": 0, "screenshot": None}
    status = str(result.get("status") or "unknown")
    outcome = result.get("outcome") or {}
    usage = result.get("usage") or {}
    shot = next((a.get("path") for a in result.get("artifacts") or [] if a.get("kind") == "screenshot"), None)
    answer = str(result.get("answer_text") or "")
    # the harness can end `succeeded` while its own answer says the result was not seen on screen: not done
    unconfirmed = answer.lstrip().lower().startswith("not confirmed on screen")
    done = status == "succeeded" and not unconfirmed
    return {
        "outcome": "done" if done else "not_done",
        "done": done,
        "status": status,
        "kind": "not_confirmed_on_screen" if unconfirmed else str(outcome.get("kind") or status),
        "reason": str(outcome.get("reason") or "")[:300],
        "answer": answer[:2000],
        "final_url": result.get("final_url"),
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "drex_calls": int(usage.get("drex_calls") or 0),
        "llm_calls": int(usage.get("llm_calls") or 0),
        "screenshot": shot,
    }


def render(s: dict[str, Any]) -> str:
    """Tool output: the answer (or why not), then a one-line JSON receipt the backend parses."""
    if s["done"]:
        head = f"Browser task DONE (harness status succeeded).\nAnswer: {s['answer']}"
    elif s["outcome"] == "aborted":
        head = "Browser task ABORTED by the user. Nothing was completed. Do not retry."
    else:
        head = (
            f"Browser task NOT DONE: harness status {s['status']} ({s['kind']}). Reason: {s['reason'] or '-'}\n"
            f"What the browser saw: {s['answer'] or '-'}\n"
            "Tell the user plainly that the task was not completed and why. Do not claim it was done, do not retry."
        )
    marker = {k: s[k] for k in ("outcome", "status", "kind", "reason", "final_url", "cost_usd", "drex_calls",
                                "llm_calls", "screenshot")}
    return f"{head}\n{MARKER} {json.dumps(marker, separators=(',', ':'))}"


def parse_marker(content: str) -> dict[str, Any] | None:
    """The receipt marker in a browser_task tool result, or None."""
    m = re.search(re.escape(MARKER) + r" (\{.*\})", content or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _descendants(root: int) -> list[int]:
    """All live descendant pids of `root` (Chromium may run in a process group of its own)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [root]
    while stack:
        for c in children.get(stack.pop(), []):
            found.append(c)
            stack.append(c)
    return found


def _signal_all(pgid: int, pids: list[int], sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the child's group and every descendant, then SIGKILL whatever is left after a short grace."""
    pids = _descendants(proc.pid)
    _signal_all(proc.pid, pids, signal.SIGTERM)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
    pids = sorted(set(pids) | set(_descendants(proc.pid)))
    _signal_all(proc.pid, pids, signal.SIGKILL)
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), 1.0)


async def run_task(task: str, start_url: str, abort: asyncio.Event | None = None,
                   cmd: list[str] | None = None) -> dict[str, Any]:
    """Run one harness task; returns the summary. Abort (the event) kills the child tree at once."""
    out_dir = reach_dir() / "browser" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    argv = cmd or command(task, start_url, out_dir)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        stdin=asyncio.subprocess.DEVNULL, start_new_session=True,
    )
    reader = asyncio.create_task(proc.stdout.read())  # type: ignore[union-attr]
    waiters = {reader}
    stopper = asyncio.create_task(abort.wait()) if abort is not None else None
    if stopper:
        waiters.add(stopper)
    try:
        done, _ = await asyncio.wait(waiters, timeout=MAX_SECONDS + 60, return_when=asyncio.FIRST_COMPLETED)
        if reader not in done:
            await kill_tree(proc)
            reader.cancel()
            aborted = stopper is not None and stopper in done
            return summarize(None, exit_code=None, aborted=aborted) if aborted else summarize(None, exit_code=None)
        raw = reader.result()
        code = await proc.wait()
        try:
            result = json.loads(raw.decode("utf-8", "replace") or "null")
        except ValueError:
            result = None
        return summarize(result, exit_code=code)
    except asyncio.CancelledError:
        await kill_tree(proc)
        raise
    finally:
        if stopper:
            stopper.cancel()
        if proc.returncode is None:
            await kill_tree(proc)
