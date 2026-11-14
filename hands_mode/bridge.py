"""Relay a hand selection to the overlay through the Reach backend's localhost WebSocket (`reach serve`).

Run files: $REACH_DIR/run/server.json (port) and $REACH_DIR/run/token. The token is read by code only, never logged.
With no server running, `select` returns False and the selection stays local.

Protocol (backend/reach/contracts.py, backend/reach/relay.py): hello carries `role: "perception"`, which lets this
client send `select`: {"type": "select", "selection": {"region": [x, y, w, h], "path"?, "text"?, "image_path"?}}.
image_path must be under the server's .reach/. The server relays it to overlay clients as `selection` and keeps it
60 s for the next ask. No ask starts.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path


def reach_dir() -> Path:
    """Same rule as backend/reach/paths.py: $REACH_DIR or <repo>/.reach."""
    return Path(os.environ.get("REACH_DIR", Path(__file__).resolve().parents[1] / ".reach"))


def _main_checkout_reach() -> Path:
    """<main checkout>/.reach (from git's common dir), so a git worktree finds the main checkout's server."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=3,
                                check=True).stdout.strip()
        return Path(common).parent / ".reach"
    except (subprocess.SubprocessError, OSError):
        return reach_dir()


def server_reach_dir() -> Path:
    """The .reach/ the server uses. Captures go here too, so the server accepts their image_path."""
    d = reach_dir()
    if (d / "run" / "server.json").exists() or "REACH_DIR" in os.environ:
        return d
    main = _main_checkout_reach()
    return main if (main / "run" / "server.json").exists() else d


def run_files() -> tuple[int, str] | None:
    d = server_reach_dir() / "run"
    try:
        return int(json.loads((d / "server.json").read_text())["port"]), (d / "token").read_text().strip()
    except (OSError, ValueError, KeyError):
        return None


class BridgeClient:
    """Connects lazily on the first send (and again later if the server went away)."""

    RETRY_S = 2.0

    def __init__(self) -> None:
        self.ws = None
        self._lock = threading.Lock()
        self._last_try = -1e9

    def connect(self, timeout: float = 2.0) -> bool:
        rf = run_files()
        if not rf:
            return False
        port, token = rf
        try:
            from websockets.sync.client import connect

            ws = connect(f"ws://127.0.0.1:{port}", open_timeout=timeout, max_size=8 * 2**20)
            ws.send(json.dumps({"type": "hello", "token": token, "role": "perception"}))
            if json.loads(ws.recv(timeout=timeout)).get("type") != "ready":
                ws.close()
                return False
        except Exception:  # noqa: BLE001 - no backend is a normal state
            return False
        self.ws = ws
        threading.Thread(target=self._drain, args=(ws,), daemon=True).start()
        return True

    def _drain(self, ws) -> None:
        """Read everything the server sends so its queue never backs up."""
        try:
            for _ in ws:
                pass
        except Exception:  # noqa: BLE001
            pass
        if self.ws is ws:
            self.ws = None

    def _send(self, obj: dict) -> bool:
        if self.ws is None and time.monotonic() - self._last_try > self.RETRY_S:
            self._last_try = time.monotonic()
            self.connect()
        ws = self.ws
        if ws is None:
            return False
        try:
            with self._lock:
                ws.send(json.dumps(obj))
            return True
        except Exception:  # noqa: BLE001
            self.ws = None
            return False

    def select(self, region: list[float], text: str | None, image_path: str | None,
               path: list[list[float]] | None = None) -> bool:
        sel: dict = {"region": region}
        if path:
            sel["path"] = path
        if text:
            sel["text"] = text
        if image_path:
            sel["image_path"] = image_path
        return self._send({"type": "select", "selection": sel})

    def send(self, obj: dict) -> bool:
        return self._send(obj)

    def gesture(self, name: str, action: dict) -> bool:
        """A custom gesture fired (EXPERIMENTAL): the server relays it to the overlay, which runs the command/ask."""
        return self._send({"type": "hands_gesture", "name": name, "action": action})

    def close(self) -> None:
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
