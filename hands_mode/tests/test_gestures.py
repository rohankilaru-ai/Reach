"""Custom gestures (EXPERIMENTAL): synthetic frames only (no camera, no keys, no real events)."""

from __future__ import annotations

import json
import math

import pytest

from finger_processing.processing import FINGERS, Finger, Frame, Hand
from hands_mode import gestures as G
from hands_mode.actions import ActionCfg, HandsEngine
from hands_mode.drex import DrexClient
from hands_mode.mouse import PrintMouse

DT = 1 / 30


def hand(side="right", ext=("index",), wrist=(0.5, 0.7), tip=None, vel=(0.0, 0.0)):
    tip = tip or (wrist[0], wrist[1] - 0.2)
    fingers = {f: Finger(f, f in ext, tip if f == "index" else wrist, tip, (0, 0), 0.0) for f in FINGERS}
    return Hand(side, fingers, ((*wrist, 0.0),) + ((0.0, 0.0, 0.0),) * 20, hand_scale=0.15, velocity=vel,
                finger_angles={f: 175.0 if f in ext else 60.0 for f in FINGERS})


def frame(i, *hands_):
    return Frame(t=i * DT, frame_id=i, hands={h.side: h for h in hands_})


def swipe_right(i0=0, n=10, y=0.6, jitter=0.0):
    """Open palm moving right fast."""
    return [frame(i0 + k, hand(ext=FINGERS, wrist=(0.3 + 0.04 * k, y + jitter * math.sin(k)), vel=(1.0, 0.0)))
            for k in range(n)]


def point_still(i0=0, n=10):
    return [frame(i0 + k, hand(ext=("index",), wrist=(0.5, 0.7))) for k in range(n)]


def two_hand_spread(i0=0, n=10):
    return [frame(i0 + k, hand("left", FINGERS, (0.4 - 0.02 * k, 0.6), vel=(-0.6, 0)),
                  hand("right", FINGERS, (0.6 + 0.02 * k, 0.6), vel=(0.6, 0))) for k in range(n)]


def gesture(name, frames_a, frames_b, kind="top", value=""):
    ex = [[G.frame_features(f) for f in fr] for fr in (frames_a, frames_b)]
    return {"name": name, "action": {"kind": kind, "value": value}, "hands": G.hand_count(ex[0] + ex[1]),
            "examples": ex, "created": "2026-11-14T00:00:00+00:00"}


class FakeBridge:
    def __init__(self):
        self.sent = []

    def gesture(self, name, action):
        self.sent.append((name, action))
        return True


def fake_post(probs=None, exc=None):
    calls = []

    def post(url, headers, body, timeout):
        calls.append(body)
        if exc:
            raise exc
        return {"answers": {"gesture": {"probabilities": probs(body) if callable(probs) else probs}},
                "model": "drex-max-v1.1", "usage": {"input_tokens": 100}}
    post.calls = calls
    return post


# ---------------------------------------------------------------- features

def test_feature_shape_one_and_two_hands():
    one = G.frame_features(frame(0, hand()))
    two = G.frame_features(frame(0, hand("left"), hand("right", wrist=(0.8, 0.7))))
    assert len(one) == len(two) == G.FEATURE_LEN == 35
    assert one[0] == 0.0 and one[G.PER_HAND] == 1.0 and one[-1] == 0.0      # right hand only
    assert two[0] == two[G.PER_HAND] == 1.0 and two[-1] > 0
    assert G.hand_count([one] * 10) == 1 and G.hand_count([two] * 10) == 2


# ---------------------------------------------------------------- recorder

def test_recorder_flow_two_examples_and_status_lines():
    r = G.Recorder("swipe", {"kind": "top", "value": ""}, now=0.0)
    statuses, now = [], 0.0
    frames = swipe_right(n=100)
    k = 0
    while not r.done and now < 20:
        s = r.update(frames[k % 100], now)
        if not statuses or statuses[-1] != s:
            statuses.append(s)
        now, k = now + DT, k + 1
    assert statuses == [
        "recording 'swipe' · example 1/2 · get ready 3", "recording 'swipe' · example 1/2 · get ready 2",
        "recording 'swipe' · example 1/2 · get ready 1", "recording 'swipe' · example 1/2 · go",
        "recording 'swipe' · example 2/2 · get ready 3", "recording 'swipe' · example 2/2 · get ready 2",
        "recording 'swipe' · example 2/2 · get ready 1", "recording 'swipe' · example 2/2 · go",
        "saved gesture 'swipe'"]
    g = r.result
    assert g["hands"] == 1 and len(g["examples"]) == 2
    assert all(len(ex) == 10 and all(len(v) == G.FEATURE_LEN for v in ex) for ex in g["examples"])


def test_recorder_cancels_without_hand():
    r = G.Recorder("x", {"kind": "top", "value": ""}, now=0.0)
    now = 0.0
    while not r.done:
        r.update(frame(0), now)
        now += 0.1
    assert r.result is None and r.status == "recording cancelled · no hand"


def test_layer_takes_request_records_saves_and_engine_stays_idle(tmp_path):
    mouse = PrintMouse(echo=False)
    engine = HandsEngine(mouse, ActionCfg())
    layer = G.GestureLayer(engine, mouse, FakeBridge(), root=tmp_path, drex=DrexClient(key=""))
    (tmp_path / "run").mkdir()
    req = {"op": "record", "id": "abc", "name": "spread", "action": {"kind": "command", "value": "next"}}
    (tmp_path / "run" / "hands_request.json").write_text(json.dumps(req))
    frames, now, paused_all = two_hand_spread(n=200), 0.0, True
    for k in range(300):
        status, paused = layer.update(frames[k % 200], now)
        if layer.recorder is None and k > 0:
            break
        paused_all &= paused
        now += DT
    assert paused_all and status == "saved gesture 'spread'" and not mouse.log
    assert not (tmp_path / "run" / "hands_request.json").exists()
    saved = G.load_gestures(tmp_path / "gestures.json")
    assert [g["name"] for g in saved] == ["spread"] and saved[0]["hands"] == 2
    # the same id again is ignored
    (tmp_path / "run" / "hands_request.json").write_text(json.dumps(req))
    layer.update(None, now + 1)
    assert layer.recorder is None


# ---------------------------------------------------------------- matcher (local)

def run_matcher(m, frames, t0=0.0):
    fired = []
    for k, f in enumerate(frames):
        g = m.update(f, t0 + k * DT)
        if g:
            fired.append(g["name"])
    return fired


def test_local_match_fires_on_similar_motion_not_on_unrelated():
    g = gesture("swipe", swipe_right(), swipe_right(y=0.55))
    m = G.Matcher([g], drex=None)
    assert run_matcher(m, swipe_right(y=0.62, jitter=0.01)) == ["swipe"]
    m = G.Matcher([g], drex=None)
    assert run_matcher(m, point_still(n=40)) == []
    # a still open palm in the same place: same pose, no motion -> no fire
    still_palm = [frame(k, hand(ext=FINGERS, wrist=(0.5, 0.6))) for k in range(40)]
    assert run_matcher(G.Matcher([g], drex=None), still_palm) == []


def test_cooldown_and_rearm():
    g = gesture("swipe", swipe_right(), swipe_right())
    m = G.Matcher([g], drex=None)
    # holding the matching window does not fire again until the hand leaves the gate
    assert run_matcher(m, swipe_right() + swipe_right(i0=10) + swipe_right(i0=20)) == ["swipe"]
    fired = run_matcher(m, point_still(n=20), t0=2.0) + run_matcher(m, swipe_right(), t0=3.0)
    assert fired == ["swipe"]


def test_two_hand_gesture_needs_two_hands():
    g = gesture("spread", two_hand_spread(), two_hand_spread())
    assert g["hands"] == 2
    one_hand = [frame(k, f.hands["right"]) for k, f in enumerate(two_hand_spread())]
    assert run_matcher(G.Matcher([g], drex=None), one_hand) == []
    assert run_matcher(G.Matcher([g], drex=None), two_hand_spread()) == ["spread"]


# ---------------------------------------------------------------- matcher (Drex)

def drex_matcher(post, tmp_path, gs=None):
    gs = gs or [gesture("swipe", swipe_right(), swipe_right()),
                gesture("point", point_still(), point_still())]
    return G.Matcher(gs, DrexClient(post=post, key="test"), run_async=False, log_path=tmp_path / "drex.jsonl")


def test_drex_accepts(tmp_path):
    post = fake_post({"swipe": 0.9, "none": 0.1})
    m = drex_matcher(post, tmp_path)
    assert run_matcher(m, swipe_right(y=0.65)) == ["swipe"]
    body = post.calls[0]
    crit = body["questions"]["gesture"]["criteria"]
    assert body["model"] == "drex-max-v1.1" and "none" in crit and "swipe" in crit and "point" not in crit
    assert '"ext":"11111"' in body["state"] and "Example 2" in crit["swipe"]
    log = [json.loads(x) for x in (tmp_path / "drex.jsonl").read_text().splitlines()]
    assert log[0]["purpose"] == "hand_gesture" and log[0]["accepted"] and log[0]["type"] == "decision"
    assert m.last.source == "drex"


@pytest.mark.parametrize("probs", [{"swipe": 0.6, "none": 0.4}, {"swipe": 0.75, "none": 0.6},
                                   {"none": 0.9, "swipe": 0.1}])
def test_drex_rejects_low_confidence_and_none(tmp_path, probs):
    m = drex_matcher(fake_post(probs), tmp_path)
    assert run_matcher(m, swipe_right()) == []      # even an exact local match: Drex said no


def test_drex_rate_limited(tmp_path):
    post = fake_post({"none": 1.0})
    m = drex_matcher(post, tmp_path)
    run_matcher(m, swipe_right(n=10) + swipe_right(i0=10, n=20))   # ~1 s of matching frames
    assert 1 <= len(post.calls) <= 5


def test_drex_error_falls_back_to_local(tmp_path):
    m = drex_matcher(fake_post(exc=TimeoutError("timed out")), tmp_path)
    assert run_matcher(m, swipe_right()) == ["swipe"]
    assert m.last.error and json.loads((tmp_path / "drex.jsonl").read_text().splitlines()[0])["ok"] is False
    # a looser match (under GATE, over LOCAL_ACCEPT) is not enough without Drex
    loose = [frame(k, hand(ext=FINGERS, wrist=(0.3 + 0.04 * k, 0.6), vel=(0.3, 0.0))) for k in range(10)]
    m = drex_matcher(fake_post(exc=TimeoutError()), tmp_path, [gesture("swipe", swipe_right(), swipe_right())])
    d = m_dist(m, loose)
    assert G.LOCAL_ACCEPT < d < G.GATE
    assert run_matcher(m, loose) == []


def m_dist(m, frames):
    for f in frames:
        m.window.append(G.frame_features(f))
    d = min(m.distances().values())
    m.window.clear()
    return d


def test_no_key_uses_local(tmp_path):
    m = G.Matcher([gesture("swipe", swipe_right(), swipe_right())], DrexClient(key=""), run_async=False)
    assert not m.use_drex and run_matcher(m, swipe_right()) == ["swipe"] and m.last.source == "local"


# ---------------------------------------------------------------- acting

@pytest.mark.parametrize("kind,value,key", [("top", "", "cmd+up"), ("bottom", "", "cmd+down"),
                                            ("keys", "Cmd+Shift+T", "cmd+shift+t")])
def test_act_keys(kind, value, key):
    mouse = PrintMouse(echo=False)
    assert G.act({"name": "g", "action": {"kind": kind, "value": value}}, mouse, None) == "gesture: g"
    assert mouse.log == [("key", key)]


def test_act_bad_keys_and_no_permission():
    mouse = PrintMouse(echo=False)
    assert "bad key combo" in G.act({"name": "g", "action": {"kind": "keys", "value": "cmd+nope"}}, mouse, None)
    mouse.can_click = False
    assert "Accessibility" in G.act({"name": "g", "action": {"kind": "top", "value": ""}}, mouse, None)
    assert mouse.log == []


@pytest.mark.parametrize("kind,value", [("command", "next"), ("ask", "summarise this page")])
def test_act_command_and_ask_go_to_bridge(kind, value):
    bridge, mouse = FakeBridge(), PrintMouse(echo=False)
    assert G.act({"name": "g", "action": {"kind": kind, "value": value}}, mouse, bridge) == "gesture: g"
    assert bridge.sent == [("g", {"kind": kind, "value": value})] and mouse.log == []


# ---------------------------------------------------------------- two-hand pause

def test_two_hands_pause_engine_only_when_a_two_hand_gesture_exists(tmp_path):
    mouse = PrintMouse(echo=False)
    engine = HandsEngine(mouse, ActionCfg())
    layer = G.GestureLayer(engine, mouse, FakeBridge(), root=tmp_path, drex=DrexClient(key=""))
    f = two_hand_spread()[0]
    assert layer.update(f, 0.0) == (None, False)
    G.save_gesture(tmp_path / "gestures.json", gesture("spread", two_hand_spread(), two_hand_spread(),
                                                       kind="command", value="next"))
    layer.matcher.set_gestures(G.load_gestures(tmp_path / "gestures.json"))
    engine.button_down, engine._down_at = True, (10.0, 10.0)
    assert layer.update(f, 1.0) == ("two hands · gestures", True)
    assert not engine.button_down and mouse.log[-1][0] == "up"
    assert layer.update(frame(0, hand()), 1.1) == (None, False)   # one hand: the mouse works


def test_layer_fires_and_flashes_status(tmp_path):
    mouse, bridge = PrintMouse(echo=False), FakeBridge()
    G.save_gesture(tmp_path / "gestures.json", gesture("swipe", swipe_right(), swipe_right(), "ask", "summarise"))
    layer = G.GestureLayer(HandsEngine(mouse, ActionCfg()), mouse, bridge, root=tmp_path, drex=DrexClient(key=""))
    out = [layer.update(f, k * DT) for k, f in enumerate(swipe_right())]
    assert out[-1] == ("gesture: swipe", False) and bridge.sent == [("swipe", {"kind": "ask", "value": "summarise"})]
    # forgetting the gesture on disk (the overlay) reloads the matcher
    G.write_gestures(tmp_path / "gestures.json", [])
    layer.update(None, 5.0)
    assert layer.matcher.gestures == {}
