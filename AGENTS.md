# AGENTS.md

## Cursor Cloud specific instructions

Atlas / Ayra ("Fase 0") is a single Python FastAPI service: a portable, user-owned memory
core exposed as a chat product. There is one deployable service (the FastAPI app), which
also serves a minimal static web console at `/`. Standard setup/run commands live in
`README.md`; per-endpoint details are in `app/api/`.

### Environment
- Python 3.12 with a virtualenv at `.venv` (created by the startup update script). Activate
  with `. .venv/bin/activate`, or call binaries directly via `.venv/bin/...`.
- Dependencies: `requirements.txt` (pip). No lockfile, no `pyproject.toml`, no build step.
- SQLite is embedded and auto-migrated at startup (`db.migrate()` in the lifespan handler);
  the `./data/` dir and `data/atlas.db` are created automatically and are gitignored.

### Non-obvious gotchas
- **The app requires a `.env` file to boot in offline dev mode.** The code default for
  `ATLAS_LLM_PROVIDER` is `gemini` (see `app/core/config.py`), and with the `gemini`
  provider the server intentionally refuses to boot without a valid `ATLAS_GEMINI_API_KEY`.
  For offline development/testing, ensure `.env` exists with `ATLAS_LLM_PROVIDER=fake`
  (copy from `.env.example`, which already ships with `fake`). The `.env` file is gitignored.
  With `fake`, the whole product runs with no API key and no internet.
- The `fake` provider echoes input as tokens; replies start with `[fake] ...`. This is
  expected, not a bug.
- Auth: `ATLAS_API_TOKEN` is optional in `dev` (`ATLAS_ENV=dev`) but required in `prod`.
  In dev, requests need no `Authorization` header; `user_id` always comes from config/token
  (default `caio`), never from the request body.

### Run / test / lint
- Run (dev): `. .venv/bin/activate && uvicorn app.main:app --reload` → serves on `:8000`
  (web console at `/`, Swagger at `/docs`, health at `/health`).
- Test: `python -m pytest tests/ -v` (needs no network or API key; 5 tests).
- Lint: `ruff check .` — note there are 2 pre-existing `F401` unused-import warnings in
  `app/api/chat.py` and `app/api/memory.py`; these are not introduced by setup.
