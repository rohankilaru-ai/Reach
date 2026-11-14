"""Finger processing: camera frames -> per-finger state and position, every frame.

Pipeline per camera frame (latency-critical path only; logging, preview and
consumers run after the Frame is published):

  capture thread (newest frame only)
    -> small grayscale -> Sobel motion gate (skips MediaPipe when idle)
    -> MediaPipe Hand Landmarker (VIDEO mode, up to two hands)
    -> assign detections to the left/right slot (continuity first, then label)
    -> per hand: lock (acquire, hold dropouts, detect swaps), curled/extended
       per finger, pinch hysteresis, One Euro smoothing, screen homography
    -> Frame: `hands` = {"left": Hand, "right": Hand} (whichever are present);
       the top-level fields (fingers, pointer_screen, ...) read from the
       primary hand (left by default), which drives the cursor and commands

Coordinates: normalized image coords (0..1), mirrored so moving your hand to
your right moves the point right. MediaPipe gets the unmirrored image and only
the landmark x is flipped, so no pixel copy is spent on mirroring.
"""

from __future__ import annotations

import itertools
import json
import math
import queue
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol

import cv2
import numpy as np
import yaml
from pydantic import BaseModel

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PKG_DIR / "config.yaml"

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
FINGERTIPS = {"thumb": 4, "index": 8, "middle": 12, "ring": 16, "pinky": 20}


# =============================================================== configuration

class CameraCfg(BaseModel):
    index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 60
    fourcc: str | None = "MJPG"
    buffer_size: int = 1
    exposure: float | None = None


class TrackerCfg(BaseModel):
    model_path: str = "hand_landmarker.task"
    min_detection_confidence: float = 0.5
    min_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    delegate: Literal["cpu", "gpu"] = "cpu"
    swap_handedness: bool = False  # MediaPipe's label already matches the user's hand on this camera


class HandLockCfg(BaseModel):
    """Lock settings for one hand slot."""

    control_hand: Literal["right", "left", "any"] = "right"
    handedness_vote_frames: int = 5
    handedness_vote_min: int = 3
    hold_frames: int = 3
    swap_jump: float = 0.30
    swap_scale_ratio: float = 0.40


class HandsCfg(BaseModel):
    max_hands: Literal[1, 2] = 2
    primary: Literal["left", "right"] = "left"   # hand that drives the cursor and commands
    primary_fallback: bool = False               # use the other hand when the primary is absent
    handedness_vote_frames: int = 5
    handedness_vote_min: int = 3
    hold_frames: int = 3
    swap_jump: float = 0.30
    swap_scale_ratio: float = 0.40

    def lock_cfg(self, side: str) -> HandLockCfg:
        return HandLockCfg(control_hand=side, handedness_vote_frames=self.handedness_vote_frames,
                           handedness_vote_min=self.handedness_vote_min, hold_frames=self.hold_frames,
                           swap_jump=self.swap_jump, swap_scale_ratio=self.swap_scale_ratio)


class FeaturesCfg(BaseModel):
    extend_angle_deg: float = 150.0
    thumb_extend_ratio: float = 0.55
    pinch_enter: float = 0.25
    pinch_exit: float = 0.35
    pattern_hold_frames: int = 2


class PointerCfg(BaseModel):
    anchor: Literal["index_tip", "index_mcp", "blend"] = "blend"
    blend_tip_weight: float = 0.5
    pinch_freeze_ms: float = 100.0


class SmoothingCfg(BaseModel):
    min_cutoff: float = 1.0
    beta: float = 0.5
    d_cutoff: float = 1.0


class GatingCfg(BaseModel):
    small_width: int = 160
    small_height: int = 120
    classify_motion: float = 0.04
    idle_motion: float = 0.02
    idle_after_s: float = 1.0
    idle_landmarker_hz: float = 5.0


class CalibrationCfg(BaseModel):
    path: str = "calibration.json"
    inset: float = 0.10
    dwell_s: float = 0.8
    still_speed: float = 0.05
    default_region: tuple[float, float, float, float] = (0.2, 0.2, 0.8, 0.8)


class CommandsCfg(BaseModel):
    """Hand-gesture commands (finger_processing/commands.py)."""

    decider: Literal["drex", "heuristic"] = "drex"   # who decides idle/point/click; heuristic is the fallback
    drex_url: str = "https://console.nace.ai/v1/systemone"
    drex_model: str = "drex-max-v1.1"
    max_rate_hz: float = 5.0          # Drex calls per second at most (plus one right away on a pattern change)
    timeout_s: float = 0.8            # hard timeout per Drex call; on timeout the heuristic decides
    stale_s: float = 1.5              # no fresh Drex decision this long -> heuristic drives the mode
    window_frames: int = 10           # frames of the primary hand sent per call (~330 ms at 30 fps)
    p_min: float = 0.70               # accept a Drex decision only if top p >= p_min ...
    margin_min: float = 0.20          # ... and top - runner-up >= margin_min
    point_hold_ms: float = 120.0      # pointing must hold this long before an index curl counts as a click
    click_curl_deg: float = 135.0     # a click needs the index middle-joint angle below this (well under extend_angle_deg)
    click_curl_frames: int = 1        # ... for this many consecutive frames
    click_curl_max_ms: float = 500.0  # ... reached within this long of leaving the pointing pose (a sag is not a tap)
    drex_click_confirm_s: float = 1.0 # a Drex click counts only if the local tracker saw such a curl this recently
    click_cooldown_ms: float = 400.0
    pre_curl_ms: float = 120.0        # the click lands where the cursor was this long before the curl began
    log_decisions: bool = True        # append Drex decisions to .reach/logs/drex.jsonl (`reach log`)


class ScreenCfg(BaseModel):
    width: int | None = None
    height: int | None = None


class Config(BaseModel):
    camera: CameraCfg = CameraCfg()
    tracker: TrackerCfg = TrackerCfg()
    hands: HandsCfg = HandsCfg()
    features: FeaturesCfg = FeaturesCfg()
    pointer: PointerCfg = PointerCfg()
    smoothing: SmoothingCfg = SmoothingCfg()
    gating: GatingCfg = GatingCfg()
    calibration: CalibrationCfg = CalibrationCfg()
    screen: ScreenCfg = ScreenCfg()
    commands: CommandsCfg = CommandsCfg()
    window_size: int = 10
    preview_hz: float = 20.0


def resolve_path(p: str | Path) -> Path:
    """Relative paths in the config are relative to this folder."""
    p = Path(p)
    return p if p.is_absolute() else PKG_DIR / p


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG
    if not p.exists():
        return Config()
    return Config.model_validate(yaml.safe_load(p.read_text()) or {})


# ============================================================== data contract

@dataclass(frozen=True, slots=True)
class Finger:
    """One finger, every frame."""

    name: str                       # thumb | index | middle | ring | pinky
    extended: bool                  # False = curled
    tip: tuple[float, float]        # smoothed fingertip, normalized mirrored image coords
    tip_raw: tuple[float, float]    # same, before smoothing
    tip_screen: tuple[int, int]     # fingertip mapped to screen pixels
    depth: float                    # MediaPipe z relative to the wrist; more negative = closer to camera

    @property
    def curled(self) -> bool:
        return not self.extended

    @property
    def state(self) -> str:
        return "extended" if self.extended else "curled"


SIDES = ("left", "right")


@dataclass(frozen=True, slots=True)
class Hand:
    """One tracked hand, every frame it is present."""

    side: Literal["left", "right"]
    fingers: dict[str, Finger]                         # thumb, index, middle, ring, pinky
    landmarks: tuple[tuple[float, float, float], ...]  # all 21 joints, mirrored normalized
    pinch_dist: float = float("inf")                   # thumb tip to index tip / hand scale
    pinch_active: bool = False
    pinch_edge: Literal["down", "up"] | None = None
    hand_scale: float = 0.0
    tracking_score: float = 0.0
    pointer_raw: tuple[float, float] | None = None     # cursor anchor before smoothing
    pointer: tuple[float, float] | None = None         # cursor anchor, smoothed
    pointer_screen: tuple[int, int] | None = None      # cursor anchor in screen pixels
    velocity: tuple[float, float] = (0.0, 0.0)         # pointer velocity, normalized units/s
    scroll_velocity: tuple[float, float] = (0.0, 0.0)  # index+middle midpoint velocity
    source: Literal["landmarker", "hold"] = "landmarker"
    trigger: str | None = None                         # hand_appear | pinch_down | pinch_up | pattern
    finger_angles: dict[str, float] = field(default_factory=dict)  # middle-joint angle, deg; 180 = straight

    @property
    def fingers_extended(self) -> dict[str, bool]:
        return {name: f.extended for name, f in self.fingers.items()}


@dataclass(frozen=True, slots=True)
class Frame:
    """Everything finger processing knows about one camera frame.

    `hands` streams every present hand. The flat fields below (`hand_present`,
    `fingers`, `pointer_screen`, ...) are read from the primary hand, the one
    that drives the cursor and commands (config `hands.primary`, default left).
    """

    t: float                                   # capture time, monotonic seconds
    frame_id: int
    hands: dict[str, Hand] = field(default_factory=dict)      # "left" / "right", present hands only
    primary_side: Literal["left", "right"] = "left"
    triggers: dict[str, str] = field(default_factory=dict)    # side -> trigger, incl. hand_lost
    motion: float = 0.0
    should_classify: bool = False
    t_capture: float = 0.0
    t_publish: float = 0.0

    # --- per-side access --------------------------------------------------------
    @property
    def left(self) -> Hand | None:
        return self.hands.get("left")

    @property
    def right(self) -> Hand | None:
        return self.hands.get("right")

    @property
    def primary(self) -> Hand | None:
        return self.hands.get(self.primary_side)

    # --- primary hand (drives cursor/commands) ------------------------------------
    @property
    def hand_present(self) -> bool:
        return self.primary is not None

    @property
    def handedness(self) -> str | None:
        return self.primary_side if self.primary is not None else None

    @property
    def fingers(self) -> dict[str, Finger]:
        h = self.primary
        return h.fingers if h else {}

    @property
    def fingers_extended(self) -> dict[str, bool]:
        h = self.primary
        return h.fingers_extended if h else {}

    @property
    def landmarks(self) -> tuple[tuple[float, float, float], ...]:
        h = self.primary
        return h.landmarks if h else ()

    @property
    def pinch_dist(self) -> float:
        h = self.primary
        return h.pinch_dist if h else float("inf")

    @property
    def pinch_active(self) -> bool:
        h = self.primary
        return h.pinch_active if h else False

    @property
    def pinch_edge(self) -> str | None:
        h = self.primary
        return h.pinch_edge if h else None

    @property
    def hand_scale(self) -> float:
        h = self.primary
        return h.hand_scale if h else 0.0

    @property
    def tracking_score(self) -> float:
        h = self.primary
        return h.tracking_score if h else 0.0

    @property
    def pointer_raw(self) -> tuple[float, float] | None:
        h = self.primary
        return h.pointer_raw if h else None

    @property
    def pointer(self) -> tuple[float, float] | None:
        h = self.primary
        return h.pointer if h else None

    @property
    def pointer_screen(self) -> tuple[int, int] | None:
        h = self.primary
        return h.pointer_screen if h else None

    @property
    def velocity(self) -> tuple[float, float]:
        h = self.primary
        return h.velocity if h else (0.0, 0.0)

    @property
    def scroll_velocity(self) -> tuple[float, float]:
        h = self.primary
        return h.scroll_velocity if h else (0.0, 0.0)

    @property
    def source(self) -> str:
        h = self.primary
        return h.source if h else "none"

    @property
    def trigger(self) -> str | None:
        """The primary hand's trigger this frame (hand_appear, hand_lost, pinch_*, pattern)."""
        return self.triggers.get(self.primary_side)

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================== One Euro filter

def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """One Euro filter (Casiez et al. 2012). `beta` is the lag knob.

    The derivative is taken between raw consecutive samples (the reference
    implementation differences against the lagging filtered value, which
    overestimates velocity during sustained motion) and doubles as velocity.
    """

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self._x: float | None = None
        self._raw = 0.0
        self._dx = 0.0
        self._t: float | None = None

    @property
    def derivative(self) -> float:
        return self._dx

    def __call__(self, x: float, t: float) -> float:
        if self._x is None or self._t is None:
            self._x, self._raw, self._t, self._dx = x, x, t, 0.0
            return x
        dt = t - self._t
        if dt <= 0.0:
            return self._x
        dx = (x - self._raw) / dt
        self._raw = x
        self._dx += _alpha(self.d_cutoff, dt) * (dx - self._dx)
        self._x += _alpha(self.min_cutoff + self.beta * abs(self._dx), dt) * (x - self._x)
        self._t = t
        return self._x


class OneEuro2D:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self.fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def reset(self) -> None:
        self.fx.reset()
        self.fy.reset()

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        return self.fx(x, t), self.fy(y, t)

    @property
    def velocity(self) -> tuple[float, float]:
        return self.fx.derivative, self.fy.derivative


# ==================================================================== features
# Geometry runs in aspect-corrected space: normalized x is scaled by image width
# and y by height, so x (and z, which shares x's scale) are multiplied by W/H.

WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, MIDDLE_TIP, PINKY_MCP = 0, 4, 5, 8, 9, 12, 17
FINGER_JOINTS = {"index": (5, 6, 8), "middle": (9, 10, 12), "ring": (13, 14, 16), "pinky": (17, 18, 20)}


def to_metric(landmarks: np.ndarray, aspect: float) -> np.ndarray:
    pts = np.asarray(landmarks, dtype=np.float64).copy()
    pts[:, 0] *= aspect
    pts[:, 2] *= aspect
    return pts


def hand_scale(pts: np.ndarray) -> float:
    """Wrist -> middle knuckle, with a palm-width fallback when the palm tilts."""
    d09 = np.linalg.norm(pts[MIDDLE_MCP, :2] - pts[WRIST, :2])
    d05 = np.linalg.norm(pts[INDEX_MCP, :2] - pts[WRIST, :2])
    d017 = np.linalg.norm(pts[PINKY_MCP, :2] - pts[WRIST, :2])
    return float(max(d09, 0.5 * (d05 + d017), 1e-6))


def _angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n < 1e-9:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1.0, 1.0))))


def finger_angles(pts: np.ndarray) -> dict[str, float]:
    """Bend at each finger's middle joint in degrees (thumb: at its IP joint). 180 = straight, lower = curled."""
    out = {"thumb": _angle_deg(pts[2], pts[3], pts[THUMB_TIP])}
    for name, (mcp, pip, tip) in FINGER_JOINTS.items():
        out[name] = _angle_deg(pts[mcp], pts[pip], pts[tip])
    return {f: round(out[f], 1) for f in FINGERS}


def fingers_extended(pts: np.ndarray, scale: float, extend_angle_deg: float,
                     thumb_extend_ratio: float) -> dict[str, bool]:
    """A long finger is extended when straight at the middle joint and its tip
    reaches past that joint; the thumb when its tip is far from the index knuckle."""
    out: dict[str, bool] = {}
    wrist = pts[WRIST]
    for name, (mcp, pip, tip) in FINGER_JOINTS.items():
        straight = _angle_deg(pts[mcp], pts[pip], pts[tip]) > extend_angle_deg
        reaches = np.linalg.norm(pts[tip] - wrist) > np.linalg.norm(pts[pip] - wrist)
        out[name] = bool(straight and reaches)
    out["thumb"] = bool(np.linalg.norm(pts[THUMB_TIP, :2] - pts[INDEX_MCP, :2]) / scale > thumb_extend_ratio)
    return {f: out[f] for f in FINGERS}


def pinch_distance(pts: np.ndarray, scale: float) -> float:
    return float(np.linalg.norm(pts[THUMB_TIP] - pts[INDEX_TIP]) / scale)


def pointer_anchor(landmarks: np.ndarray, anchor: str, tip_weight: float) -> tuple[float, float]:
    tip, mcp = landmarks[INDEX_TIP, :2], landmarks[INDEX_MCP, :2]
    p = tip if anchor == "index_tip" else mcp if anchor == "index_mcp" else tip_weight * tip + (1 - tip_weight) * mcp
    return float(p[0]), float(p[1])


class PinchDetector:
    """Hysteresis on pinch_dist; edges fire immediately, with no low-pass delay."""

    def __init__(self, enter: float, exit: float):
        if enter >= exit:
            raise ValueError("pinch_enter must be below pinch_exit")
        self.enter, self.exit, self.active = enter, exit, False

    def reset(self) -> None:
        self.active = False

    def update(self, dist: float) -> str | None:
        if not self.active and dist < self.enter:
            self.active = True
            return "down"
        if self.active and dist > self.exit:
            self.active = False
            return "up"
        return None


class PatternWatcher:
    """Fires once when the curled/extended pattern changes and holds for N frames."""

    def __init__(self, hold_frames: int):
        self.hold = max(1, hold_frames)
        self.reset()

    def reset(self) -> None:
        self.stable: tuple[bool, ...] | None = None
        self._candidate: tuple[bool, ...] | None = None
        self._count = 0

    def update(self, flags: dict[str, bool]) -> bool:
        pattern = tuple(flags[f] for f in FINGERS)
        if pattern == self.stable:
            self._candidate, self._count = None, 0
            return False
        self._count = self._count + 1 if pattern == self._candidate else 1
        self._candidate = pattern
        if self._count >= self.hold:
            first = self.stable is None
            self.stable, self._candidate, self._count = pattern, None, 0
            return not first  # the first pattern is covered by hand_appear
        return False


# =================================================================== hand lock

@dataclass(slots=True)
class Observation:
    landmarks: np.ndarray        # (21, 3) normalized, mirrored
    handedness: str              # "left" | "right"
    score: float


@dataclass(slots=True)
class LockResult:
    obs: Observation | None
    held: bool = False           # last good hand repeated through a dropout
    event: str | None = None     # acquired | lost | swap


class HandLock:
    """Tracks one hand. SEARCHING accepts a hand once enough handedness votes
    match control_hand; LOCKED ignores label flips, holds short dropouts, and
    treats a big wrist jump or scale change as a different hand."""

    def __init__(self, cfg: HandLockCfg):
        self.cfg = cfg
        self._votes: deque[bool] = deque(maxlen=cfg.handedness_vote_frames)
        self.reset()

    def reset(self) -> None:
        self.locked = False
        self._votes.clear()
        self._missing = 0
        self._last: Observation | None = None
        self._last_scale = 0.0

    def _matches(self, obs: Observation) -> bool:
        return self.cfg.control_hand == "any" or obs.handedness == self.cfg.control_hand

    def update(self, obs: Observation | None, scale: float = 0.0) -> LockResult:
        if not self.locked:
            if obs is None:
                self._missing += 1
                if self._missing > self.cfg.hold_frames:
                    self._votes.clear()
                return LockResult(None)
            self._missing = 0
            self._votes.append(self._matches(obs))
            if sum(self._votes) >= self.cfg.handedness_vote_min:
                self.locked, self._last, self._last_scale = True, obs, scale
                return LockResult(obs, event="acquired")
            return LockResult(None)

        if obs is None:
            self._missing += 1
            if self._missing <= self.cfg.hold_frames and self._last is not None:
                return LockResult(self._last, held=True)
            self.reset()
            return LockResult(None, event="lost")

        if self._is_swap(obs, scale):
            self.reset()
            self._votes.append(self._matches(obs))
            return LockResult(None, event="swap")

        self._missing, self._last, self._last_scale = 0, obs, scale
        return LockResult(obs)

    @property
    def last_wrist(self) -> np.ndarray | None:
        return self._last.landmarks[0, :2] if self._last is not None else None

    def _is_swap(self, obs: Observation, scale: float) -> bool:
        if self._last is None:
            return False
        if np.linalg.norm(obs.landmarks[0, :2] - self._last.landmarks[0, :2]) > self.cfg.swap_jump:
            return True
        return (self._last_scale > 0 and scale > 0
                and abs(scale - self._last_scale) / self._last_scale > self.cfg.swap_scale_ratio)


# Assignment costs. A locked slot costs its wrist distance (per-frame motion is
# well under swap_jump), so it beats any unlocked slot (>= UNLOCKED_MATCH) and a
# one-frame left/right label flip can't steal a tracked hand.
UNLOCKED_MATCH, UNLOCKED_MISMATCH, LOCKED_TOO_FAR = 0.3, 1.0, 2.0


def assign_hands(observations: list[Observation], locks: dict[str, HandLock]) -> dict[str, Observation | None]:
    """Map up to two detections onto the "left"/"right" slots.

    Tracked hands keep their slot by continuity (nearest wrist); new hands go by
    MediaPipe's label. If two hands carry the same label, the one further left
    in the mirrored view is taken as the left hand.
    """
    out: dict[str, Observation | None] = {s: None for s in SIDES}
    obs = list(observations)[:2]
    if not obs:
        return out
    if len(obs) == 2 and obs[0].handedness == obs[1].handedness:
        a, b = sorted(obs, key=lambda o: o.landmarks[0, 0])
        obs = [Observation(a.landmarks, "left", a.score), Observation(b.landmarks, "right", b.score)]

    def cost(o: Observation, side: str) -> float:
        lock = locks[side]
        wrist = lock.last_wrist
        if lock.locked and wrist is not None:
            d = float(np.linalg.norm(o.landmarks[0, :2] - wrist))
            return d if d <= lock.cfg.swap_jump else LOCKED_TOO_FAR + d
        return UNLOCKED_MATCH if o.handedness == side else UNLOCKED_MISMATCH

    best = min(itertools.permutations(SIDES, len(obs)),
               key=lambda sides: sum(cost(o, s) for o, s in zip(obs, sides)))
    for o, s in zip(obs, best):
        out[s] = o
    return out


# ================================================================ motion gate

def gradient_magnitude(gray_small: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude normalized by its mean, so global brightness and
    auto-exposure gain changes don't read as motion."""
    g = gray_small.astype(np.float32)
    mag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    return mag / (float(mag.mean()) + 1e-3)


def hand_roi(landmarks_mirrored: np.ndarray, pad: float = 0.1) -> tuple[float, float, float, float]:
    """Hand bounding box in unmirrored normalized coords, padded. Pass several
    hands stacked (N*21 x 3) to get the box around all of them."""
    xs, ys = 1.0 - landmarks_mirrored[:, 0], landmarks_mirrored[:, 1]
    return float(xs.min() - pad), float(ys.min() - pad), float(xs.max() + pad), float(ys.max() + pad)


class MotionGate:
    def __init__(self, cfg: GatingCfg):
        self.cfg = cfg
        self._prev: np.ndarray | None = None
        self._no_hand_since: float | None = None
        self._last_landmarker_t = -1e9

    def motion(self, gray_small: np.ndarray, roi: tuple[float, float, float, float] | None) -> float:
        mag = gradient_magnitude(gray_small)
        prev, self._prev = self._prev, mag
        if prev is None or prev.shape != mag.shape:
            return 0.0
        diff = np.abs(mag - prev)
        if roi is not None:
            h, w = diff.shape
            xs, xe = max(0, int(roi[0] * w)), min(w, int(np.ceil(roi[2] * w)))
            ys, ye = max(0, int(roi[1] * h)), min(h, int(np.ceil(roi[3] * h)))
            if xe > xs and ye > ys:
                diff = diff[ys:ye, xs:xe]
        return float(diff.mean() / 4.0)

    def should_run_landmarker(self, t: float, hand_present: bool, motion: float) -> bool:
        """With no hand and a still scene, run MediaPipe at idle_landmarker_hz only."""
        if hand_present:
            self._no_hand_since = None
        elif self._no_hand_since is None:
            self._no_hand_since = t
        idle = (self._no_hand_since is not None and t - self._no_hand_since > self.cfg.idle_after_s
                and motion < self.cfg.idle_motion)
        if idle and t - self._last_landmarker_t < 1.0 / self.cfg.idle_landmarker_hz:
            return False
        self._last_landmarker_t = t
        return True


# ================================================================ calibration

def inset_targets(screen_w: int, screen_h: int, inset: float) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left, inset from the edges."""
    x0, x1 = inset * screen_w, (1.0 - inset) * screen_w
    y0, y1 = inset * screen_h, (1.0 - inset) * screen_h
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)


@dataclass
class Calibration:
    """Homography from normalized mirrored camera coords to screen pixels."""

    homography: np.ndarray
    screen_size: tuple[int, int]
    capture_size: tuple[int, int]
    camera_index: int = 0
    created: float = field(default_factory=time.time)
    is_default: bool = False

    @classmethod
    def from_points(cls, cam_points, screen_size, inset, capture_size, camera_index=0) -> "Calibration":
        src = np.asarray(cam_points, dtype=np.float32).reshape(4, 2)
        h = cv2.getPerspectiveTransform(src, inset_targets(*screen_size, inset))
        return cls(h, tuple(screen_size), tuple(capture_size), camera_index)

    @classmethod
    def default(cls, region, screen_size, capture_size) -> "Calibration":
        """No calibration: map a central camera region linearly onto the screen."""
        x0, y0, x1, y1 = region
        cal = cls.from_points([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], screen_size, 0.0, capture_size)
        cal.is_default = True
        return cal

    def apply(self, x: float, y: float) -> tuple[int, int]:
        v = self.homography @ np.array([x, y, 1.0])
        z = v[2] if abs(v[2]) > 1e-9 else 1e-9
        w, h = self.screen_size
        return int(min(max(v[0] / z, 0.0), w - 1)), int(min(max(v[1] / z, 0.0), h - 1))

    def matches(self, screen_size, capture_size, camera_index) -> bool:
        return (tuple(self.screen_size) == tuple(screen_size)
                and tuple(self.capture_size) == tuple(capture_size)
                and self.camera_index == camera_index)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "homography": self.homography.tolist(), "screen_size": list(self.screen_size),
            "capture_size": list(self.capture_size), "camera_index": self.camera_index,
            "created": self.created}, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Calibration | None":
        p = Path(path)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text())
            return cls(np.array(d["homography"], dtype=np.float64), tuple(d["screen_size"]),
                       tuple(d["capture_size"]), int(d.get("camera_index", 0)), float(d.get("created", 0)))
        except (KeyError, ValueError, TypeError):
            return None


class CalibrationRoutine:
    """Captures a corner when the pointer is still for dwell_s (median of the dwell)."""

    def __init__(self, dwell_s: float, still_speed: float):
        self.dwell_s, self.still_speed = dwell_s, still_speed
        self.points: list[tuple[float, float]] = []
        self._samples: list[tuple[float, float]] = []
        self._still_since: float | None = None
        self._last_capture_t = -1e9
        self._last_t: float | None = None

    @property
    def target_index(self) -> int:
        return len(self.points)

    @property
    def done(self) -> bool:
        return len(self.points) >= 4

    @property
    def progress(self) -> float:
        if self._still_since is None or self._last_t is None:
            return 0.0
        return min(1.0, (self._last_t - self._still_since) / self.dwell_s)

    def update(self, frame: Frame) -> bool:
        if self.done:
            return False
        self._last_t = frame.t
        if not frame.hand_present or frame.pointer is None:
            self._still_since, self._samples = None, []
            return False
        if frame.t - self._last_capture_t < 0.5:
            return False
        if math.hypot(*frame.velocity) > self.still_speed:
            self._still_since, self._samples = None, []
            return False
        if self._still_since is None:
            self._still_since = frame.t
        self._samples.append(frame.pointer)
        if frame.t - self._still_since >= self.dwell_s:
            arr = np.array(self._samples)
            self.points.append((float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))))
            self._still_since, self._samples, self._last_capture_t = None, [], frame.t
            return True
        return False


def screen_size(override: tuple[int | None, int | None] = (None, None)) -> tuple[int, int]:
    """Main display size in logical points (what mouse APIs use)."""
    if override[0] and override[1]:
        return int(override[0]), int(override[1])
    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util

            class _P(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            class _R(ctypes.Structure):
                _fields_ = [("origin", _P), ("size", _P)]

            cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayBounds.restype = _R
            cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
            r = cg.CGDisplayBounds(cg.CGMainDisplayID())
            return int(r.size.x), int(r.size.y)
        except OSError:
            pass
    elif sys.platform == "win32":
        import ctypes

        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    return 1920, 1080


# ============================================================= frame sources

@dataclass(slots=True)
class RawFrame:
    image: np.ndarray            # BGR, unmirrored
    t_capture: float
    index: int


class FrameSource(Protocol):
    size: tuple[int, int]
    camera_index: int
    live: bool                   # t_capture is time.monotonic()
    def read(self, timeout: float = 1.0) -> RawFrame | None: ...
    def close(self) -> None: ...


class CameraSource:
    """Capture thread that keeps only the newest frame, so frames never queue
    behind slow processing (cv2's own buffer adds 1-3 frames of latency and
    CAP_PROP_BUFFERSIZE is ignored on macOS)."""

    live = True

    def __init__(self, cfg: CameraCfg):
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(cfg.index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Could not open camera {cfg.index}. On macOS, allow Camera access for your terminal "
                "app in System Settings > Privacy & Security > Camera, then restart the terminal.")
        if cfg.fourcc:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg.fourcc))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        self.cap.set(cv2.CAP_PROP_FPS, cfg.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, cfg.buffer_size)
        if cfg.exposure is not None:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, cfg.exposure)
        self.camera_index = cfg.index
        ok, img = self.cap.read()
        if not ok:
            raise RuntimeError("Camera opened but returned no frame (check permissions).")
        self.size = (img.shape[1], img.shape[0])
        self._cond = threading.Condition()
        self._latest: RawFrame | None = None
        self._consumed, self._count = -1, 0
        self._arrivals: deque[float] = deque(maxlen=60)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok, img = self.cap.read()
            t = time.monotonic()
            if not ok:
                time.sleep(0.005)
                continue
            with self._cond:
                self._latest = RawFrame(img, t, self._count)
                self._count += 1
                self._arrivals.append(t)
                self._cond.notify_all()

    @property
    def measured_fps(self) -> float:
        with self._cond:
            a = list(self._arrivals)
        return (len(a) - 1) / (a[-1] - a[0]) if len(a) > 1 else 0.0

    def read(self, timeout: float = 1.0) -> RawFrame | None:
        with self._cond:
            self._cond.wait_for(lambda: self._stop.is_set() or (
                self._latest is not None and self._latest.index > self._consumed), timeout)
            if self._stop.is_set() or self._latest is None or self._latest.index <= self._consumed:
                return None
            self._consumed = self._latest.index
            return self._latest

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=1.0)
        self.cap.release()


class VideoFileSource:
    """Replays a recorded video; timestamps come from the frame index."""

    live = False
    camera_index = -1

    def __init__(self, path: str, realtime: bool = True):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video {path}")
        self.measured_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.size = (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self.realtime, self._i, self._t0 = realtime, 0, time.monotonic()

    def read(self, timeout: float = 1.0) -> RawFrame | None:
        ok, img = self.cap.read()
        if not ok:
            return None
        t = self._i / self.measured_fps
        if self.realtime and (delay := self._t0 + t - time.monotonic()) > 0:
            time.sleep(delay)
        self._i += 1
        return RawFrame(img, t, self._i - 1)

    def close(self) -> None:
        self.cap.release()


# ================================================================== MediaPipe

class HandTracker(Protocol):
    def detect(self, bgr: np.ndarray, t: float) -> list[Observation]: ...
    def close(self) -> None: ...


def as_observations(result: Observation | list[Observation] | None) -> list[Observation]:
    """Normalize a tracker result (one hand, a list, or nothing) to a list."""
    if result is None:
        return []
    if isinstance(result, Observation):
        return [result]
    return list(result)


class MediaPipeTracker:
    """Hand Landmarker, Tasks API, VIDEO mode, up to `num_hands` hands. VIDEO mode
    is synchronous (landmarks for frame N come back for frame N) and tracks
    between frames instead of re-running palm detection. With num_hands=2, palm
    detection keeps running while fewer than two hands are tracked."""

    def __init__(self, cfg: TrackerCfg, num_hands: int = 2):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        model = resolve_path(cfg.model_path)
        if not model.exists():
            raise FileNotFoundError(
                f"Hand landmarker model not found at {model}. Download it with:\n  curl -L -o {model} "
                "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/"
                "float16/latest/hand_landmarker.task")
        delegate = BaseOptions.Delegate.GPU if cfg.delegate == "gpu" else BaseOptions.Delegate.CPU
        self._landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model), delegate=delegate),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=cfg.min_detection_confidence,
            min_hand_presence_confidence=cfg.min_presence_confidence,
            min_tracking_confidence=cfg.min_tracking_confidence,
        ))
        self._mp = mp
        self._gpu = cfg.delegate == "gpu"   # the Metal path only accepts 4-channel input
        self._swap = cfg.swap_handedness    # flip MediaPipe's left/right label if a camera reports it inverted
        self._last_ms = -1

    def detect(self, bgr: np.ndarray, t: float) -> list[Observation]:
        ts = max(int(t * 1000.0), self._last_ms + 1)  # VIDEO mode needs increasing timestamps
        self._last_ms = ts
        if self._gpu:
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGBA,
                                   data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA))
        else:
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                                   data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        result = self._landmarker.detect_for_video(image, ts)
        out = []
        for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
            lm = np.array([(p.x, p.y, p.z) for p in landmarks], dtype=np.float64)
            lm[:, 0] = 1.0 - lm[:, 0]
            cat = handedness[0]
            label = cat.category_name.lower()
            if self._swap:
                label = "left" if label == "right" else "right"
            out.append(Observation(lm, label, float(cat.score)))
        return out

    def close(self) -> None:
        self._landmarker.close()


# =================================================================== pipeline

class StageTimer:
    """Rolling p50/p95 per pipeline stage, in milliseconds."""

    def __init__(self, window: int = 300):
        self._data: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window))
        self._lock = threading.Lock()

    def record(self, stage: str, ms: float) -> None:
        with self._lock:
            self._data[stage].append(ms)

    def summary(self) -> dict[str, tuple[float, float]]:
        with self._lock:
            snap = {k: np.array(v) for k, v in self._data.items() if v}
        return {k: (float(np.percentile(v, 50)), float(np.percentile(v, 95))) for k, v in snap.items()}


class HandChannel:
    """Per-hand state for one slot ("left" or "right"): lock, filters, pinch,
    pattern watcher and the pinch cursor freeze."""

    def __init__(self, side: str, cfg: Config):
        self.side = side
        self.cfg = cfg
        sm = cfg.smoothing
        new_filter = lambda: OneEuro2D(sm.min_cutoff, sm.beta, sm.d_cutoff)  # noqa: E731
        self.lock = HandLock(cfg.hands.lock_cfg(side))
        self.pinch = PinchDetector(cfg.features.pinch_enter, cfg.features.pinch_exit)
        self.pattern = PatternWatcher(cfg.features.pattern_hold_frames)
        self.pointer_filter = new_filter()
        self.scroll_filter = new_filter()
        self.tip_filters = {name: new_filter() for name in FINGERS}
        self.present = False
        self.landmarks: np.ndarray | None = None
        self._last_screen: tuple[int, int] | None = None
        self._freeze_until = -1e9
        self._frozen_screen: tuple[int, int] | None = None

    def reset(self) -> None:
        self.pinch.reset()
        self.pattern.reset()
        self.pointer_filter.reset()
        self.scroll_filter.reset()
        for f in self.tip_filters.values():
            f.reset()
        self.landmarks, self._last_screen, self._freeze_until = None, None, -1e9

    def step(self, obs: Observation | None, t: float, aspect: float, calibration: Calibration,
             skipped: bool = False) -> tuple[Hand | None, str | None]:
        """Advance this hand by one frame. Returns (Hand or None, trigger or None)."""
        if skipped and not self.lock.locked:
            res = LockResult(None)
        else:
            scale = hand_scale(to_metric(obs.landmarks, aspect)) if obs is not None else 0.0
            res = self.lock.update(obs, scale)

        if res.obs is None:
            lost = self.present or res.event in ("lost", "swap")
            if lost:
                self.reset()
            self.present = False
            return None, ("hand_lost" if lost else None)

        fc, pc = self.cfg.features, self.cfg.pointer
        obs, lm = res.obs, res.obs.landmarks
        pts = to_metric(lm, aspect)
        scale = hand_scale(pts)
        flags = fingers_extended(pts, scale, fc.extend_angle_deg, fc.thumb_extend_ratio)
        pdist = pinch_distance(pts, scale)
        edge = self.pinch.update(pdist)

        fingers = {}
        for name, idx in FINGERTIPS.items():
            raw_tip = (float(lm[idx, 0]), float(lm[idx, 1]))
            tip = self.tip_filters[name](raw_tip[0], raw_tip[1], t)
            fingers[name] = Finger(name, flags[name], tip, raw_tip, calibration.apply(*tip), float(lm[idx, 2]))

        raw_ptr = pointer_anchor(lm, pc.anchor, pc.blend_tip_weight)
        ptr = self.pointer_filter(raw_ptr[0], raw_ptr[1], t)
        mid = 0.5 * (lm[INDEX_TIP, :2] + lm[MIDDLE_TIP, :2])
        self.scroll_filter(float(mid[0]), float(mid[1]), t)
        screen = calibration.apply(*ptr)
        if edge is not None:  # freeze where the cursor was before the pinch pulled the index tip
            self._frozen_screen = self._last_screen or screen
            self._freeze_until = t + pc.pinch_freeze_ms / 1000.0
        if t < self._freeze_until and self._frozen_screen is not None:
            screen = self._frozen_screen
        self._last_screen = screen

        pattern_changed = False if res.held else self.pattern.update(flags)
        trigger = ("hand_appear" if res.event == "acquired" or not self.present
                   else f"pinch_{edge}" if edge else "pattern" if pattern_changed else None)
        self.present, self.landmarks = True, lm

        return Hand(
            side=self.side,  # type: ignore[arg-type]
            fingers=fingers, landmarks=tuple(map(tuple, lm.tolist())),
            pinch_dist=pdist, pinch_active=self.pinch.active, pinch_edge=edge,  # type: ignore[arg-type]
            hand_scale=scale, tracking_score=obs.score,
            pointer_raw=raw_ptr, pointer=ptr, pointer_screen=screen,
            velocity=self.pointer_filter.velocity, scroll_velocity=self.scroll_filter.velocity,
            source="hold" if res.held else "landmarker", trigger=trigger,
            finger_angles=finger_angles(pts),
        ), trigger


class FingerPipeline:
    """Single-threaded per-frame processing: RawFrame (or recorded observations) -> Frame."""

    def __init__(self, cfg: Config, tracker: HandTracker | None, calibration: Calibration,
                 capture_size: tuple[int, int], live_clock: bool = False):
        self.cfg = cfg
        self.tracker = tracker
        self.calibration = calibration
        self.aspect = capture_size[0] / capture_size[1]
        self.live_clock = live_clock
        self.timer = StageTimer()
        self.on_observation: Callable[[float, int, list[Observation]], None] | None = None
        self.gate = MotionGate(cfg.gating)
        self.channels = {side: HandChannel(side, cfg) for side in SIDES}
        self._frame_id = 0

    def process(self, raw: RawFrame) -> Frame:
        t_start = time.perf_counter()
        g = self.cfg.gating
        small = cv2.cvtColor(cv2.resize(raw.image, (g.small_width, g.small_height),
                                        interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2GRAY)
        tracked = [c.landmarks for c in self.channels.values() if c.landmarks is not None]
        roi = hand_roi(np.vstack(tracked)) if tracked else None
        motion = self.gate.motion(small, roi)
        self.timer.record("gate", (time.perf_counter() - t_start) * 1000)

        any_locked = any(c.lock.locked for c in self.channels.values())
        if self.tracker is None or not self.gate.should_run_landmarker(raw.t_capture, any_locked, motion):
            return self._finish([], raw.t_capture, raw.t_capture, motion, t_start, skipped=True)

        t_mp = time.perf_counter()
        observations = as_observations(self.tracker.detect(raw.image, raw.t_capture))
        self.timer.record("landmarker", (time.perf_counter() - t_mp) * 1000)
        if self.on_observation is not None:
            self.on_observation(raw.t_capture, self._frame_id, observations)
        return self._finish(observations, raw.t_capture, raw.t_capture, motion, t_start)

    def process_observation(self, obs: Observation | list[Observation] | None, t: float,
                            motion: float = 0.0) -> Frame:
        """Replay path: skip camera and MediaPipe. Takes one hand, a list, or None."""
        return self._finish(as_observations(obs), t, t, motion, time.perf_counter())

    def _finish(self, observations: list[Observation], t: float, t_capture: float, motion: float,
                t_start: float, skipped: bool = False) -> Frame:
        t_feat = time.perf_counter()
        frame_id, self._frame_id = self._frame_id, self._frame_id + 1
        if self.cfg.hands.max_hands == 1:
            observations = observations[:1]
        assigned = assign_hands(observations, {s: c.lock for s, c in self.channels.items()})

        hands: dict[str, Hand] = {}
        triggers: dict[str, str] = {}
        for side, channel in self.channels.items():
            hand, trigger = channel.step(assigned[side], t, self.aspect, self.calibration, skipped)
            if hand is not None:
                hands[side] = hand
            if trigger is not None:
                triggers[side] = trigger

        primary = self.cfg.hands.primary
        if primary not in hands and self.cfg.hands.primary_fallback and hands:
            primary = next(iter(hands))
        frame = Frame(
            t=t, frame_id=frame_id, hands=hands, primary_side=primary,  # type: ignore[arg-type]
            triggers=triggers, motion=motion,
            should_classify=bool(triggers) or (bool(hands) and motion > self.cfg.gating.classify_motion),
            t_capture=t_capture,
        )
        now = time.perf_counter()
        self.timer.record("features", (now - t_feat) * 1000)
        self.timer.record("total", (now - t_start) * 1000)
        t_pub = time.monotonic()
        if self.live_clock:
            self.timer.record("capture_to_publish", (t_pub - t_capture) * 1000)
        object.__setattr__(frame, "t_publish", t_pub)
        return frame


# ================================================================ public API

class JsonlWriter:
    """Background JSONL writer so file I/O never touches the processing thread."""

    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "w")
        self._q: queue.Queue[dict | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="jsonl-writer", daemon=True)
        self._thread.start()

    def write(self, record: dict) -> None:
        self._q.put(record)

    def _loop(self) -> None:
        while (rec := self._q.get()) is not None:
            self._f.write(json.dumps(rec) + "\n")
        self._f.close()

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=2.0)


class FingerProcessor:
    """Runs finger processing on its own thread and hands Frames downstream.

        with FingerProcessor() as fp:
            frame = fp.wait_next()           # block for the next Frame
            fp.latest()                      # newest Frame, non-blocking
            fp.window()                      # last `window_size` Frames
            fp.subscribe(callback)           # called on every Frame (keep it fast)

    Consumers always get the newest Frame; if one is slower than the camera,
    intermediate Frames are skipped rather than queued (lowest latency).
    """

    def __init__(self, config: Config | str | Path | None = None, *, video: str | None = None,
                 record: str | Path | None = None, source: FrameSource | None = None,
                 tracker: HandTracker | None = None):
        self.cfg = config if isinstance(config, Config) else load_config(config)
        self.source = source or (VideoFileSource(video) if video else CameraSource(self.cfg.camera))
        self.screen = screen_size((self.cfg.screen.width, self.cfg.screen.height))
        self.calibration_path = resolve_path(self.cfg.calibration.path)
        cal = Calibration.load(self.calibration_path)
        if cal is None or not cal.matches(self.screen, self.source.size, self.source.camera_index):
            cal = Calibration.default(self.cfg.calibration.default_region, self.screen, self.source.size)
        self.pipeline = FingerPipeline(self.cfg, tracker or MediaPipeTracker(self.cfg.tracker, self.cfg.hands.max_hands), cal,
                                       self.source.size, live_clock=getattr(self.source, "live", False))
        self._writer = None
        if record:
            self._writer = JsonlWriter(record)
            self.pipeline.on_observation = lambda t, fid, obs: self._writer.write({
                "t": t, "frame_id": fid,
                "hands": [{"landmarks": o.landmarks.tolist(), "handedness": o.handedness, "score": o.score}
                          for o in obs]})

        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._latest_raw: RawFrame | None = None
        self._window: deque[Frame] = deque(maxlen=self.cfg.window_size)
        self._subs: list[Callable[[Frame], None]] = []
        self._stop = threading.Event()
        self.finished = threading.Event()   # source ended or processing failed repeatedly
        self.error: str | None = None
        self._thread = threading.Thread(target=self._loop, name="finger-processing", daemon=True)

    # lifecycle ---------------------------------------------------------------
    def start(self) -> "FingerProcessor":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.source.close()
        if self.pipeline.tracker is not None:
            self.pipeline.tracker.close()
        if self._writer:
            self._writer.close()

    def __enter__(self) -> "FingerProcessor":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # consumers ---------------------------------------------------------------
    def latest(self) -> Frame | None:
        with self._cond:
            return self._latest

    def latest_raw(self) -> RawFrame | None:
        with self._cond:
            return self._latest_raw

    def window(self) -> list[Frame]:
        with self._cond:
            return list(self._window)

    def subscribe(self, callback: Callable[[Frame], None]) -> None:
        self._subs.append(callback)

    def wait_next(self, after_id: int = -1, timeout: float = 1.0) -> Frame | None:
        """Newest Frame with frame_id > after_id, or None on timeout / end of source."""
        with self._cond:
            self._cond.wait_for(lambda: self.finished.is_set() or (
                self._latest is not None and self._latest.frame_id > after_id), timeout)
            f = self._latest
            return f if f is not None and f.frame_id > after_id else None

    @property
    def calibration(self) -> Calibration:
        return self.pipeline.calibration

    @calibration.setter
    def calibration(self, cal: Calibration) -> None:
        self.pipeline.calibration = cal

    # worker ------------------------------------------------------------------
    def _loop(self) -> None:
        misses = errors = 0
        try:
            while not self._stop.is_set():
                raw = self.source.read(timeout=0.5)
                if raw is None:
                    misses += 1
                    if not self.source.live or misses > 20:
                        return
                    continue
                misses = 0
                try:
                    frame = self.pipeline.process(raw)
                    errors = 0
                except Exception:  # never let the processing thread die silently
                    errors += 1
                    if errors == 1:
                        print(f"[finger_processing] frame {raw.index} failed:\n{traceback.format_exc()}")
                    if errors >= 10:
                        self.error = traceback.format_exc()
                        return
                    continue
                with self._cond:
                    self._latest, self._latest_raw = frame, raw
                    self._window.append(frame)
                    self._cond.notify_all()
                for cb in self._subs:
                    cb(frame)
        finally:
            with self._cond:
                self.finished.set()
                self._cond.notify_all()


def finger_stream(config: Config | str | Path | None = None, *, video: str | None = None,
                  record: str | Path | None = None, timeout: float = 1.0,
                  **processor_kwargs) -> Iterator[Frame]:
    """Continuous loop of Frames, one per processed camera frame.

        for frame in finger_stream():
            for side, hand in frame.hands.items():          # both hands
                for finger in hand.fingers.values():
                    print(side, finger.name, finger.state, finger.tip)
            frame.fingers                                     # primary (left) hand only

    Stops when the source ends; breaking out of the loop releases the camera.
    """
    with FingerProcessor(config, video=video, record=record, **processor_kwargs) as fp:
        last = -1
        while True:
            frame = fp.wait_next(last, timeout)
            if frame is None:
                if fp.finished.is_set():
                    if fp.error:
                        raise RuntimeError(f"finger processing failed:\n{fp.error}")
                    return
                continue
            last = frame.frame_id
            yield frame


def read_observations(path: str | Path) -> Iterator[tuple[float, list[Observation]]]:
    """Yield (t, hands) from a recording. Reads both the two-hand format
    ({"hands": [...]}) and the older one-hand format ({"landmarks": ...})."""
    def obs(d: dict) -> Observation:
        return Observation(np.array(d["landmarks"], dtype=np.float64), d["handedness"], d["score"])

    with open(path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                if "hands" in d:
                    yield d["t"], [obs(h) for h in d["hands"]]
                else:
                    yield d["t"], [obs(d)] if d.get("landmarks") is not None else []


def replay(path: str | Path, config: Config | None = None, capture_size=(640, 480),
           screen=(1440, 900)) -> list[Frame]:
    """Camera-free replay of a recording (made with record=...) through the pipeline."""
    cfg = config or load_config()
    cal = Calibration.default(cfg.calibration.default_region, screen, capture_size)
    pipeline = FingerPipeline(cfg, None, cal, capture_size)
    return [pipeline.process_observation(hands, t) for t, hands in read_observations(path)]
