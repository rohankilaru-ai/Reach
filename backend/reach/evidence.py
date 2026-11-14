"""The evidence store: NDI-parsed documents cached by file hash as passages E1..En, keyword search, citations, shelf.

Everything lives in `.reach/evidence.db` (gitignored). One SQLite connection per call, so it is safe from the CLI,
the server and Intern tools alike. NDI itself is behind a `parse_fn` so tests run with no key.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .paths import reach_dir

ParseFn = Callable[[Path], dict[str, Any]]
"""path -> {"job_id": str, "file_id": str|None, "workspace_id": str|None, "result": <NDI parse result dict>}"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, parse_job_id TEXT,
    file_id TEXT, workspace_id TEXT, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS passages (
    n INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT NOT NULL, page INTEGER, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shelf (
    passage TEXT PRIMARY KEY, quote TEXT, pinned_at REAL NOT NULL
);
"""

MAX_PASSAGE_CHARS = 1200
CITE_RE = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")


def db_path() -> Path:
    return reach_dir() / "evidence.db"


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---- passages from an NDI parse result ----


def _page_of(item: dict[str, Any]) -> int | None:
    for key in ("page", "page_number", "page_index", "pageNumber"):
        v = item.get(key)
        if isinstance(v, int):
            return v + 1 if key == "page_index" else v
    pages = item.get("pages") or item.get("page_numbers") or item.get("page_range")
    if isinstance(pages, list) and pages and isinstance(pages[0], int):
        return pages[0]
    if isinstance(pages, dict) and isinstance(pages.get("start"), int):
        return pages["start"]
    for key in ("bbox", "location", "grounding", "provenance"):
        sub = item.get(key)
        if isinstance(sub, dict):
            p = _page_of(sub)
            if p is not None:
                return p
        if isinstance(sub, list) and sub and isinstance(sub[0], dict):
            p = _page_of(sub[0])
            if p is not None:
                return p
    return None


def _text_of(item: dict[str, Any]) -> str:
    for key in ("markdown", "text", "content"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _split_long(text: str, page: int | None) -> list[tuple[int | None, str]]:
    text = text.strip()
    if len(text) <= MAX_PASSAGE_CHARS:
        return [(page, text)] if text else []
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > MAX_PASSAGE_CHARS:
            out.append((page, cur.strip()))
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append((page, cur.strip()))
    return out


def passages_from_parse(result: dict[str, Any]) -> list[tuple[int | None, str]]:
    """(page, text) passages: NDI chunks if present, else blocks merged per page, else the Markdown split by blank lines."""
    doc = result.get("document", result) if isinstance(result, dict) else {}
    chunks = doc.get("chunks") or []
    if chunks:
        out: list[tuple[int | None, str]] = []
        for c in chunks:
            if isinstance(c, dict):
                out += _split_long(_text_of(c), _page_of(c))
            elif isinstance(c, str):
                out += _split_long(c, None)
        if out:
            return out
    blocks = doc.get("blocks") or []
    if blocks:
        out, cur, cur_page = [], "", None
        for b in blocks:
            if not isinstance(b, dict):
                continue
            t, p = _text_of(b).strip(), _page_of(b)
            if not t:
                continue
            if cur and (p != cur_page or len(cur) + len(t) > MAX_PASSAGE_CHARS):
                out.append((cur_page, cur.strip()))
                cur = ""
            cur_page = p
            cur += t + "\n\n"
        if cur.strip():
            out.append((cur_page, cur.strip()))
        if out:
            return out
    return markdown_passages(doc.get("markdown") or "")


PAGE_RE = re.compile(r"^-{3}\s*Page\s+(\d+)\s*-{3}\s*$", re.M)


def markdown_passages(md: str) -> list[tuple[int | None, str]]:
    """NDI Markdown carries `--- Page N ---` lines (seen live 2026-11-14, empty chunks/blocks): split per page, then
    into passages of whole paragraphs up to MAX_PASSAGE_CHARS."""
    out: list[tuple[int | None, str]] = []
    marks = list(PAGE_RE.finditer(md))
    if not marks:
        return _split_long(md, None)
    if md[: marks[0].start()].strip():
        out += _split_long(md[: marks[0].start()], None)
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md)
        out += _split_long(md[m.end() : end], int(m.group(1)))
    return out


# ---- documents ----


def add_document(path: Path, parse_fn: ParseFn) -> dict[str, Any]:
    """Parse `path` with NDI unless its sha256 is cached. Returns {sha256, name, cached, passages:[ids]}."""
    path = Path(path).expanduser().resolve()
    sha = sha256_file(path)
    with connect() as con:
        row = con.execute("SELECT * FROM docs WHERE sha256=?", (sha,)).fetchone()
        if row is not None:
            ids = [f"E{r['n']}" for r in con.execute("SELECT n FROM passages WHERE sha256=? ORDER BY n", (sha,))]
            return {"sha256": sha, "name": row["name"], "cached": True, "passages": ids, "parse_job_id": row["parse_job_id"]}
    parsed = parse_fn(path)
    passages = passages_from_parse(parsed["result"])
    with connect() as con:
        con.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?,?)",
            (sha, str(path), path.name, parsed.get("job_id"), parsed.get("file_id"), parsed.get("workspace_id"), time.time()),
        )
        ids = []
        for page, text in passages:
            cur = con.execute("INSERT INTO passages (sha256, page, text) VALUES (?,?,?)", (sha, page, text))
            ids.append(f"E{cur.lastrowid}")
    return {"sha256": sha, "name": path.name, "cached": False, "passages": ids, "parse_job_id": parsed.get("job_id")}


def has_documents() -> bool:
    if not db_path().exists():
        return False
    with connect() as con:
        return con.execute("SELECT 1 FROM docs LIMIT 1").fetchone() is not None


def get_passage(pid: str) -> dict[str, Any] | None:
    m = re.fullmatch(r"E(\d+)", pid.strip())
    if not m:
        return None
    with connect() as con:
        r = con.execute(
            "SELECT p.n, p.page, p.text, d.name, d.path, d.parse_job_id FROM passages p JOIN docs d USING (sha256) WHERE p.n=?",
            (int(m.group(1)),),
        ).fetchone()
    if r is None:
        return None
    return {"id": f"E{r['n']}", "page": r["page"], "text": r["text"], "file": r["name"], "path": r["path"], "parse_job_id": r["parse_job_id"]}


# ---- search (BM25 over passages) ----

_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were with what which how why does do".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


def search(query: str, k: int = 5) -> list[dict[str, Any]]:
    if not db_path().exists():
        return []
    with connect() as con:
        rows = con.execute("SELECT p.n, p.page, p.text, d.name FROM passages p JOIN docs d USING (sha256)").fetchall()
    if not rows:
        return []
    docs = [tokens(r["text"]) for r in rows]
    avg = sum(len(d) for d in docs) / len(docs) or 1.0
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    q = tokens(query)
    scored = []
    for r, d in zip(rows, docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [{"id": f"E{r['n']}", "file": r["name"], "page": r["page"], "text": r["text"], "score": round(s, 3)} for s, r in scored[:k]]


# ---- citations ----


def cited_ids(text: str) -> list[str]:
    """Passage ids cited as [E3] or [E3, E7], in first-seen order, deduplicated."""
    seen: list[str] = []
    for m in CITE_RE.finditer(text or ""):
        for pid in re.split(r"\s*,\s*", m.group(1)):
            if pid not in seen:
                seen.append(pid)
    return seen


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"^\s*#+\s*", "", s, flags=re.M)
    s = s.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", s).strip()


def _strip_math(s: str) -> str:
    """Keep inline math as readable text: drop the $ delimiters and simple LaTeX commands."""
    s = re.sub(r"\$([^$\n]+)\$", r"\1", s)
    s = re.sub(r"\\(?:mathrm|text|mathbf|mathit)\{([^}]*)\}", r"\1", s)
    return s.replace("\\%", "%")


def sentences(text: str) -> list[str]:
    """Prose sentences of a passage. Inline math is tolerated; display math, table rows and code are skipped."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        lines = [
            ln for ln in para.splitlines()
            if not ln.lstrip().startswith(("|", "```", "!", "<table", "<tr", "<td", "$$"))
        ]
        para = _strip_math(" ".join(lines))
        if "$$" in para or "\\begin" in para:
            continue
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", _clean(para)):
            s = s.strip()
            if 30 <= len(s) <= 400 and not re.search(r"[\[\]*_`\\|]", s):
                out.append(s)
    return out


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return set(_NUM_RE.findall(text))


def best_quote(passage: str, context: str) -> str:
    """The passage sentence that best supports the citing answer: shared numbers first, then term overlap."""
    cands = sentences(passage)
    if not cands:
        return _clean(_strip_math(passage))[:240]
    ctx = set(tokens(context))
    nums = _numbers(context)

    def score(s: str) -> tuple:
        return (3 * len(nums & _numbers(s)) + len(ctx & set(tokens(s))), -abs(len(s) - 160))

    return max(cands, key=score)


def citing_context(answer: str, pid: str) -> str:
    parts = [s for s in re.split(r"(?<=[.!?])\s+", answer) if re.search(rf"\b{re.escape(pid)}\b", s)]
    return " ".join(parts) or answer


def evidence_items(answer: str) -> list[dict[str, Any]]:
    """Cards for every cited id that exists: {id, file, path, page, quote, parse_job_id}. Unknown ids are dropped."""
    items = []
    for pid in cited_ids(answer):
        p = get_passage(pid)
        if p is None:
            continue
        items.append({**p, "quote": best_quote(p["text"], citing_context(answer, pid))})
    return items


def normalize_for_match(s: str) -> str:
    """NFKC, lowercase, and drop whitespace and hyphens, so PDF line breaks and ligatures do not matter."""
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s\-‐-―­]+", "", s)


# ---- shelf ----


def pin(pid: str, quote: str | None = None) -> bool:
    if get_passage(pid) is None:
        return False
    if quote is None:  # keep the quote the last card for this id showed
        cards = reach_dir() / "logs" / "evidence.jsonl"
        if cards.exists():
            for line in cards.read_text().splitlines():
                try:
                    c = json.loads(line)
                except ValueError:
                    continue
                if c.get("id") == pid and c.get("quote"):
                    quote = c["quote"]
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO shelf VALUES (?,?,?)", (pid, quote, time.time()))
    return True


def unpin(pid: str) -> bool:
    with connect() as con:
        return con.execute("DELETE FROM shelf WHERE passage=?", (pid,)).rowcount > 0


def shelf() -> list[dict[str, Any]]:
    if not db_path().exists():
        return []
    with connect() as con:
        rows = con.execute("SELECT passage, quote, pinned_at FROM shelf ORDER BY pinned_at").fetchall()
    out = []
    for r in rows:
        p = get_passage(r["passage"])
        if p is not None:
            out.append({**p, "quote": r["quote"] or best_quote(p["text"], ""), "pinned_at": r["pinned_at"]})
    return out


def shelf_prompt() -> str:
    """Pinned items, offered to the next ask."""
    items = shelf()
    if not items:
        return ""
    lines = [f"[{i['id']}] ({i['file']}{', p.' + str(i['page']) if i['page'] else ''}) {i['quote']}" for i in items]
    return "Pinned evidence on the user's shelf (cite by id if relevant):\n" + "\n".join(lines)


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
