"""Hands mode tests: synthetic landmarks only (no camera, no MediaPipe, no real mouse events)."""

from __future__ import annotations

import types

import numpy as np
import pytest

from finger_processing.processing import (
    Calibration,
    Config,
    FingerPipeline,
    HandLock,
    HandLockCfg,
    MediaPipeTracker,
    Observation,
    RawFrame,
    hand_scale,
    pinch_distance_pair,
    to_metric,
)
from finger_processing.test_processing import hand_metric, obs, to_normalized
from hands_mode import hud
from hands_mode.actions import ActionCfg, HandsEngine, PrintMouse

CAP, SCR, ASPECT = (640, 480), (1440, 900), 4 / 3
BLANK = np.zeros((480, 640, 3), np.uint8)


def pipeline(tracker=None, **overrides) -> FingerPipeline:
    cfg = Config()
    cfg.hand_lock.control_hand = "closest"
    cfg.features.pinch_fingers = "index_or_middle"
    for k, v in overrides.items():
        section, name = k.split("__")
        setattr(getattr(cfg, section), name, v)
    return FingerPipeline(cfg, tracker, Calibration.default(cfg.calibration.default_region, SCR, CAP), CAP)


# ------------------------------------------------------------------ (1) direction


class _FakeLandmarker:
    """Stands in for MediaPipe: returns the given RAW (unmirrored camera) landmarks."""

    def __init__(self):
        self.raw: np.ndarray | None = None

    def detect_for_video(self, image, ts):
        pts = [types.SimpleNamespace(x=float(x), y=float(y), z=float(z)) for x, y, z in self.raw]
        cat = types.SimpleNamespace(category_name="Left", score=0.95)
        return types.SimpleNamespace(hand_landmarks=[pts], handedness=[[cat]])


def _real_tracker_with_fake_model():
    """Kevin's MediaPipeTracker with its real detect/detect_all (incl. the mirror flip); only the model is faked."""
    t = object.__new__(MediaPipeTracker)
    t._landmarker = _FakeLandmarker()
    t._mp = types.SimpleNamespace(Image=lambda image_format, data: None,
                                  ImageFormat=types.SimpleNamespace(SRGB=1, SRGBA=2))
    t._gpu, t._swap, t._last_ms = False, True, -1
    return t


def _raw_camera_hand(wrist_raw_x: float, wrist_y: float) -> np.ndarray:
    """A pointing hand as the camera sensor sees it (NOT mirrored)."""
    mirrored = to_normalized(hand_metric({"index"}), ASPECT, (1.0 - wrist_raw_x, wrist_y))
    raw = mirrored.copy()
    raw[:, 0] = 1.0 - raw[:, 0]
    return raw


@pytest.mark.parametrize("mode", ["single", "closest"])
def test_direction_hand_right_moves_cursor_right_and_up_is_up(mode):
    """The user moves their hand to THEIR right: facing the camera, the raw sensor image shows it moving
    LEFT (raw x falls). After Kevin's flip the cursor must move RIGHT. Raising the hand (raw y falls)
    must move the cursor UP (screen y falls)."""
    tracker = _real_tracker_with_fake_model()
    p = pipeline(tracker, hand_lock__control_hand="closest" if mode == "closest" else "any")
    xs, ys = [], []
    steps = [(0.65 - 0.02 * i, 0.85) for i in range(12)] + [(0.41, 0.85 - 0.02 * i) for i in range(12)]
    for i, (rx, ry) in enumerate(steps):
        tracker._landmarker.raw = _raw_camera_hand(rx, ry)
        f = p.process(RawFrame(BLANK, i / 30, i))
        if f.hand_present:
            xs.append(f.pointer_screen[0])
            ys.append(f.pointer_screen[1])
    right, up = xs[:9], ys[9:]
    assert right[-1] > right[0] + 50 and all(b >= a for a, b in zip(right, right[1:]))
    assert up[-1] < up[0] - 50 and all(b <= a for a, b in zip(up, up[1:]))


def test_hud_preview_is_mirrored_and_dot_matches():
    img = np.zeros((480, 640, 3), np.uint8)
    img[:, 500:520] = 255                      # a bright stripe on the raw image's right
    tracker = _real_tracker_with_fake_model()
    p = pipeline(tracker, hand_lock__control_hand="any")
    tracker._landmarker.raw = _raw_camera_hand(0.8, 0.85)
    frames = [p.process(RawFrame(BLANK, i / 30, i)) for i in range(4)]
    out = hud.render(img, frames[-1], "point to move · pinch to click")
    assert out.shape[:2] == (180, 240)
    cols = np.where(out[60, :, 0] > 100)[0]
    assert cols.max() < 120                    # raw right -> preview left, like a mirror
    x, y = frames[-1].fingers["index"].tip     # mirrored landmarks: dot at x * width
    assert x < 0.5                             # raw x 0.8 -> mirrored left half
    px, py = int(round(x * 239)), int(round(y * 179))
    assert tuple(out[py, px]) == hud.dot_colour("point", False)


# ------------------------------------------------------------------ (2) closest hand


def _scale(o):
    return hand_scale(to_metric(o.landmarks, ASPECT))


def test_closest_picks_largest_then_sticks_even_if_other_grows():
    lock = HandLock(HandLockCfg(control_hand="closest", hold_frames=3))
    small_l = obs(wrist=(0.25, 0.8), scale=0.8, hand="left")
    big_r = obs(wrist=(0.75, 0.8), scale=1.0)
    chosen, _ = lock.choose([small_l, big_r], [_scale(small_l), _scale(big_r)])
    assert chosen is big_r
    for _ in range(3):
        lock.update(big_r, _scale(big_r))
    assert lock.locked
    grown_l = obs(wrist=(0.25, 0.8), scale=1.15, hand="left")   # the other hand is now bigger
    chosen, _ = lock.choose([grown_l, big_r], [_scale(grown_l), _scale(big_r)])
    assert chosen is big_r
    chosen, _ = lock.choose([grown_l], [_scale(grown_l)])         # locked hand missing: a dropout, not a swap
    assert chosen is None


def test_closest_pipeline_never_flips_then_reacquires_after_loss():
    p = pipeline(None, hand_lock__hold_frames=3)
    a, b = obs(wrist=(0.7, 0.8), scale=1.0), obs(wrist=(0.3, 0.8), scale=0.9, hand="left")
    frames = [p.process_observations([b, a], i / 30) for i in range(5)]
    assert frames[-1].hand_present and frames[-1].landmarks[0][0] == pytest.approx(0.7)
    b_big = obs(wrist=(0.3, 0.8), scale=1.2, hand="left")
    frames = [p.process_observations([b_big, a], (5 + i) / 30) for i in range(5)]
    assert all(f.landmarks[0][0] == pytest.approx(0.7) for f in frames)
    # a leaves: held for hold_frames, then lost; only then does b take over
    frames = [p.process_observations([b_big], (10 + i) / 30) for i in range(10)]
    assert [f.hand_present for f in frames[:3]] == [True] * 3
    assert any(f.trigger == "hand_lost" for f in frames)
    assert frames[-1].hand_present and frames[-1].landmarks[0][0] == pytest.approx(0.3)


def test_default_config_unchanged():
    c = Config()
    assert c.hand_lock.control_hand == "right" and c.tracker.num_hands == 1 and c.features.pinch_fingers == "index"


# ------------------------------------------------------------------ (3) pinch fingers


def _with_thumb_at(extended, tip_idx):
    m = hand_metric(set(extended))
    m[4, :2] = m[tip_idx, :2] + (0.01, 0.01)
    return m


def test_pinch_index_or_middle():
    m = to_metric(to_normalized(_with_thumb_at(("index", "middle"), 12), ASPECT), ASPECT)
    d, finger = pinch_distance_pair(m, hand_scale(m))
    assert finger == "middle" and d < 0.25
    m = to_metric(to_normalized(_with_thumb_at(("index", "middle"), 8), ASPECT), ASPECT)
    d, finger = pinch_distance_pair(m, hand_scale(m))
    assert finger == "index" and d < 0.25


def test_point_pose_with_thumb_on_curled_middle_is_not_a_pinch():
    m = hand_metric({"index"})
    m[4, :2] = m[12, :2] + (0.005, 0.005)      # thumb resting on the curled middle finger
    m = to_metric(to_normalized(m, ASPECT), ASPECT)
    d, finger = pinch_distance_pair(m, hand_scale(m))
    assert finger == "index" and d > 0.35


def test_middle_pinch_fires_edges_in_pipeline():
    p = pipeline(None)
    open_hand = Observation(to_normalized(hand_metric({"index", "middle"}), ASPECT), "right", 0.9)
    pinched = Observation(to_normalized(_with_thumb_at(("index", "middle"), 12), ASPECT), "right", 0.9)
    seq = [open_hand] * 4 + [pinched] * 3 + [open_hand] * 3
    frames = [p.process_observations([o], i / 30) for i, o in enumerate(seq)]
    edges = [(f.pinch_edge, f.pinch_finger) for f in frames if f.pinch_edge]
    assert edges == [("down", "middle"), ("up", "index")]


# ------------------------------------------------------------------ (4) actions


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def F(present=True, pose=("index",), pinch=False, edge=None, at=(500, 400), vy=0.0, v=(0.0, 0.0),
      landmarks=((0.5, 0.6, 0.0), (0.5, 0.4, 0.0)), tip=(0.5, 0.5)):
    fingers = {n: types.SimpleNamespace(extended=n in pose, tip=tip)
               for n in ("thumb", "index", "middle", "ring", "pinky")}
    return types.SimpleNamespace(hand_present=present, fingers=fingers if present else {},
                                 pointer_screen=at if present else None, pinch_active=pinch,
                                 pinch_edge=edge, scroll_velocity=(0.0, vy), velocity=v, landmarks=landmarks)


def pinch_down(e, c, **kw):
    """A pinch must last two frames (debounce) before the button goes down."""
    e.update(F(pinch=True, edge="down", **kw))
    c.t += 0.016
    e.update(F(pinch=True, **kw))


def engine(**kw):
    clock, mouse = Clock(), PrintMouse(echo=False)
    return HandsEngine(mouse, ActionCfg(**kw), clock), mouse, clock


def kinds(mouse):
    return [a[0] for a in mouse.log if a[0] != "move"]


def test_short_pinch_is_a_click_at_the_down_point():
    e, m, c = engine()
    e.update(F(at=(500, 400)))
    pinch_down(e, c, at=(500, 400))
    c.t += 0.1
    e.update(F(pinch=True, at=(506, 404)))            # index drifts while pinching: below drag threshold
    e.update(F(edge="up", at=(508, 405)))
    assert [a for a in m.log if a[0] != "move"] == [("down", 500, 400, "clicks=1"), ("up", 500, 400, "clicks=1")]


def test_pinch_hold_move_is_a_drag_with_dragged_events():
    e, m, c = engine()
    pinch_down(e, c, at=(100, 100))
    for i in range(1, 6):
        c.t += 0.05
        e.update(F(pinch=True, at=(100 + 30 * i, 100)))
    e.update(F(edge="up", at=(250, 100)))
    assert kinds(m)[0] == "down" and kinds(m)[-1] == "up" and kinds(m).count("drag") >= 4
    assert m.log[-1][:3] == ("up", 250, 100)


def test_two_quick_pinches_double_click():
    e, m, c = engine()
    for _ in range(2):
        pinch_down(e, c, at=(300, 300))
        c.t += 0.08
        e.update(F(edge="up", at=(302, 301)))
        c.t += 0.2
    downs = [a for a in m.log if a[0] == "down"]
    assert downs == [("down", 300, 300, "clicks=1"), ("down", 300, 300, "clicks=2")]


def test_click_rate_limit():
    e, m, c = engine(min_down_interval_s=0.2)
    pinch_down(e, c)
    c.t += 0.02
    e.update(F(edge="up"))
    c.t += 0.02
    e.update(F(pinch=True, edge="down"))             # too soon: ignored
    assert kinds(m).count("down") == 1


@pytest.mark.parametrize("loss", ["no_hand", "no_frames", "stop", "takeover", "held_too_long", "palm"])
def test_button_always_released(loss):
    e, m, c = engine()
    pinch_down(e, c)
    assert e.button_down
    if loss == "no_hand":
        e.update(F(present=False))
    elif loss == "no_frames":
        e.no_frames()
    elif loss == "stop":
        e.stop()
    elif loss == "takeover":
        m.mode = "fake"
        m._pos = (900, 900)                          # the user moved the real mouse
        e.update(F(pinch=True, at=(500, 400)))
        assert e.state.startswith("paused")
    elif loss == "held_too_long":
        c.t += 21
        e.update(F(pinch=True))
    elif loss == "palm":
        e.update(F(pose=("thumb", "index", "middle", "ring", "pinky")))
    assert not e.button_down and kinds(m)[-1] == "up"


def test_open_palm_held_leaves_hands_mode():
    e, m, c = engine()
    palm = F(pose=("thumb", "index", "middle", "ring", "pinky"))
    e.update(palm)
    c.t += 0.5
    e.update(palm)
    assert e.active
    c.t += 0.6
    e.update(palm)
    assert not e.active and "off" in e.state
    pinch_down(e, c)
    assert "down" not in kinds(m)


def test_two_finger_scroll_direction():
    e, m, c = engine()
    e.update(F(pose=("index", "middle"), vy=-1.0))   # fingers move up (image y falls)
    c.t += 0.1
    e.update(F(pose=("index", "middle"), vy=+1.0))   # fingers move down
    s = [a[1] for a in m.log if a[0] == "scroll"]
    assert len(s) == 2 and s[0] < 0 < s[1]           # up -> content up (wheel negative), like touch
    e2, m2, _ = engine(scroll_invert=True)
    e2.update(F(pose=("index", "middle"), vy=-1.0))
    assert [a[1] for a in m2.log if a[0] == "scroll"][0] > 0


def test_point_moves_clutch_does_not():
    e, m, c = engine()
    e.update(F(pose=("index",), at=(10, 20)))
    e.update(F(pose=(), at=(400, 400)))              # fist: clutch
    assert [a for a in m.log if a[0] == "move"] == [("move", 10, 20)]


def test_no_accessibility_never_holds_button():
    e, m, c = engine()
    m.can_click = False
    pinch_down(e, c)
    assert not e.button_down and kinds(m) == []


# ------------------------------------------------------------------ fast hand loss (owner safety)


def _drag_start(e, c):
    pinch_down(e, c, at=(100, 100))
    c.t += 0.05
    e.update(F(pinch=True, at=(160, 100)))
    assert e.button_down and e.state == "dragging"


def test_flicker_of_one_frame_does_not_release_drag_but_longer_loss_does():
    """Real pipeline + lock (hold_frames=2 as hands mode sets it) feeding the engine."""
    from hands_mode.run import hands_config

    cfg = hands_config()
    p = FingerPipeline(cfg, None, Calibration.default(cfg.calibration.default_region, SCR, CAP), CAP)
    e, m, c = engine()
    pinched = Observation(to_normalized(_with_thumb_at(("index", "middle"), 12), ASPECT), "right", 0.9)
    seq = [[pinched]] * 4 + [[]] + [[pinched]] * 2        # 1-frame flicker mid-pinch
    for i, cands in enumerate(seq):
        c.t = 100 + i / 30
        e.update(p.process_observations(cands, c.t))
    assert e.button_down, "a one-frame flicker must not release"
    for i in range(3):                                     # hand gone for 3 frames
        c.t += 1 / 30
        e.update(p.process_observations([], c.t))
    assert not e.button_down and kinds(m)[-1] == "up"


def test_hand_drops_out_mid_drag_releases():
    e, m, c = engine()
    _drag_start(e, c)
    e.update(F(present=False))
    assert not e.button_down and m.log[-1][0] == "up"


def test_hand_leaving_downward_mid_pinch_releases_and_blocks_pinch():
    e, m, c = engine()
    pinch_down(e, c)
    c.t += 0.03
    e.update(F(pinch=True, v=(0.0, 2.0), tip=(0.5, 0.9)))   # fast down, fingertip near the bottom
    assert not e.button_down and "leaving" in e.state
    c.t += 0.2
    pinch_down(e, c)                                       # lowering hand "pinches": ignored
    assert kinds(m).count("down") == 1
    c.t += 0.5
    e.update(F(edge="up"))
    pinch_down(e, c)                                       # after the lockout clicks work again
    assert kinds(m).count("down") == 2


def test_half_visible_hand_at_edge_never_clicks():
    e, m, c = engine()
    pinch_down(e, c, tip=(0.5, 0.99))
    assert kinds(m) == [] and not e.button_down
    _drag_start(e, c)
    e.update(F(pinch=True, at=(170, 100), tip=(0.5, 0.99)))
    assert not e.button_down


def test_low_hand_still_moves_cursor_to_the_bottom():
    e, m, c = engine()
    e.update(F(at=(500, 890), landmarks=((0.5, 0.99, 0.0),), tip=(0.5, 0.8)))
    e.update(F(at=(500, 899), landmarks=((0.5, 0.995, 0.0),), tip=(0.5, 0.985)))   # tips at the border too
    assert [a[2] for a in m.log if a[0] == "move"] == [890, 899]


def test_fast_downward_moves_mid_frame_are_not_a_drop():
    e, m, c = engine()
    e.update(F(at=(500, 300), v=(0.0, 3.0)))               # fast point down, mid-frame
    assert m.log[-1] == ("move", 500, 300)
    _drag_start(e, c)
    c.t += 0.03
    e.update(F(pinch=True, at=(160, 300), v=(0.0, 3.0)))   # fast drag down, mid-frame
    assert e.button_down and m.log[-1][0] == "drag"
    e.update(F(pinch=True, at=(160, 340), v=(0.0, 3.0), landmarks=((0.5, 0.85, 0.0),), tip=(0.5, 0.6)))
    assert e.button_down                                   # low wrist, mid-frame tip: still dragging


def test_index_pinch_lands_where_the_cursor_was():
    """A real index pinch curls the index: the pose leaves 'point' and the pointer drifts before the pinch
    fires. The click must land where the cursor was (where you aimed), and a drag follows the hand's delta."""
    e, m, c = engine()
    e.update(F(at=(500, 400)))
    for i, at in enumerate([(506, 404), (514, 410), (522, 416)]):   # index curling: clutch, pointer drifts
        c.t += 0.033
        e.update(F(pose=(), at=at))
    pinch_down(e, c, pose=(), at=(528, 420))
    assert m.log[-1] == ("down", 500, 400, "clicks=1")
    c.t += 0.033
    e.update(F(pose=(), pinch=True, at=(578, 420)))        # hand moves 50 px right while pinched
    assert m.log[-1] == ("drag", 550, 400)


def test_one_frame_pinch_jitter_does_not_click():
    e, m, c = engine()
    e.update(F(pinch=True, edge="down"))
    c.t += 0.016
    e.update(F(edge="up"))
    assert kinds(m) == []


def test_still_hand_dead_zone():
    e, m, c = engine()
    for x in (500, 501, 500, 501, 520):
        e.update(F(at=(x, 400)))
    assert [a[1] for a in m.log if a[0] == "move"] == [500, 520]
