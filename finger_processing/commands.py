"""Hand-gesture commands: idle, point, click.

Each command has an action (ACTIONS); the actor (cursor_demo/run.py) performs it:

  idle  -> do nothing
  point -> the cursor follows the primary hand's index fingertip
  click -> left click where the cursor was just before the index finger started curling

Click gesture: hold the pointing pose (index extended; middle, ring, pinky curled), then curl the index finger.
Because a curl now means "click", stop the cursor with an open palm or by lowering your hand, not by curling.

Who decides (config `commands.decider`):
  drex       Drex Max classifies a rolling window of the streamed hand data (compact per-frame features, never
             pixels) with one `choice` question per call: at most `max_rate_hz`, plus one immediately when the
             finger pattern changes. Decisions below p_min / margin_min are rejected (the mode stays). Timeouts,
             errors, a missing NACE_API_KEY, or no fresh decision for `stale_s` hand control to the heuristic.
  heuristic  Local rules only: instant, no network.

Drex calls run on a background thread and never block the frame loop. Every Drex decision is appended to
`.reach/logs/drex.jsonl` (purpose `gesture_route`), so `reach log` shows it next to the other Drex decisions.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from finger_processing.processing import FINGERS, CommandsCfg, Frame

REPO_ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ labels and actions

GESTURES: dict[str, str] = {
    "idle": ("Not steering: open palm, relaxed or resting hand, a fist held still, or any pose that is neither "
             "pointing nor a fresh index curl. Do nothing."),
    "point": ("Pointing: the index finger is extended (angle near 180) while middle, ring and pinky are curled, and "
              "it stays extended through the window. The user is steering the cursor."),
    "click": ("Click: within this window the index finger goes from extended to curled (its angle drops sharply) "
              "while middle, ring and pinky stay curled. A deliberate tap of the index finger."),
}
LABELS: tuple[str, ...] = tuple(GESTURES)

ACTIONS: dict[str, tuple[str, str]] = {
    "idle": ("none", "do nothing"),
    "point": ("move", "cursor follows the index fingertip"),
    "click": ("click", "left click at the cursor position from just before the curl"),
}

INSTRUCTIONS = (
    "The state is a short stream of hand-tracking frames for one hand, oldest first. Which gesture command is the "
    "user making at the end of this window?"
)


@dataclass
class Decision:
    label: str                       # idle | point | click
    probs: dict[str, float]
    margin: float
    source: str                      # drex | heuristic
    accepted: bool
    reason: str
    latency_ms: float = 0.0
    t: float = 0.0                   # time of the newest frame the decision is about
    error: str | None = None


def decide(probs: dict[str, float], p_min: float, margin_min: float) -> tuple[str, float, bool, str]:
    """(label, margin, accepted, reason). Rejected decisions leave the current mode unchanged."""
    clean = {k: float(v) for k, v in probs.items() if k in LABELS}
    if not clean:
        return "idle", 0.0, False, "no probabilities"
    ranked = sorted(clean.items(), key=lambda kv: kv[1], reverse=True)
    top, p = ranked[0]
    margin = p - (ranked[1][1] if len(ranked) > 1 else 0.0)
    if p < p_min:
        return top, margin, False, f"top p {p:.2f} < {p_min}"
    if margin < margin_min:
        return top, margin, False, f"margin {margin:.2f} < {margin_min}"
    return top, margin, True, "ok"


# ------------------------------------------------------------------ the stream Drex sees

def frame_features(frame: Frame, t_end: float) -> dict[str, Any] | None:
    """Compact features of the primary hand for one frame (about 40 tokens)."""
    hand = frame.primary
    if hand is None:
        return None
    return {
        "dt_ms": int(round((frame.t - t_end) * 1000)),
        "ext": "".join("1" if hand.fingers[f].extended else "0" for f in FINGERS),
        "angle": [int(round(hand.finger_angles.get(f, 0.0))) for f in FINGERS],
        "vel": [round(hand.velocity[0], 2), round(hand.velocity[1], 2)],
    }


def window_state(frames: list[Frame]) -> str:
    """The Drex `state`: the last frames of the primary hand as text."""
    if not frames:
        return "No hand in view."
    t_end = frames[-1].t
    rows = [r for f in frames if (r := frame_features(f, t_end)) is not None]
    side = frames[-1].primary_side
    if not rows:
        return f"No {side} hand in view."
    legend = (f"User's {side} hand, {len(rows)} frames, oldest first. dt_ms = time relative to the newest frame. "
              "ext = extended flags for thumb,index,middle,ring,pinky (1 = extended). angle = bend at each finger's "
              "middle joint in degrees, same order (180 = straight, under ~150 = curled). vel = index velocity "
              "in camera widths/s (x right, y down).")
    return legend + "\n" + "\n".join(json.dumps(r, separators=(",", ":")) for r in rows)


# ------------------------------------------------------------------ Drex client

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


class DrexGestureClient:
    """One Drex Max `choice` question over GESTURES per call (same API as backend/reach/commands.py)."""

    def __init__(self, cfg: CommandsCfg, post: Post = http_post, key: str | None = None):
        self.cfg = cfg
        self.post = post
        self.key = key if key is not None else load_key()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def body(self, state: str) -> dict[str, Any]:
        return {"model": self.cfg.drex_model, "state": state,
                "questions": {"gesture": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": GESTURES}}}

    def classify(self, state: str) -> tuple[dict[str, float], dict[str, Any]]:
        """(probabilities, raw response). Raises on network/HTTP errors and on responses with no probabilities."""
        headers = {"Authorization": f"Bearer {self.key}", "X-Nace-Client": "reach-gestures/1"}
        resp = self.post(self.cfg.drex_url, headers, self.body(state), self.cfg.timeout_s)
        ans = (resp.get("answers") or {}).get("gesture") or {}
        probs = {k: float(v) for k, v in (ans.get("probabilities") or {}).items()}
        if not probs:
            raise RuntimeError("no probabilities in response")
        return probs, resp


def drex_log_path() -> Path:
    return Path(os.environ.get("REACH_DIR", REPO_ROOT / ".reach")) / "logs" / "drex.jsonl"


def log_decision(d: Decision, state: str, resp: dict[str, Any] | None, path: Path | None = None) -> None:
    """Same shape as backend `log_route` (type decision), purpose gesture_route, so `reach log` prints it."""
    usage = (resp or {}).get("usage") or {}
    tin = int(usage.get("input_tokens") or 0)
    event = {
        "task_id": "gesture", "type": "decision", "purpose": "gesture_route",
        "timestamp": int(time.time() * 1000), "ok": d.error is None, "error": d.error,
        "model": (resp or {}).get("model"), "state": state[:300],
        "answers": {"gesture": {"choice": d.label, "probabilities": d.probs}},
        "reason": d.reason, "accepted": d.accepted, "latency_ms": round(d.latency_ms),
        "evaluation_time_ms": (resp or {}).get("evaluation_time_ms"),
        "tokens": {"input": tin, "output": int(usage.get("output_tokens") or 0)},
        "cost_usd": round(tin * 0.12e-6, 8),  # Drex Max input list price; see backend/reach/commands.py
    }
    path = path or drex_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


class DrexGestureStreamer:
    """Streams the primary hand to Drex on a background thread; never blocks the caller.

    push(frame) every frame; poll() returns Drex decisions that arrived since the last poll.
    """

    def __init__(self, cfg: CommandsCfg, client: DrexGestureClient, log_path: Path | None = None):
        self.cfg = cfg
        self.client = client
        self.log_path = log_path
        self._window: deque[Frame] = deque(maxlen=cfg.window_frames)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._busy = False
        self._last_request = -1e9
        self._out: deque[Decision] = deque()
        self.calls = 0
        self.errors = 0
        self._thread = threading.Thread(target=self._loop, name="drex-gestures", daemon=True)
        self._thread.start()

    def push(self, frame: Frame) -> None:
        with self._lock:
            self._window.append(frame)
            if frame.primary is None or self._busy:
                return
            now = time.monotonic()
            urgent = frame.trigger in ("pattern", "hand_appear")
            due = now - self._last_request >= 1.0 / self.cfg.max_rate_hz
            if urgent or (due and frame.should_classify):
                self._busy = True
                self._last_request = now
                self._wake.set()

    def poll(self) -> list[Decision]:
        with self._lock:
            out, self._out = list(self._out), deque()
        return out

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                frames = list(self._window)
            state = window_state(frames)
            t_end = frames[-1].t if frames else 0.0
            t0 = time.monotonic()
            resp = None
            try:
                probs, resp = self.client.classify(state)
                label, margin, accepted, reason = decide(probs, self.cfg.p_min, self.cfg.margin_min)
                d = Decision(label, probs, margin, "drex", accepted, reason, (time.monotonic() - t0) * 1000, t_end)
            except Exception as e:  # timeout, HTTP, parse: the heuristic covers this decision
                self.errors += 1
                d = Decision("idle", {}, 0.0, "drex", False, "error", (time.monotonic() - t0) * 1000, t_end,
                             error=str(e)[:200])
            self.calls += 1
            if self.cfg.log_decisions:
                try:
                    log_decision(d, state, resp, self.log_path)
                except OSError:
                    pass
            with self._lock:
                self._out.append(d)
                self._busy = False


# ------------------------------------------------------------------ local heuristic

class HeuristicGestures:
    """Rules on the primary hand: point = index extended with middle/ring/pinky curled; click = after pointing for
    point_hold_ms, the index curls while the others stay curled. One click per curl; pointing again re-arms it.

    "Curls" means a deliberate tap, not the index merely dropping out of "extended": its middle-joint angle must
    fall below click_curl_deg (well under extend_angle_deg) for click_curl_frames consecutive frames, within
    click_curl_max_ms of leaving the pointing pose. A relaxed pointing finger hovers near the extended threshold and
    flickers across it; those flickers, and slow sags, return to pointing without clicking."""

    def __init__(self, cfg: CommandsCfg):
        self.cfg = cfg
        self._point_since: float | None = None
        self._armed = False
        self._last_click = -1e9
        self._pending = False                  # left the pointing pose via the index; maybe a click
        self._deep_frames = 0
        self.curl_onset: float | None = None   # when the index last went extended -> curled
        self.last_deep_curl = -1e9             # last time the index was curled past click_curl_deg (others curled)
        self.curl_min_angle: float | None = None  # deepest index angle of the current/last curl attempt (for tuning)

    def _reset(self) -> None:
        self._point_since, self._armed, self._pending, self._deep_frames = None, False, False, 0

    def update(self, frame: Frame) -> tuple[str, bool]:
        """(mode, clicked this frame)."""
        hand = frame.primary
        t = frame.t
        if hand is None:
            self._reset()
            return "idle", False
        ext = hand.fingers_extended
        others_curled = not (ext["middle"] or ext["ring"] or ext["pinky"])
        index_angle = hand.finger_angles.get("index", 180.0)
        deep = others_curled and not ext["index"] and index_angle < self.cfg.click_curl_deg
        if deep:
            self.last_deep_curl = t
        if ext["index"] and others_curled:
            self._pending, self._deep_frames = False, 0   # a flicker or aborted curl: keep pointing, stay armed
            if self._point_since is None:
                self._point_since = t
            if (t - self._point_since) * 1000 >= self.cfg.point_hold_ms:
                self._armed = True
            return "point", False
        if self._point_since is not None and others_curled and not ext["index"]:
            if not self._pending:
                self._pending, self.curl_onset, self.curl_min_angle = True, t, index_angle
            self.curl_min_angle = min(self.curl_min_angle or index_angle, index_angle)
            self._deep_frames = self._deep_frames + 1 if deep else 0
            if self._deep_frames >= self.cfg.click_curl_frames:
                clicked = self._armed and (t - self._last_click) * 1000 >= self.cfg.click_cooldown_ms
                self._reset()  # point again to re-arm
                if clicked:
                    self._last_click = t
                return "idle", clicked
            if (t - self.curl_onset) * 1000 > self.cfg.click_curl_max_ms and self._deep_frames == 0:
                self._reset()  # a slow sag, not a tap
            return "idle", False
        self._reset()  # any other pose ends pointing; point again to re-arm
        return "idle", False


# ------------------------------------------------------------------ the policy

@dataclass
class CommandOutput:
    mode: str                                   # idle | point
    click_at: tuple[int, int] | None = None     # screen position to click this frame, if any
    source: str = "heuristic"                   # who decided this frame's mode/click: drex | heuristic
    decisions: list[Decision] = field(default_factory=list)  # Drex decisions that arrived this frame


class CommandEngine:
    """Turns the hand stream into commands. Feed every Frame to update(); act on the CommandOutput.

    The cursor position comes from `position(frame)` (default: the primary index fingertip on screen). Clicks land
    at the position recorded pre_curl_ms before the index started curling, so the curl itself can't drag the click.
    """

    def __init__(self, cfg: CommandsCfg, streamer: DrexGestureStreamer | None = None,
                 position: Callable[[Frame], tuple[int, int] | None] | None = None):
        self.cfg = cfg
        self.streamer = streamer
        self.heuristic = HeuristicGestures(cfg)
        self.position = position or (lambda f: f.fingers["index"].tip_screen if f.hand_present else None)
        self._history: deque[tuple[float, tuple[int, int]]] = deque(maxlen=90)
        self._drex_mode: str | None = None
        self._drex_t = -1e9
        self._last_click = -1e9
        self.last_decision: Decision | None = None

    @property
    def using_drex(self) -> bool:
        return self.cfg.decider == "drex" and self.streamer is not None and self.streamer.client.available

    def close(self) -> None:
        if self.streamer is not None:
            self.streamer.close()

    def _pre_curl_position(self, now: float) -> tuple[int, int] | None:
        onset = self.heuristic.curl_onset if self.heuristic.curl_onset is not None else now
        target = onset - self.cfg.pre_curl_ms / 1000.0
        best = None
        for t, pos in self._history:
            if t <= target:
                best = pos
            else:
                break
        return best or (self._history[0][1] if self._history else None)

    def _click(self, t: float) -> tuple[int, int] | None:
        if (t - self._last_click) * 1000 < self.cfg.click_cooldown_ms:
            return None
        pos = self._pre_curl_position(t)
        if pos is not None:
            self._last_click = t
        return pos

    def update(self, frame: Frame) -> CommandOutput:
        t = frame.t
        pos = self.position(frame)
        if pos is not None and frame.fingers.get("index") is not None and frame.fingers["index"].extended:
            self._history.append((t, pos))
        h_mode, h_click = self.heuristic.update(frame)

        if not self.using_drex:
            return CommandOutput(h_mode, self._click(t) if h_click else None, "heuristic")

        self.streamer.push(frame)
        decisions = self.streamer.poll()
        click_at = None
        for d in decisions:
            self.last_decision = d
            if d.error is not None or not d.accepted:
                continue
            self._drex_t = time.monotonic()
            if d.label == "click":
                if t - self.heuristic.last_deep_curl > self.cfg.drex_click_confirm_s:
                    d.accepted, d.reason = False, "no deep index curl seen locally"
                    continue  # never click on Drex's word alone while the index is visibly not curled
                click_at = click_at or self._click(t)
                self._drex_mode = "idle"
            else:
                self._drex_mode = d.label
        drex_fresh = time.monotonic() - self._drex_t <= self.cfg.stale_s
        if not drex_fresh or self._drex_mode is None:
            # Graceful degradation: Drex is slow, failing or not confident -> the heuristic decides.
            return CommandOutput(h_mode, self._click(t) if h_click else click_at, "heuristic", decisions)
        mode = self._drex_mode
        if mode == "point" and h_mode != "point":
            mode = "idle"  # never move the cursor while the index is visibly not pointing
        return CommandOutput(mode, click_at, "drex", decisions)


def make_engine(cfg: CommandsCfg, post: Post = http_post, key: str | None = None,
                position: Callable[[Frame], tuple[int, int] | None] | None = None) -> CommandEngine:
    """Engine with a Drex streamer when decider=drex and a key is available; heuristic-only otherwise."""
    streamer = None
    if cfg.decider == "drex":
        client = DrexGestureClient(cfg, post, key)
        if client.available:
            streamer = DrexGestureStreamer(cfg, client)
    return CommandEngine(cfg, streamer, position)
