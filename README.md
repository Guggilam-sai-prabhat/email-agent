# mail-agent

Privacy-focused local email agent. It reads explicitly permitted Gmail
messages, analyzes them with a local Qwen3 model via Ollama, and tells you
what needs your attention: a daily brief, a list of requests still awaiting
your reply, and a desktop orb that nags you about the old ones. Read-only: it
never sends, deletes, forwards, labels, modifies, or replies to email. Nothing
leaves your machine.

## How it works

1. **Auth** — one-time OAuth with the single `gmail.readonly` scope
   ([docs/gmail-oauth-setup.md](docs/gmail-oauth-setup.md)). The token is
   stored outside the repo, mode 0600.
2. **Run** — `POST /api/v1/runs` (manual, or the optional scheduler) fetches
   recent messages, sanitizes them, and has the local model produce a
   validated summary, priority, category, action items and deadlines.
   Results are stored in SQLite.
3. **Brief** — `GET /api/v1/daily-brief` groups the stored analyses into
   urgent / deadlines / needs reply / other actions. Completed or dismissed
   items and requests you have already replied to are left out.
4. **Open items** — `GET /api/v1/open-items` lists requests still awaiting a
   reply (checked read-only against your Gmail Sent label) with their age and
   a `nag_level`: `quiet`, `noticeable` (≥ 4 h) or `persistent` (≥ 24 h).
5. **Companion** — the Tauri desktop orb in
   [desktop-companion/](desktop-companion/) polls the API. The ring turns
   amber for noticeable and red for persistent items, with one native
   notification per item naming the sender and age. A persistent item keeps
   the orb on screen until you reply to it or dismiss it.

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && cp .env.example .env
ollama pull qwen3:1.7b            # needs Ollama running: ollama serve

# 2. Put your OAuth client file at ./credentials.json (docs/gmail-oauth-setup.md),
#    then check everything. It prints what is missing:
make setup

# 3. Authorize Gmail (opens a browser once) and create the DB
make auth && make migrate

# 4. Start the API (127.0.0.1:8000) and trigger a first run
make run                           # in another terminal:
KEY=$(cat ~/.config/mail-agent/api_key); curl -X POST -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/runs

# 5. Start the companion
cd desktop-companion && npm install && npm run tauri dev
```

The backend creates the shared secret at `~/.config/mail-agent/api_key` and the
companion reads it from there, so there is nothing to copy. On first launch the
companion opens a setup wizard that checks disk space, Ollama and the model,
then walks you through the Google credentials. `interval_minutes` in the
companion's `companion-settings.json` store sets the poll interval (default 30).

Or skip the manual steps: `./start.sh` (Mac/Linux) or `start.bat` (Windows)
installs dependencies on first run, migrates the DB, starts the backend and
opens the companion.

## Configuration

Set via environment or `.env` (see `.env.example`):

- `OLLAMA_MODEL` — default `qwen3:1.7b`; tested alternatives and eval results
  are in [docs/model-selection.md](docs/model-selection.md). The model is never
  pulled automatically.
- `OLLAMA_BASE_URL` — must be a local address; the client refuses anything
  else.
- `SCHEDULER_ENABLED` — run on a schedule (every `SCHEDULE_INTERVAL_MINUTES`)
  instead of only manually.
- `NAG_NOTICEABLE_AFTER_HOURS` / `NAG_PERSISTENT_AFTER_HOURS` — open-item
  thresholds (4 / 24).
- `API_KEY_FILE` — where the API key lives (default `~/.config/mail-agent/api_key`).

## API

Mounted under `/api/v1`; interactive docs at `http://127.0.0.1:8000/docs`.
Every request needs an `X-API-Key` header matching `.api_key` (generated on
startup, mode 0600). `/health` is open. CORS allows only the
Tauri origins.

- `POST /runs` — start a processing run (409 if one is in progress). Always
  uses the operator's configured label/query; the body is ignored.
- `GET /runs/{run_id}` — run status and counters.
- `GET /status` — model availability, Gmail authorization, last run.
- `GET /summaries`, `GET /summaries/{id}` — paginated, filterable analyses
  and safe message metadata. Never returns tokens or full email bodies.
- `PATCH /summaries/{id}/review`, `PUT /summaries/{id}/review/note` — mark an
  item reviewed, completed or dismissed, with an optional note.
- `POST /summaries/{id}/reprocess` — re-fetch read-only and re-analyze.
- `GET /daily-brief`, `GET /open-items` — described above.

**Keep the API on `127.0.0.1`.** The key is a single shared secret, not
per-user auth. Do not set `APP_HOST` to `0.0.0.0`, put the API behind a public
proxy, or port-forward it.

## Development

```bash
make test                          # unit/API tests
python -m eval.run_eval            # live eval against local Ollama
```

## Docker

```bash
docker compose up --build
```
