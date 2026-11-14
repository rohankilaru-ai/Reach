"""Hands mode as a child process: `reach hands on|off|status` and the `hands` WebSocket message.

Hands mode (hands_mode/ at the repo root) is the one opt-in module that sends real mouse events.
Reach's agent tools never do. This file only starts and stops that process; its pid lives in
.reach/run/hands.pid.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .paths import REPO_ROOT, run_dir


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


def start(preview: bool = True) -> dict:
    st = status()
    if st["on"]:
        return st
    run_dir().mkdir(parents=True, exist_ok=True)
    log = open(run_dir() / "hands.log", "ab")
    cmd = command() + ([] if preview else ["--no-preview"])
    proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pidfile().write_text(str(proc.pid))
    return {"on": True, "pid": proc.pid}


def stop(timeout: float = 3.0) -> dict:
    """SIGINT first (hands mode releases any held button and exits), then SIGTERM, then SIGKILL."""
    st = status()
    if not st["on"]:
        return st
    pid = st["pid"]
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
    return {"on": False, "pid": None}


def set_on(on: bool) -> dict:
    return start() if on else stop()


def cmd_hands(args) -> int:
    st = {"on": start, "off": stop, "status": status}[args.action]()
    print(f"hands mode {'on' if st['on'] else 'off'}" + (f" (pid {st['pid']})" if st["pid"] else ""))
    if args.action == "on":
        print(f"log: {run_dir() / 'hands.log'}")
    return 0
