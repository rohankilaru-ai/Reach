"""Hands mode as a child process: `reach hands on|off|status` and the `hands` WebSocket message.

Hands mode (hands_mode/ at the repo root) is the one opt-in module that sends real mouse events.
Reach's agent tools never do. This file only starts and stops that process; its pid lives in
.reach/run/hands.pid.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

from .paths import REPO_ROOT, reach_dir, run_dir

GESTURE_KINDS = ("top", "bottom", "keys", "command", "ask")
GESTURE_NAME_MAX = 40


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


# ---------------------------------------------------------------- custom gestures (EXPERIMENTAL)
# hands_mode/gestures.py owns the format; this side only asks for a recording and removes gestures.

def gestures_path() -> Path:
    return reach_dir() / "gestures.json"


def request_path() -> Path:
    return run_dir() / "hands_request.json"


def check_gesture(name, action=None) -> str | None:
    """None if fine, else why not."""
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > GESTURE_NAME_MAX:
        return f"gesture name must be 1-{GESTURE_NAME_MAX} characters"
    if action is None:
        return None
    if not isinstance(action, dict) or action.get("kind") not in GESTURE_KINDS:
        return f"gesture action kind must be one of {', '.join(GESTURE_KINDS)}"
    value = action.get("value", "")
    if not isinstance(value, str) or (action["kind"] not in ("top", "bottom") and not value.strip()):
        return "gesture action needs a value"
    return None


def _write_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def request_record(name: str, action: dict) -> dict:
    """Start hands mode if needed and ask it to record a gesture. Raises ValueError on a bad name/action."""
    if why := check_gesture(name, action):
        raise ValueError(why)
    st = start()
    _write_atomic(request_path(), {"op": "record", "id": uuid.uuid4().hex, "name": name.strip(),
                                   "action": {"kind": action["kind"], "value": action.get("value", "")}})
    return st


def forget(name: str) -> dict:
    """Remove the gesture called `name` from gestures.json (hands mode reloads it)."""
    if why := check_gesture(name):
        raise ValueError(why)
    try:
        data = json.loads(gestures_path().read_text())
        gestures = data.get("gestures", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        gestures = []
    kept = [g for g in gestures if not (isinstance(g, dict) and g.get("name") == name.strip())]
    if len(kept) != len(gestures):
        _write_atomic(gestures_path(), {"gestures": kept})
    return status()


def gesture_event(d: dict) -> str | None:
    """A clean `hands_gesture` message for overlays, or None if malformed (only command/ask reach the overlay)."""
    action = d.get("action")
    if (check_gesture(d.get("name"), action) is None and action["kind"] in ("command", "ask")):
        return json.dumps({"type": "hands_gesture", "name": d["name"].strip(),
                           "action": {"kind": action["kind"], "value": action["value"]}})
    return None


def cmd_hands(args) -> int:
    st = {"on": start, "off": stop, "status": status}[args.action]()
    print(f"hands mode {'on' if st['on'] else 'off'}" + (f" (pid {st['pid']})" if st["pid"] else ""))
    if args.action == "on":
        print(f"log: {run_dir() / 'hands.log'}")
    return 0
