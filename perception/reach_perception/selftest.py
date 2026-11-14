"""--selftest: run the gesture pipeline on the SYNTHETIC landmark fixtures. No camera, no keys.

With --bridge, the clean_pinch replay also goes to a running `reach serve`: one `pointer` per frame and the `select`
(synthetic text plus a tiny synthetic PNG under .reach/captures/). The server relays both to overlay clients.
"""

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


SYNTH_TEXT = "SYNTHETIC selftest selection\nsecond line"


def _synthetic_png(dir_) -> str:
    """A 2x2 PNG (no screen capture), so the overlay thumbnail path is exercised."""
    import struct
    import zlib

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([200, 120, 40]) * 2 for _ in range(2))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / "selftest-selection.png"
    p.write_bytes(png)
    return str(p)


def bridge_replay(smap: ScreenMap) -> bool:
    import time

    from .bridge_client import BridgeClient, server_reach_dir

    bridge = BridgeClient()
    if not bridge.connect():
        print(f"bridge: FAIL ({bridge.status})")
        return False
    print(f"bridge: {bridge.status} (role perception)")
    doc = load("clean_pinch")
    eng = GestureEngine(*doc["frame"])
    pointers, sent_select = 0, False
    for t, lm in doc["frames"]:
        for e in eng.update(t, lm):
            if e.kind == "select":
                rect = smap.rect_to_screen(e.data["start"], e.data["end"])
                img = _synthetic_png(server_reach_dir() / "captures")
                sent_select = bridge.select(rect, SYNTH_TEXT, img)
                print(f"bridge: select rect={rect} image={img} sent={sent_select}")
        if eng.pointer:
            sx, sy = smap.to_screen(eng.pointer)
            bridge._last_pointer = 0  # replay is faster than real time; do not rate-limit
            pointers += bridge.pointer(round(sx), round(sy), eng.pinching)
        time.sleep(0.01)
    time.sleep(0.5)  # let any server error arrive
    errors = []
    while not bridge.inbox.empty():
        m = bridge.inbox.get_nowait()
        if m.get("type") == "error":
            errors.append(m.get("message"))
    bridge.close()
    print(f"bridge: pointers sent={pointers} select sent={sent_select} server errors={errors or 'none'}")
    return sent_select and pointers > 0 and not errors


def main(bridge: bool = False) -> int:
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
    if bridge:
        ok &= bridge_replay(smap)
    print("selftest (synthetic fixtures):", "PASS" if ok else "FAIL")
    return 0 if ok else 1
