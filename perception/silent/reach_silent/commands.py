"""The 12-command vocabulary, hypothesis rescoring and the `transcript` message. Pure Python: no torch, no camera.

Two signals per clip:
- open-vocabulary n-best from the beam search (what the model thinks was mouthed, any words), and
- closed-set log-probs of the 11 command phrases (how well each phrase fits the clip; see LipReader.score).

The open n-best is what gets sent: the backend's Drex router then picks a command or abstains. Sending only the
closed-set phrases would force every clip, including unrelated mouthing, onto some command, so they are used to
re-order and annotate (`local`) only. The final say is the backend's; an `uncertain` result never acts.
"""

from __future__ import annotations

import difflib
import math
import re

# label -> spoken phrase. Labels match backend/reach/commands.py LABELS.
VOCAB: dict[str, str] = {
    "explain_this": "explain this",
    "open_this": "open this",
    "scroll_down": "scroll down",
    "scroll_up": "scroll up",
    "pause": "pause",
    "continue": "continue",
    "compare_these": "compare these",
    "put_this_here": "put this here",
    "cancel": "cancel",
    "next": "next",
    "stop": "stop",
    "uncertain": "uncertain",
}
UNCERTAIN = "uncertain"
PHRASES: dict[str, str] = {k: v for k, v in VOCAB.items() if k != UNCERTAIN}  # what the closed set scores

MAX_HYPS = 10
MAX_CHARS = 200
# Local abstain rule on the closed set (mirrors the backend's THRESHOLD / MARGIN idea).
LOCAL_THRESHOLD = 0.5
LOCAL_MARGIN = 0.15
MATCH_MIN = 0.6  # string similarity below this means "no command phrase in this hypothesis"


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def nearest_command(text: str) -> tuple[str, float]:
    """The closest command phrase by character similarity: (label, 0..1). `uncertain` when nothing is close."""
    t = normalize(text)
    if not t:
        return UNCERTAIN, 0.0
    best, sim = UNCERTAIN, 0.0
    for label, phrase in PHRASES.items():
        s = difflib.SequenceMatcher(None, t, phrase).ratio()
        if s > sim:
            best, sim = label, s
    return (best if sim >= MATCH_MIN else UNCERTAIN), round(sim, 3)


def closed_set_probs(closed: list[tuple[str, float]]) -> dict[str, float]:
    """[(phrase, log_prob)] -> {label: prob} by softmax over the command phrases."""
    by_phrase = {v: k for k, v in PHRASES.items()}
    pairs = [(by_phrase[p], lp) for p, lp in closed if p in by_phrase and math.isfinite(lp)]
    if not pairs:
        return {}
    m = max(lp for _, lp in pairs)
    exp = {label: math.exp(lp - m) for label, lp in pairs}
    z = sum(exp.values())
    return {label: round(e / z, 4) for label, e in sorted(exp.items(), key=lambda kv: -kv[1])}


def local_decide(probs: dict[str, float], threshold: float = LOCAL_THRESHOLD,
                 margin: float = LOCAL_MARGIN) -> tuple[str, float, bool]:
    """(label, probability, uncertain). Uncertain when nothing clearly wins."""
    if not probs:
        return UNCERTAIN, 0.0, True
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    top, p = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top == UNCERTAIN or p < threshold or p - second < margin:
        return UNCERTAIN, p, True
    return top, p, False


def rescore(nbest: list[tuple[str, float | None]], closed: list[tuple[str, float]] | None = None) -> list[dict]:
    """Rank hypotheses for the router: each {text, score, nearest, similarity}, best first.

    Order: hypotheses that contain a command phrase (similarity >= MATCH_MIN) first, the one agreeing with the
    closed-set winner before others; within a group, the recognizer's own order is kept. Empty texts are dropped.
    """
    winner = local_decide(closed_set_probs(closed))[0] if closed else None
    rows = []
    for i, (text, score) in enumerate(nbest):
        t = normalize(text)[:MAX_CHARS]
        if not t:
            continue
        label, sim = nearest_command(t)
        rows.append({"text": t, "score": None if score is None else round(float(score), 3),
                     "nearest": label, "similarity": sim, "_i": i})
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (r["nearest"] == UNCERTAIN, r["nearest"] != winner, r["_i"])):
        if r["text"] in seen:
            continue
        seen.add(r["text"])
        r.pop("_i")
        out.append(r)
    return out[:MAX_HYPS]


def transcript_message(ranked: list[dict], source: str = "lipreading") -> dict:
    """The inbound `transcript` message for the backend (backend/reach/contracts.py `Hypotheses`)."""
    if not ranked:
        raise ValueError("no hypotheses")
    msg: dict = {
        "type": "transcript",
        "hypotheses": [r["text"] for r in ranked],
        "source": source,
        "experimental": True,
    }
    scores = [r["score"] for r in ranked]
    if all(s is not None for s in scores):
        msg["scores"] = scores
    return msg


def local_summary(ranked: list[dict], closed: list[tuple[str, float]] | None = None) -> dict:
    """What the worker can say without the backend: never acts, preview only."""
    if closed:
        label, p, unsure = local_decide(closed_set_probs(closed))
    elif ranked:
        label, p = ranked[0]["nearest"], ranked[0]["similarity"]
        unsure = label == UNCERTAIN
    else:
        label, p, unsure = UNCERTAIN, 0.0, True
    return {"command": UNCERTAIN if unsure else label, "probability": round(p, 3), "uncertain": unsure}
