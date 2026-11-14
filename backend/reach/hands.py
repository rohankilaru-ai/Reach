"""Hands mode as a child process: `reach hands on|off|status` and the `hands` WebSocket message.

Hands mode (hands_mode/ at the repo root) is the one opt-in module that sends real mouse events.
Reach's agent tools never do. This file only starts and stops that process; its pid lives in
.reach/run/hands.pid.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .paths import REPO_ROOT, run_dir


# Called with the new state ({"on", "pid", "reason"}) whenever hands mode starts, stops or exits on its own.
# ipc.Server points it at the relay so every overlay client sees the true state. May be called from a thread.
_emit: Callable[[dict], None] | None = None
_stopping: set[int] = set()   # pids we are stopping on request (their exit is "stopped", not "exited")


def set_emitter(fn: Callable[[dict], None] | None) -> None:
    global _emit
    _emit = fn


def _announce(st: dict) -> None:
    if _emit is not None:
        try:
            _emit(st)
        except Exception:  # a closed loop or dropped client never breaks hands mode
            pass


def stop_perception() -> None:
    """Two controllers must never fight over the mouse: stop the old pinch worker (reach_perception) if it runs."""
    subprocess.run(["pkill", "-f", "reach_perception"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


PHASES = ("no_hand", "pointing", "pinching", "dragging", "paused", "leaving")


def parse_state_line(line: str) -> str | None:
    """hands_mode prints `[hands] <state>` on every state change; returns <state>, or None for any other line."""
    line = line.strip()
    if not line.startswith("[hands] "):
        return None
    return line[len("[hands] "):].strip() or None


def phase_of(state: str) -> str | None:
    """hands_mode's engine state -> the pill's phase (one of PHASES); None leaves the phase as it was."""
    s = state.lower()
    if s.startswith("paused"):
        return "paused"
    if "open palm" in s or "leaving" in s:
        return "leaving"
    if "dragging" in s:
        return "dragging"
    if s.startswith(("point", "clutch", "scroll", "two fingers")):   # "point to move · pinch to click" is pointing
        return "pointing"
    if "pinch" in s or "double click" in s:
        return "pinching"
    if "waiting for hand" in s or "no hand" in s or "lost" in s:
        return "no_hand"
    return None


MIN_PHASE_GAP = 0.1   # at most ~10 phase updates a second


def _watch(proc: subprocess.Popen, log=None) -> None:
    """Reads the child's state lines (broadcast as phases), then waits for it; when it exits on its own (open palm,
    crash, camera failure) says so to every client."""
    phase, last_emit = None, 0.0
    if proc.stdout is not None:
        for raw in proc.stdout:
            if log is not None:
                try:
                    log.write(raw)
                    log.flush()
                except (OSError, ValueError):
                    pass
            st = parse_state_line(raw.decode(errors="replace") if isinstance(raw, bytes) else raw)
            new = phase_of(st) if st else None
            now = time.monotonic()
            if new and new != phase and now - last_emit >= MIN_PHASE_GAP and proc.pid not in _stopping:
                phase, last_emit = new, now
                _announce({"on": True, "pid": proc.pid, "phase": phase})
            elif new:
                phase = new
    code = proc.wait()
    if log is not None:
        try:
            log.close()
        except OSError:
            pass
    requested = proc.pid in _stopping
    _stopping.discard(proc.pid)
    try:
        if int(pidfile().read_text().strip()) == proc.pid:
            pidfile().unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
    if not requested:
        why = "open palm" if phase == "leaving" else ("exited" if code == 0 else f"exited with code {code}")
        _announce({"on": False, "pid": None, "reason": why})


def pidfile() -> Path:
    return run_dir() / "hands.pid"


def command() -> list[str]:
    """The child: hands_mode with real control, in the repo-root environment (finger_processing deps)."""
    if override := os.environ.get("REACH_HANDS_CMD"):   # tests use a harmless stand-in
        return override.split()
    return ["uv", "run", "--project", str(REPO_ROOT), "python", "-m", "hands_mode.run", "--control"]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie (our own exited child) is not running
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def status() -> dict:
    try:
        pid = int(pidfile().read_text().strip())
    except (OSError, ValueError):
        return {"on": False, "pid": None}
    if _alive(pid):
        return {"on": True, "pid": pid}
    pidfile().unlink(missing_ok=True)
    return {"on": False, "pid": None}


def start(preview: bool = False) -> dict:
    """Reach starts hands mode with --no-preview: no separate camera window; its state shows in the pill."""
    st = status()
    if st["on"]:
        return st   # starting twice is a no-op
    stop_perception()
    run_dir().mkdir(parents=True, exist_ok=True)
    log = open(run_dir() / "hands.log", "ab")
    cmd = command() + ([] if preview else ["--no-preview"])
    proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    pidfile().write_text(str(proc.pid))
    threading.Thread(target=_watch, args=(proc, log), name="hands-watch", daemon=True).start()
    st = {"on": True, "pid": proc.pid}
    _announce({**st, "reason": "started"})
    return st


def stop(timeout: float = 3.0) -> dict:
    """SIGINT first (hands mode releases any held button and exits), then SIGTERM, then SIGKILL."""
    st = status()
    if not st["on"]:
        return st
    pid = st["pid"]
    _stopping.add(pid)
    for sig, wait in ((signal.SIGINT, timeout), (signal.SIGTERM, 1.0), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            os.kill(pid, sig)
        end = time.monotonic() + wait
        while time.monotonic() < end and _alive(pid):
            time.sleep(0.05)
        if not _alive(pid):
            break
    pidfile().unlink(missing_ok=True)
    _announce({"on": False, "pid": None, "reason": "stopped"})
    return {"on": False, "pid": None}


def set_on(on: bool) -> dict:
    return start() if on else stop()


def cmd_hands(args) -> int:
    st = {"on": start, "off": stop, "status": status}[args.action]()
    print(f"hands mode {'on' if st['on'] else 'off'}" + (f" (pid {st['pid']})" if st["pid"] else ""))
    if args.action == "on":
        print(f"log: {run_dir() / 'hands.log'}")
    return 0
