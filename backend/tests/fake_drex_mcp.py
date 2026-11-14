"""A fake Drex pairing MCP server (stdio, newline JSON-RPC) for tests. Mode from FAKE_MODE."""

import json
import os
import sys
import time

MODE = os.environ.get("FAKE_MODE", "done")


def out(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def result_for(mode):
    if mode == "done":
        r = {"status": "done", "message": None, "answer": "Your top video has 1,234 views.",
             "end_page": {"url": "https://studio.youtube.com/", "title": "Studio"}, "cost_usd": 0.004,
             "screenshot": "data:image/png;base64," + "A" * 200_000}
        return {"content": [{"type": "text", "text": json.dumps(r)},
                            {"type": "image", "mimeType": "image/png", "data": "A" * 200_000}]}
    if mode in ("partial", "needs_user"):
        r = {"status": mode, "message": "A sign-in screen needs the person", "answer": "Stopped at the login page",
             "end_page": {"url": "https://accounts.google.com/"}, "cost_usd": 0.001}
        return {"content": [{"type": "text", "text": json.dumps(r)}]}
    if mode == "not_connected":
        return {"isError": True, "content": [{"type": "text", "text": "The Drex extension isn't connected."}]}
    if mode == "echo_code":
        return {"isError": True, "content": [{"type": "text", "text": "code is " + os.environ.get("DREX_PAIRING_CODE", "")}]}
    raise SystemExit(1)


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        if os.environ.get("FAKE_PID_FILE"):
            with open(os.environ["FAKE_PID_FILE"], "w") as f:
                f.write(str(os.getpid()))
        out({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                                           "serverInfo": {"name": "drex", "version": "fake"}}})
    elif method == "tools/list":
        out({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "drex_run"}, {"name": "drex_tabs"}]}})
    elif method == "tools/call":
        if MODE == "hang":
            time.sleep(600)
        out({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}})  # noise to ignore
        out({"jsonrpc": "2.0", "id": msg["id"], "result": result_for(MODE)})
