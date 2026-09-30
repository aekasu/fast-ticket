.PHONY: doctor test test-unit test-integration test-e2e lint install

PYTHON := .venv/bin/python

install:
	uv sync --extra dev

test:
	$(PYTHON) -m pytest tests/ -v

test-unit:
	$(PYTHON) -m pytest tests/unit/ -v

test-integration:
	$(PYTHON) -m pytest tests/integration/ -v

# Live end-to-end tests against the running stack (`make start` first). Source .env so the
# admin login is available. Writes are executed by the osticket-worker service (~1 s); if a command is
# still pending after 20 s the tests run osTicket's cron themselves (E2E_CRON_CMD):
#   set -a; . ./.env; set +a; make test-e2e
test-e2e:
	E2E_CRON_CMD="docker compose exec -T -u www-data osticket-cron php /var/www/html/api/cron.php" \
	    $(PYTHON) -m pytest tests/e2e -m e2e -v

# Preflight of the osTicket instance the API points at (prefix, plugin tables, every read query
# against the live schema, plugin handshake, DB privileges, queue health). Exit 1 on a failure.
doctor:
	docker compose exec -T api /app/.venv/bin/python -m app.doctor

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
	docker compose -f docker-compose.test.yml run --build --rm tests

# Tear down everything for a clean install on next 'make start'.
# Removes all volumes: DB, and the persisted osTicket ost-config.php.
# Only needed for a deliberate wipe — 'make start' is idempotent on its own.
reset:
	docker compose down -v
	@echo "Reset complete. Run 'make start' to reinstall from scratch."

logs:
	docker compose logs -f osticket
