"""Key-free gate for the pinch relay (EXPERIMENTAL; no camera, no screen capture, no synthetic input events).

  uv run --project backend python smoke/pinch_relay_gate.py [--overlay apps/overlay/.build/debug/Reach]

Starts `reach serve --replay` on a scratch REACH_DIR, connects a tiny fake overlay client (role overlay), optionally
starts the real overlay's `--relay-selftest`, replays the perception worker's synthetic `clean_pinch` fixture to the
server (`--selftest --bridge`), and checks that `selection` and `pointer` reached the overlay clients and that the
overlay's next ask carried the hand selection (relay.jsonl). Kills everything on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_for(cond, timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.1)
    raise SystemExit(f"GATE FAIL: timed out waiting for {what}")


async def fake_overlay(port: int, token: str, got: dict, ready: asyncio.Event, seconds: float) -> None:
    import websockets

    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
        await ws.send(json.dumps({"type": "hello", "token": token, "role": "overlay"}))
        assert json.loads(await ws.recv())["type"] == "ready"
        ready.set()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
            except asyncio.TimeoutError:
                continue
            got.setdefault(m["type"], []).append(m)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", help="path to the built Reach binary; runs its --relay-selftest too")
    a = ap.parse_args()
    rd = Path(tempfile.mkdtemp(prefix="reach-gate-")) / ".reach"
    rd.mkdir()
    env = {**os.environ, "REACH_DIR": str(rd)}
    procs: list[subprocess.Popen] = []
    try:
        srv = subprocess.Popen(["uv", "run", "reach", "serve", "--replay", str(ROOT / "fixtures/events/ask.jsonl")],
                               cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        procs.append(srv)
        wait_for(lambda: (rd / "run/server.json").exists() and (rd / "run/token").exists(), 30, "reach serve")
        port = json.loads((rd / "run/server.json").read_text())["port"]
        token = (rd / "run/token").read_text().strip()
        print(f"[gate] reach serve --replay up on 127.0.0.1:{port}, REACH_DIR={rd}")

        ov_log = rd / "overlay.log"
        if a.overlay:
            procs.append(subprocess.Popen([a.overlay, "--reach-dir", str(rd), "--relay-selftest", "what does this say"],
                                          env=env, stdout=ov_log.open("w"), stderr=subprocess.STDOUT))
            wait_for(lambda: "connected (protocol" in ov_log.read_text(), 20, "overlay connect")
            print("[gate] overlay --relay-selftest connected (role overlay)")

        got: dict = {}

        async def run() -> int:
            ready = asyncio.Event()
            fake = asyncio.create_task(fake_overlay(port, token, got, ready, 8))
            done, _ = await asyncio.wait({fake, asyncio.create_task(ready.wait())}, timeout=10,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not ready.is_set():
                raise SystemExit(f"GATE FAIL: fake overlay did not connect ({fake.exception() if fake.done() else 'timeout'})")
            print("[gate] fake overlay client connected (role overlay)")
            worker = await asyncio.create_subprocess_exec(
                "uv", "run", "python", "-m", "reach_perception.worker", "--selftest", "--bridge",
                cwd=ROOT / "perception", env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(worker.communicate(), 120)
            for line in out.decode().splitlines():
                if line.startswith(("bridge", "selftest", "PASS clean")):
                    print(f"[worker] {line}")
            await fake
            return worker.returncode

        rc = asyncio.run(run())
        sels, ptrs = got.get("selection", []), got.get("pointer", [])
        print(f"[gate] fake overlay received: selection x{len(sels)}, pointer x{len(ptrs)}")
        if sels:
            s = sels[0]["selection"]
            print(f"[gate]   selection source={s.get('source')} text={s.get('text', '').splitlines()[0]!r} "
                  f"region={s.get('region')} image={Path(s.get('image_path') or '').name}")
        if ptrs:
            print(f"[gate]   first pointer {ptrs[0]}")
        ok = rc == 0 and len(sels) == 1 and len(ptrs) > 0 and sels[0]["selection"].get("source") == "pinch"

        if a.overlay:
            ov = procs[-1]
            try:
                ov.wait(timeout=60)
            except subprocess.TimeoutExpired:
                ov.kill()
            for line in ov_log.read_text().splitlines():
                if any(k in line for k in ("selection by hand", "chip", "ask:", "relay selftest", "hand pointers",
                                           "pointer ")):
                    print(f"[overlay] {line}")
            ok &= ov.returncode == 0
        relay_log = [json.loads(x) for x in (rd / "logs/relay.jsonl").read_text().splitlines()]
        asks = [x for x in relay_log if x["kind"] == "ask"]
        for x in relay_log:
            print(f"[relay.jsonl] {x}")
        if a.overlay:
            ok &= bool(asks) and asks[-1]["from"] == "overlay" and asks[-1]["selection"] == "pinch"
        print("GATE PASS" if ok else "GATE FAIL")
        return 0 if ok else 1
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    sys.exit(main())
