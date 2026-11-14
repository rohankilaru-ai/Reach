import asyncio
from types import SimpleNamespace as NS

from reach.tools import cloud_browser as cb


def fake_run(status="succeeded", kind="completed", reason="", answer="42", cost=0.003):
    return NS(status=status, outcome=NS(kind=kind, reason=reason), usage=NS(cost_usd=cost), answer_text=answer,
              answer=None, final_url="https://example.com/")


def test_origin_of():
    assert cb.origin_of("https://news.ycombinator.com/item?id=1") == "https://news.ycombinator.com"
    assert cb.origin_of("ftp://x.com") is None and cb.origin_of("not a url") is None


def test_summarize_done_and_not_done():
    assert cb.summarize(fake_run())["done"] is True
    r = cb.summarize(fake_run(status="partial", kind="blocked_login", reason="login required"))
    assert r["done"] is False and r["kind"] == "blocked_login"
    assert cb.summarize(fake_run(status="timed_out", kind="timeout"))["done"] is False


def test_run_task_passes_origins_and_limits(monkeypatch):
    seen = {}

    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        class browser_runs:
            @staticmethod
            def run(**kw):
                seen.update(kw); return fake_run()

    monkeypatch.setattr("reach.commands.load_keys", lambda names: None)
    out = cb.run_task("find x", "https://example.com/page", ["https://cdn.example.com/a", "bad"], client_factory=Client)
    assert out["done"] and seen["allowed_origins"][0] == "https://example.com"
    assert "https://cdn.example.com" in seen["allowed_origins"] and "bad" not in seen["allowed_origins"]
    assert seen["limits"].max_cost_usd == cb.MAX_COST_USD


def test_bad_start_url_is_refused():
    try:
        cb.run_task("t", "javascript:alert(1)")
        assert False
    except ValueError:
        pass


def test_tool_reports_sdk_errors_as_not_done(monkeypatch):
    def boom(*a, **k): raise RuntimeError("origin_blocked: youtube.com")
    monkeypatch.setattr(cb, "run_task", boom)
    out = asyncio.run(cb.CloudBrowser().run(cb.CloudArgs(task="t", start_url="https://www.youtube.com"), None))
    assert out.status == "error" and "outcome=not_done" in out.content and "origin_blocked" in out.content
