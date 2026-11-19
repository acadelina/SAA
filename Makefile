PYTHON ?= python3
VENV ?= .venv

.PHONY: install test test-integration lint up down db-init backtest

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"

test:
	$(VENV)/bin/pytest -m "not integration"

test-integration:
	$(VENV)/bin/pytest -m integration

backtest:
	$(VENV)/bin/python -I scripts/backtest/run.py report --work work --cutoff 2024-05-31

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/mypy

up:
	docker compose up -d

down:
	docker compose down

db-init:
	docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < sql/001_init.sql
