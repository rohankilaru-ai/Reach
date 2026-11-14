"""Camera-free, key-free tests on SYNTHETIC landmark fixtures."""

from __future__ import annotations

import pytest

from reach_perception import selftest
from reach_perception.pinch import LOST_MS, PINCH_HOLD_MS, GestureEngine, rect_from_points
from reach_perception.screen_map import ScreenMap, box_around, homography
from reach_perception.synth import hand


def kinds(name):
    events, eng = selftest.run_fixture(name)
    return [e.kind for e in events if e.kind != "found"], events, eng


def test_jitter_fires_nothing():
    k, _, eng = kinds("jitter_no_pinch")
    assert k == []
    assert not eng.pinching


def test_open_palm_10s_pauses_once_and_never_pinches():
    k, _, _ = kinds("open_palm_10s")
    assert k == ["pause"]


def test_clean_pinch_selects_once_with_rect():
    k, events, _ = kinds("clean_pinch")
    assert k == ["pinch_down", "select"]
    sel = events[-1].data
    x, y, w, h = sel["rect"]
    assert w > 0.05 and h > 0.02  # the fixture drags ~100 px left and ~50 px up


def test_tracking_loss_hides_pointer_and_cancels_drag():
    k, events, eng = kinds("tracking_loss")
    assert k == ["pinch_down", "lost"]
    lost = [e for e in events if e.kind == "lost"][0]
    assert lost.data["drag_cancelled"] is True
    assert eng.pointer is None and not eng.tracking and eng.drag_start is None


def test_short_gap_keeps_tracking():
    eng = GestureEngine()
    eng.update(0, hand(320, 330))
    assert eng.update(LOST_MS - 100, None) == []
    assert eng.tracking and eng.pointer is not None


def test_pinch_needs_hold_time():
    eng = GestureEngine()
    eng.update(0, hand(320, 330, 0.8))
    assert eng.update(33, hand(320, 330, 0.1)) == []
    assert [e.kind for e in eng.update(33 + PINCH_HOLD_MS - 10, hand(320, 330, 0.1))] == []
    assert [e.kind for e in eng.update(33 + PINCH_HOLD_MS + 1, hand(320, 330, 0.1))] == ["pinch_down"]


def test_hysteresis_band_does_not_release():
    eng = GestureEngine()
    eng.update(0, hand(320, 330, 0.8))
    eng.update(10, hand(320, 330, 0.1))
    eng.update(10 + PINCH_HOLD_MS, hand(320, 330, 0.1))
    assert eng.pinching
    for t in range(200, 2000, 33):  # between ON (0.25) and OFF (0.40): stays pinched
        assert eng.update(t, hand(320, 330, 0.33)) == []
    assert eng.pinching


def test_rect_from_points_any_corner_order():
    assert rect_from_points((0.6, 0.2), (0.4, 0.5)) == pytest.approx([0.4, 0.2, 0.2, 0.3])
    assert rect_from_points((0.4, 0.5), (0.6, 0.2)) == pytest.approx([0.4, 0.2, 0.2, 0.3])


def test_fixed_mapping_corners_and_clamp():
    m = ScreenMap(1440, 900)
    assert m.to_screen((0.15, 0.15)) == (0, 0)
    assert m.to_screen((0.85, 0.85)) == pytest.approx((1440, 900))
    assert m.to_screen((0.5, 0.5)) == pytest.approx((720, 450))
    assert m.to_screen((-1, 2)) == (0, 900)
    assert m.rect_to_screen((0.5, 0.5), (0.15, 0.15)) == [0, 0, 720, 450]


def test_calibrated_mapping_hits_corners(tmp_path):
    corners = [(0.2, 0.25), (0.8, 0.2), (0.85, 0.8), (0.18, 0.75)]
    m = ScreenMap(1440, 900, corners)
    for c, want in zip(corners, [(0, 0), (1440, 0), (1440, 900), (0, 900)]):
        assert m.to_screen(c) == pytest.approx(want, abs=1e-6)
    p = m.save(corners, tmp_path / "calibration.json")
    again = ScreenMap.load(1440, 900, p)
    assert again.calibrated and again.to_screen((0.5, 0.5)) == pytest.approx(m.to_screen((0.5, 0.5)))
    assert not ScreenMap.load(1440, 900, tmp_path / "missing.json").calibrated


def test_degenerate_calibration_raises():
    with pytest.raises(ValueError):
        homography([(0.5, 0.5)] * 4, [(0, 0), (1, 0), (1, 1), (0, 1)])


def test_box_around_clamps():
    assert box_around((10, 10), 1440, 900) == [0, 0, 480, 160]
    assert box_around((1430, 890), 1440, 900) == [960, 740, 480, 160]


def test_selftest_entrypoint_passes():
    assert selftest.main() == 0


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS Vision only")
def test_vision_ocr_reads_rendered_text(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from reach_perception.capture import ocr

    img = np.full((120, 700, 3), 255, np.uint8)
    cv2.putText(img, "Reach reads this paragraph", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 3)
    p = tmp_path / "probe.png"  # rendered text, not a camera frame
    cv2.imwrite(str(p), img)
    assert "paragraph" in ocr(p)


# --- Greptile pinch worker findings ---


def test_open_palm_clears_stale_pinch_timer():
    eng = GestureEngine()
    eng.update(0, hand(320, 330, 0.8))
    eng.update(33, hand(320, 330, 0.1))  # starts the below-threshold timer
    eng.update(66, hand(320, 330, 0.8, palm=True))  # open palm interrupts
    assert eng._below_since is None
    # pinch resumes later: the old timer must not make it count immediately
    assert eng.update(33 + PINCH_HOLD_MS + 50, hand(320, 330, 0.1)) == []
    assert not eng.pinching


def test_lost_hand_resets_palm_fired():
    eng = GestureEngine()
    t = 0
    while t <= 600:
        eng.update(t, hand(320, 330, palm=True))
        t += 33
    assert eng._palm_fired and eng.palm
    eng.update(t + LOST_MS + 100, None)
    assert not eng._palm_fired and not eng.palm and eng._palm_gone_since is None


def test_bad_calibration_falls_back_with_warning():
    from reach_perception.screen_map import build_map

    m, warn = build_map(1440, 900, [(0.5, 0.5)] * 4)
    assert not m.calibrated and warn and "fixed" in warn
    ok, none = build_map(1440, 900, [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)])
    assert ok.calibrated and none is None


def test_reset_deletes_saved_calibration(tmp_path):
    from reach_perception.screen_map import reset_calibration

    p = tmp_path / "calibration.json"
    ScreenMap(1440, 900, [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]).save(
        [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)], p)
    assert p.exists()
    m = reset_calibration(1440, 900, p)
    assert not m.calibrated and not p.exists()
    assert not ScreenMap.load(1440, 900, p).calibrated  # does not come back on restart


def test_read_worker_does_not_block_and_returns_results():
    import threading
    import time

    from reach_perception.worker import ReadWorker

    gate = threading.Event()

    def slow_capture(rect):
        gate.wait(2)
        return "img.png"

    rw = ReadWorker(slow_capture, lambda img: "hello")
    t = time.monotonic()
    assert rw.submit([0, 0, 10, 10])
    assert time.monotonic() - t < 0.2 and rw.busy and rw.poll() == []
    gate.set()
    out = []
    for _ in range(100):
        out += rw.poll()
        if out:
            break
        time.sleep(0.02)
    rw.close()
    assert out == [([0, 0, 10, 10], "hello", "img.png")] and not rw.busy


def test_read_worker_survives_capture_error():
    import time

    from reach_perception.worker import ReadWorker

    def boom(rect):
        raise OSError("no permission")

    rw = ReadWorker(boom, lambda i: "x")
    rw.submit([0, 0, 5, 5])
    out = []
    for _ in range(100):
        out += rw.poll()
        if out:
            break
        time.sleep(0.02)
    rw.close()
    assert out == [([0, 0, 5, 5], "", None)]


def test_run_exits_cleanly_after_consecutive_frame_failures(monkeypatch, capsys):
    import sys
    import types

    from reach_perception import worker

    keys = []

    class FakeCam:
        def ok(self): return True
        def read(self): return None
        def release(self): pass

    class FakeHands:
        def detect(self, f, t): raise AssertionError("no frame, no detect")
        def close(self): pass

    class FakeBridge:
        status = "off"
        def connect(self): pass
        def pointer(self, *a): pass
        def select(self, *a): return False
        def close(self): pass

    mods = {
        "reach_perception.camera": types.SimpleNamespace(Camera=FakeCam, PERMISSION_MSG="perm"),
        "reach_perception.hands": types.SimpleNamespace(Hands=FakeHands),
        "reach_perception.bridge_client": types.SimpleNamespace(BridgeClient=FakeBridge),
        "reach_perception.capture": types.SimpleNamespace(capture_region=lambda r: None, ocr=lambda p: ""),
        "reach_perception.preview": types.SimpleNamespace(
            poll_key=lambda: keys.append(1) or -1, show=lambda i: -1, draw=lambda *a: None, close=lambda: None),
    }
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    monkeypatch.setattr("reach_perception.screen_map.screen_size", lambda: (1440.0, 900.0))
    monkeypatch.setattr(worker, "screen_size", lambda: (1440.0, 900.0))
    monkeypatch.setattr(worker, "MAX_FRAME_FAILURES", 5)
    monkeypatch.setattr(worker.ScreenMap, "load", classmethod(lambda cls, w, h, path=None: cls(w, h)))
    assert worker.run(no_preview=False, no_ocr=True) == 3
    assert len(keys) == 4  # keys were polled on failed frames before exit
    assert "camera stopped" in capsys.readouterr().out
def test_loop_path_simplifies_and_rejects_tiny():
    import math

    from reach_perception.screen_map import loop_path, path_bbox

    circle = [(500 + 100 * math.cos(a / 200 * 2 * math.pi), 400 + 100 * math.sin(a / 200 * 2 * math.pi)) for a in range(200)]
    p = loop_path(circle)
    assert p is not None and 8 <= len(p) <= 64
    x, y, w, h = path_bbox(p)
    assert abs(w - 200) < 5 and abs(h - 200) < 5 and abs(x - 400) < 3
    assert loop_path([(0, 0), (10, 5), (20, 0), (5, 15)]) is None  # tiny: rect fallback
    assert loop_path([(0, 0), (1, 1)]) is None
