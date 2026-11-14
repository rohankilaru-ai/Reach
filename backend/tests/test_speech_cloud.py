"""Cloud voice, key-free: a fake HTTP layer stands in for DeepInfra."""

from __future__ import annotations

import io
import json
import wave

import pytest

from reach import speech_cloud as sc
from reach.contracts import SttIn, SttResult, TtsAudio, TtsIn, inbound_adapter, outbound_adapter


@pytest.fixture(autouse=True)
def reach_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("REACH_DIR", str(tmp_path / ".reach"))
    return tmp_path / ".reach"


def wav_bytes(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


def put(name: str, seconds: float = 1.0):
    p = sc.audio_dir() / name
    p.write_bytes(wav_bytes(seconds))
    return p


def log_entries(reach_tmp):
    return [json.loads(l) for l in (reach_tmp / "logs" / "drex.jsonl").read_text().splitlines()]


def test_transcribe_ok_and_logged(reach_tmp):
    p = put("a.wav")
    r = sc.transcribe(str(p), post=lambda path, model: {"text": " hello there ", "language": "en", "duration": 1.0})
    assert r["text"] == "hello there" and r["language"] == "en"
    e = log_entries(reach_tmp)[-1]
    assert e["type"] == "speech" and e["op"] == "stt" and e["ok"] and e["seconds"] == 1.0


def test_transcribe_retries_once_then_raises(reach_tmp):
    p = put("b.wav")
    calls = []

    def flaky(path, model):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError()
        return {"text": "ok"}

    assert sc.transcribe(str(p), post=flaky)["text"] == "ok" and len(calls) == 2

    def down(path, model):
        calls.append(1)
        raise RuntimeError("HTTP 503")

    calls.clear()
    with pytest.raises(RuntimeError):
        sc.transcribe(str(p), post=down)
    assert len(calls) == 2
    assert log_entries(reach_tmp)[-1]["ok"] is False


def test_paths_outside_audio_dir_refused(tmp_path, reach_tmp):
    outside = tmp_path / "x.wav"
    outside.write_bytes(wav_bytes(0.5))
    with pytest.raises(PermissionError):
        sc.transcribe(str(outside), post=lambda *a: {"text": "no"})
    with pytest.raises(PermissionError):
        sc.transcribe("../x.wav", post=lambda *a: {"text": "no"})


def test_audio_over_60s_refused(reach_tmp):
    p = put("long.wav", seconds=61)
    with pytest.raises(ValueError):
        sc.transcribe(str(p), post=lambda *a: {"text": "no"})


def test_synthesize_writes_under_audio_and_cleans_text(reach_tmp):
    seen = {}

    def fake(text, voice, model):
        seen.update(text=text, voice=voice, model=model)
        return wav_bytes(0.2), 0.05

    r = sc.synthesize("**Yes.** The total is 42 [E3] [1, 2]. See [docs](http://x).\n```code```", post=fake)
    assert seen["text"] == "Yes. The total is 42. See docs."
    assert seen["model"] == sc.TTS_MODEL and seen["voice"] == sc.TTS_VOICE
    out = sc.audio_dir() / r["path"].split("/")[-1]
    assert out.is_file() and r["path"].startswith(str(sc.audio_dir()))
    e = log_entries(reach_tmp)[-1]
    assert e["op"] == "tts" and e["chars"] == len(seen["text"]) and e["first_ms"] == 50


def test_text_capped_at_600():
    s = sc.spoken("word " * 400)
    assert len(s) <= 600
    s2 = sc.spoken("A sentence here. " * 60)
    assert len(s2) <= 600 and s2.endswith(".")


def test_synthesize_failure_logged(reach_tmp):
    def boom(*a):
        raise RuntimeError("HTTP 500")

    with pytest.raises(RuntimeError):
        sc.synthesize("hi", post=boom)
    assert log_entries(reach_tmp)[-1]["ok"] is False
    with pytest.raises(ValueError):
        sc.synthesize("[E1]", post=boom)


def test_wire_messages():
    m = inbound_adapter.validate_json('{"type":"stt","audio_path":"a.wav","format":"wav","id":"s1"}')
    assert isinstance(m, SttIn) and m.id == "s1"
    assert isinstance(inbound_adapter.validate_json('{"type":"tts","text":"hi"}'), TtsIn)
    assert isinstance(outbound_adapter.validate_json(SttResult(text="x", ms=3).model_dump_json()), SttResult)
    assert isinstance(outbound_adapter.validate_json(TtsAudio(error="HTTP 500").model_dump_json()), TtsAudio)


def test_server_speech_handler(reach_tmp, monkeypatch):
    import asyncio

    from reach.ipc import Server

    monkeypatch.setattr(sc, "_post_stt", lambda path, model: {"text": "hi", "duration": 1.0})
    monkeypatch.setattr(sc, "transcribe", lambda path: {"text": "hi", "ms": 5, "language": "en"})
    srv = Server.__new__(Server)
    q: asyncio.Queue = asyncio.Queue()
    asyncio.run(srv._speech(SttIn(audio_path="a.wav", id="k"), q))
    r = json.loads(q.get_nowait())
    assert r == {"type": "stt_result", "text": "hi", "ms": 5, "language": "en", "error": None, "id": "k"}
    asyncio.run(srv._speech(SttIn(audio_path="/etc/passwd"), q))
    monkeypatch.undo()
    monkeypatch.setenv("REACH_DIR", str(reach_tmp))
    asyncio.run(srv._speech(SttIn(audio_path="/etc/passwd"), q))
    r = json.loads(q.get_nowait())
    r = json.loads(q.get_nowait())
    assert r["type"] == "stt_result" and r["error"].startswith("refused")
