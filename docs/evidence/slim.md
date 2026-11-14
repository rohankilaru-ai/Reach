# Slim pass: dead code and duplicates

Done 2026-11-14 on branch `rohan/slim`, rebased on `origin/main` at `cae2bd3`. Scope: `backend/`, `scripts/`, `smoke/`,
`fixtures/`, `patches/` and the repo root. `apps/overlay`, `perception/`, `finger_processing/`, `cursor_demo/` and the
existing files in `docs/evidence/` were searched for references but not edited.

The repo was already lean. The pass found 26 removable lines; everything else that looked removable is cited by a
doc, used by a test, or sits in a file other builders are editing.

## Method

- Every function, class and module-level constant in `backend/`, `scripts/` and `smoke/` was counted as a whole word
  across all tracked files (Python, Swift, docs, fixtures, scripts). A name that appears only at its definition is a
  candidate.
- Each candidate was then searched on every `origin/*` branch, so an in-flight branch that uses it would show up.
- Unused imports were found per file from the syntax tree.
- Modules, scripts, fixtures and CLI subcommands were searched by name in the README, `AGENTS.md`, `docs/`,
  the tests and the Swift sources.
- Dependencies in `backend/pyproject.toml` were matched against imports, including imports inside functions.
- No committed caches (`__pycache__`, `.pyc`, `.DS_Store`, `.pytest_cache`, `.egg-info`) and no commented-out code
  were found.

## Removed

| What | Where | Lines | Proof it was unused |
| --- | --- | --- | --- |
| `smoke/ndi_smoke.py` (whole file) | `smoke/` | 17 | One-off S0 probe of the NDI response shape. No reference on `main`; the only mentions are in `docs/STATUS.md` on old branches, a table `main` no longer has. The shape it recorded is in `docs/GROUND_SHAPE.md` and `fixtures/docs/ndi_parse_small.json` |
| `strip_wake()` | `backend/reach/addressed.py` | 4 | Never called or imported in Python, Swift, tests or docs, on any branch |
| `CHROME_WRITE_TOOLS` | `backend/reach/tools/chrome.py` | 3 | Never read. The live list is `WRITE_TOOLS` in `ledger.py`, which already holds the same four names |
| `from pathlib import Path` | `backend/tests/test_greptile_backend.py` | 1 | `Path` is not used in the file |
| `DOC` constant | `smoke/do_mode_gate.py` | 1 | Never read. The script itself stays; `do-mode.md` cites it |

Total: 26 lines in 5 files, one file deleted.

## Line counts

`git ls-files <path> | xargs wc -l`

| Path | Before | After | Change |
| --- | --- | --- | --- |
| `backend/reach` | 7124 | 7117 | -7 |
| `backend/` without `uv.lock` | 11001 | 10993 | -8 |
| `smoke/` | 256 | 238 | -18 |

## Found and kept on purpose

| What | Why it stays |
| --- | --- |
| Four copies of key loading: `commands.load_keys`, `speech_cloud`, `tools/vision`, `tools/ndi` | They look duplicated but differ: each reads a different list of files in a different order, and `tools/ndi` reads the repo `.env` only. `AGENTS.md` documents those differences as the contract. Merging them changes which file a key comes from, so it is not a trivial change this close to the freeze. About 45 lines could go in a later pass with tests for each search order |
| `scripts/check_evidence.py` | No doc or script names it. It is the manual check that every evidence card quotes its PDF word for word, for a feature the README lists as implemented. Kept as the only way to rerun that check |
| `backend/reach/addressed_eval.py` | Not named in any doc, but it is the only program that reproduces the numbers in `addressed.md`, `addressed_dev.json` and `addressed_held_out.json` |
| `backend/reach/bench.py`, `bench_v2.py`, `intent_eval.py`, `backend/scripts/guide_eval.py` | Cited by `docs/evidence`; the results are reproducible from them |
| `backend/scripts/cloud_voice_gate.py`, `scripts/latency.py`, `smoke/do_mode_gate.py`, `smoke/pinch_relay_gate.py` | Each is named in a `docs/evidence` file or in `apps/overlay/README.md` |
| CLI subcommands `route`, `bench`, `shelf`, `pin`, `unpin` | `route` and `bench` are named in `bench.py`, `bench_v2.py` and `bench_v2.md`. `shelf`, `pin` and `unpin` have no doc reference, but `reach shelf` prints the hint "pin a card with: reach pin E3", and `cli.py` is being edited by other builders |
| Re-export line in `backend/reach/tools/native.py` (`WRITE_TOOLS`, `parse_outcome`, `worst`) | Marked as an intentional re-export. Nothing imports those names through `native` today, but removing them saves no lines |
| Three-step build-up of `WRITE_TOOLS` in `ledger.py` | Could be one line; `ledger.py` is a shared file, so left alone |
| `setup_function` in `backend/tests/test_convo.py` | Looks unreferenced; pytest calls it by name before each test |
| `backend/reach/tools/__init__.py` (empty) | Package marker |
| All dependencies in `backend/pyproject.toml` | `intern-agent` and `websockets` are imported directly. `nace-sdk` is imported inside `tools/ndi.py`. The two `pyobjc` extras are imported inside `tools/ax.py` for Do mode |
| All fixtures | Each is read by a test or by a benchmark the evidence cites |
| Long module docstrings in `tools/*.py`, `guard_policy.py`, `commands.py` | They state safety rules and where keys are read, which the code alone does not show |
| `ipc.py`, `bridge.py`, `contracts.py`, `cli.py`, `ledger.py`, `intern_ext.py` | No dead code found by the scan; not edited |

## Noticed, not fixed here

- `AGENTS.md` tells readers to read `docs/PLAN.md` and to find a slice table in `docs/STATUS.md`. Neither exists on
  `main`. `perception/` also refers to `PLAN.md`. Docs belong to the driver, so this is left for them.

## Check

After the change, with no keys and no network:

- `cd backend && uv run pytest -q`: 325 passed (325 before).
- `uv run reach replay ../fixtures/events/ask.jsonl`: exit 0, receipt printed with `status: ok`.
