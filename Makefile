.PHONY: run test migrate

PYTHON := .venv/bin/python
HOST ?= 127.0.0.1
PORT ?= 8000

run:
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

test:
	$(PYTHON) -m pytest -q

migrate:
	$(PYTHON) -m alembic upgrade head
