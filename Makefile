SHELL := /bin/bash
.PHONY: run test migrate setup auth

PYTHON := .venv/bin/python
HOST ?= 127.0.0.1
PORT ?= 8000

run:
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

test:
	$(PYTHON) -m pytest -q

migrate:
	$(PYTHON) -m alembic upgrade head

# Prints what is missing (non-zero exit if anything is); creates the API key if absent.
setup:
	@$(PYTHON) -c "from app.api.auth import get_api_key; get_api_key()"
	@missing=0; \
	read -r model url creds token <<< "$$($(PYTHON) -c "from app.config import settings as s; from app.auth.gmail_auth import _token_path; print(s.ollama_model, s.ollama_base_url, s.gmail_credentials_file, _token_path())")"; \
	if ! command -v ollama >/dev/null; then echo "MISSING ollama binary: install from https://ollama.com"; missing=1; \
	elif ! tags=$$(curl -fsS -m 3 "$$url/api/tags"); then echo "MISSING ollama server at $$url: run 'ollama serve'"; missing=1; \
	elif ! echo "$$tags" | grep -q "\"name\":\"$$model\""; then echo "MISSING model $$model: run 'ollama pull $$model'"; missing=1; \
	else echo "ok      ollama + $$model"; fi; \
	if [ ! -f "$$creds" ]; then echo "MISSING $$creds: see docs/gmail-oauth-setup.md"; missing=1; else echo "ok      $$creds"; fi; \
	if [ -f "$$token" ]; then echo "ok      Gmail token ($$token)"; else echo "TODO    Gmail not authorized yet: run 'make auth'"; fi; \
	echo "ok      API key created (the companion reads it automatically)"; \
	exit $$missing

auth:
	$(PYTHON) -c "from app.auth.gmail_auth import get_credentials; get_credentials()"
