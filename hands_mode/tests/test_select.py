"""Selector tests: no camera, no Screen Recording permission, no `reach serve`."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

from hands_mode.select import Selector, box_around, loop_path, path_bbox

SCREEN = (1440.0, 900.0)


class FakeBridge:
    def __init__(self, up: bool = True) -> None:
        self.up = up
        self.sent: list[tuple] = []

    def select(self, region, text, image_path, path=None) -> bool:
        if not self.up:
            return False
        self.sent.append((region, text, image_path, path))
        return True


def wait_status(sel: Selector, prefix: str, timeout: float = 2.0) -> str:
    end = time.monotonic() + timeout
    seen = []
    while time.monotonic() < end:
        s = sel.poll()
        if s:
            seen.append(s)
            if s.startswith(prefix):
                return s
        time.sleep(0.01)
    raise AssertionError(f"no status starting {prefix!r}; saw {seen}")


def make(bridge=None, capture=None, ocr=None) -> tuple[Selector, FakeBridge]:
    bridge = bridge or FakeBridge()
    sel = Selector(bridge=bridge, capture=capture or (lambda r: Path("/tmp/x.png")),
                   ocr=ocr or (lambda p: "hello"), screen=SCREEN)
    return sel, bridge


def circle(cx=500, cy=400, r=100, n=200):
    return [(cx + r * math.cos(a / n * 2 * math.pi), cy + r * math.sin(a / n * 2 * math.pi)) for a in range(n)]


def draw(sel: Selector, pts) -> None:
    sel.begin(*pts[0])
    for p in pts[1:]:
        sel.add(*p)
    sel.finish()


def test_loop_path_simplifies_and_rejects_tiny():
    p = loop_path(circle())
    assert p is not None and 8 <= len(p) <= 64
    x, y, w, h = path_bbox(p)
    assert abs(w - 200) < 5 and abs(h - 200) < 5 and abs(x - 400) < 3
    assert loop_path([(0, 0), (10, 5), (20, 0), (5, 15)]) is None  # tiny: rect fallback
    assert loop_path([(0, 0), (1, 1)]) is None


def test_box_around_clamps():
    assert box_around((10, 10), *SCREEN) == [0, 0, 480, 160]
    assert box_around((1430, 890), *SCREEN) == [960, 740, 480, 160]


def test_loop_relays_bbox_and_path():
    sel, bridge = make()
    draw(sel, circle())
    assert wait_status(sel, "selected") == "selected · 5 chars sent"
    region, text, img, path = bridge.sent[0]
    assert abs(region[0] - 400) <= 3 and abs(region[2] - 200) <= 5 and abs(region[3] - 200) <= 5
    assert text == "hello" and img == "/tmp/x.png"
    assert path and 8 <= len(path) <= 64
    sel.close()


def test_tap_selects_box_around_point():
    sel, bridge = make()
    draw(sel, [(700, 450), (705, 452)])
    wait_status(sel, "selected")
    region, _, _, path = bridge.sent[0]
    assert region == box_around((705, 452), *SCREEN) and path is None
    sel.close()


def test_straight_drag_selects_rect():
    sel, bridge = make()
    draw(sel, [(300, 200), (400, 200.5), (500, 201)])  # straight: not a loop
    wait_status(sel, "selected")
    region, _, _, path = bridge.sent[0]
    assert region == [300, 200, 200, 1] and path is None
    sel.close()


def test_drag_any_direction_selects_rect():
    sel, bridge = make()
    draw(sel, [(500, 300), (100, 100)])
    wait_status(sel, "selected")
    assert bridge.sent[0][0] == [100, 100, 400, 200]
    sel.close()


def test_read_runs_off_thread_and_poll_reports_it():
    gate = threading.Event()
    sel, bridge = make(capture=lambda r: gate.wait(2) and Path("/tmp/x.png"))
    t0 = time.monotonic()
    draw(sel, [(500, 300), (100, 100)])
    assert time.monotonic() - t0 < 0.1  # finish did not wait for the capture
    assert sel.poll() == "select [100, 100, 400, 200]: reading..."
    assert not bridge.sent
    gate.set()
    assert wait_status(sel, "selected") == "selected · 5 chars sent"
    sel.close()


def test_capture_error_does_not_kill_the_thread():
    calls = []

    def capture(rect):
        calls.append(rect)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return Path("/tmp/x.png")

    sel, bridge = make(capture=capture)
    draw(sel, [(500, 300), (100, 100)])
    wait_status(sel, "selected")  # the failed read is still relayed, rect only
    assert bridge.sent[0][1] is None and bridge.sent[0][2] is None
    draw(sel, [(500, 300), (100, 100)])
    assert wait_status(sel, "selected") == "selected · 5 chars sent"
    assert len(calls) == 2
    sel.close()


def test_cancel_drops_the_trail():
    sel, bridge = make()
    sel.begin(100, 100)
    sel.add(400, 300)
    sel.cancel()
    assert not sel.active
    sel.finish()  # nothing to finish
    time.sleep(0.05)
    assert sel.poll() is None and not bridge.sent
    sel.close()


def test_no_server_is_local_only():
    sel, _ = make(bridge=FakeBridge(up=False))
    draw(sel, [(500, 300), (100, 100)])
    assert wait_status(sel, "selected") == "selected · local only (no reach serve)"
    sel.close()


def test_no_text_says_so():
    sel, _ = make(capture=lambda r: None)
    draw(sel, [(500, 300), (100, 100)])
    assert "no text" in wait_status(sel, "selected")
    sel.close()
