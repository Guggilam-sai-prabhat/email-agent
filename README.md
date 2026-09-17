# mail-agent

Privacy-focused local email-summary agent. Reads explicitly permitted Gmail
messages, summarizes them with a locally running Qwen3 1.7B model via Ollama,
and extracts action items. Read-only: it never sends, deletes, forwards,
labels, modifies, or replies to email.

This is the foundation phase — no Gmail or model integration yet.

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

## Tests

```bash
pytest
```

## Docker

```bash
docker compose up --build
```
