.PHONY: test test-unit test-integration lint install

PYTHON := .venv/bin/python

install:
	uv sync --extra dev

test:
	$(PYTHON) -m pytest tests/ -v

test-unit:
	$(PYTHON) -m pytest tests/unit/ -v

test-integration:
	$(PYTHON) -m pytest tests/integration/ -v

test-cov:
	$(PYTHON) -m pytest tests/ --cov=app --cov-report=term-missing -v

dev:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Docker targets
up:
	docker compose up

up-build:
	docker compose up --build

down:
	docker compose down

test-docker:
	docker compose -f docker-compose.test.yml run --rm tests
