PYTHON ?= python3
VENV ?= .venv

.PHONY: install test test-integration lint up down db-init

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"

test:
	$(VENV)/bin/pytest -m "not integration"

test-integration:
	$(VENV)/bin/pytest -m integration

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/mypy

up:
	docker compose up -d

down:
	docker compose down

db-init:
	docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < sql/001_init.sql
