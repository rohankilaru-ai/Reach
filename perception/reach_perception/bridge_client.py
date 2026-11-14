"""Client for the Reach backend's localhost WebSocket (see backend/reach/contracts.py).

Run files: $REACH_DIR/run/server.json (port) and $REACH_DIR/run/token. The token is read by code only and never logged.
If no server is running the worker runs local-only.

Protocol (backend/reach/contracts.py, backend/reach/relay.py):
- hello carries `role: "perception"`, which lets this client send `select` and `pointer`.
- `select`: {"type": "select", "selection": {"region": [x, y, w, h], "text", "image_path"}}; image_path must be under
  .reach/. The server relays it to overlay clients as `selection` and keeps it 60 s for the next ask. No ask starts.
- `pointer`: {"type": "pointer", "x", "y", "state": "hover" | "pinch"} in screen points. When the hand is lost we
  simply stop sending; the overlay fades the ghost after 1 s.
"""

from __future__ import annotations

import json
import os
import subprocess
import queue
import threading
import time

from .screen_map import reach_dir


def server_reach_dir():
    """The .reach/ the server uses: $REACH_DIR, else this checkout's .reach, else (in a git worktree with no server
    of its own) the main checkout's .reach. Captures go here too, so the server accepts their image_path."""
    d = reach_dir()
    if (d / "run" / "server.json").exists() or "REACH_DIR" in os.environ:
        return d
    main = main_checkout_reach()
    return main if (main / "run" / "server.json").exists() else d


def run_files() -> tuple[int, str] | None:
    d = server_reach_dir() / "run"
    try:
        port = int(json.loads((d / "server.json").read_text())["port"])
        return port, (d / "token").read_text().strip()
    except (OSError, ValueError, KeyError):
        return None


def main_checkout_reach() -> "os.PathLike":
    """<main checkout>/.reach, found from git's common dir; falls back to reach_dir()."""
    from pathlib import Path

    here = Path(__file__).resolve().parent
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=here,
                                capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        return Path(common).parent / ".reach"
    except (subprocess.SubprocessError, OSError):
        return reach_dir()


class BridgeClient:
    def __init__(self) -> None:
        self.ws = None
        self.status = "local-only (no backend running)"
        self.inbox: queue.Queue[dict] = queue.Queue(maxsize=200)
        self._lock = threading.Lock()
        self._last_pointer = 0.0

    def connect(self, timeout: float = 3.0) -> bool:
        rf = run_files()
        if not rf:
            return False
        port, token = rf
        try:
            from websockets.sync.client import connect

            ws = connect(f"ws://127.0.0.1:{port}", open_timeout=timeout, max_size=8 * 2**20)
            ws.send(json.dumps({"type": "hello", "token": token, "role": "perception"}))
            first = json.loads(ws.recv(timeout=timeout))
            if first.get("type") != "ready":
                ws.close()
                self.status = "backend refused hello"
                return False
        except Exception as e:  # noqa: BLE001 - no backend is a normal state
            self.status = f"local-only ({type(e).__name__})"
            return False
        self.ws = ws
        self.status = f"connected 127.0.0.1:{port}"
        threading.Thread(target=self._drain, daemon=True).start()
        return True

    def _drain(self) -> None:
        """Read everything the server sends so its queue never backs up."""
        try:
            for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "error":
                    print(f"[bridge] server error: {msg.get('message', '')[:160]}")
                try:
                    self.inbox.put_nowait(msg)
                except queue.Full:
                    pass
        except Exception:  # noqa: BLE001
            pass
        self.ws = None
        self.status = "disconnected"

    def _send(self, obj: dict) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            with self._lock:
                ws.send(json.dumps(obj))
            return True
        except Exception:  # noqa: BLE001
            self.ws = None
            self.status = "disconnected"
            return False

    def pointer(self, x: float, y: float, pinch: bool, hz: float = 15) -> bool:
        now = time.monotonic()
        if now - self._last_pointer < 1 / hz:
            return False
        self._last_pointer = now
        return self._send({"type": "pointer", "x": float(x), "y": float(y), "state": "pinch" if pinch else "hover"})

    def select(self, region: list[float], text: str | None, image_path: str | None,
               path: list[list[float]] | None = None) -> bool:
        sel = {"region": region}
        if path:
            sel["path"] = path
        if text:
            sel["text"] = text
        if image_path:
            sel["image_path"] = image_path
        return self._send({"type": "select", "selection": sel})

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:  # noqa: BLE001
                pass
