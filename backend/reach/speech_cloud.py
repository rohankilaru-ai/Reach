"""Cloud voice: speech-to-text (Whisper large-v3-turbo) and text-to-speech (Kokoro-82M) on DeepInfra.

Both go to DeepInfra's OpenAI-compatible endpoints (verified 2026-11-14):
  POST https://api.deepinfra.com/v1/openai/audio/transcriptions  multipart: file, model, response_format=verbose_json
  POST https://api.deepinfra.com/v1/openai/audio/speech          json: model, input, voice, response_format=wav

The key (`DEEPINFRA_API_KEY`) is loaded inside this process only, from `$REACH_ENV_FILE`, the repo `.env` or
`$INTERN_HOME/.env`, and is never printed. Audio files live under `.reach/audio/` only; the client deletes them after
use. Every call is logged to the Drex log as `{"type": "speech", "op": "stt"|"tts", ...}`.
"""

from __future__ import annotations

import os
import re
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Callable

from .ledger import append_tool_entry
from .paths import REPO_ROOT, reach_dir

BASE = "https://api.deepinfra.com/v1/openai"
KEY = "DEEPINFRA_API_KEY"
STT_MODEL = os.environ.get("REACH_STT_MODEL", "openai/whisper-large-v3-turbo")
TTS_MODEL = os.environ.get("REACH_TTS_MODEL", "hexgrad/Kokoro-82M")
TTS_VOICE = os.environ.get("REACH_TTS_VOICE", "af_heart")
STT_TIMEOUT_S = 6.0
TTS_TIMEOUT_S = 10.0
MAX_TEXT = 600
MAX_AUDIO_S = 60.0
MAX_AUDIO_BYTES = 8 * 2**20
# Estimates only (approximate DeepInfra list prices; not verified against the bill): Whisper turbo per audio minute,
# Kokoro per character.
STT_USD_PER_MIN = 0.0002
TTS_USD_PER_CHAR = 0.62 / 1_000_000


def load_key() -> None:
    if os.environ.get(KEY):
        return
    candidates = []
    if os.environ.get("REACH_ENV_FILE"):
        candidates.append(Path(os.environ["REACH_ENV_FILE"]))
    candidates.append(REPO_ROOT / ".env")
    if os.environ.get("INTERN_HOME"):
        candidates.append(Path(os.environ["INTERN_HOME"]) / ".env")
    for env in candidates:
        try:
            lines = env.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == KEY:
                os.environ.setdefault(KEY, v.strip().strip('"').strip("'"))
                return


def _headers() -> dict[str, str]:
    load_key()
    key = os.environ.get(KEY)
    if not key:
        raise RuntimeError("DEEPINFRA_API_KEY is not set")
    return {"Authorization": f"Bearer {key}"}


def audio_dir() -> Path:
    d = reach_dir() / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_audio(path: str) -> Path:
    """A WAV under .reach/audio/, at most 60 s. Raises PermissionError / FileNotFoundError / ValueError."""
    root = audio_dir().resolve()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if not p.is_relative_to(root):
        raise PermissionError("refused: stt reads only files under .reach/audio/")
    if not p.is_file():
        raise FileNotFoundError("no such audio file")
    if p.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("audio too large")
    if p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as w:
                secs = w.getnframes() / float(w.getframerate() or 1)
        except (wave.Error, EOFError) as e:
            raise ValueError(f"unreadable wav: {e}") from None
        if secs > MAX_AUDIO_S:
            raise ValueError(f"audio longer than {int(MAX_AUDIO_S)} s")
    return p


def spoken(text: str, limit: int = MAX_TEXT) -> str:
    """Text as it should be spoken: no code, links, citation brackets or markdown; at most `limit` characters."""
    s = re.sub(r"```[\s\S]*?```", " ", text)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[(?:[A-Z]?\d+(?:[,;\s]+[A-Z]?\d+)*|source[^\]]*|p\.? ?\d+)\]", "", s, flags=re.I)
    s = re.sub(r"(?m)^\s{0,3}(#{1,6}|[-*+]|\d+\.|>)\s+", "", s)
    s = re.sub(r"[*_`~]+", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r" ([.,;:!?])", r"\1", s).strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut >= limit // 3:
        return head[: cut + 1]
    sp = head.rfind(" ")
    return head[:sp] if sp > 0 else head


# ---- HTTP layer (tests inject fakes) ----


def _post_stt(path: Path, model: str) -> dict[str, Any]:
    import httpx

    r = httpx.post(
        f"{BASE}/audio/transcriptions",
        files={"file": (path.name, path.read_bytes(), "audio/wav")},
        data={"model": model, "response_format": "verbose_json"},
        headers=_headers(),
        timeout=STT_TIMEOUT_S,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def _post_tts(text: str, voice: str | None, model: str) -> tuple[bytes, float]:
    """Returns (wav bytes, seconds to the first audio byte)."""
    import httpx

    body: dict[str, Any] = {"model": model, "input": text, "response_format": "wav"}
    if voice:
        body["voice"] = voice
    t0 = time.monotonic()
    first = None
    chunks: list[bytes] = []
    with httpx.stream("POST", f"{BASE}/audio/speech", json=body, headers=_headers(), timeout=TTS_TIMEOUT_S) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        for ch in r.iter_bytes():
            if first is None:
                first = time.monotonic() - t0
            chunks.append(ch)
    return b"".join(chunks), first or (time.monotonic() - t0)


def _err(e: Exception) -> str:
    s = str(e)
    return s[:80] if s.startswith(("HTTP", "DEEPINFRA", "refused", "audio", "unreadable", "no such")) else type(e).__name__


def transcribe(path: str | Path, model: str | None = None, post: Callable = _post_stt) -> dict[str, Any]:
    """{text, language, ms, seconds}. 6 s timeout, one retry. Raises on failure (logged)."""
    model = model or STT_MODEL
    p = resolve_audio(str(path))
    t0 = time.monotonic()
    last: Exception | None = None
    for _attempt in range(2):
        try:
            data = post(p, model)
            break
        except (PermissionError, FileNotFoundError, ValueError):
            raise
        except Exception as e:  # timeout / HTTP error: one retry
            last = e
    else:
        ms = int((time.monotonic() - t0) * 1000)
        append_tool_entry("speech", op="stt", model=model, ok=False, ms=ms, error=_err(last))
        raise last  # type: ignore[misc]
    ms = int((time.monotonic() - t0) * 1000)
    secs = data.get("duration")
    text = (data.get("text") or "").strip()
    append_tool_entry(
        "speech", op="stt", model=model, ok=True, ms=ms, seconds=secs,
        cost_usd=round(secs / 60 * STT_USD_PER_MIN, 7) if isinstance(secs, (int, float)) else None,
    )
    return {"text": text, "language": data.get("language"), "ms": ms, "seconds": secs}


def synthesize(text: str, voice: str | None = None, model: str | None = None,
               post: Callable = _post_tts) -> dict[str, Any]:
    """Writes a WAV under .reach/audio/ and returns {path, format, ms, first_ms, chars}. Raises on failure."""
    model = model or TTS_MODEL
    voice = voice or (TTS_VOICE if model == TTS_MODEL else None)
    clean = spoken(text)
    if not clean:
        raise ValueError("nothing to say")
    t0 = time.monotonic()
    try:
        audio, first = post(clean, voice, model)
    except Exception as e:
        append_tool_entry("speech", op="tts", model=model, ok=False, ms=int((time.monotonic() - t0) * 1000),
                          chars=len(clean), error=_err(e))
        raise
    ms = int((time.monotonic() - t0) * 1000)
    out = audio_dir() / f"tts-{uuid.uuid4().hex[:12]}.wav"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(audio)
    append_tool_entry("speech", op="tts", model=model, voice=voice, ok=True, ms=ms, first_ms=int(first * 1000),
                      chars=len(clean), cost_usd=round(len(clean) * TTS_USD_PER_CHAR, 7))
    return {"path": str(out), "format": "wav", "ms": ms, "first_ms": int(first * 1000), "chars": len(clean)}
