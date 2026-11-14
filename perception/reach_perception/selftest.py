"""--selftest: run the gesture pipeline on the SYNTHETIC landmark fixtures. No camera, no bridge, no keys."""

from __future__ import annotations

from .pinch import GestureEngine
from .screen_map import ScreenMap
from .synth import load

EXPECT = {  # fixture -> event kinds that must appear, in order (found/lost bookkeeping excluded except lost)
    "clean_pinch": ["pinch_down", "select"],
    "jitter_no_pinch": [],
    "open_palm_10s": ["pause"],
    "tracking_loss": ["pinch_down", "lost"],
}


def run_fixture(name: str) -> tuple[list, GestureEngine]:
    doc = load(name)
    eng = GestureEngine(*doc["frame"])
    events = []
    for t, lm in doc["frames"]:
        events += eng.update(t, lm)
    return events, eng


def main() -> int:
    smap = ScreenMap(1440, 900)
    ok = True
    for name, want in EXPECT.items():
        events, eng = run_fixture(name)
        kinds = [e.kind for e in events if e.kind != "found"]
        good = kinds == want
        extra = ""
        for e in events:
            if e.kind == "select":
                extra = f" rect(screen)={smap.rect_to_screen(e.data['start'], e.data['end'])}"
            if e.kind == "lost":
                extra = f" drag_cancelled={e.data['drag_cancelled']} pointer={eng.pointer}"
        print(f"{'PASS' if good else 'FAIL'} {name}: events={kinds} expected={want}{extra}")
        ok &= good
    print("selftest (synthetic fixtures):", "PASS" if ok else "FAIL")
    return 0 if ok else 1
