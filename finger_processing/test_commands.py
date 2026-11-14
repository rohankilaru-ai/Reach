"""Tests for hand-gesture commands (no camera, no network: Drex is a fake `post`)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from finger_processing.commands import (
    ACTIONS,
    GESTURES,
    LABELS,
    CommandEngine,
    DrexGestureClient,
    DrexGestureStreamer,
    HeuristicGestures,
    decide,
    make_engine,
    window_state,
)
from finger_processing.processing import FINGERS, CommandsCfg, Finger, Frame, Hand

DT = 1 / 30
POINT, FIST, PALM = "01000", "00000", "11111"


def hframe(i: int, ext: str = POINT, pos=(500, 400), trigger=None, should=False, side="left",
           index_angle: float | None = None) -> Frame:
    angles = {f: (175.0 if e == "1" else 80.0) for f, e in zip(FINGERS, ext)}
    if index_angle is not None:
        angles["index"] = index_angle
    fingers = {f: Finger(f, e == "1", (0.5, 0.5), (0.5, 0.5), pos if f == "index" else (0, 0), 0.0)
               for f, e in zip(FINGERS, ext)}
    hand = Hand(side=side, fingers=fingers, landmarks=(), finger_angles=angles, velocity=(0.1, -0.2))
    return Frame(t=i * DT, frame_id=i, hands={side: hand}, triggers={side: trigger} if trigger else {},
                 should_classify=should)


def no_hand(i: int) -> Frame:
    return Frame(t=i * DT, frame_id=i)


def cfg(**kw) -> CommandsCfg:
    c = CommandsCfg(log_decisions=False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ------------------------------------------------------------------ labels and actions

def test_labels_and_actions():
    assert LABELS == ("idle", "point", "click")
    assert set(ACTIONS) == set(GESTURES) == set(LABELS)
    assert ACTIONS["idle"][0] == "none"
    assert ACTIONS["click"][0] == "click"


def test_decide_thresholds():
    assert decide({"idle": 0.1, "point": 0.1, "click": 0.8}, 0.7, 0.2)[2] is True
    assert decide({"idle": 0.35, "point": 0.0, "click": 0.65}, 0.7, 0.2)[3].startswith("top p")
    assert decide({"idle": 0.0, "point": 0.3, "click": 0.7}, 0.7, 0.5)[3].startswith("margin")
    assert decide({}, 0.7, 0.2)[2] is False


# ------------------------------------------------------------------ heuristic click = index curl

def run_heuristic(frames, **kw):
    h = HeuristicGestures(cfg(**kw))
    return [h.update(f) for f in frames]


def test_point_then_index_curl_clicks_once():
    frames = [hframe(i, POINT) for i in range(10)] + [hframe(10 + i, FIST) for i in range(10)]
    out = run_heuristic(frames)
    assert [m for m, _ in out[:10]] == ["point"] * 10
    assert sum(c for _, c in out) == 1 and out[10] == ("idle", True)


def test_idle_pointing_flicker_is_not_a_click():
    """A relaxed index hovers near the extended threshold: brief 'not extended' frames at ~140 deg must not click."""
    seq = [hframe(i, POINT) for i in range(10)]
    seq += [hframe(10, FIST, index_angle=145), hframe(11, FIST, index_angle=140), hframe(12, POINT)]
    seq += [hframe(13 + i, POINT) for i in range(5)] + [hframe(18, FIST, index_angle=138)]
    out = run_heuristic(seq)
    assert not any(c for _, c in out)
    assert out[12][0] == "point"  # pointing resumes after the flicker


def test_single_deep_frame_is_not_a_click_when_two_are_required():
    seq = [hframe(i, POINT) for i in range(10)] + [hframe(10, FIST), hframe(11, POINT)]
    assert not any(c for _, c in run_heuristic(seq, click_curl_frames=2))


def test_shallow_quick_tap_clicks_and_reports_its_depth():
    h = HeuristicGestures(cfg())
    seq = [hframe(i, POINT) for i in range(10)] + [hframe(10, FIST, index_angle=142), hframe(11, FIST, index_angle=130)]
    out = [h.update(f) for f in seq]
    assert out[11] == ("idle", True) and h.curl_min_angle == 130


def test_slow_sag_then_curl_is_not_a_click():
    seq = [hframe(i, POINT) for i in range(10)]
    seq += [hframe(10 + i, FIST, index_angle=140) for i in range(18)]  # ~600 ms sag, past click_curl_max_ms
    seq += [hframe(22 + i, FIST) for i in range(3)]
    assert not any(c for _, c in run_heuristic(seq))


def test_short_point_does_not_click():
    out = run_heuristic([hframe(0, POINT), hframe(1, POINT), hframe(2, FIST)], point_hold_ms=120)
    assert not any(c for _, c in out)


def test_open_palm_to_fist_is_not_a_click():
    frames = [hframe(i, PALM) for i in range(10)] + [hframe(10 + i, FIST) for i in range(5)]
    assert not any(c for _, c in run_heuristic(frames))


def test_scroll_pose_then_curl_is_not_a_click():
    frames = [hframe(i, "01100") for i in range(10)] + [hframe(10 + i, FIST) for i in range(5)]
    assert not any(c for _, c in run_heuristic(frames))


def test_rearm_and_cooldown():
    seq = ([POINT] * 6 + [FIST] * 2) * 3
    out = run_heuristic([hframe(i, e) for i, e in enumerate(seq)], click_cooldown_ms=0)
    assert sum(c for _, c in out) == 3
    out = run_heuristic([hframe(i, e) for i, e in enumerate(seq)], click_cooldown_ms=1000)
    assert sum(c for _, c in out) == 1


def test_hand_lost_disarms():
    frames = [hframe(i, POINT) for i in range(10)] + [no_hand(10)] + [hframe(11, FIST)]
    assert not any(c for _, c in run_heuristic(frames))


# ------------------------------------------------------------------ engine (heuristic decider)

def test_click_lands_before_the_curl():
    """The fingertip dips while the index curls; the click must land where the cursor was before that."""
    eng = CommandEngine(cfg(decider="heuristic", pre_curl_ms=100))
    frames = [hframe(i, POINT, pos=(500, 400)) for i in range(10)]
    frames += [hframe(10, POINT, pos=(500, 430)), hframe(11, POINT, pos=(500, 460))]  # tip starts dipping
    frames += [hframe(12, FIST, pos=(500, 500)), hframe(13, FIST, pos=(500, 510))]
    outs = [eng.update(f) for f in frames]
    clicks = [o.click_at for o in outs if o.click_at]
    assert clicks == [(500, 400)]
    assert outs[0].mode == "point" and outs[-1].mode == "idle" and outs[-1].source == "heuristic"


def test_idle_action_without_hand():
    out = CommandEngine(cfg(decider="heuristic")).update(no_hand(0))
    assert out.mode == "idle" and out.click_at is None


# ------------------------------------------------------------------ Drex stream

def test_window_state_is_compact_features():
    frames = [hframe(i, POINT) for i in range(8)] + [hframe(8, FIST), hframe(9, FIST)]
    s = window_state(frames)
    rows = s.splitlines()[1:]
    assert len(rows) == 10 and "left hand" in s
    first, last = json.loads(rows[0]), json.loads(rows[-1])
    assert first["ext"] == POINT and last["ext"] == FIST and last["dt_ms"] == 0 and first["dt_ms"] < 0
    assert len(s) < 1500  # a few hundred tokens, not raw landmarks
    assert window_state([]) == "No hand in view."
    assert window_state([no_hand(0)]) == "No left hand in view."


def test_request_body_shape():
    c = DrexGestureClient(cfg(), post=lambda *a: {}, key="k")
    body = c.body("state")
    q = body["questions"]["gesture"]
    assert body["model"] == "drex-max-v1.1" and q["type"] == "choice" and q["criteria"] == GESTURES


class FakeDrex:
    """Stands in for POST /v1/systemone; records calls."""

    def __init__(self, probs=None, error=None, delay=0.0):
        self.probs, self.error, self.delay = probs, error, delay
        self.bodies, self.headers = [], []

    def __call__(self, url, headers, body, timeout):
        self.bodies.append(body)
        self.headers.append(headers)
        time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return {"model": "drex-max-v1.1", "answers": {"gesture": {"choice": max(self.probs, key=self.probs.get),
                                                                  "probabilities": self.probs}},
                "usage": {"input_tokens": 300, "output_tokens": 1}}


def drive(eng: CommandEngine, frames, settle_s: float = 0.03):
    outs = []
    for f in frames:
        outs.append(eng.update(f))
        time.sleep(settle_s)  # let the background Drex call land
    return outs


def test_drex_click_decision_clicks_at_pre_curl_position(tmp_path):
    fake = FakeDrex({"idle": 0.05, "point": 0.05, "click": 0.9})
    c = cfg(decider="drex", max_rate_hz=100)
    eng = make_engine(c, post=fake, key="secret")
    assert eng.using_drex
    frames = [hframe(i, POINT, pos=(300, 200), should=True) for i in range(6)] + [hframe(6, FIST, trigger="pattern")]
    outs = drive(eng, frames)
    outs += drive(eng, [hframe(7 + i, FIST) for i in range(3)])
    clicks = [o for o in outs if o.click_at]
    eng.close()
    assert len(clicks) == 1 and clicks[0].source == "drex" and clicks[0].click_at == (300, 200)
    assert fake.headers[0]["Authorization"] == "Bearer secret"
    assert "ext" in fake.bodies[0]["state"]


def test_drex_click_needs_a_local_deep_curl():
    """Drex saying 'click' while the index is only sagging (not curled past click_curl_deg) must not click."""
    fake = FakeDrex({"idle": 0.05, "point": 0.05, "click": 0.9})
    eng = make_engine(cfg(decider="drex", max_rate_hz=100), post=fake, key="k")
    frames = [hframe(i, POINT, should=True) for i in range(6)]
    frames += [hframe(6 + i, FIST, index_angle=140, should=True) for i in range(4)]
    outs = drive(eng, frames)
    eng.close()
    assert not any(o.click_at for o in outs)
    assert any(d.reason == "no deep index curl seen locally" for o in outs for d in o.decisions)


def test_drex_point_drives_mode():
    fake = FakeDrex({"idle": 0.05, "point": 0.9, "click": 0.05})
    eng = make_engine(cfg(decider="drex", max_rate_hz=100), post=fake, key="k")
    outs = drive(eng, [hframe(i, POINT, should=True) for i in range(6)])
    eng.close()
    assert outs[-1].mode == "point" and outs[-1].source == "drex"


def test_low_confidence_drex_falls_back_to_heuristic():
    fake = FakeDrex({"idle": 0.4, "point": 0.35, "click": 0.25})
    eng = make_engine(cfg(decider="drex", max_rate_hz=100), post=fake, key="k")
    frames = [hframe(i, POINT, should=True) for i in range(6)] + [hframe(6, FIST, trigger="pattern"), hframe(7, FIST)]
    outs = drive(eng, frames)
    eng.close()
    assert all(o.source == "heuristic" for o in outs)
    assert sum(1 for o in outs if o.click_at) == 1  # the heuristic still clicks
    assert any(d.reason.startswith("top p") for o in outs for d in o.decisions)


def test_drex_error_falls_back_and_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    fake = FakeDrex(error="HTTP 503: unavailable")
    c = cfg(decider="drex", max_rate_hz=100)
    c.log_decisions = True
    eng = make_engine(c, post=fake, key="k")
    outs = drive(eng, [hframe(i, POINT, should=True) for i in range(6)])
    eng.close()
    assert outs[-1].mode == "point" and outs[-1].source == "heuristic"
    assert eng.streamer.errors >= 1
    entry = json.loads((tmp_path / "logs" / "drex.jsonl").read_text().splitlines()[0])
    assert entry["type"] == "decision" and entry["purpose"] == "gesture_route" and entry["ok"] is False
    assert "HTTP 503" in entry["error"] and entry["task_id"] == "gesture"


def test_log_entry_shape_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path))
    c = cfg(decider="drex", max_rate_hz=100)
    c.log_decisions = True
    eng = make_engine(c, post=FakeDrex({"idle": 0.1, "point": 0.85, "click": 0.05}), key="k")
    drive(eng, [hframe(i, POINT, should=True) for i in range(4)])
    eng.close()
    entry = json.loads((tmp_path / "logs" / "drex.jsonl").read_text().splitlines()[0])
    assert entry["answers"]["gesture"]["choice"] == "point" and entry["accepted"] is True
    assert entry["tokens"]["input"] == 300 and entry["cost_usd"] > 0


def test_no_key_means_heuristic_only(monkeypatch):
    eng = make_engine(cfg(decider="drex"), post=FakeDrex({"idle": 1.0}), key="")
    assert not eng.using_drex and eng.streamer is None
    assert eng.update(hframe(0, POINT)).source == "heuristic"


def test_streamer_rate_limit_and_one_call_in_flight():
    fake = FakeDrex({"idle": 0.9, "point": 0.05, "click": 0.05}, delay=0.05)
    s = DrexGestureStreamer(cfg(max_rate_hz=5), DrexGestureClient(cfg(max_rate_hz=5), post=fake, key="k"))
    t_end = time.monotonic() + 0.6
    i = 0
    while time.monotonic() < t_end:
        s.push(hframe(i, POINT, should=True))
        i += 1
        time.sleep(DT / 3)
    time.sleep(0.1)
    s.close()
    assert 2 <= len(fake.bodies) <= 5  # ~5 Hz over 0.6 s, not one per frame


def test_streamer_skips_frames_without_the_primary_hand():
    fake = FakeDrex({"idle": 1.0, "point": 0.0, "click": 0.0})
    s = DrexGestureStreamer(cfg(max_rate_hz=100), DrexGestureClient(cfg(), post=fake, key="k"))
    for i in range(10):
        s.push(no_hand(i))
        s.push(hframe(i, POINT, side="right", should=True))  # right hand only: not the primary
    time.sleep(0.1)
    s.close()
    assert fake.bodies == []


def test_drex_call_never_blocks_the_frame_loop():
    fake = FakeDrex({"idle": 0.1, "point": 0.8, "click": 0.1}, delay=0.5)
    eng = make_engine(cfg(decider="drex", max_rate_hz=100), post=fake, key="k")
    t0 = time.perf_counter()
    for i in range(30):
        eng.update(hframe(i, POINT, should=True))
    elapsed = time.perf_counter() - t0
    eng.close()
    assert elapsed < 0.1
