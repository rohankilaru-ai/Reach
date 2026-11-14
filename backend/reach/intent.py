"""Drex task understanding: what is the user asking, before Intern runs.

ONE Drex Max request per ask with several typed questions: `kind` (choice) plus five `noul` flags. The result:
  - adds a short, honest hint to the Intern prompt (likely task kind and the tool family that fits; never forced),
  - when the request is about the screen and nothing is selected, tells System 2 to look with see_screen
    (action `see_screen`); only with REACH_AUTO_SCREEN=0 does it short-circuit with a non-LLM reply asking the user
    to circle the thing (reason `needs_selection`, no Intern run),
  - adds a REQUIRED confirmation instruction when the request is risky or irreversible (Reach has no confirm UI
    yet, so the model replies with the question and does not act).
Every call is logged to the Drex log as a `decision` with purpose `intent`. On any error, a missing key or a
timeout (TIMEOUT_S) the ask goes ahead unchanged and an `intent` error entry is logged. REACH_INTENT=0 turns it off.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .commands import DREX_URL, DREX_USD_PER_INPUT_TOKEN, http_post, load_keys
from .ledger import append_drex
from .paths import drex_log

MODEL = os.environ.get("REACH_INTENT_MODEL", "drex-max-v1.1")
TIMEOUT_S = 1.5
PURPOSE = "intent"

KINDS: dict[str, str] = {
    "chat": "Conversation, opinions, general knowledge or how-to questions answerable from knowledge alone.",
    "document_question": "A question about the user's added documents or files (what they say, quotes, summaries).",
    "browser_task": "Doing something on a website: look up live info online, search the web, fill or navigate a site.",
    "write_to_notes": "Save, jot down or append something to the user's notes.",
    "screen_question": "A question about something visible on the screen or a selected region or selected text.",
    "mac_control": "Control the Mac itself: open/close apps, files or folders, change settings, move or delete files.",
    "other": "None of the above, or too unclear to tell.",
}
FLAGS: dict[str, str] = {
    "needs_documents": "Answering needs the user's own added documents or files.",
    "needs_screen_region": (
        "The request refers to something on screen with 'this', 'that', 'these', 'here' or 'it' and needs to see "
        "it, and the state says no selection is present."
    ),
    "writes_user_data": "Carrying it out writes or changes the user's data (notes, files, documents, settings).",
    "risky_or_irreversible": (
        "Carrying it out has an effect that is hard to undo or affects others: sending a message or email, buying "
        "or paying, deleting or overwriting, submitting a form, posting publicly."
    ),
    "ambiguous": "The request is unclear enough that a careful assistant would ask what the user means first.",
    "continues_previous": (
        "The state includes a previous exchange. The user's new message refers back to it or only makes sense "
        "with it: a follow-up, 'that', 'it', 'the same', 'again', 'also', 'and then', 'what about...'. It is NO if "
        "the message stands on its own or starts a new topic, or when there is no previous exchange."
    ),
}

# All thresholds in one place. Tuned on the `dev` split only (fixtures/intent/requests.jsonl); never on held_out.
THRESHOLDS: dict[str, float] = {
    "kind": 0.55,  # top kind below this -> `other` (abstain)
    "needs_screen_region": 0.7,  # high bar: a false positive blocks a real ask
    "risky_or_irreversible": 0.5,
    "hint": 0.6,  # a kind hint is only added at or above this
}

TOOL_FAMILY: dict[str, str] = {
    "document_question": "use evidence_search on the user's documents and cite passage ids",
    "browser_task": (
        "for a web SEARCH use chrome_open_url with https://www.google.com/search?q=<url-encoded query> in the user's "
        "own Chrome (their signed-in browser is not bot-checked), then see_screen to read the results; use "
        "browser_task for navigating or filling forms on ordinary sites (search engines show it a CAPTCHA); for email "
        "use email_draft, which opens a Gmail draft and never sends"
    ),
    "write_to_notes": (
        "use note_append (or shelf_to_note for evidence ids); to copy text so the user can paste it use "
        "clipboard_copy; for 'email X about Y' use email_draft (opens a Gmail draft, never sends)"
    ),
    "screen_question": (
        "use see_region for 'what is this' about a circled region image (otherwise the selected text); with nothing "
        "selected and a question about the screen, call see_screen; use point_at for 'where do I click / how do I "
        "fill this in / show me how' (after a selection or see_screen), then answer in words: the pointer shows "
        "where, the user clicks and types (never claim you did)"
    ),
    "chat": "answer directly; no tool is needed",
    "mac_control": (
        "use chrome_open_url / chrome_list_tabs / chrome_activate_tab / chrome_close_tab for Chrome tabs, finder_open "
        "and finder_list for folders and files, ax_describe then ax_press / ax_set_text to press a button or type in "
        "another app (when those tools exist), clipboard_copy to copy text, email_draft for email; if no tool fits, "
        "say what you cannot do rather than pretend"
    ),
}

NEEDS_SELECTION_REPLY = (
    "Which thing do you mean? Nothing is selected yet. Circle it on screen (or select the text) and ask again, "
    "and I'll take it from there."
)

SEE_SCREEN_HINT = (
    "Hint: nothing is selected, but this looks like a question about what is on the user's screen (Drex "
    "needs_screen_region p={p:.2f}, kind {kind}). Call see_screen with the user's question to look at the screen "
    "(target 'window' if they mean the app in front), then answer from what it returns. If it says it needs "
    "Screen Recording permission, tell the user exactly that."
)

CONFIRM_REQUIRED = (
    "REQUIRED: Drex judged this request risky or irreversible (p={p:.2f}). Do NOT act and do not call any tool that "
    "changes anything. Reply only by describing the exact effect it would have (what, where, how many, whether it "
    "can be undone) and ask the user to confirm. Reach has no confirm button yet, so stop after asking."
)

INSTRUCTIONS = (
    "The state is a request a user typed or said to a desktop assistant, plus what is selected on screen and whether "
    "documents or an image are attached. Judge the request as written; typos are expected."
)


@dataclass
class Intent:
    kind: str = "other"
    probs: dict[str, float] = field(default_factory=dict)  # kind probabilities + one p per flag
    flags: dict[str, float] = field(default_factory=dict)
    action: str = "none"  # none | hint | see_screen | needs_selection | confirm
    hint_used: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    model: str | None = None

    def flag(self, name: str) -> float:
        return float(self.flags.get(name, 0.0))

    def receipt_dict(self) -> dict[str, Any]:
        p = {self.kind: round(self.probs.get(self.kind, 0.0), 3)} if self.kind in self.probs else {}
        p.update({k: round(v, 3) for k, v in self.flags.items()})
        return {"kind": self.kind, "probs": p, "hint_used": self.hint_used, "action": self.action,
                "latency_ms": self.latency_ms, "error": self.error}


def enabled() -> bool:
    return os.environ.get("REACH_INTENT", "1") != "0"


def auto_screen() -> bool:
    """see_screen on (default): a screen question with no selection looks at the screen instead of asking to circle."""
    return os.environ.get("REACH_AUTO_SCREEN", "1") != "0"


def state_for(text: str, selection_summary: str | None, has_documents: bool, has_image: bool) -> str:
    sel = f"selected text: {selection_summary[:200]!r}" if selection_summary else "none"
    if has_image:
        sel = "a screen region image" + ("" if sel == "none" else f" and {sel}")
    return (f"The user said: {text}\nSelection present: {sel}\n"
            f"Documents added: {'yes' if has_documents else 'no'}")


def body(state: str, model: str = MODEL) -> dict[str, Any]:
    qs: dict[str, Any] = {"kind": {"type": "choice", "instructions": INSTRUCTIONS + " What kind of task is it?",
                                   "criteria": dict(KINDS)}}
    for name, desc in FLAGS.items():
        qs[name] = {"type": "noul", "instructions": INSTRUCTIONS + " " + desc}
    return {"model": model, "state": state, "questions": qs}


def _noul(a: Any) -> float | None:
    """Tolerant: {'noul': p}, {'probability': p}, a bare number, or yes/no probabilities."""
    if isinstance(a, (int, float)):
        return float(a)
    if not isinstance(a, dict):
        return None
    for k in ("noul", "probability", "p", "value"):
        if isinstance(a.get(k), (int, float)):
            return float(a[k])
    probs = a.get("probabilities")
    if isinstance(probs, dict):
        for k in ("yes", "true", "1"):
            if isinstance(probs.get(k), (int, float)):
                return float(probs[k])
    return None


def decide(intent: Intent, has_selection: bool) -> Intent:
    """Apply THRESHOLDS: abstain to `other`, then pick the action. Selection is checked in code, not trusted."""
    kp = {k: v for k, v in intent.probs.items() if k in KINDS}
    if kp:
        top, p = max(kp.items(), key=lambda kv: kv[1])
        intent.kind = top if p >= THRESHOLDS["kind"] else "other"
    else:
        intent.kind = "other"
    screen = not has_selection and (intent.flag("needs_screen_region") >= THRESHOLDS["needs_screen_region"]
                                    or intent.kind == "screen_question")
    if intent.flag("risky_or_irreversible") >= THRESHOLDS["risky_or_irreversible"]:
        intent.action = "confirm"  # risky always wins: never act, ask first
    elif screen and auto_screen():
        intent.action = "see_screen"
    elif not has_selection and intent.flag("needs_screen_region") >= THRESHOLDS["needs_screen_region"]:
        intent.action = "needs_selection"
    elif intent.kind != "other" and kp.get(intent.kind, 0.0) >= THRESHOLDS["hint"]:
        intent.action = "hint"
    return intent


def parse(resp: dict[str, Any], latency_ms: int, has_selection: bool) -> Intent:
    answers = resp.get("answers") or {}
    usage = resp.get("usage") or {}
    tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    k = answers.get("kind") or {}
    probs = {str(a): float(b) for a, b in (k.get("probabilities") or {}).items() if isinstance(b, (int, float))}
    flags = {n: v for n in FLAGS if (v := _noul(answers.get(n))) is not None}
    it = Intent(probs=probs, flags=flags, latency_ms=latency_ms, input_tokens=tin, output_tokens=tout,
                cost_usd=round(tin * DREX_USD_PER_INPUT_TOKEN, 8), model=resp.get("model"))
    if not probs and not flags:
        it.error = "no answers in response"
        return it
    return decide(it, has_selection)


def understand(text: str, selection_summary: str | None = None, has_documents: bool = False, has_image: bool = False,
               post=http_post, timeout: float = TIMEOUT_S) -> Intent:
    """One Drex Max request. Never raises: errors come back in `Intent.error` with kind `other`, action `none`."""
    t0 = time.monotonic()
    load_keys(("NACE_API_KEY",))
    key = os.environ.get("NACE_API_KEY") or os.environ.get("DREX_API_KEY")
    if not key and post is http_post:
        return Intent(error="no Drex key")
    headers = {"Authorization": f"Bearer {key or ''}", "X-Nace-Client": "reach/1"}
    state = state_for(text, selection_summary, has_documents, has_image)
    try:
        from . import convo

        prev = convo.previous_summary()
        state += f"\nPrevious exchange: {prev}" if prev else "\nPrevious exchange: none"
    except Exception:
        pass
    try:
        resp = post(DREX_URL, headers, body(state), timeout)
    except Exception as e:
        return Intent(error=f"{type(e).__name__}: {e}"[:200], latency_ms=int((time.monotonic() - t0) * 1000))
    return parse(resp, int((time.monotonic() - t0) * 1000), bool(selection_summary or has_image))


def hint_text(intent: Intent) -> str | None:
    if intent.action != "hint":
        return None
    p = intent.probs.get(intent.kind, 0.0)
    fam = TOOL_FAMILY.get(intent.kind)
    s = f"Drex (a fast classifier) judged this a {intent.kind.replace('_', ' ')} (p={p:.2f})"
    s += f"; {fam}." if fam else "."
    return s + " This is a hint, not an order: if the request says otherwise, follow the request."


def prompt_addendum(intent: Intent | None) -> str:
    """Text appended to the Intern prompt. Empty when there is nothing to add."""
    if intent is None or intent.error:
        return ""
    if intent.action == "confirm":
        return CONFIRM_REQUIRED.format(p=intent.flag("risky_or_irreversible"))
    if intent.action == "see_screen":
        intent.hint_used = True
        return SEE_SCREEN_HINT.format(p=intent.flag("needs_screen_region"), kind=intent.kind)
    h = hint_text(intent)
    if h:
        intent.hint_used = True
        return "Hint: " + h
    return ""


def log_event(intent: Intent, text: str) -> dict[str, Any]:
    """The Drex log entry (type `decision`, purpose `intent`), so `reach log` shows it."""
    answers: dict[str, Any] = {}
    if intent.probs:
        answers["kind"] = {"choice": intent.kind, "probabilities": {k: round(v, 4) for k, v in intent.probs.items()}}
    for n, v in intent.flags.items():
        answers[n] = {"noul": round(v, 4)}
    return {
        "type": "decision", "purpose": PURPOSE, "timestamp": int(time.time() * 1000),
        "ok": intent.error is None, "error": intent.error, "model": intent.model, "state": text[:300],
        "answers": answers, "action": intent.action, "latency_ms": intent.latency_ms,
        "tokens": {"input": intent.input_tokens, "output": intent.output_tokens}, "cost_usd": intent.cost_usd,
    }


def log(intent: Intent, text: str, task_id: str, path: Path | None = None) -> dict[str, Any]:
    ev = log_event(intent, text)
    append_drex(path or drex_log(), ev, task_id)
    return ev


# ---- the bridge hook ----


def start(ask: Any) -> asyncio.Task | None:
    """Start understanding the ask in a thread; the caller awaits `finish` before starting Intern."""
    if not enabled():
        return None
    from . import evidence

    try:
        has_docs = bool(evidence.has_documents())
    except Exception:
        has_docs = False
    sel = ask.selection
    return asyncio.ensure_future(asyncio.to_thread(
        understand, ask.text, sel.text if sel else None, has_docs, bool(sel and sel.image_path)))


async def finish(fut: asyncio.Task | None, started: float) -> Intent | None:
    """Wait for the result, at most TIMEOUT_S from `started`. A late result is discarded, never acted on."""
    if fut is None:
        return None
    remaining = max(0.0, TIMEOUT_S - (time.monotonic() - started))
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=remaining + 0.05)
    except asyncio.TimeoutError:
        fut.cancel()
        return Intent(error="timeout", latency_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return Intent(error=f"{type(e).__name__}: {e}"[:200])


def receipt_dict(intent: Intent | None) -> dict[str, Any] | None:
    return intent.receipt_dict() if intent is not None else None


def to_json(intent: Intent) -> dict[str, Any]:
    return asdict(intent)
