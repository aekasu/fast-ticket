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
init:
	./scripts/setup_env.sh

start: init
	docker compose up --build -d

up:
	docker compose up

up-build:
	docker compose up --build

down:
	docker compose down

test-docker:
	docker compose -f docker-compose.test.yml run --rm tests

# Tear down everything and reset for a clean install on next 'make start'.
# Use this when the osTicket installation is broken or you want a fresh DB.
reset:
	docker compose down -v
	@echo "Resetting ost-config.php to sample format for clean reinstall..."
	cp docker/osticket/ost-sampleconfig.php docker/osticket/ost-config.php 2>/dev/null || \
	    git checkout docker/osticket/ost-config.php
	chmod 0666 docker/osticket/ost-config.php
	@echo "Reset complete. Run 'make start' to reinstall from scratch."

logs:
	docker compose logs -f osticket
