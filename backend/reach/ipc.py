"""Localhost WebSocket server speaking the contracts.py protocol."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from pathlib import Path

import websockets
from pydantic import ValidationError

from .contracts import (
    Abort, Accepted, AddDoc, Added, Ask, ErrorMsg, EventMsg, Hello, Hypotheses, PointerIn, Ready, Select, Transcript,
    Utterance,
    SttIn, SttResult, TtsAudio, TtsIn,
    inbound_adapter,
)
from .bridge import evidence_cards
from .ledger import count_ndi
from .relay import Relay, log_line
from .paths import drex_log, reach_dir, run_dir


GROUND_DEADLINE_S = 150


def _hands_msg(raw: str | bytes) -> bool | None:
    """{"type": "hands", "on": true|false} -> the bool; anything else -> None."""
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if isinstance(d, dict) and d.get("type") == "hands" and isinstance(d.get("on"), bool):
        return d["on"]
    return None


HANDS_TYPES = ("hands_record", "hands_forget", "hands_gesture")


def _hands_extra(raw: str | bytes) -> dict | None:
    """hands_record / hands_forget / hands_gesture (custom gestures, EXPERIMENTAL) as a dict; else None."""
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("type") in HANDS_TYPES else None


def log_evidence(card) -> None:
    p = reach_dir() / "logs" / "evidence.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(card.model_dump_json() + "\n")


def write_run_files(port: int, token: str) -> None:
    d = run_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tok = d / "token"
    fd = os.open(tok, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(tok, 0o600)
    (d / "server.json").write_text(json.dumps({"host": "127.0.0.1", "port": port, "pid": os.getpid()}))


def clear_run_files() -> None:
    for name in ("server.json", "token"):
        (run_dir() / name).unlink(missing_ok=True)


def read_run_files() -> tuple[int, str] | None:
    d = run_dir()
    try:
        info = json.loads((d / "server.json").read_text())
        return int(info["port"]), (d / "token").read_text().strip()
    except (OSError, ValueError, KeyError):
        return None


class Server:
    def __init__(self, bridge) -> None:
        from .bridge import Task

        self.bridge = bridge
        self.Task = Task
        self.token = secrets.token_urlsafe(32)
        self.tasks: dict[str, Task] = {}
        self.relay = Relay()
        self.router = None  # S8a: commands.DrexRouter by default; tests inject a fake
        self._routed = 0
        self.addressed_post = None  # addressed.py: commands.http_post by default; tests inject a fake Drex
        from .tools import guide

        guide.set_emitter(self.relay.broadcast)  # point_at's step pointers go to overlay clients
        self.loop: asyncio.AbstractEventLoop | None = None

    def _hands_emit(self, st: dict) -> None:
        """hands.py's emitter: the TRUE hands state to every overlay client (thread-safe; the watcher is a thread)."""
        msg = json.dumps({"type": "hands_state", **st})
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            if asyncio.get_running_loop() is loop:
                self.relay.broadcast(msg)
                return
        except RuntimeError:
            pass
        loop.call_soon_threadsafe(self.relay.broadcast, msg)

    async def handle(self, ws) -> None:
        try:
            first = inbound_adapter.validate_json(await asyncio.wait_for(ws.recv(), 10))
        except (ValidationError, asyncio.TimeoutError, websockets.ConnectionClosed):
            await ws.close(4400, "expected hello")
            return
        if not isinstance(first, Hello) or not secrets.compare_digest(first.token, self.token):
            await ws.close(4401, "bad token")
            return
        await ws.send(Ready().model_dump_json())

        out: asyncio.Queue[str | None] = asyncio.Queue()

        async def pump() -> None:
            while (msg := await out.get()) is not None:
                try:
                    await ws.send(msg)
                except websockets.ConnectionClosed:
                    return

        pump_task = asyncio.create_task(pump())
        role, key = first.role, id(ws)
        self.relay.join(key, role, out)
        if role == "overlay":  # the current truth on connect: the menu and pill never guess
            from . import hands

            if self.loop is None:
                self.loop = asyncio.get_running_loop()
                hands.set_emitter(self._hands_emit)
            out.put_nowait(json.dumps({"type": "hands_state", **hands.status()}))
        mine: dict[str, object] = {}
        running: set[asyncio.Task] = set()
        try:
            async for raw in ws:
                if isinstance(raw, str) and '"hands"' in raw and (hm := _hands_msg(raw)) is not None:  # hands mode on/off (not an agent action)
                    from . import hands

                    if self.loop is None:
                        self.loop = asyncio.get_running_loop()
                        hands.set_emitter(self._hands_emit)
                    st = await asyncio.to_thread(hands.set_on, hm)
                    out.put_nowait(json.dumps({"type": "hands_state", **st}))  # also answers a no-op repeat
                    continue
                if isinstance(raw, str) and '"hands_' in raw and (hx := _hands_extra(raw)) is not None:
                    from . import hands

                    if hx["type"] == "hands_gesture":   # hands mode -> every overlay (never back to the sender)
                        if role and role != "overlay" and (ev := hands.gesture_event(hx)):
                            n = self.relay._broadcast(key, ev)
                            log_line({"kind": "hands_gesture", "from": role, "name": hx["name"], "relayed_to": n})
                        else:
                            out.put_nowait(ErrorMsg(message="hands_gesture rejected").model_dump_json())
                        continue
                    try:
                        if hx["type"] == "hands_record":
                            st = await asyncio.to_thread(hands.request_record, hx.get("name"), hx.get("action"))
                        else:
                            st = await asyncio.to_thread(hands.forget, hx.get("name"))
                    except ValueError as e:
                        out.put_nowait(ErrorMsg(message=str(e)).model_dump_json())
                        continue
                    out.put_nowait(json.dumps({"type": "hands_state", **st}))
                    continue
                try:
                    msg = inbound_adapter.validate_json(raw)
                except ValidationError as e:
                    out.put_nowait(ErrorMsg(message=f"bad message: {e.errors()[0]['msg']}").model_dump_json())
                    continue
                if isinstance(msg, Ask):
                    from .tools import guide

                    guide.cancel()  # a new ask stops any guide pointers still pending
                    ask, sel_from = self.relay.attach(msg)
                    task = self.Task(ask)
                    task.selection_source = "pinch" if sel_from and sel_from.startswith("pinch") else None
                    log_line({"kind": "ask", "task_id": task.id, "from": role, "selection": sel_from,
                              "chars": len((ask.selection.text or "") if ask.selection else "")})
                    self.tasks[task.id] = task
                    mine[task.id] = task
                    out.put_nowait(Accepted(task_id=task.id).model_dump_json())
                    out.put_nowait(Transcript(task_id=task.id, role="user", text=msg.text).model_dump_json())
                    t = asyncio.create_task(self._run(task, out, mine))
                    running.add(t)
                    t.add_done_callback(running.discard)
                elif isinstance(msg, Abort):
                    if msg.task_id:
                        targets = [mine[msg.task_id]] if msg.task_id in mine else []
                    else:  # no id: only this client's own tasks
                        targets = list(mine.values())
                    for task in targets:
                        task.abort()
                    from .tools import guide

                    guide.cancel(msg.task_id)  # the guide outlives its ask's run, so cancel by id (or all)
                elif isinstance(msg, (Select, PointerIn)):
                    fn = self.relay.select if isinstance(msg, Select) else self.relay.pointer
                    if err := fn(key, role, msg):
                        out.put_nowait(ErrorMsg(message=err).model_dump_json())
                elif isinstance(msg, AddDoc):
                    t = asyncio.create_task(self._add(msg, out))
                    running.add(t)
                    t.add_done_callback(running.discard)
                elif isinstance(msg, (SttIn, TtsIn)):
                    t = asyncio.create_task(self._speech(msg, out))
                    running.add(t)
                    t.add_done_callback(running.discard)
                elif isinstance(msg, Hypotheses):
                    t = asyncio.create_task(self._route_hypotheses(key, msg, out))
                    running.add(t)
                    t.add_done_callback(running.discard)
                elif isinstance(msg, Utterance):  # hands-free speech: is it addressed to the assistant? never acts
                    t = asyncio.create_task(self._addressed(msg, out))
                    running.add(t)
                    t.add_done_callback(running.discard)
        finally:
            self.relay.leave(key)
            for task in list(mine.values()):  # a client that goes away takes its running tasks with it
                if task.id in self.tasks:
                    task.abort()
            if running:
                await asyncio.wait(running, timeout=15)
            out.put_nowait(None)
            await pump_task

    async def _run(self, task, out: asyncio.Queue, mine: dict | None = None) -> None:
        def sink(task_id: str, event: dict) -> None:
            out.put_nowait(EventMsg(task_id=task_id, event=event).model_dump_json())

        from .ledger import CURRENT_OUT

        CURRENT_OUT.set(out.put_nowait)  # Do mode: tools can show the ghost pointer before acting
        try:
            receipt = await self.bridge.run(task, sink)
            stopped = False  # aborted while grounding (an abort during bridge.run is already in the receipt)
            if receipt.final_text and getattr(self.bridge, "grounds", True) and not task.aborted:
                try:
                    cards = await self._ground(task, receipt.final_text)
                except Exception:  # the evidence store is optional; the receipt always ships
                    cards = []
                if task.aborted:  # stopped while grounding: no cards, and never the earlier success
                    stopped, cards = True, []
                    update = {"status": "aborted", "reason": "aborted"}
                    if receipt.outcome is not None:
                        update["outcome"] = "aborted"
                    receipt = receipt.model_copy(update=update)
                for card in cards:
                    try:
                        log_evidence(card)
                    except Exception:  # an unwritable log must not lose the card or the receipt
                        pass
                    out.put_nowait(card.model_dump_json())
                try:
                    receipt.ndi_calls = count_ndi(drex_log(), task.id)
                except Exception:
                    pass
            receipt.selection_source = getattr(task, "selection_source", None)
            if receipt.final_text and not stopped:
                out.put_nowait(
                    Transcript(task_id=task.id, role="assistant", text=receipt.final_text).model_dump_json()
                )
            out.put_nowait(receipt.model_dump_json())
        finally:
            self.tasks.pop(task.id, None)
            if mine is not None:
                mine.pop(task.id, None)

    async def _ground(self, task, answer: str) -> list:
        """Evidence cards with NDI grounding. Stops starting ground jobs once the ask is aborted or the deadline
        passes, and returns at once on abort (an in-flight ground call finishes in its thread and starts no other)."""
        stop = threading.Event()
        deadline = time.monotonic() + GROUND_DEADLINE_S

        def should_stop() -> bool:
            return stop.is_set() or task.aborted or time.monotonic() > deadline

        fut = asyncio.ensure_future(asyncio.to_thread(evidence_cards, task.id, answer, True, should_stop))
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())  # never "exception was never retrieved"
        cards = None
        try:
            while not fut.done() and not task.aborted and time.monotonic() <= deadline:
                await asyncio.wait({fut}, timeout=0.2)
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                cards = fut.result()
        finally:
            stop.set()
        if task.aborted:
            return []
        if cards is None:  # ground slow or down: level-1/2 cards still ship
            cards = evidence_cards(task.id, answer, do_ground=False)
        return cards

    async def _route_hypotheses(self, sender: int, msg: Hypotheses, out: asyncio.Queue) -> None:
        """EXPERIMENTAL (S8a): route N-best hypotheses with the 12-command router (logged as purpose command_route),
        answer the sender and relay the result to overlay clients. An `uncertain` result has uncertain=True and must
        never act."""
        from . import commands

        try:
            r = await asyncio.to_thread(commands.route, hypotheses=list(msg.hypotheses), router=self.router)
        except Exception as e:
            out.put_nowait(ErrorMsg(message=f"route failed: {type(e).__name__}").model_dump_json())
            return
        self._routed += 1
        probs = r.probabilities or {}
        prob = probs.get(r.command, max(probs.values(), default=0.0))
        result = Transcript(
            task_id=f"{msg.source}-{self._routed}", role="user", text=msg.hypotheses[0], command=r.command,
            probability=round(float(prob), 4), uncertain=r.command == commands.UNCERTAIN, reason=r.reason,
            source=msg.source, experimental=msg.experimental,
        ).model_dump_json()
        out.put_nowait(result)
        self.relay._broadcast(sender, result)

    async def _addressed(self, msg: Utterance, out: asyncio.Queue) -> None:
        from . import addressed
        from .contracts import Addressed

        wake = bool(msg.wake_matched) or addressed.wake_matched(msg.text)
        r = await addressed.check_async(msg.text, wake, **({"post": self.addressed_post} if self.addressed_post else {}))
        try:
            addressed.log(r)
        except Exception:
            pass
        out.put_nowait(Addressed(**addressed.reply(r)).model_dump_json())

    async def _speech(self, msg, out: asyncio.Queue) -> None:
        """Cloud voice (speech_cloud): `stt` -> `stt_result`, `tts` -> `tts_audio`. Failures come back as `error`."""
        from . import speech_cloud

        if isinstance(msg, SttIn):
            try:
                r = await asyncio.to_thread(speech_cloud.transcribe, msg.audio_path)
                res = SttResult(text=r["text"], ms=r["ms"], language=r.get("language"), id=msg.id)
            except Exception as e:
                res = SttResult(error=speech_cloud._err(e), id=msg.id)
        else:
            try:
                r = await asyncio.to_thread(speech_cloud.synthesize, msg.text, msg.voice)
                res = TtsAudio(path=r["path"], format=r["format"], ms=r["ms"], id=msg.id)
            except Exception as e:
                res = TtsAudio(error=speech_cloud._err(e), id=msg.id)
        out.put_nowait(res.model_dump_json())

    async def _add(self, msg: AddDoc, out: asyncio.Queue) -> None:
        from . import evidence
        from .tools import ndi

        try:
            r = await asyncio.to_thread(evidence.add_document, Path(msg.path), ndi.parse_pdf)
            out.put_nowait(
                Added(name=r["name"], sha256=r["sha256"], cached=r["cached"], passages=len(r["passages"])).model_dump_json()
            )
        except Exception as e:
            out.put_nowait(ErrorMsg(message=f"add failed: {type(e).__name__}").model_dump_json())

    async def serve(self, port: int = 0) -> None:
        try:  # privacy: screen captures live at most an hour
            from .tools.screen import prune_captures

            prune_captures()
        except Exception:
            pass
        async with websockets.serve(self.handle, "127.0.0.1", port, max_size=8 * 2**20) as server:
            bound = server.sockets[0].getsockname()[1]
            write_run_files(bound, self.token)
            try:
                await asyncio.Future()
            finally:
                clear_run_files()
