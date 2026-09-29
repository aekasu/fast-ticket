---
name: dev-stack
description: Boot, reset, and debug the fast-ticket Docker stack (db, osticket, osticket-cron, api) and verify the FastAPI → inbox → PHP bridge → outbox → WebSocket round-trip hop by hop. Use when the stack won't start, commands stay pending, login fails, events don't arrive, or the user asks to run/smoke-test the app.
---

# Dev stack & bridge debugging

Full runbook: `notes/fast-ticket-bridge-bringup-and-testing.md`. Read Part 1 (footguns) before debugging anything.

## Services (`docker-compose.yml`)

| Service | Role | Host access |
|---|---|---|
| `db` | MariaDB with osTicket schema + `ost_integration_inbox/outbox` | `localhost:13306` |
| `osticket` | osTicket web + bridge plugin (installer + `bridge/install.php` on first boot) | http://localhost:8080 (`/scp` admin) |
| `osticket-cron` | Runs osTicket cron → `processInbox()` — **the only thing that executes commands** | — |
| `api` | FastAPI | http://localhost:8000 (`/docs`, `/health`) |

## Common commands

```bash
make start        # first boot: generates .env, builds, boots everything (never bare `docker compose up` first time)
make logs         # osticket logs
docker compose logs -f osticket-cron api
docker compose ps
make reset        # destroys volumes + resets ost-config.php → then `make start`
```

Never `cat` / print `.env` — it holds generated secrets. Read `.env.example` for variable names.

## Known footguns (summary — details in the runbook Part 1)

1. First boot must go through `make start`.
2. `setup_env.sh` may leave `OSTICKET_AUTO_SETUP=false` — headless install then won't run.
3. Cron runs only in `osticket-cron`; if it's down, commands stay `pending` forever.
4. Writes are async: expect up to ~60 s before an inbox row is processed.
5. Permission keys from `bridge/auth.php` may not match the keys routes check → unexpected 403s.
6. Two things can register `/api/auth/integration` — know which one answers.
7. WebSocket auth uses the `Sec-WebSocket-Protocol` header, not a query param.

## Debug hop by hop (never start with the full chain)

1. **DB / inbox state**
   ```bash
   docker compose exec db sh -c 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE" -e \
     "SELECT id,command_type,status,error_message,created_at,processed_at FROM ost_integration_inbox ORDER BY id DESC LIMIT 10;
      SELECT id,event_type,aggregate_id,status,created_at FROM ost_integration_outbox ORDER BY id DESC LIMIT 10;"'
   ```
   - `pending` stuck → cron not running / plugin not enabled / no `case` for the command type.
   - `failed` → read `error_message`; it's the exception thrown by the PHP handler.
2. **Auth hop A** — osTicket bridge auth directly (bypasses FastAPI). **Hop B** — from inside `api` using the compose hostname `osticket`. **Hop C** — `POST /api/v1/auth/login` through FastAPI. See runbook Part 2 for exact curls.
3. **Write direction in isolation** — runbook Part 3a (insert an inbox row, run cron, inspect result).
4. **Event direction in isolation** — runbook Part 3b/3c (insert an outbox row, watch the worker, WebSocket smoke client).
5. **Read SQL against real data** — runbook Part 3d (`read_repo_smoke_test.py` via `uv run python`).

Put throwaway smoke scripts in the session scratchpad or `notes/.scratch/` (gitignored), not the repo root.

## Tests without the stack

`make test` uses in-memory SQLite and needs no containers. `make test-docker` runs the same suite in `docker-compose.test.yml`.
