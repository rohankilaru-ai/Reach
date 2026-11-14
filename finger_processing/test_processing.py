"""Tests for finger processing (synthetic data; no camera, no MediaPipe)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from finger_processing.processing import (
    FINGERS,
    Calibration,
    CalibrationRoutine,
    Config,
    FingerPipeline,
    FingerProcessor,
    Frame,
    Hand,
    GatingCfg,
    HandLock,
    HandLockCfg,
    MotionGate,
    Observation,
    OneEuro2D,
    OneEuroFilter,
    PatternWatcher,
    PinchDetector,
    RawFrame,
    assign_hands,
    finger_stream,
    fingers_extended,
    hand_roi,
    hand_scale,
    inset_targets,
    pinch_distance,
    pointer_anchor,
    replay,
    to_metric,
)

CAP, SCR = (640, 480), (1440, 932)
BLANK = np.zeros((480, 640, 3), np.uint8)

# ------------------------------------------------------------ synthetic hands

MCP = {"index": (-0.06, -0.20), "middle": (0.0, -0.20), "ring": (0.05, -0.19), "pinky": (0.09, -0.17)}
BASE = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}


def hand_metric(extended, thumb_out=False, pinch=False, angle_deg=0.0, scale=1.0) -> np.ndarray:
    """21x3 landmarks in square units, wrist at origin, fingers pointing up."""
    p = np.zeros((21, 3))
    for name, (mx, my) in MCP.items():
        mcp = np.array([mx, my])
        pip = mcp + (0, -0.07)
        dip, tip = ((pip + (0, -0.05), pip + (0, -0.09)) if name in extended
                    else (pip + (0.01, 0.04), pip + (0.0, 0.06)))
        for k, q in enumerate((mcp, pip, dip, tip)):
            p[BASE[name] + k, :2] = q
    p[1, :2], p[2, :2], p[3, :2] = (-0.05, -0.05), (-0.09, -0.09), (-0.12, -0.12)
    p[4, :2] = (-0.18, -0.13) if thumb_out else (-0.04, -0.14)
    if pinch:
        p[4, :2] = p[8, :2] + (0.01, 0.01)
    a = np.radians(angle_deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    p[:, :2] = p[:, :2] @ rot.T * scale
    return p


def to_normalized(metric, aspect, wrist=(0.5, 0.8)) -> np.ndarray:
    n = metric.copy()
    n[:, 0] = wrist[0] + metric[:, 0] / aspect
    n[:, 1] = wrist[1] + metric[:, 1]
    n[:, 2] = metric[:, 2] / aspect
    return n


def obs(extended=("index",), aspect=4 / 3, wrist=(0.5, 0.8), hand="left", **kw) -> Observation:
    return Observation(to_normalized(hand_metric(set(extended), **kw), aspect, wrist), hand, 0.95)


def textured(w=640, h=480, seed=0):
    img = np.random.default_rng(seed).integers(0, 255, (h // 8, w // 8), dtype=np.uint8)
    return cv2.GaussianBlur(cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC), (5, 5), 1.5)


def shift(img, dx, dy):
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)


# ------------------------------------------------------------------ smoothing

def test_filter_first_sample_and_constant():
    f = OneEuroFilter(1.0, 0.5)
    assert f(0.3, 0.0) == 0.3
    for i in range(1, 100):
        y = f(0.3, i / 30)
    assert abs(y - 0.3) < 1e-9 and abs(f.derivative) < 1e-9


def test_filter_reduces_jitter():
    noisy = 0.5 + np.random.default_rng(0).normal(0, 0.01, 300)
    f = OneEuroFilter(1.0, 0.0)
    out = np.array([f(x, i / 30) for i, x in enumerate(noisy)])
    assert out[50:].std() < noisy[50:].std() / 3


def test_filter_velocity_unbiased_and_beta_reduces_lag():
    slope, lagged, fast = 0.8, OneEuroFilter(1.0, 0.0), OneEuroFilter(1.0, 5.0)
    for i in range(120):
        t = i / 30
        a, b = lagged(slope * t, t), fast(slope * t, t)
    assert abs(fast.derivative - slope) < 0.05
    assert abs(slope * t - b) < abs(slope * t - a)


def test_filter_real_dt_and_duplicate_timestamps():
    f = OneEuroFilter(1.0, 0.0)
    f(0.0, 0.0)
    v1 = f(1.0, 0.1)
    assert f(5.0, 0.1) == v1
    g = OneEuroFilter(1.0, 0.0)
    g(0.0, 0.0)
    assert g(1.0, 0.5) > v1


def test_filter_2d_velocity():
    f = OneEuro2D(1.0, 1.0)
    for i in range(60):
        f(0.1 * i / 30, -0.2 * i / 30, i / 30)
    assert f.velocity == pytest.approx((0.1, -0.2), abs=0.02)


# ------------------------------------------------------------------- features

def flags_for(metric, aspect=4 / 3):
    pts = to_metric(to_normalized(metric, aspect), aspect)
    return fingers_extended(pts, hand_scale(pts), 150.0, 0.55)


@pytest.mark.parametrize("ext,thumb", [(set(), False), ({"index"}, False), ({"index", "middle"}, False),
                                       ({"index", "middle", "ring", "pinky"}, True)])
def test_poses(ext, thumb):
    expect = {f: f in ext for f in FINGERS}
    expect["thumb"] = thumb
    assert flags_for(hand_metric(ext, thumb_out=thumb)) == expect


@pytest.mark.parametrize("angle", [-60, -30, 30, 45, 90])
def test_rotation_invariant(angle):
    assert flags_for(hand_metric({"index", "middle"}, angle_deg=angle)) == flags_for(hand_metric({"index", "middle"}))


@pytest.mark.parametrize("aspect", [4 / 3, 16 / 9, 1.0])
def test_aspect_corrected(aspect):
    m = hand_metric({"index"}, angle_deg=40)
    assert hand_scale(to_metric(to_normalized(m, aspect), aspect)) == pytest.approx(hand_scale(m))
    assert flags_for(m, aspect)["index"] is True


def test_aspect_matters():
    n = to_normalized(hand_metric({"index"}, angle_deg=90), 16 / 9)
    good, bad = to_metric(n, 16 / 9), to_metric(n, 1.0)
    assert abs(pinch_distance(good, hand_scale(good)) - pinch_distance(bad, hand_scale(bad))) > 0.05


def test_pinch_distance_scale_invariant_and_small_when_pinching():
    ref = hand_metric({"index"})
    for s in (0.5, 2.0):
        m = hand_metric({"index"}, scale=s)
        assert pinch_distance(m, hand_scale(m)) == pytest.approx(pinch_distance(ref, hand_scale(ref)))
    m = hand_metric({"index"}, pinch=True)
    assert pinch_distance(m, hand_scale(m)) < 0.15


def test_pinch_hysteresis():
    d = PinchDetector(0.25, 0.35)
    seq = [0.5, 0.3, 0.24, 0.2, 0.3, 0.34, 0.36, 0.3, 0.2]
    assert [d.update(x) for x in seq] == [None, None, "down", None, None, None, "up", None, "down"]
    with pytest.raises(ValueError):
        PinchDetector(0.4, 0.3)


def test_pattern_watcher():
    w = PatternWatcher(2)
    point = {"thumb": False, "index": True, "middle": False, "ring": False, "pinky": False}
    scroll = dict(point, middle=True)
    assert [w.update(point), w.update(point)] == [False, False]
    assert [w.update(scroll), w.update(point)] == [False, False]
    assert [w.update(scroll), w.update(scroll), w.update(scroll)] == [False, True, False]


def test_pointer_anchor_modes():
    lm = np.zeros((21, 3))
    lm[8, :2], lm[5, :2] = (0.6, 0.2), (0.4, 0.4)
    assert pointer_anchor(lm, "index_tip", 0.5) == (0.6, 0.2)
    assert pointer_anchor(lm, "index_mcp", 0.5) == (0.4, 0.4)
    assert pointer_anchor(lm, "blend", 0.5) == pytest.approx((0.5, 0.3))


# ------------------------------------------------------------------ hand lock

def lock(**kw):
    kw.setdefault("control_hand", "left")
    return HandLock(HandLockCfg(**kw))


def test_lock_acquire_and_wrong_hand():
    h = lock(handedness_vote_min=3)
    assert [h.update(obs(), 0.2).obs is None for _ in range(3)] == [True, True, False]
    h2 = lock()
    assert all(h2.update(obs(hand="right"), 0.2).obs is None for _ in range(20))


def test_lock_noisy_label_and_flip_after_lock():
    h = lock(handedness_vote_min=3)
    results = [h.update(obs(hand=s), 0.2) for s in ["left", "right", "left", "right", "left"]]
    assert results[-1].event == "acquired"
    assert h.update(obs(hand="right"), 0.2).obs is not None


def test_lock_hold_then_lose_and_recover():
    h = lock(handedness_vote_min=1, hold_frames=3)
    h.update(obs(), 0.2)
    assert all(r.held for r in [h.update(None) for _ in range(3)])
    assert h.update(None).event == "lost" and not h.locked
    h.update(obs(), 0.2)
    h.update(None)
    assert not h.update(obs(), 0.2).held


def test_lock_swap_detection():
    h = lock(handedness_vote_min=1)
    h.update(obs(wrist=(0.3, 0.8)), 0.2)
    assert h.update(obs(wrist=(0.75, 0.8)), 0.2).event == "swap"
    h = lock(handedness_vote_min=1)
    h.update(obs(), 0.2)
    assert h.update(obs(), 0.21).obs is not None
    assert h.update(obs(), 0.35).event == "swap"


# --------------------------------------------------------------------- gating

def small(img):
    return cv2.resize(img, (160, 120), interpolation=cv2.INTER_AREA)


def test_gate_static_vs_moving():
    g = MotionGate(GatingCfg())
    s = small(textured())
    g.motion(s, None)
    assert g.motion(s, None) < 1e-6
    assert g.motion(small(shift(textured(), 12, 0)), None) > GatingCfg().classify_motion


@pytest.mark.parametrize("op", [lambda i: cv2.add(i, 30), lambda i: cv2.convertScaleAbs(i, alpha=0.7)])
def test_gate_ignores_brightness_and_gain(op):
    g = MotionGate(GatingCfg())
    g.motion(small(textured()), None)
    assert g.motion(small(op(textured())), None) < GatingCfg().idle_motion


def test_gate_idle_rate():
    g = MotionGate(GatingCfg(idle_after_s=1.0, idle_landmarker_hz=5, idle_motion=0.02))
    runs = [g.should_run_landmarker(i / 30, False, 0.0) for i in range(90)]
    assert all(runs[:30]) and 3 <= sum(runs[31:]) <= 12
    assert g.should_run_landmarker(3.01, False, 0.5)
    assert g.should_run_landmarker(3.02, True, 0.0)


def test_hand_roi_unmirrors():
    lm = np.zeros((21, 3))
    lm[:, 0], lm[:, 1] = 0.2, 0.5
    x0, _, x1, _ = hand_roi(lm, pad=0.05)
    assert (x0, x1) == pytest.approx((0.75, 0.85))


# ---------------------------------------------------------------- calibration

CAM_PTS = np.array([[0.25, 0.2], [0.78, 0.22], [0.75, 0.8], [0.22, 0.78]])


def test_calibration_corners_extrapolate_clamp():
    cal = Calibration.from_points(CAM_PTS, SCR, 0.1, CAP)
    for (x, y), (tx, ty) in zip(CAM_PTS, inset_targets(*SCR, 0.1)):
        assert cal.apply(x, y) == pytest.approx((tx, ty), abs=1)
    assert cal.apply(-1, -1) == (0, 0) and cal.apply(2, 2) == (SCR[0] - 1, SCR[1] - 1)
    sx, sy = cal.apply(0.22, 0.17)
    assert sx < 0.1 * SCR[0] and sy < 0.1 * SCR[1]


def test_calibration_default_and_persistence(tmp_path):
    assert Calibration.default((0.2, 0.2, 0.8, 0.8), SCR, CAP).apply(0.5, 0.5) == pytest.approx(
        (SCR[0] // 2, SCR[1] // 2), abs=1)
    cal = Calibration.from_points(CAM_PTS, SCR, 0.1, CAP)
    cal.save(tmp_path / "c.json")
    loaded = Calibration.load(tmp_path / "c.json")
    assert np.allclose(loaded.homography, cal.homography) and loaded.matches(SCR, CAP, 0)
    assert not loaded.matches((1920, 1080), CAP, 0)
    (tmp_path / "bad.json").write_text("{}")
    assert Calibration.load(tmp_path / "bad.json") is None and Calibration.load(tmp_path / "x") is None


def frame_with_pointer(t, pointer, velocity=(0.0, 0.0)) -> Frame:
    hand = Hand(side="left", fingers={}, landmarks=(), pointer=pointer, velocity=velocity)
    return Frame(t=t, frame_id=0, hands={"left": hand})


def test_calibration_routine():
    r = CalibrationRoutine(dwell_s=0.5, still_speed=0.05)
    t = 0.0
    for corner in CAM_PTS:
        for _ in range(5):
            r.update(frame_with_pointer(t, tuple(corner), velocity=(1.0, 0.0)))
            t += 1 / 30
        for _ in range(60):
            t += 1 / 30
            if r.update(frame_with_pointer(t, tuple(corner))):
                break
        t += 0.6
    assert r.done and np.allclose(r.points, CAM_PTS)


# ------------------------------------------------------------------- pipeline

class FakeTracker:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def detect(self, bgr, t):
        self.calls += 1
        return self.script.pop(0) if self.script else None

    def close(self):
        pass


def make(script, **overrides) -> FingerPipeline:
    cfg = Config()
    for k, v in overrides.items():
        section, name = k.split("__")
        setattr(getattr(cfg, section), name, v)
    cal = Calibration.default(cfg.calibration.default_region, SCR, CAP)
    return FingerPipeline(cfg, FakeTracker(script), cal, CAP)


def run(p, n):
    return [p.process(RawFrame(BLANK, i / 30, i)) for i in range(n)]


def test_every_finger_has_state_and_position():
    frames = run(make([obs(extended=("index", "middle"))] * 6), 6)
    assert [f.hand_present for f in frames] == [False, False, True, True, True, True]
    f = frames[-1]
    assert list(f.fingers) == list(FINGERS)
    assert {n: g.state for n, g in f.fingers.items()} == {
        "thumb": "curled", "index": "extended", "middle": "extended", "ring": "curled", "pinky": "curled"}
    assert f.fingers["ring"].curled and not f.fingers["index"].curled
    for idx, name in zip((4, 8, 12, 16, 20), FINGERS):
        g = f.fingers[name]
        assert g.tip == pytest.approx(f.landmarks[idx][:2])  # still hand -> smoothed == raw
        assert 0 <= g.tip_screen[0] < SCR[0] and 0 <= g.tip_screen[1] < SCR[1]
    assert frames[2].trigger == "hand_appear" and frames[3].trigger is None
    assert frames[0].fingers == {}


def test_fingertip_positions_follow_the_hand():
    script = [obs(wrist=(0.3 + 0.01 * i, 0.8)) for i in range(15)]
    frames = run(make(script), 15)
    first = next(f for f in frames if f.hand_present)
    for name in FINGERS:
        assert frames[-1].fingers[name].tip[0] > first.fingers[name].tip[0]
        assert frames[-1].fingers[name].tip_screen[0] > first.fingers[name].tip_screen[0]
    assert frames[-1].velocity[0] > 0.1


def test_pinch_edge_and_cursor_freeze():
    script = [obs()] * 4 + [obs(pinch=True, wrist=(0.52, 0.8))] * 3 + [obs(wrist=(0.55, 0.8))] * 10
    frames = run(make(script, pointer__pinch_freeze_ms=100), len(script))
    i = next(k for k, f in enumerate(frames) if f.pinch_edge == "down")
    assert frames[i].trigger == "pinch_down" and frames[i].pinch_active
    assert frames[i].pointer_screen == frames[i - 1].pointer_screen == frames[i + 1].pointer_screen
    assert next(f for f in frames if f.pinch_edge == "up").trigger == "pinch_up"
    assert frames[-1].pointer_screen != frames[i - 1].pointer_screen


def test_dropout_hold_then_loss():
    frames = run(make([obs()] * 4 + [None] * 5), 9)
    assert [f.source for f in frames[4:]] == ["hold", "hold", "hold", "none", "none"]
    assert frames[7].trigger == "hand_lost" and frames[7].fingers == {}
    assert frames[8].trigger is None


def test_right_hand_alone_is_streamed_but_not_primary():
    frames = run(make([obs(hand="right")] * 10), 10)
    assert not any(f.hand_present for f in frames)  # primary (left) is absent
    assert frames[-1].right is not None and frames[-1].left is None
    assert frames[-1].hands["right"].fingers["index"].extended


# ------------------------------------------------------------ public API loop

class FakeSource:
    live, camera_index, size = False, -1, CAP

    def __init__(self, n):
        self.n, self.i = n, 0

    def read(self, timeout=1.0):
        if self.i >= self.n:
            return None
        self.i += 1
        return RawFrame(BLANK, self.i / 30, self.i - 1)

    def close(self):
        pass


def test_finger_stream_loop(tmp_path):
    cfg = Config()
    cfg.calibration.path = str(tmp_path / "none.json")
    script = [obs(extended=("index",))] * 40
    frames = list(finger_stream(cfg, source=FakeSource(40), tracker=FakeTracker(script)))
    assert frames, "stream produced nothing"
    ids = [f.frame_id for f in frames]
    assert ids == sorted(set(ids))  # increasing, no repeats (may skip when the consumer lags)
    last = frames[-1]
    assert last.hand_present and last.fingers["index"].extended and last.fingers["middle"].curled


def test_processor_record_and_replay(tmp_path):
    cfg = Config()
    cfg.calibration.path = str(tmp_path / "none.json")
    script = [obs(wrist=(0.4 + 0.005 * i, 0.8)) for i in range(10)] + [None] * 5
    seen: list[Frame] = []
    fp = FingerProcessor(cfg, source=FakeSource(15), tracker=FakeTracker(script),
                         record=tmp_path / "obs.jsonl")
    fp.subscribe(seen.append)
    with fp:
        fp.finished.wait(5)
    assert len(seen) == 15 and fp.error is None
    replayed = replay(tmp_path / "obs.jsonl", cfg, CAP, fp.screen)
    assert [f.hand_present for f in replayed] == [f.hand_present for f in seen]
    assert [f.fingers_extended for f in replayed] == [f.fingers_extended for f in seen]
    assert [f.pointer_screen for f in replayed] == [f.pointer_screen for f in seen]


def test_processor_survives_and_reports_errors(tmp_path):
    class Boom(FakeTracker):
        def detect(self, bgr, t):
            raise RuntimeError("boom")

    cfg = Config()
    cfg.calibration.path = str(tmp_path / "none.json")
    with FingerProcessor(cfg, source=FakeSource(30), tracker=Boom([])) as fp:
        fp.finished.wait(5)
    assert fp.error is not None and "boom" in fp.error


# ------------------------------------------------------------------ two hands

L_WRIST, R_WRIST = (0.3, 0.8), (0.7, 0.8)


def both(left_kw=None, right_kw=None, left_label="left", right_label="right"):
    """One frame with both hands; the user's left hand sits on the left of the mirrored view."""
    left_kw, right_kw = left_kw or {}, right_kw or {}
    return [obs(wrist=L_WRIST, hand=left_label, **left_kw), obs(wrist=R_WRIST, hand=right_label, **right_kw)]


def test_both_hands_streamed_and_left_is_primary():
    script = [both(left_kw={"extended": ("index",)}, right_kw={"extended": ("index", "middle")})] * 6
    f = run(make(script), 6)[-1]
    assert set(f.hands) == {"left", "right"}
    assert f.hands["left"].fingers_extended == {"thumb": False, "index": True, "middle": False,
                                               "ring": False, "pinky": False}
    assert f.hands["right"].fingers["middle"].extended
    # Flat fields come from the primary (left) hand.
    assert f.primary_side == "left" and f.hand_present and f.handedness == "left"
    assert f.fingers is f.hands["left"].fingers
    assert f.pointer_screen == f.hands["left"].pointer_screen
    # Each hand's fingertips are where that hand is.
    assert f.left.fingers["index"].tip[0] < 0.5 < f.right.fingers["index"].tip[0]


def test_same_label_on_both_hands_resolved_by_position():
    script = [both(left_label="right", right_label="right")] * 6
    f = run(make(script), 6)[-1]
    assert f.left.landmarks[0][0] == pytest.approx(L_WRIST[0])
    assert f.right.landmarks[0][0] == pytest.approx(R_WRIST[0])


def test_label_flip_does_not_swap_tracked_hands():
    script = [both()] * 5 + [both(left_label="right", right_label="left")] * 3
    frames = run(make(script), len(script))
    for f in frames[5:]:
        assert f.left.landmarks[0][0] == pytest.approx(L_WRIST[0])
        assert f.right.landmarks[0][0] == pytest.approx(R_WRIST[0])
        assert "hand_lost" not in f.triggers.values()


def test_single_hand_mislabel_keeps_its_slot():
    script = [[obs(hand="left")]] * 5 + [[obs(hand="right")]] * 2 + [[obs(hand="left")]] * 2
    frames = run(make(script), len(script))
    assert all(f.left is not None and f.right is None for f in frames[2:])


def test_per_hand_triggers_and_primary_trigger():
    script = [both()] * 4 + [both(right_kw={"pinch": True})] * 2
    frames = run(make(script), len(script))
    assert frames[2].triggers == {"left": "hand_appear", "right": "hand_appear"}
    pinch = frames[4]
    assert pinch.triggers == {"right": "pinch_down"} and pinch.right.pinch_active
    assert pinch.trigger is None and not pinch.pinch_active  # primary (left) did nothing
    assert pinch.should_classify


def test_left_lost_while_right_stays():
    script = [both()] * 4 + [[obs(wrist=R_WRIST, hand="right")]] * 6
    frames = run(make(script), len(script))
    lost = next(f for f in frames if f.triggers.get("left") == "hand_lost")
    assert lost.trigger == "hand_lost" and not lost.hand_present
    assert lost.right is not None and frames[-1].right is not None


def test_primary_fallback():
    script = [[obs(hand="right")]] * 6
    assert not run(make(script), 6)[-1].hand_present
    f = run(make(script, hands__primary_fallback=True), 6)[-1]
    assert f.hand_present and f.primary_side == "right" and f.fingers["index"].extended


def test_primary_can_be_right():
    f = run(make([both()] * 6, hands__primary="right"), 6)[-1]
    assert f.primary_side == "right" and f.pointer_screen == f.right.pointer_screen


def test_max_hands_one():
    frames = run(make([both()] * 6, hands__max_hands=1), 6)
    assert all(len(f.hands) <= 1 for f in frames)


def test_assign_hands_unit():
    locks = {"left": lock(control_hand="left"), "right": lock(control_hand="right")}
    a = assign_hands([obs(wrist=R_WRIST, hand="right"), obs(wrist=L_WRIST, hand="left")], locks)
    assert a["left"].landmarks[0, 0] == pytest.approx(L_WRIST[0])
    assert a["right"].landmarks[0, 0] == pytest.approx(R_WRIST[0])
    assert assign_hands([], locks) == {"left": None, "right": None}


def test_two_hand_record_and_replay(tmp_path):
    cfg = Config()
    cfg.calibration.path = str(tmp_path / "none.json")
    script = [[obs(wrist=(0.3 + 0.005 * i, 0.8)), obs(wrist=R_WRIST, hand="right")] for i in range(10)] + [[]] * 5
    seen: list[Frame] = []
    fp = FingerProcessor(cfg, source=FakeSource(15), tracker=FakeTracker(script), record=tmp_path / "two.jsonl")
    fp.subscribe(seen.append)
    with fp:
        fp.finished.wait(5)
    replayed = replay(tmp_path / "two.jsonl", cfg, CAP, fp.screen)
    assert [sorted(f.hands) for f in replayed] == [sorted(f.hands) for f in seen]
    assert [f.right.pointer_screen if f.right else None for f in replayed] == \
           [f.right.pointer_screen if f.right else None for f in seen]
    assert any(set(f.hands) == {"left", "right"} for f in seen)
