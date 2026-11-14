# Working together without conflicts

Several people and agents build Reach at once. These rules keep merges boring.

## Rules
1. **One branch and one git worktree per task.** `git worktree add ../Reach-wt-<task> -b rohan/<task> origin/main`. Nobody edits the main checkout while others work, and nobody switches branches inside another task's worktree.
2. **One owner per area at a time** (table below). If you need a change in someone else's area, ask the owner or make a small additive edit and say so in the PR.
3. **New files beat edits to shared files.** Shared files (`backend/reach/{ipc,bridge,contracts,ledger,intern_ext}.py`) take small, additive changes only; `contracts.py` fields are only ever added.
4. **Rebase on `origin/main` right before opening the PR**, then run `cd backend && uv run pytest -q`.
5. **Merge order is set by one person** (the driver): backend before overlay, small before large. After every merge the full test suite runs on `main`.
6. **Never rebuild the macOS app casually**: each build is a new signature and resets the owner's Screen Recording grant.
7. **No secrets, no real `.env` reads.** Tests use fake keys in temp folders.

## Areas and owners
| Area | Files | Owner |
| --- | --- | --- |
| Overlay (Swift) | `apps/overlay/**` | one agent at a time (currently the circle-select / hub builder) |
| Bridge and server | `backend/reach/{ipc,bridge,contracts}.py` | the driver; others add small edits |
| Tools | `backend/reach/tools/*.py` (new file per tool) | whoever builds the tool |
| Policy and ledger | `backend/reach/{guard_policy,ledger}.py` | the driver |
| Pinch worker | `perception/**` | one agent at a time |
| Docs and evidence | `README.md`, `docs/**`, `THIRD_PARTY.md` | the driver |

## Where to find things
`docs/STATUS.md` is the live status. `docs/SOURCES.md` has the verified upstream facts. `AGENTS.md` has the commands.
