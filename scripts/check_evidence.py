"""S3 gate check: every evidence card of a task quotes its PDF verbatim.

Usage: python scripts/check_evidence.py <task_id>   (reads .reach/logs/evidence.jsonl, runs `pdftotext` on each file)
Text: `pdftotext` in both default and `-raw` (content-stream) order, since two-column papers interleave columns in the
default order. Match rule: NFKC, lowercase, whitespace and hyphens removed on both sides (PDF line breaks, ligatures). Exit 1 if
there are no cards or any quote is missing.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from reach.evidence import normalize_for_match  # noqa: E402
from reach.paths import reach_dir  # noqa: E402

task = sys.argv[1]
cards = [
    json.loads(line)
    for line in (reach_dir() / "logs" / "evidence.jsonl").read_text().splitlines()
    if line.strip() and json.loads(line)["task_id"] == task
]
texts: dict[str, str] = {}
ok = bool(cards)
for c in cards:
    if c["file"] not in texts:
        both = [
            subprocess.run(["pdftotext", *mode, c["file"], "-"], capture_output=True, text=True, timeout=60).stdout
            for mode in ([], ["-raw"])
        ]
        texts[c["file"]] = "\n".join(normalize_for_match(t) for t in both)
    found = normalize_for_match(c["quote"]) in texts[c["file"]]
    ok &= found
    print(f"{'FOUND  ' if found else 'MISSING'} [{c['id']}] {Path(c['file']).name} p.{c.get('page')} L{c.get('level')} ground={(c.get('locator') or {}).get('match_method')}: {c['quote'][:80]}")
print(f"{len(cards)} cards, {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
