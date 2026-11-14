# Working on Reach (humans and agents)

Read `docs/SOURCES.md` (verified upstream facts) and the README status table (what is implemented, experimental or planned).

Rules
- Never commit secrets. Keys live in the repo-root `.env` (gitignored; names in `.env.example`). Do not print or paste keys.
- Where keys are read: Reach's own NDI calls (`reach add`, evidence grounding) read `NACE_API_KEY` only from the repo-root `.env`; the command router reads `$REACH_ENV_FILE`, then the repo-root `.env`, then `.intern-home/.env`; `see_region` reads the repo-root `.env`, then `$INTERN_HOME/.env`. Intern itself runs with `INTERN_HOME` (the backend defaults it to `<repo>/.intern-home`), so put a symlink `.intern-home/.env -> ../.env` there rather than a second copy of the keys.
- Every autonomous action goes through an Intern tool. No second agent loop.
- One branch per slice, named `rohan/<slice>`; open a PR into `main`. Do not edit `backend/reach/contracts.py` except to add fields.
- Run `cd backend && uv run pytest -q` before pushing. Tests must not need keys. The overlay's tests are `cd apps/overlay && swift test`.
- Label anything simulated, replayed or experimental in the UI and the README.

Run it
```
cd backend
export INTERN_HOME=$PWD/../.intern-home   # optional (this is the default); holds the .env symlink to ../.env
uv run reach add some.pdf
uv run reach ask "question about the pdf"
uv run reach log
uv run reach replay ../fixtures/events/ask.jsonl   # no keys needed
```

Status: see the README status table. Hands mode, dictation, the mode gestures, `browser_in_chrome` pairing and silent speech are the experimental parts not yet verified live.

## Permissions
`swift scripts/request-permissions.swift` raises every macOS permission prompt Reach can use (Screen Recording, Accessibility, Microphone, Camera, Speech Recognition, and Automation for Chrome, Finder, Notes and TextEdit) for the app you run it from, and reports what is still missing. Re-run it to check.
