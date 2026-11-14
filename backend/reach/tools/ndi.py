"""NDI (Nace document intelligence) calls: upload + parse for `reach add`, ground for evidence cards.

The key is loaded inside the process from the repo `.env` (only `NACE_API_KEY`, via setdefault) and never printed.
Every call is written to the Drex/NDI log as a `{"type": "ndi"}` entry so the log shows NDI is real.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ..ledger import append_ndi
from ..paths import REPO_ROOT, drex_log

GROUND_TIMEOUT_S = 90
PARSE_TIMEOUT_S = 420


def load_key() -> None:
    if os.environ.get("NACE_API_KEY"):
        return
    env = REPO_ROOT / ".env"
    try:
        lines = env.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "NACE_API_KEY":
            os.environ.setdefault("NACE_API_KEY", v.strip().strip('"').strip("'"))
            return


def _client():
    load_key()
    from nace_sdk import NaceClient

    return NaceClient()


def _safe_err(e: Exception) -> str:
    return f"{type(e).__name__}"[:80]


def parse_pdf(path: Path, task_id: str = "add") -> dict[str, Any]:
    """Upload and parse with NDI. Returns {job_id, file_id, workspace_id, result}."""
    t0 = time.time()
    with _client() as client:
        f = client.documents.upload(str(path))
        job = client.documents.parse(f.as_source(), output={"formats": ["markdown"]})
        done = client.jobs.wait(job.job_id, timeout=PARSE_TIMEOUT_S)
    result = done.result if hasattr(done, "result") else done
    append_ndi(
        drex_log(), task_id, "parse",
        file=path.name, job_id=str(job.job_id), units=(result or {}).get("units"),
        pages=((result or {}).get("document") or {}).get("page_count"), ms=int((time.time() - t0) * 1000),
    )
    return {"job_id": str(job.job_id), "file_id": f.file_id, "workspace_id": f.workspace_id, "result": result}


def ground(parse_job_id: str, targets: list[dict[str, str]], task_id: str = "-") -> dict[str, Any]:
    """One ground job for all quotes of an answer. Returns the raw result ({targets:[{id,status,matches:[...]}]})."""
    t0 = time.time()
    try:
        with _client() as client:
            job = client.documents.ground(
                {"type": "parse_result", "job_id": parse_job_id},
                targets=targets,
                options={"max_matches": 3, "include_previews": True},
            )
            done = client.jobs.wait(job.job_id, timeout=GROUND_TIMEOUT_S)
        result = done.result if hasattr(done, "result") else done
    except Exception as e:
        append_ndi(drex_log(), task_id, "ground", targets=len(targets), ok=False, error=_safe_err(e))
        raise
    found = sum(1 for t in (result or {}).get("targets", []) if t.get("status") == "found")
    append_ndi(
        drex_log(), task_id, "ground", targets=len(targets), found=found, units=(result or {}).get("units"),
        ms=int((time.time() - t0) * 1000),
    )
    return result


def locator_from(target: dict[str, Any]) -> dict[str, Any] | None:
    """Best match of one ground target -> {page, polygons, match_method, confidence, matched_text} (no preview URL)."""
    matches = target.get("matches") or []
    if target.get("status") != "found" or not matches:
        return None
    m = sorted(matches, key=lambda x: x.get("rank", 99))[0]
    loc = m.get("location") or {}
    return {
        "page": loc.get("page"),
        "kind": loc.get("kind"),
        "polygons": loc.get("polygons"),
        "match_method": m.get("match_method"),
        "confidence": m.get("confidence"),
        "matched_text": m.get("matched_text"),
    }
