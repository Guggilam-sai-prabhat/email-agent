# mail-agent

Privacy-focused local email-summary agent. Reads explicitly permitted Gmail
messages, summarizes them with a locally running Qwen3 1.7B model via Ollama,
and extracts action items. Read-only: it never sends, deletes, forwards,
labels, modifies, or replies to email.

This is the foundation phase — email-processing orchestration is not wired up
yet. Gmail read access uses OAuth with the single `gmail.readonly` scope; see
[docs/gmail-oauth-setup.md](docs/gmail-oauth-setup.md) for setup.

## Local model (Ollama + Qwen3)

The agent calls a locally running [Ollama](https://ollama.com) server — no
email content or model prompts/output are ever sent off-machine.

```bash
ollama pull qwen3:1.7b
ollama serve
```

`OLLAMA_BASE_URL` must point at a local address (`127.0.0.1`, `localhost`, or
`::1`); the client refuses to start against anything else. If the model
hasn't been pulled, `OllamaModelClient` raises a clear error at startup
instead of pulling it automatically.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env

uvicorn app.main:app --reload
```

The API binds to `127.0.0.1:8000` by default. Check it with:

```bash
curl http://127.0.0.1:8000/health
```

## Manual API

Operator endpoints for starting runs and browsing results, mounted under
`/api/v1`. Interactive docs (OpenAPI): `http://127.0.0.1:8000/docs`.

- `POST /api/v1/runs` — start a manual processing run (rejects if one is
  already running). Always uses this operator's own configured Gmail label
  and query (`app/config.py`) — the request body is ignored, so a caller
  cannot redirect the run at arbitrary mailboxes/labels.
- `GET /api/v1/runs/{run_id}` — run status and counters.
- `GET /api/v1/summaries` — paginated, filterable list of analyzed emails
  (`priority`, `action_required`, `category`, `date_from`, `date_to`). Never
  returns OAuth tokens/credentials.
- `GET /api/v1/summaries/{summary_id}` — one analysis plus safe message
  metadata (subject, sender, dates). Never returns the full original email
  body.
- `POST /api/v1/summaries/{summary_id}/reprocess` — re-fetches the message
  read-only and reruns analysis; never calls a Gmail write/modify method.

**No authentication is implemented.** This API must never be exposed beyond
`127.0.0.1`/localhost — there is nothing standing between a network caller
and full read access to your email summaries plus the ability to trigger
runs. Do not change `APP_HOST` to `0.0.0.0`, put this behind a public
reverse proxy, or port-forward it, without first adding authentication.

## Tests

```bash
pytest
```

## Docker

```bash
docker compose up --build
```
