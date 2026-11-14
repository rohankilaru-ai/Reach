"""Where Reach keeps its runtime files (all under .reach/, gitignored)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def reach_dir() -> Path:
    return Path(os.environ.get("REACH_DIR", REPO_ROOT / ".reach"))


def run_dir() -> Path:
    return reach_dir() / "run"


def drex_log() -> Path:
    return reach_dir() / "logs" / "drex.jsonl"
