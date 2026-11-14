# Working on Reach (humans and agents)

Read `docs/PLAN.md` (plan, historical), `docs/SOURCES.md` (verified upstream facts) and `docs/STATUS.md` (what is true now).

Rules
- Never commit secrets. Keys live in `.env` (gitignored; names in `.env.example`). Do not print or paste keys.
- Every autonomous action goes through an Intern tool. No second agent loop.
- One branch per slice, named `rohan/<slice>`; open a PR into `main`. Do not edit `backend/reach/contracts.py` except to add fields.
- Run `cd backend && uv run pytest -q` before pushing. Tests must not need keys.
- Label anything simulated, replayed or experimental in the UI and the README.

Run it
```
cd backend
export INTERN_HOME=$PWD/../.intern-home   # keep an .env symlink or file in there with the keys
uv run reach add some.pdf
uv run reach ask "question about the pdf"
uv run reach log
uv run reach replay ../fixtures/events/ask.jsonl   # no keys needed
```

Slice board: see the table in `docs/STATUS.md`. Pinch selection (S6) and silent speech (S8a) are the open experimental slices.
