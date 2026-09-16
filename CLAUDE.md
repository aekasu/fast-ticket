# fast-ticket — Agent Guide

## Project purpose

**fast-ticket** wraps osTicket's PHP model layer with a clean FastAPI interface through a **PHP integration bridge plugin** (installed inside osTicket itself). The goal is to expose osTicket's native capabilities as a well-typed, secured REST API — first mirroring every operation the PHP model layer supports, then building custom functionality on top.

This is not a re-implementation of osTicket. All writes go through the bridge; reads query osTicket's MySQL tables directly (read-only).

---

## Repository layout

```
fast-ticket/
├── app/                        # FastAPI application (Python 3.12, uv)
│   ├── core/                   # Config, DB session, auth, events, websocket manager
│   ├── integration/
│   │   └── osticket/           # Bridge boundary: commands (inbox), outbox events, raw SQL reads
│   └── modules/                # One sub-package per API domain
│       ├── tickets/
│       ├── tasks/
│       ├── users/
│       └── …
├── tests/
│   ├── conftest.py             # Auth override + SQLite in-memory fixtures
│   └── integration/            # Integration tests (SQLite + ASGITransport)
├── notes/
│   └── OSTICKET_CAPABILITIES.md  # Full capability inventory — read this before adding any endpoint
├── pyproject.toml              # uv-managed dependencies
├── CLAUDE.md                   # ← you are here
└── fitsm.zip                   # Predecessor snapshot (reference only, do not unzip into repo)
```

> The `fitsm.zip` is a reference snapshot of the messy predecessor. Extract it to a temp dir when you need to check how something was done before, then discard. Never commit its contents into this repository.

---

## Architecture

### The integration bridge (write path)

All mutation operations use a **command inbox** pattern:

1. FastAPI writes a row into `ost_integration_inbox` (our table, same MySQL DB as osTicket).
2. A cron-driven PHP plugin picks up `pending` rows and calls the matching osTicket PHP model method.
3. On success/failure the plugin writes a result event into `ost_integration_outbox`.
4. A background `asyncio` worker (`app/workers/events.py`) polls the outbox, dispatches to registered Python handlers, and broadcasts over WebSocket.

**Never bypass the bridge.** Direct MySQL writes to osTicket tables (other than our own `ost_integration_*` tables) are forbidden — they skip PHP-side validation, filters, SLA logic, email notifications, and audit trails.

### Read path

`app/integration/osticket/read_repository.py` contains raw SQL queries against osTicket's tables. All reads are **read-only** (`SELECT` only). Keep them in this one module — no osTicket SQL anywhere else in the app.

### Command dispatch

`app/integration/osticket/commands_service.py` — the single entry point for writing to the inbox. Every command carries:
- `command_type` — dot-namespaced string, e.g. `ticket.reply`, `task.assign`
- `aggregate_type` / `aggregate_id` — the resource being acted on
- `payload` — JSON dict of operation-specific fields
- `requested_by_staff_id` — the authenticated agent
- `idempotency_key` — forwarded from the `Idempotency-Key` HTTP header; prevents duplicate commands on retry

### Outbox events

`app/integration/osticket/models.py` defines `IntegrationOutbox`. Events flow from osTicket PHP → outbox → Python handlers. Event types mirror osTicket's own naming where possible (e.g. `ticket.created`, `user.created`).

---

## Security model

- **Auth**: Staff authenticate against osTicket's own PHP auth endpoint (`OSTICKET_AUTH_URL`). On success, FastAPI mints a short-lived JWT (HS256). All API routes require `Bearer` token except `/health` and `/api/v1/auth/login`.
- **No hardcoded secrets**: `JWT_SECRET_KEY` falls back to a per-process random secret if unset (safe for single-instance; use an explicit secret for multi-instance).
- **No `is_admin` flag in JWT**: Authorization is coarse today (any authenticated staff can call any endpoint). A proper Role-based permission layer mapped to osTicket's `PERM_*` system is a planned follow-up.
- **CORS**: `CORS_ORIGINS` env var, comma-separated. Default is `localhost:3000` — always override in production.
- **Idempotency**: POST commands accept an `Idempotency-Key` header. Re-submitting the same key returns the original command record without creating a duplicate.

---

## osTicket capability reference

`notes/OSTICKET_CAPABILITIES.md` is the authoritative map of every PHP model operation, its API suitability rating, permissions, and relationships. **Read the relevant section before implementing any new endpoint.** The summary matrix is in §2; detailed notes per domain start at §3.

Priority order for implementing new functionality:
1. **High suitability** domains first: tickets (full lifecycle), threads/messages, users, organizations, staff, teams, departments, help topics, KB/FAQ/canned, tasks, SLA, banlist.
2. **Medium suitability** next: forms/custom fields (introspection + answers), priorities, filters, business hours, search, export.
3. **Low/not applicable**: queues query-builder internals, sequences, session/lock internals, plugin registration, raw config bulk-write, draft autosave, email piping.

---

## Key conventions

### Command types (established)
| Command type | Aggregate | Notes |
|---|---|---|
| `ticket.create` | ticket | aggregate_id = "" (pre-creation) |
| `ticket.reply` | ticket | |
| `ticket.assign` | ticket | staff_id in payload |
| `ticket.close` | ticket | |
| `ticket.reopen` | ticket | |
| `ticket.note` | ticket | title + message |
| `ticket.answered` / `ticket.unanswered` | ticket | |
| `ticket.collaborator.add` | ticket | user_id |
| `ticket.transfer` | ticket | dept_id + note |
| `ticket.change_owner` | ticket | user_id |
| `task.create` | task | |
| `user.set_org` | user | org_id (0 = remove) |

New command types must be registered in the PHP bridge plugin too — coordinate with the PHP side when adding write operations.

### Module structure
Each domain lives at `app/modules/<domain>/`:
- `router.py` — FastAPI routes + Pydantic request/response models
- `models.py` — SQLModel table models (local FastAPI DB only, not osTicket tables)
- `service.py` — business logic + `register_event_handlers(registry)` function

Routers must not import from other routers. Shared logic goes in `app/core/` or `app/integration/`.

### Ticket identifier
Use **osTicket ticket_id** (internal int PK) as the URL path parameter (e.g. `/{osticket_id}`). The public ticket `number` string is a display field. When in doubt, check §8.1.3 of the capabilities doc.

### Response shapes
- Write operations return `{"status": "accepted", "command_id": <int>}` — the command is async; success means "queued".
- Read operations return a top-level key matching the resource name, e.g. `{"tickets": [...]}`.
- Errors follow FastAPI's default `{"detail": "..."}` shape.

---

## Testing

**Stack**: `pytest` + `pytest-asyncio` + SQLite in-memory (via `aiosqlite`).

The test database uses `SQLModel.metadata.create_all` — every test gets a fresh schema via the `setup_db` autouse fixture. The auth dependency is overridden globally in `conftest.py`.

**Run tests**:
```bash
cd app/   # or wherever pyproject.toml lives
uv run pytest tests/ -v
```

**Rules**:
- Write a test before (or alongside) every new endpoint.
- Integration tests go in `tests/integration/` and use `ASGITransport` — no real server.
- Unit tests for pure functions (event handlers, service logic) go in `tests/unit/` (create as needed).
- Do not test against a real osTicket instance in the test suite — mock the bridge boundary.
- Tests must be deterministic and database-isolated (each test gets its own clean schema).

---

## Development environment

```bash
# Install dependencies
uv sync

# Run dev server (auto-reload)
uv run fastapi dev app/main.py

# Required environment variables (see .env.example)
DATABASE_URL=mysql+aiomysql://...   # osTicket's MySQL database
FASTAPI_INTEGRATION_KEY=<key>       # Must match PHP plugin config
JWT_SECRET_KEY=<secret>             # Optional; random if unset
```

`AUTO_CREATE_SCHEMA=true` (default) runs `SQLModel.metadata.create_all` at startup — safe for dev, disable in production and use Alembic migrations instead.

---

## Commit discipline

- Commits should be atomic: one logical change per commit.
- Commit message format: `<type>(<scope>): <summary>` — e.g. `feat(tickets): add team-assign command`, `test(tasks): cover task.close path`, `fix(auth): handle 503 from osticket`.
- Do not commit `fitsm.zip`, `__pycache__/`, `.env`, or extracted archive contents.

---

## What NOT to do

- Do not write SQL UPDATE/INSERT/DELETE against `ost_ticket`, `ost_staff`, `ost_thread_entry`, or any other core osTicket table — only SELECT.
- Do not add business logic to `read_repository.py` — it is a dumb query module.
- Do not import osTicket-schema models into non-integration modules — the `app/integration/` boundary must stay clean.
- Do not expose osTicket's `class.queue.php` query-builder logic as API resources — implement simple filter parameters instead.
- Do not expose sequences, session internals, plugin registration, or raw config bulk-write.
- Do not bypass the idempotency key check when dispatching commands.
