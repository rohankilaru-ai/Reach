"""Custom hand gestures (EXPERIMENTAL): record, store, match (local distance + Drex) and act.

Store: <reach>/gestures.json = {"gestures": [{"name", "action": {"kind", "value"}, "hands": 1|2,
"examples": [WINDOW frames x FEATURE_LEN] x 2, "created"}]}. The overlay reads it to list gestures.

Recording: the backend writes <reach>/run/hands_request.json ({"op": "record", "id", "name", "action"}); the layer
takes it (deletes the file), counts down 3-2-1, captures WINDOW frames with a hand, twice, and saves. The mouse
engine does nothing while recording.

Matching: every frame the live window (last WINDOW frames with a hand) is compared to each example with the same
hand count (mean per-frame L2, min over examples). Under GATE, Drex Max picks one of the candidates or `none`
(background thread, one call in flight, at most one per DREX_MIN_INTERVAL_S). Without a key, or on a Drex
error/timeout, the local distance decides alone with the tighter LOCAL_ACCEPT (source `local`). One firing per
COOLDOWN_S, and the hand must leave the gate before the same pose fires again.

Acting: top -> Cmd+Up, bottom -> Cmd+Down, keys -> a "cmd+shift+t" combo (Accessibility needed, like clicks);
command / ask -> `hands_gesture` to the overlay through the Reach server.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from finger_processing.processing import FINGERS
from hands_mode.bridge import reach_dir
from hands_mode.drex import DrexClient, decide, log_decision

# ---------------------------------------------------------------- tunables (tuned on the synthetic tests)
WINDOW = 10                  # frames per example and per live window
EXAMPLES = 2
COUNT_S = 1.0                # countdown step (3, 2, 1)
CAPTURE_TIMEOUT_S = 5.0      # no hand for this long during "go": recording cancelled
GATE = 1.0                   # mean per-frame L2 under this: a candidate (ask Drex)
LOCAL_ACCEPT = 0.5           # no Drex (no key, error, timeout): local alone fires under this
P_MIN, MARGIN_MIN = 0.7, 0.2
COOLDOWN_S = 1.5
DREX_MIN_INTERVAL_S = 0.25   # at most 4 calls/s
VEL_CLIP = 1.0               # velocity (normalized units/s) clipped to +-this
REQUEST_POLL_S = 0.2
REQUEST_STALE_S = 30.0
FLASH_S = 1.0                # how long "gesture: NAME" stays the status
NAME_MAX = 40
KINDS = ("top", "bottom", "keys", "command", "ask")
NONE = "none"

PER_HAND = 17                # present, wrist xy, index tip xy, 5 extended, 5 angles/180, velocity xy
FEATURE_LEN = 2 * PER_HAND + 1

INSTRUCTIONS = (
    "The state is the last 10 hand-tracking frames, oldest first. Each option is a gesture the user recorded, "
    "described by its recorded example frames in the same format. Which recorded gesture is the user making now? "
    "Pick 'none' unless the live frames clearly match one of them."
)
LEGEND = ("Rows: L/R = user's left/right hand. ext = extended flags thumb,index,middle,ring,pinky. ang = middle-joint "
          "angle per finger in degrees (180 straight). wrist, tip = wrist and index tip, normalized image coords "
          "(x right, y down). vel = hand velocity (units/s). d = distance between the two index tips / hand size.")


# ---------------------------------------------------------------- features

def _clip(v: float, lim: float) -> float:
    return max(-lim, min(lim, float(v)))


def hand_features(hand) -> list[float]:
    if hand is None:
        return [0.0] * PER_HAND
    wx, wy = hand.landmarks[0][:2] if hand.landmarks else (0.0, 0.0)
    ix, iy = hand.fingers["index"].tip
    ext = [1.0 if hand.fingers[f].extended else 0.0 for f in FINGERS]
    ang = [hand.finger_angles.get(f, 180.0) / 180.0 for f in FINGERS]
    vx, vy = (_clip(v, VEL_CLIP) for v in hand.velocity)
    return [1.0, float(wx), float(wy), float(ix), float(iy), *ext, *ang, vx, vy]


def frame_features(frame) -> list[float]:
    """FEATURE_LEN floats: left hand, right hand (zeros when absent), index-tip distance / mean hand scale."""
    left, right = frame.hands.get("left"), frame.hands.get("right")
    d = 0.0
    if left is not None and right is not None:
        scale = (left.hand_scale + right.hand_scale) / 2
        if scale > 0:
            (ax, ay), (bx, by) = left.fingers["index"].tip, right.fingers["index"].tip
            d = min(math.hypot(ax - bx, ay - by) / scale, 4.0) / 4.0
    return [*hand_features(left), *hand_features(right), d]


def hand_count(vectors) -> int:
    """2 if both hands are present in most frames, else 1."""
    both = sum(1 for v in vectors if v[0] > 0.5 and v[PER_HAND] > 0.5)
    return 2 if both * 2 > len(vectors) else 1


def window_distance(a, b) -> float:
    """Mean per-frame L2 between two WINDOW x FEATURE_LEN windows."""
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float), axis=1).mean())


def rows(vectors) -> str:
    """Compact text rows (one per frame, oldest first) for Drex."""
    out = []
    for v in vectors:
        r: dict = {}
        for side, o in (("L", 0), ("R", PER_HAND)):
            if v[o] > 0.5:
                r[side] = {"ext": "".join("1" if x > 0.5 else "0" for x in v[o + 5:o + 10]),
                           "ang": [round(x * 180) for x in v[o + 10:o + 15]],
                           "wrist": [round(v[o + 1], 2), round(v[o + 2], 2)],
                           "tip": [round(v[o + 3], 2), round(v[o + 4], 2)],
                           "vel": [round(v[o + 15], 2), round(v[o + 16], 2)]}
        if v[-1] > 0:
            r["d"] = round(v[-1] * 4, 2)
        out.append(json.dumps(r, separators=(",", ":")))
    return "\n".join(out)


def describe(g: dict) -> str:
    """Drex criterion for one gesture: its examples as rows."""
    parts = [f"A {g['hands']}-hand gesture. Recorded examples:"]
    for i, ex in enumerate(g["examples"], 1):
        parts.append(f"Example {i}:\n{rows(ex)}")
    return "\n".join(parts)


# ---------------------------------------------------------------- store

def store_path(root: Path | None = None) -> Path:
    return (root or reach_dir()) / "gestures.json"


def valid_action(action) -> bool:
    return (isinstance(action, dict) and action.get("kind") in KINDS and isinstance(action.get("value", ""), str)
            and (action["kind"] in ("top", "bottom") or bool(str(action.get("value", "")).strip())))


def valid_name(name) -> bool:
    return isinstance(name, str) and 0 < len(name.strip()) <= NAME_MAX


def load_gestures(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for g in data.get("gestures", []) if isinstance(data, dict) else []:
        try:
            if (valid_name(g["name"]) and valid_action(g["action"]) and g["hands"] in (1, 2)
                    and all(np.asarray(ex, float).shape == (WINDOW, FEATURE_LEN) for ex in g["examples"])
                    and g["examples"]):
                out.append(g)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def write_gestures(path: Path, gestures: list[dict]) -> None:
    """Atomic: the overlay never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"gestures": gestures}))
    os.replace(tmp, path)


def save_gesture(path: Path, g: dict) -> None:
    """Add g, replacing a gesture with the same name."""
    write_gestures(path, [x for x in load_gestures(path) if x["name"] != g["name"]] + [g])


# ---------------------------------------------------------------- recorder

class Recorder:
    """Countdown 3-2-1, capture WINDOW frames with a hand; EXAMPLES times. `result` is the gesture when done."""

    def __init__(self, name: str, action: dict, now: float):
        self.name, self.action = name, action
        self.example, self.capturing, self.phase_start = 1, False, now
        self.frames: list[list[float]] = []
        self.examples: list[list[list[float]]] = []
        self.done, self.result, self.status = False, None, ""

    def update(self, frame, now: float) -> str:
        if self.done:
            return self.status
        head = f"recording '{self.name}' · example {self.example}/{EXAMPLES}"
        if not self.capturing:
            left = 3 - int((now - self.phase_start) / COUNT_S)
            if left > 0:
                self.status = f"{head} · get ready {left}"
                return self.status
            self.capturing, self.phase_start, self.frames = True, now, []
        if frame is not None and frame.hands:
            self.frames.append(frame_features(frame))
        if len(self.frames) >= WINDOW:
            self.examples.append(self.frames[:WINDOW])
            if len(self.examples) == EXAMPLES:
                self.done = True
                self.result = {"name": self.name, "action": self.action,
                               "hands": hand_count([v for ex in self.examples for v in ex]),
                               "examples": [[[round(x, 4) for x in v] for v in ex] for ex in self.examples],
                               "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                self.status = f"saved gesture '{self.name}'"
            else:
                self.example, self.capturing, self.phase_start = self.example + 1, False, now
                self.status = f"recording '{self.name}' · example {self.example}/{EXAMPLES} · get ready 3"
        elif now - self.phase_start > CAPTURE_TIMEOUT_S:
            self.done, self.status = True, "recording cancelled · no hand"
        else:
            self.status = f"{head} · go"
        return self.status


# ---------------------------------------------------------------- matcher

@dataclass
class Decision:
    label: str
    accepted: bool
    source: str                       # drex | local
    probs: dict = field(default_factory=dict)
    error: str | None = None
    local_best: str | None = None
    local_d: float = math.inf


class Matcher:
    """Feed every frame to update(); it returns the gesture dict that fired this frame, or None."""

    def __init__(self, gestures: list[dict], drex: DrexClient | None = None, run_async: bool = True,
                 log_path: Path | None = None):
        self.drex, self.run_async, self.log_path = drex, run_async, log_path
        self.window: deque[list[float]] = deque(maxlen=WINDOW)
        self._lock = threading.Lock()
        self._out: list[Decision] = []
        self._busy, self._last_call, self._last_fire, self._armed = False, -1e9, -1e9, True
        self.last: Decision | None = None
        self.set_gestures(gestures)

    def set_gestures(self, gestures: list[dict]) -> None:
        self.gestures = {g["name"]: g for g in gestures}
        self._examples = {g["name"]: [np.asarray(ex, float) for ex in g["examples"]] for g in gestures}

    @property
    def has_two_hand(self) -> bool:
        return any(g["hands"] == 2 for g in self.gestures.values())

    @property
    def use_drex(self) -> bool:
        return self.drex is not None and self.drex.available

    def distances(self) -> dict[str, float]:
        """Min distance from the live window to each gesture with the same hand count."""
        live = np.asarray(self.window, float)
        n = hand_count(self.window)
        return {name: min(window_distance(live, ex) for ex in self._examples[name])
                for name, g in self.gestures.items() if g["hands"] == n}

    def update(self, frame, now: float) -> dict | None:
        if frame.hands:
            self.window.append(frame_features(frame))
        else:
            self.window.clear()
            self._armed = True
        fired = None
        if len(self.window) == WINDOW and self.gestures:
            dist = self.distances()
            best = min(dist, key=dist.get) if dist else None
            best_d = dist[best] if best else math.inf
            if best_d > GATE:
                self._armed = True
            elif self._armed and now - self._last_fire >= COOLDOWN_S:
                if not self.use_drex:
                    if best_d < LOCAL_ACCEPT:
                        fired = best
                        self.last = Decision(best, True, "local", local_best=best, local_d=best_d)
                elif not self._busy and now - self._last_call >= DREX_MIN_INTERVAL_S:
                    self._ask([n for n, d in dist.items() if d <= GATE], best, best_d, now)
        for d in self._poll():   # Drex decisions that arrived (this frame when run inline)
            self.last = d
            if d.accepted:
                fired = d.label
            elif d.error is not None and d.local_d < LOCAL_ACCEPT:   # Drex failed: the local match decides
                fired = d.local_best
                d.source = "local"
        if fired in self.gestures and self._armed and now - self._last_fire >= COOLDOWN_S:
            self._last_fire, self._armed = now, False
            return self.gestures[fired]
        return None

    def _poll(self) -> list[Decision]:
        with self._lock:
            out, self._out = self._out, []
        return out

    def _ask(self, names: list[str], best: str, best_d: float, now: float) -> None:
        self._busy, self._last_call = True, now
        state = LEGEND + "\nLive frames:\n" + rows(self.window)
        criteria = {n: describe(self.gestures[n]) for n in names}
        criteria[NONE] = "None of the recorded gestures: any other hand pose or motion."
        job = lambda: self._run(state, criteria, best, best_d)   # noqa: E731
        if self.run_async:
            threading.Thread(target=job, name="drex-hand-gesture", daemon=True).start()
        else:
            job()

    def _run(self, state: str, criteria: dict[str, str], best: str, best_d: float) -> None:
        t0, resp = time.monotonic(), None
        try:
            probs, resp = self.drex.classify(state, INSTRUCTIONS, criteria)
            label, _margin, accepted, reason = decide(probs, P_MIN, MARGIN_MIN)
            if label == NONE:
                accepted, reason = False, "none"
            d = Decision(label, accepted, "drex", probs, local_best=best, local_d=best_d)
        except Exception as e:  # noqa: BLE001 - timeout, HTTP, parse: the local match covers this one
            reason = "error"
            d = Decision(NONE, False, "drex", error=str(e)[:200], local_best=best, local_d=best_d)
        log_decision(d.label, d.probs, d.accepted, reason, (time.monotonic() - t0) * 1000, state, resp, d.error,
                     self.log_path)
        with self._lock:
            self._out.append(d)
            self._busy = False


# ---------------------------------------------------------------- acting

def act(g: dict, mouse, bridge) -> str:
    """Perform the gesture's action; returns the status line."""
    kind, value, name = g["action"]["kind"], g["action"].get("value", ""), g["name"]
    if kind in ("top", "bottom", "keys"):
        if not mouse.can_click:
            return f"gesture: {name} · needs Accessibility permission"
        try:
            mouse.key({"top": "cmd+up", "bottom": "cmd+down"}.get(kind, value))
        except ValueError as e:
            return f"gesture: {name} · {e}"
    elif bridge is None or not bridge.gesture(name, {"kind": kind, "value": value}):
        return f"gesture: {name} · no Reach server"
    return f"gesture: {name}"


# ---------------------------------------------------------------- the layer run.py uses

class GestureLayer:
    """Recording requests, matching, acting and the two-hand pause, around the mouse engine.

    update(frame or None, now) -> (status or None, pause_engine). When pause_engine is True, do not feed the frame
    to the engine (it has already been released).
    """

    def __init__(self, engine, mouse, bridge=None, root: Path | None = None, drex: DrexClient | None = None,
                 run_async: bool = True, log_path: Path | None = None):
        self.engine, self.mouse, self.bridge = engine, mouse, bridge
        root = root or reach_dir()
        self.store, self.request = store_path(root), root / "run" / "hands_request.json"
        self.matcher = Matcher(load_gestures(self.store), drex, run_async, log_path)
        self.recorder: Recorder | None = None
        self._seen: set[str] = set()
        self._next_poll = -1e9
        self._store_mtime = self._mtime()
        self._flash, self._flash_until = "", -1e9

    def _mtime(self) -> float:
        try:
            return self.store.stat().st_mtime
        except OSError:
            return 0.0

    def poll_request(self, now: float) -> None:
        try:
            age = time.time() - self.request.stat().st_mtime
            req = json.loads(self.request.read_text())
        except (OSError, ValueError):
            return
        self.request.unlink(missing_ok=True)
        rid = req.get("id") if isinstance(req, dict) else None
        if (age > REQUEST_STALE_S or not isinstance(rid, str) or rid in self._seen or req.get("op") != "record"
                or not valid_name(req.get("name")) or not valid_action(req.get("action"))):
            return
        self._seen.add(rid)
        self.recorder = Recorder(req["name"].strip(), req["action"], now)

    def _flash_status(self, now: float) -> str | None:
        return self._flash if now < self._flash_until else None

    def update(self, frame, now: float) -> tuple[str | None, bool]:
        if now >= self._next_poll:
            self._next_poll = now + REQUEST_POLL_S
            self.poll_request(now)
            if (m := self._mtime()) != self._store_mtime:   # the overlay forgot a gesture (or we saved one)
                self._store_mtime = m
                self.matcher.set_gestures(load_gestures(self.store))
        if self.recorder is not None:
            self.engine.release()
            status = self.recorder.update(frame, now)
            if self.recorder.done:
                if self.recorder.result is not None:
                    save_gesture(self.store, self.recorder.result)
                    self.matcher.set_gestures(load_gestures(self.store))
                self.recorder = None
                self._flash, self._flash_until = status, now + 2 * FLASH_S
            return status, True
        if frame is None:
            return self._flash_status(now), False
        g = self.matcher.update(frame, now)
        if g is not None:
            self._flash, self._flash_until = act(g, self.mouse, self.bridge), now + FLASH_S
        if len(frame.hands) == 2 and self.matcher.has_two_hand:
            self.engine.release()
            return self._flash_status(now) or "two hands · gestures", True
        return self._flash_status(now), False
