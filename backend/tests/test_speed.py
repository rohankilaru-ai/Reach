"""Speed hook, persona and vision downscale: key-free."""

from __future__ import annotations

import asyncio
import io

from intern.models.profile import Compat
from intern.providers.base import ProviderRequest

from reach import bridge, speed
from reach.contracts import Ask
from reach.ledger import CURRENT_TASK
from reach.tools import vision


def req(**kw) -> ProviderRequest:
    return ProviderRequest(model=kw.pop("model", "Qwen/Qwen3.8-27B"), messages=[], max_output_tokens=8192, **kw)


def test_simple_kinds_turn_reasoning_off_and_cap_output(monkeypatch):
    monkeypatch.delenv("REACH_THINK", raising=False)
    for kind in ("chat", "write_to_notes", "screen_question", None):
        out = speed.shape(req(compat=Compat(extra_body={"x": 1}), reasoning_effort="high"), kind)
        assert out.compat.extra_body == {"x": 1, "chat_template_kwargs": {"enable_thinking": False}}
        assert out.reasoning_effort is None
        assert out.max_output_tokens == speed.NO_THINK_CAP


def test_tool_heavy_kinds_and_reach_think_keep_reasoning(monkeypatch):
    monkeypatch.delenv("REACH_THINK", raising=False)
    for kind in ("document_question", "browser_task", "mac_control", "other"):
        assert speed.shape(req(), kind) is None
    monkeypatch.setenv("REACH_THINK", "1")
    assert speed.shape(req(), "chat") is None


def test_hook_only_touches_bridge_tasks(monkeypatch):
    monkeypatch.delenv("REACH_THINK", raising=False)
    hook = speed.SpeedHook()
    token = CURRENT_TASK.set("t-speed")
    try:
        assert asyncio.run(hook.before_provider_request(req(), None)) is None  # unknown task: unchanged
        speed.KINDS["t-speed"] = "chat"
        out = asyncio.run(hook.before_provider_request(req(), None))
        assert out.compat.extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    finally:
        speed.KINDS.pop("t-speed", None)
        CURRENT_TASK.reset(token)


def test_bridge_registers_the_hook_and_turns_memory_off():
    src = open(bridge.__file__).read()
    assert bridge.SPEED == "reach.speed:SpeedHook"
    assert "SPEED," in src and "memory_off=True" in src


def test_non_qwen_models_get_only_the_cap(monkeypatch):
    monkeypatch.delenv("REACH_THINK", raising=False)
    out = speed.shape(req(model="other/model"), "chat")
    assert out.compat.extra_body == {} and out.max_output_tokens == speed.NO_THINK_CAP


def test_persona_asks_for_short_plain_answers():
    p = bridge.prompt_for(Ask(text="hi"))
    assert "1 to 3 short plain sentences" in p
    assert "No markdown headings" in p and "no bold" in p
    assert "at most 5" in p and "Never print tool logs" in p


def test_vision_downscales_long_side_and_caps_tokens():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2560, 1440), "red").save(buf, "PNG")
    small = vision.downscale_png(buf.getvalue())
    assert max(Image.open(io.BytesIO(small)).size) == vision.MAX_SIDE
    tiny = io.BytesIO()
    Image.new("RGB", (100, 50), "red").save(tiny, "PNG")
    assert vision.downscale_png(tiny.getvalue()) == tiny.getvalue()
    assert vision.downscale_png(b"not a png") == b"not a png"
    assert vision.build_request(buf.getvalue(), "q")["max_tokens"] == 400


def test_shaped_request_reaches_the_deepinfra_body(monkeypatch):
    """The field the hook sets is what Intern's OpenAI-compatible provider sends (measured to disable reasoning)."""
    from intern.models.registry import Endpoint
    from intern.providers.openai_compat import OpenAICompatProvider

    monkeypatch.delenv("REACH_THINK", raising=False)
    p = OpenAICompatProvider(Endpoint(provider="deepinfra", model="Qwen/Qwen3.8-27B", base_url="https://x.invalid"))
    body = p.body(speed.shape(req(), "chat"))
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] == speed.NO_THINK_CAP and "reasoning_effort" not in body
