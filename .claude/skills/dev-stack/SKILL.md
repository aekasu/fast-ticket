---
name: dev-stack
description: Boot, reset, and debug the fast-ticket Docker stack (db, osticket, osticket-worker, osticket-cron, api) and verify the FastAPI → inbox → PHP bridge → outbox → WebSocket round-trip hop by hop. Use when the stack won't start, commands stay pending, login fails, events don't arrive, or the user asks to run/smoke-test the app.
---

# Dev stack & bridge debugging

Full runbook: `notes/fast-ticket-bridge-bringup-and-testing.md`. Read Part 1 (footguns) before debugging anything.

## Services (`docker-compose.yml`)

| Service | Role | Host access |
|---|---|---|
| `db` | MariaDB with osTicket schema + `ost_integration_inbox/outbox` | `localhost:13306` |
| `osticket` | osTicket web + bridge plugin (installer + `bridge/install.php` on first boot) | http://localhost:8080 (`/scp` admin) |
| `osticket-worker` | `bridge/worker.php` loop → executes commands within ~1 s (one fresh PHP process per command) | — |
| `osticket-cron` | osTicket cron once a minute → the same claim via `worker.php --one` — **fallback** if the worker is down | — |
| `api` | FastAPI | http://localhost:8000 (`/docs`, `/health`) |

## Common commands

```bash
make start        # first boot: generates .env, builds, boots everything (never bare `docker compose up` first time)
make logs         # osticket logs
docker compose logs -f osticket-worker api
docker compose ps
make reset        # destroys ALL volumes (DB + the persisted osTicket config) → then `make start`
make test-docker  # unit/integration suite on the image's pinned deps (rebuilds the test image)
make test-e2e     # live tests of every command/read against the running stack
                  # (needs OSTICKET_ADMIN_USERNAME/PASSWORD exported; a human sources .env, agents must not read it)
```

Never `cat` / print `.env` — it holds generated secrets. Read `.env.example` for variable names.

## Known footguns

Install / boot
1. First boot must go through `make start`. The install is **idempotent and state-driven** (`docker/osticket/entrypoint.sh` derives it from the DB `schema_signature` + the persisted config, not a marker file); every combination self-heals, and only a half-finished install (`make reset`) fails.
2. `ost-config.php` lives in the `osticket_config` **named volume** and is symlinked into osTicket — it is not in git and not on the host. `docker compose down -v` (or `make reset`) deletes it together with the DB.
3. osTicket's `api/.htaccess` rewrites every non-file `/api/*` URL to its own dispatcher; the bridge auth URL works only because `docker/osticket/apache.conf` rewrites `/api/auth/integration` first. The osticket healthcheck is a keyless POST expecting **403**.
4. The `api` image installs from `uv.lock`; after changing dependencies run `uv lock` and commit it. `./app` is bind-mounted with `--reload`, but **`bridge/` is baked into the osticket image** — rebuild (`docker compose up -d --build`) or `docker cp` to test bridge edits.

Bridge
5. The plugin only runs if osTicket *loads* it: `ost_plugin.install_path` must be the plugin **directory** (`plugins/fastapi_integration`), there must be an enabled `ost_plugin_instance` row, and the plugin needs a `PluginConfig` class; signals are connected in `bootstrap()`, not the constructor. `bridge/install.php` does all of this (idempotently, repairing old rows). If commands stay `pending`, check the plugin loads before anything else.
6. Commands are executed by `osticket-worker` (~1 s) with `osticket-cron` as the fallback (up to ~60 s); if both are down they stay `pending`. Clients can hold a request with `GET /api/v1/commands/{id}?wait=20`. To drain by hand: `docker compose exec -T -u www-data osticket-cron php /var/www/html/api/cron.php`. A row stuck `processing` > 5 min is failed by recovery, not retried.
7. `failed` rows carry osTicket's real reason in `error_message`; a handler that "succeeds" without creating anything is turned into `failed` (no id returned).

Auth / events
8. Permission keys from `bridge/auth.php` may not match the keys routes check → unexpected 403s.
9. WebSocket auth uses the `Sec-WebSocket-Protocol` header, not a query param.

## Debug hop by hop (never start with the full chain)

1. **DB / inbox state**
   ```bash
   docker compose exec db sh -c 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE" -e \
     "SELECT id,command_type,status,error_message,created_at,processed_at FROM ost_integration_inbox ORDER BY id DESC LIMIT 10;
      SELECT id,event_type,aggregate_id,status,created_at FROM ost_integration_outbox ORDER BY id DESC LIMIT 10;"'
   ```
   - `pending` stuck → worker and cron both down / plugin not enabled / no `bridge/registry.php` entry for the command type.
   - `failed` → read `error_message`; it's the exception thrown by the PHP handler.
2. **Auth hop A** — osTicket bridge auth directly (bypasses FastAPI). **Hop B** — from inside `api` using the compose hostname `osticket`. **Hop C** — `POST /api/v1/auth/login` through FastAPI. See runbook Part 2 for exact curls.
3. **Write direction in isolation** — runbook Part 3a (insert an inbox row, run cron, inspect result).
4. **Event direction in isolation** — runbook Part 3b/3c (insert an outbox row, watch the worker, WebSocket smoke client).
5. **Read SQL against real data** — runbook Part 3d (`read_repo_smoke_test.py` via `uv run python`).

Put throwaway smoke scripts in `notes/.scratch/` (gitignored; the session scratchpad may disappear mid-session), not the repo root. A handy pattern: a script that queues a command through the API, runs cron, then reads the inbox row and the API read-back — that is exactly what `tests/e2e/` automates.

## Tests

- `make test` — in-memory SQLite, no containers (local venv).
- `make test-docker` — same suite in `docker-compose.test.yml` on the image's pinned dependencies; also mounts `bridge/` read-only for `tests/unit/test_bridge_registry.py`.
- `make test-e2e` — live stack; needs `OSTICKET_ADMIN_USERNAME` / `OSTICKET_ADMIN_PASSWORD` exported (the admin login). See the `endpoint-tests` skill.
