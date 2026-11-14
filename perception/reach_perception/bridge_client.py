"""Client for the Reach backend's localhost WebSocket (see backend/reach/contracts.py).

Run files: $REACH_DIR/run/server.json (port) and $REACH_DIR/run/token. The token is read by code only and never logged.
If no server is running the worker runs local-only.

Protocol notes (as of main e54aacd):
- `select` is inbound: {"type": "select", "selection": {"region": [x, y, w, h], "text", "image_path"}}. The server
  accepts it and currently ignores it (S2 consumes it).
- `pointer` is declared server -> client only (needs task_id). We still send {"type": "pointer", x, y, pinch,
  tracking}; on the first "bad message" reply we stop sending pointers and say so once. Backend change needed.
"""

from __future__ import annotations

import json
import queue
import threading
import time

from .screen_map import reach_dir


def run_files() -> tuple[int, str] | None:
    d = reach_dir() / "run"
    try:
        port = int(json.loads((d / "server.json").read_text())["port"])
        return port, (d / "token").read_text().strip()
    except (OSError, ValueError, KeyError):
        return None


class BridgeClient:
    def __init__(self) -> None:
        self.ws = None
        self.status = "local-only (no backend running)"
        self.pointer_ok = True
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
            ws.send(json.dumps({"type": "hello", "token": token}))
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
                if msg.get("type") == "error" and "bad message" in msg.get("message", "") and self.pointer_ok:
                    self.pointer_ok = False
                    print("[bridge] backend rejects inbound `pointer`; pointer messages stopped (backend change needed)")
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

    def pointer(self, x: float | None, y: float | None, pinch: bool, tracking: bool, hz: float = 15) -> None:
        now = time.monotonic()
        if not self.pointer_ok or now - self._last_pointer < 1 / hz:
            return
        self._last_pointer = now
        self._send({"type": "pointer", "x": x, "y": y, "pinch": pinch, "tracking": tracking})

    def select(self, region: list[float], text: str | None, image_path: str | None) -> bool:
        sel = {"region": region}
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
