.PHONY: test test-unit test-integration test-e2e lint install

PYTHON := .venv/bin/python

install:
	uv sync --extra dev

test:
	$(PYTHON) -m pytest tests/ -v

test-unit:
	$(PYTHON) -m pytest tests/unit/ -v

test-integration:
	$(PYTHON) -m pytest tests/integration/ -v

# Live round trip against the running stack. Source .env first so the admin login is available:
#   set -a; . ./.env; set +a; make test-e2e
test-e2e:
	$(PYTHON) -m pytest tests/e2e -m e2e -v

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

# Tear down everything for a clean install on next 'make start'.
# Removes all volumes: DB, and the persisted osTicket ost-config.php.
# Only needed for a deliberate wipe — 'make start' is idempotent on its own.
reset:
	docker compose down -v
	@echo "Reset complete. Run 'make start' to reinstall from scratch."

logs:
	docker compose logs -f osticket
