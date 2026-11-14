"""Hand signals: thumbs-up and spread peace, held 0.6 s, 1.5 s cooldown, never while pinching or pointing."""

from __future__ import annotations

import types

from hands_mode.actions import ActionCfg, HandsEngine, is_pointing
from hands_mode.mouse import PrintMouse
from hands_mode.signals import SignalWatcher, enabled, is_peace, is_thumbs_up, pose_of

NAMES = ("thumb", "index", "middle", "ring", "pinky")


def H(pose=(), spread=0.0, pinch=False, edge=None, present=True, scale=0.2, vy=0.0, gap=0.05, source="landmarker"):
    """A synthetic frame: `pose` = extended fingers; index and middle tips `spread` knuckle gaps apart."""
    lm = [(0.5, 0.6, 0.0)] * 21
    lm[5], lm[9] = (0.5, 0.6, 0.0), (0.5 + gap, 0.6, 0.0)
    lm[8], lm[12] = (0.5, 0.4, 0.0), (0.5 + spread * gap, 0.4, 0.0)
    fingers = {n: types.SimpleNamespace(extended=n in pose, tip=(0.5, 0.5)) for n in NAMES}
    return types.SimpleNamespace(hand_present=present, fingers=fingers if present else {},
                                 pointer_screen=(500, 400) if present else None, pinch_active=pinch, pinch_edge=edge,
                                 pinch_finger="index", finger_angles={"index": 170.0}, pinch_dist=1.0, hand_scale=scale,
                                 scroll_velocity=(0.0, vy), velocity=(0.0, 0.0), landmarks=tuple(lm),
                                 source=source)


THUMBS = H(("thumb",))
PEACE = H(("index", "middle"), spread=3.0)
SCROLL = H(("index", "middle"), spread=1.0)


def run(w, frames, t0=0.0, dt=0.05, busy=False):
    out, t = [], t0
    for f in frames:
        if (g := w.update(f, t, busy=busy)):
            out.append((round(t, 2), g))
        t += dt
    return out, t


def test_poses():
    assert is_thumbs_up(THUMBS) and pose_of(THUMBS) == "thumbs_up"
    assert not is_thumbs_up(H(()))                       # closed fist is not a signal
    assert pose_of(H(())) is None
    assert not is_thumbs_up(H(("thumb", "index")))       # pointing with the thumb out
    assert pose_of(H(("index",))) is None
    assert is_peace(PEACE) and pose_of(PEACE) == "peace"
    assert not is_peace(SCROLL) and pose_of(SCROLL) is None   # fingers together = the scroll pose
    assert not is_peace(H(("index", "middle", "ring"), spread=3.0))
    assert not is_peace(H(("index", "middle"), spread=3.0, gap=0.0))   # unknown knuckle gap: never


def test_thumbs_up_fires_once_after_hold():
    w = SignalWatcher()
    out, _ = run(w, [THUMBS] * 30)                       # 1.5 s held
    assert out == [(0.6, "thumbs_up")]


def test_short_hold_does_not_fire():
    w = SignalWatcher()
    out, _ = run(w, [THUMBS] * 11 + [H(("index",))] + [THUMBS] * 11)
    assert out == []


def test_peace_fires_and_scroll_pose_never_does():
    assert run(SignalWatcher(), [PEACE] * 14)[0] == [(0.6, "peace")]
    assert run(SignalWatcher(), [SCROLL] * 40)[0] == []


def test_cooldown_after_any_signal():
    w = SignalWatcher()
    out, t = run(w, [THUMBS] * 13)                       # fires at 0.6
    out2, t = run(w, [H(())] + [PEACE] * 14, t0=t)       # peace held 0.65 s, but within 1.5 s of the first
    assert out == [(0.6, "thumbs_up")] and out2 == []
    out3, _ = run(w, [PEACE] * 20, t0=t)                 # still held past the cooldown -> fires
    assert [g for _, g in out3] == ["peace"]


def test_no_signal_while_pinching_busy_or_without_hand():
    assert run(SignalWatcher(), [H(("thumb",), pinch=True)] * 30)[0] == []
    assert run(SignalWatcher(), [THUMBS] * 30, busy=True)[0] == []
    assert run(SignalWatcher(), [H((), present=False)] * 30)[0] == []
    w = SignalWatcher()                                  # a pinch mid-hold restarts the timer
    out, _ = run(w, [THUMBS] * 8 + [H(("thumb",), edge="down", pinch=True)] + [THUMBS] * 8)
    assert out == []
    assert run(SignalWatcher(), [H(("thumb",), source="hold")] * 30)[0] == []   # held (stale) frames
    assert is_pointing(H(("index",)))                    # run.py passes pointing as busy


def test_switch_reads_env():
    assert enabled({}) and enabled({"REACH_HAND_SIGNALS": "1"})
    assert not enabled({"REACH_HAND_SIGNALS": "0"})


def test_engine_spread_two_fingers_does_not_scroll_with_signals_on():
    clock = types.SimpleNamespace(t=100.0)
    mouse = PrintMouse(echo=False)
    e = HandsEngine(mouse, ActionCfg(), lambda: clock.t)
    e.signals = True
    for _ in range(5):
        e.update(H(("index", "middle"), spread=3.0, vy=1.0))
        clock.t += 0.1
    assert not [a for a in mouse.log if a[0] == "scroll"]
    for _ in range(5):                                   # together: the scroll pose still scrolls
        e.update(H(("index", "middle"), spread=1.0, vy=1.0))
        clock.t += 0.1
    assert [a for a in mouse.log if a[0] == "scroll"]
