"""`reach serve | ask | log | replay | stop`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import websockets

from .contracts import Abort, Ask, Hello, Selection
from .ledger import ReceiptBuilder, format_log_line
from .paths import drex_log, reach_dir, run_dir


def _print_receipt(r: dict) -> None:
    keys = ("task_id", "status", "reason", "tools", "refused", "system1_asks", "tokens", "est_cost_usd", "duration_ms", "ndi_calls", "outcome", "browser")
    print("\n--- receipt ---")
    print(json.dumps({k: r.get(k) for k in keys}, indent=2))


def _print_card(c: dict) -> None:
    where = f" p.{c['page']}" if c.get("page") else ""
    print(f"\n[evidence {c.get('id')}] {c.get('source')}{where} (level {c.get('level')})\n  \"{c.get('quote')}\"", flush=True)


def _render(event: dict) -> None:
    kind = event.get("type")
    if kind == "text_delta":
        sys.stdout.write(event["text"])
        sys.stdout.flush()
    elif kind == "tool_call_start":
        print(f"\n[tool {event['name']}]", flush=True)
    elif kind == "decision":
        print(f"\n[drex {event.get('purpose')}]", file=sys.stderr, flush=True)


def cmd_serve(args) -> int:
    from .ipc import Server

    if args.replay:
        from .bridge import ReplayBridge

        bridge = ReplayBridge(Path(args.replay))
    else:
        from .bridge import InternBridge

        bridge = InternBridge()
    try:
        asyncio.run(Server(bridge).serve())
    except KeyboardInterrupt:
        pass
    return 0


async def _connect():
    from .ipc import read_run_files

    info = read_run_files()
    if info is None:
        return None
    port, token = info
    try:
        ws = await websockets.connect(f"ws://127.0.0.1:{port}", max_size=8 * 2**20, open_timeout=3)
    except (OSError, asyncio.TimeoutError):
        return None
    await ws.send(Hello(token=token).model_dump_json())
    await ws.recv()
    return ws


async def _ensure_server():
    ws = await _connect()
    if ws is not None:
        return ws
    from .ipc import clear_run_files

    clear_run_files()  # stale files from a dead server
    logs = reach_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "server.log").open("a") as log:
        subprocess.Popen(
            [sys.executable, "-m", "reach.cli", "serve"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        await asyncio.sleep(0.3)
        ws = await _connect()
        if ws is not None:
            return ws
    raise SystemExit("reach: server did not start; see .reach/logs/server.log")


async def _ask(text: str, selection: str | None) -> int:
    ws = await _ensure_server()
    ask = Ask(text=text, selection=Selection(text=selection) if selection else None)
    await ws.send(ask.model_dump_json())
    task_id = None
    loop = asyncio.get_running_loop()

    def on_sigint() -> None:
        print("\n[abort requested]", file=sys.stderr, flush=True)
        loop.create_task(ws.send(Abort(task_id=task_id).model_dump_json()))

    loop.add_signal_handler(signal.SIGINT, on_sigint)
    async for raw in ws:
        msg = json.loads(raw)
        if msg["type"] == "accepted":
            task_id = msg["task_id"]
        elif msg["type"] == "event":
            _render(msg["event"])
        elif msg["type"] == "evidence":
            _print_card(msg)
        elif msg["type"] == "receipt":
            _print_receipt(msg)
            await ws.close()
            return 0 if msg["status"] == "ok" else 1
        elif msg["type"] == "error":
            print(f"reach: {msg['message']}", file=sys.stderr)
    return 1


def cmd_ask(args) -> int:
    return asyncio.run(_ask(" ".join(args.text), args.selection))


def cmd_log(args) -> int:
    path = drex_log()
    if not path.exists():
        print("no Drex log yet")
        return 0
    lines = path.read_text().splitlines()
    for line in lines[-args.n :] if args.n else lines:
        print(format_log_line(json.loads(line)))
    return 0


def cmd_replay(args) -> int:
    builder = ReceiptBuilder("replay")
    for line in Path(args.file).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        builder.feed(event)
        _render(event)
        if args.delay:
            time.sleep(args.delay)
    _print_receipt(builder.receipt().model_dump())
    return 0


def cmd_add(args) -> int:
    from . import evidence
    from .tools import ndi

    code = 0
    for f in args.files:
        path = Path(f)
        if not path.is_file():
            print(f"reach: no such file {f}", file=sys.stderr)
            code = 1
            continue
        t0 = time.time()
        r = evidence.add_document(path, ndi.parse_pdf)
        ids = r["passages"]
        span = f"{ids[0]}..{ids[-1]}" if ids else "none"
        how = "cached (same sha256)" if r["cached"] else f"parsed by NDI in {time.time() - t0:.1f}s"
        print(f"added {r['name']}: {len(ids)} passages {span}, {how}, sha256 {r['sha256'][:12]}")
    return code


def cmd_shelf(args) -> int:
    from . import evidence

    items = evidence.shelf()
    if not items:
        print("shelf is empty (pin a card with: reach pin E3)")
    for i in items:
        where = f" p.{i['page']}" if i["page"] else ""
        print(f"[{i['id']}] {i['file']}{where}: \"{i['quote']}\"")
    return 0


def cmd_pin(args) -> int:
    from . import evidence

    ok = evidence.pin(args.id, args.quote)
    print(f"pinned {args.id}" if ok else f"reach: no passage {args.id}")
    return 0 if ok else 1


def cmd_unpin(args) -> int:
    from . import evidence

    print(f"unpinned {args.id}" if evidence.unpin(args.id) else f"{args.id} was not pinned")
    return 0


def cmd_stop(args) -> int:
    try:
        pid = json.loads((run_dir() / "server.json").read_text())["pid"]
        os.kill(pid, signal.SIGTERM)
        print(f"stopped server pid {pid}")
    except (OSError, ValueError, KeyError):
        print("no server running")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reach")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the local WebSocket server")
    s.add_argument("--replay", help="answer every ask by replaying this event file (no keys)")
    s.set_defaults(fn=cmd_serve)
    a = sub.add_parser("ask", help="ask through the local server (started on demand)")
    a.add_argument("text", nargs="+")
    a.add_argument("--selection", help="selected text to ask about")
    a.set_defaults(fn=cmd_ask)
    lg = sub.add_parser("log", help="print the Drex log, one line per decision/nudge/guard")
    lg.add_argument("-n", type=int, default=0, help="only the last N entries")
    lg.set_defaults(fn=cmd_log)
    r = sub.add_parser("replay", help="replay a recorded event stream")
    r.add_argument("file")
    r.add_argument("--delay", type=float, default=0.0)
    r.set_defaults(fn=cmd_replay)
    ad = sub.add_parser("add", help="parse documents with NDI into the evidence store (cached by sha256)")
    ad.add_argument("files", nargs="+")
    ad.set_defaults(fn=cmd_add)
    sh = sub.add_parser("shelf", help="list pinned evidence (offered to the next ask)")
    sh.set_defaults(fn=cmd_shelf)
    pn = sub.add_parser("pin", help="pin a passage to the shelf")
    pn.add_argument("id")
    pn.add_argument("--quote", help="the quote to keep with it")
    pn.set_defaults(fn=cmd_pin)
    up = sub.add_parser("unpin", help="remove a passage from the shelf")
    up.add_argument("id")
    up.set_defaults(fn=cmd_unpin)
    st = sub.add_parser("stop", help="stop the background server")
    st.set_defaults(fn=cmd_stop)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
