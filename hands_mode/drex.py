"""Drex Max client for custom hand gestures (EXPERIMENTAL).

One `choice` question per call (same API as backend/reach/commands.py). The key comes from NACE_API_KEY in the
environment or the repo .env / .intern-home/.env; it is never printed or logged. Every decision is appended to
`<reach>/logs/drex.jsonl` in the backend's decision format (purpose `hand_gesture`), so `reach log` shows it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from hands_mode.bridge import reach_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
DREX_URL = "https://console.nace.ai/v1/systemone"
DREX_MODEL = os.environ.get("REACH_GESTURE_MODEL", "drex-max-v1.1")
DREX_TIMEOUT_S = 0.8
USD_PER_INPUT_TOKEN = 0.12e-6   # Drex Max input list price; see backend/reach/commands.py

Post = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def http_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from None


def load_key(name: str = "NACE_API_KEY") -> str | None:
    """The key from the environment, else the repo .env / .intern-home/.env (same files as backend). Never printed."""
    if os.environ.get(name):
        return os.environ[name]
    for path in (os.environ.get("REACH_ENV_FILE"), REPO_ROOT / ".env", REPO_ROOT / ".intern-home" / ".env"):
        if not path:
            continue
        try:
            lines = Path(path).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip().removeprefix("export ")
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() == name and v.strip():
                    return v.strip().strip('"').strip("'")
    return None


def decide(probs: dict[str, float], p_min: float, margin_min: float) -> tuple[str, float, bool, str]:
    """(top label, margin, accepted, reason)."""
    if not probs:
        return "none", 0.0, False, "no probabilities"
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, p = ranked[0]
    margin = p - (ranked[1][1] if len(ranked) > 1 else 0.0)
    if p < p_min:
        return top, margin, False, f"top p {p:.2f} < {p_min}"
    if margin < margin_min:
        return top, margin, False, f"margin {margin:.2f} < {margin_min}"
    return top, margin, True, "ok"


class DrexClient:
    def __init__(self, post: Post = http_post, key: str | None = None, timeout: float = DREX_TIMEOUT_S):
        self.post, self.timeout = post, timeout
        self.key = key if key is not None else load_key()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def classify(self, state: str, instructions: str, criteria: dict[str, str]) -> tuple[dict[str, float], dict]:
        """(probabilities, raw response). Raises on network/HTTP errors and on responses with no probabilities."""
        body = {"model": DREX_MODEL, "state": state,
                "questions": {"gesture": {"type": "choice", "instructions": instructions, "criteria": criteria}}}
        headers = {"Authorization": f"Bearer {self.key}", "X-Nace-Client": "reach-hand-gestures/1"}
        resp = self.post(DREX_URL, headers, body, self.timeout)
        ans = (resp.get("answers") or {}).get("gesture") or {}
        probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items() if k in criteria}
        if not probs:
            raise RuntimeError("no probabilities in response")
        return probs, resp


def drex_log_path() -> Path:
    return reach_dir() / "logs" / "drex.jsonl"


def log_decision(label: str, probs: dict[str, float], accepted: bool, reason: str, latency_ms: float, state: str,
                 resp: dict | None, error: str | None, path: Path | None = None) -> None:
    """Same shape as backend `log_route` (type decision), so `reach log` prints it."""
    usage = (resp or {}).get("usage") or {}
    tin = int(usage.get("input_tokens") or 0)
    event = {
        "task_id": "hand_gesture", "type": "decision", "purpose": "hand_gesture",
        "timestamp": int(time.time() * 1000), "ok": error is None, "error": error,
        "model": (resp or {}).get("model"), "state": state[:300],
        "answers": {"gesture": {"choice": label, "probabilities": probs}},
        "reason": reason, "accepted": accepted, "latency_ms": round(latency_ms),
        "evaluation_time_ms": (resp or {}).get("evaluation_time_ms"),
        "tokens": {"input": tin, "output": int(usage.get("output_tokens") or 0)},
        "cost_usd": round(tin * USD_PER_INPUT_TOKEN, 8),
    }
    path = path or drex_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass
