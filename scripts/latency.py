"""Latency harness: run fixed asks through a running `reach serve` and report per-stage times.

Usage (from backend/):  uv run python ../scripts/latency.py --n 3 --out ../docs/evidence/latency-before.md
Needs a running server (same REACH_DIR) and real keys in the server's environment; this script reads no keys.
Times come from the client's clock at each event's arrival, relative to sending the ask.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import struct
import sys
import time
import zlib
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from reach.contracts import Ask, Hello, Selection  # noqa: E402
from reach.ipc import read_run_files  # noqa: E402
from reach.paths import reach_dir  # noqa: E402


def test_png(path: Path, w: int = 1600, h: int = 900) -> Path:
    """A plain PNG with a red block on the left half and a blue block on the right (no dependencies)."""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            row += bytes((220, 30, 30) if x < w // 2 else (30, 60, 220))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def asks() -> list[tuple[str, Ask]]:
    png = test_png(reach_dir() / "captures" / "latency-test.png")
    return [
        ("a chat", Ask(text="hey, how's it going?")),
        ("b math", Ask(text="what is 17 times 23?")),
        ("c document", Ask(text="In the Transformer paper, what is the dimension d_model of the base model? Quote it.")),
        ("d notes", Ask(text="Add a note: latency test, buy oat milk.")),
        ("e screen", Ask(text="What colours are in this region, left and right?",
                         selection=Selection(image_path=str(png), region=[0, 0, 1600, 900]))),
        ("f say ready", Ask(text="say ready")),
    ]


async def run_one(ask: Ask) -> dict:
    port, token = read_run_files()
    async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=8 * 2**20) as ws:
        await ws.send(Hello(token=token).model_dump_json())
        await ws.recv()
        t0 = time.monotonic()
        await ws.send(ask.model_dump_json())
        st: dict = {"ttft": None, "intent": None, "tools": [], "turns": 0, "text": "", "reasoning_chars": 0}
        starts: dict[str, float] = {}
        async for raw in ws:
            now = time.monotonic() - t0
            msg = json.loads(raw)
            if msg["type"] == "accepted":
                st["task_id"] = msg.get("task_id")
            elif msg["type"] == "event":
                e = msg["event"]
                t = e.get("type")
                if t == "text_delta":
                    st["ttft"] = st["ttft"] if st["ttft"] is not None else now
                    st["text"] += e.get("text", "")
                elif t == "decision" and e.get("purpose") == PURPOSE_INTENT:
                    st["intent"] = now
                elif t == "turn_start":
                    st["turns"] += 1
                elif t == "tool_call_start":
                    starts[e.get("call_id")] = now
                elif t == "tool_call_end":
                    s = starts.get(e.get("call_id"), now)
                    st["tools"].append(f"{e.get('name')}:{e.get('status')}:{now - s:.1f}s")
                elif t == "message" and (e.get("message") or {}).get("role") == "assistant":
                    st["reasoning_chars"] += len((e["message"].get("reasoning") or ""))
            elif msg["type"] == "receipt":
                st["total"] = now
                st["status"] = msg.get("status")
                st["final"] = (msg.get("final_text") or st["text"])[:160].replace("\n", " ")
                return st
    return st


PURPOSE_INTENT = "intent"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    rows, raw = [], []
    for name, ask in asks():
        if a.only and not name.startswith(tuple(a.only.split(","))):
            continue
        runs = []
        for i in range(a.n):
            r = await run_one(ask)
            runs.append(r)
            raw.append((name, i, r))
            print(name, i, json.dumps({k: r.get(k) for k in ("task_id", "ttft", "intent", "total", "turns",
                                                             "reasoning_chars", "tools", "final")}),
                  flush=True)
        med = lambda k: statistics.median([r[k] for r in runs if r.get(k) is not None] or [float("nan")])  # noqa: E731
        rows.append((name, med("ttft"), med("intent"), med("total"), med("turns"), med("reasoning_chars"),
                     runs[-1].get("final", "")))
    lines = [f"# Latency {a.label}".rstrip(), "",
             f"N={a.n} per ask, real server, client clock. Times in seconds (median).", "",
             "| ask | first token | Drex intent | total | turns | reasoning chars | last answer (trimmed) |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, ttft, it, tot, turns, rc, fin in rows:
        lines.append(f"| {name} | {ttft:.1f} | {it:.1f} | {tot:.1f} | {turns:.0f} | {rc:.0f} | {fin.replace('|', '/')} |")
    lines += ["", "## Runs", ""]
    for name, i, r in raw:
        lines.append(f"- {name} #{i}: total {r.get('total', float('nan')):.1f}s, first token "
                     f"{(r['ttft'] if r['ttft'] is not None else float('nan')):.1f}s, turns {r['turns']}, "
                     f"tools {', '.join(r['tools']) or '-'}, status {r.get('status')}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:12 + len(rows)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
