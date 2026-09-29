# fast-ticket — Agent Guide

Canonical instructions for any AI coding agent working in this repo (Claude Code, GitHub Copilot, Codex, etc.).
`CLAUDE.md` and `.github/copilot-instructions.md` both defer to this file — edit rules **here**.

## Project purpose

**fast-ticket** wraps osTicket's PHP model layer with a clean, security-first FastAPI REST API through a **PHP integration bridge plugin** (installed inside osTicket itself). The goal is to expose osTicket's native capabilities as a well-typed, properly secured API — first mirroring every high-suitability operation the PHP model layer supports, then building custom functionality on top.

This is **not** a re-implementation of osTicket. All writes go through the bridge; reads query osTicket's MySQL tables directly (read-only). There is no frontend — the API itself is the product.

---

## Repository layout

```
fast-ticket/
├── AGENTS.md                         # ← you are here (canonical agent rules)
├── CLAUDE.md                         # Claude Code entry point (imports this file)
├── .claude/skills/                   # Task playbooks (Claude Code + Copilot agent skills)
├── .github/                          # Copilot instructions, PR template
├── app/                              # FastAPI application (Python 3.12, uv)
│   ├── api/v1/router.py              # Mounts every module router
│   ├── core/                         # Config, DB, auth, security (require_perm), events, WS manager
│   ├── integration/osticket/         # Bridge boundary only: inbox model, dispatch, outbox, read-only SQL
│   ├── modules/<domain>/             # One sub-package per API domain (router / models / service)
│   └── workers/events.py             # Outbox poller → event handlers → WebSocket
├── bridge/                           # PHP integration bridge plugin (installed into osTicket)
│   ├── plugin.php                    # Signal hooks, inbox cron, dispatchCommand switch, outbox emit
│   └── handlers/<domain>.php         # FastApi<Domain>Handler classes — one static method per command
├── tests/
│   ├── conftest.py                   # In-memory SQLite, auth_user/admin_user/client fixtures
│   ├── unit/                         # Pure function tests (no I/O)
│   └── integration/                  # ASGITransport tests against the full app
├── docker/                           # osticket / fastapi / mysql images + init SQL
├── docker-compose.yml                # Local stack: osTicket + MySQL + FastAPI (+ cron)
├── docker-compose.test.yml           # Isolated test env (no real osTicket)
├── notes/                            # Research + runbooks (tracked)
└── pyproject.toml                    # uv-managed dependencies
```

> `fitsm.zip` is a reference snapshot of the messy predecessor. Extract to a temp dir when you need it, then discard. **Never commit its contents.**

---

## Architecture

### Write path — the integration bridge

All mutation operations use a **command inbox** pattern:

1. FastAPI validates + authorizes the request and writes a row to `ost_integration_inbox` via `dispatch_command(...)`.
2. The PHP plugin (`bridge/plugin.php`, `cron` signal) polls `pending` rows and calls the matching `bridge/handlers/*.php` method, which uses osTicket's PHP models.
3. On success/failure the plugin writes a result event to `ost_integration_outbox` (event name from `commandToEventType()`).
4. The asyncio worker (`app/workers/events.py`) polls the outbox, dispatches to registered Python handlers, and broadcasts over WebSocket.

The plugin also hooks osTicket's `model.*` / `object.*` signals so changes made in the osTicket UI emit outbox events too.

**Never bypass the bridge.** Direct writes to osTicket's own tables are forbidden — they skip PHP validation, filters, SLA computation, email notifications, and audit trails.

### Read path

`app/integration/osticket/read_repository.py` — raw `SELECT`-only SQL against osTicket tables. This is the only file that may contain osTicket-schema SQL. Services map rows → Pydantic models.

### Command dispatch

`app/integration/osticket/commands_service.py::dispatch_command` is the single entry point for writing to the inbox. Every command carries:
- `command_type` — dot-namespaced, e.g. `ticket.reply`, `task.assign`, `kb.faq.create`
- `aggregate_type` / `aggregate_id` — the resource (`aggregate_id=""` for creates)
- `payload` — JSON dict built from a typed Pydantic body (`body.model_dump()`), never a raw dict from the client
- `requested_by_staff_id` — `current_user["staff_id"]`
- `idempotency_key` — from the `Idempotency-Key` header; a repeat key returns the original inbox row instead of creating a new one

### Command type registry — source of truth

There is no hand-maintained table. A command type exists when **all three** agree:
1. A Python service calls `dispatch_command(command_type="<x>", ...)`
2. `bridge/plugin.php::dispatchCommand()` has a `case '<x>':` routing to a handler
3. `bridge/plugin.php::commandToEventType()` maps `<x>` → its past-tense event (`ticket.close` → `ticket.closed`)

Domain prefixes in use: `ticket.*`, `task.*`, `user.*`, `org.*`, `staff.*`, `dept.*`, `team.*`, `topic.*`, `sla.*`, `priority.*`, `kb.category.*`, `kb.faq.*`, `kb.canned.*`, plus banlist/forms. List them with:
```bash
grep -oP "case '\K[a-z_.]+" bridge/plugin.php
```

---

## Security model

Security is implemented **in tandem** with each domain, not as a later layer.

### Authentication
- Staff authenticate via the osTicket bridge auth endpoint (`OSTICKET_AUTH_URL`, `bridge/auth.php`).
- On success, FastAPI mints a short-lived JWT (HS256, configurable expiry).
- All routes require `Bearer` except `GET /health` and `POST /api/v1/auth/login`.
- `JWT_SECRET_KEY` must be set in production (startup fails if missing and `REQUIRE_JWT_SECRET=true`).

### Authorization
- JWT claims carry `staff_id`, `username`, `is_admin`, `permissions` (osTicket `PERM_*`-derived keys).
- Every route declares its permission as a default-value dependency:
  ```python
  current_user: dict = require_perm("task.assign"),   # NOT Depends(require_perm(...))
  current_user: dict = require_admin(),               # when osTicket has no finer PERM_* (staff/teams)
  ```
- `app/core/security.py` houses all permission dependencies. Admins pass every `require_perm`.
- Document the module's permission map in the router's module docstring.
- The coarse "any authenticated staff can do anything" model from the predecessor is **not acceptable**.

### Rate limiting
- Global token-bucket limit at middleware level (`slowapi`, `app/core/rate_limit.py`); tighter limit on auth.

### Input validation
- All request bodies are Pydantic models with explicit field constraints. No `Any`, no raw `dict` bodies.

### Secrets
- Nothing hardcoded. Missing required secrets = startup failure with a clear message. `.env.example` documents every variable. Never read or print `.env`.

### CORS
- `CORS_ORIGINS` comma-separated; empty = locked down. `allow_credentials=True` only when explicitly needed.

### Idempotency
- Every write route accepts the `Idempotency-Key` header and passes it to `dispatch_command`.

---

## osTicket capability references (`notes/`)

Read the relevant section **before** implementing any endpoint.
- `notes/OSTICKET_CAPABILITIES.md` — inventory of osTicket PHP model operations: suitability ratings, permissions, relationships (summary matrix §2, per-domain from §3, unverified items flagged in §8).
- `notes/INTEGRATION_CAPABILITIES.md` — API ↔ bridge matrix: required commands (§3), events (§4), read-throughs (§5), open decisions (§6).
- `notes/fast-ticket-bridge-bringup-and-testing.md` — Docker bring-up order, known footguns, testing each hop in isolation.

Implementation priority:
- **Wave 1 (high):** Tickets, Threads/Messages, Users, Organizations, Staff, Teams, Departments, Help Topics, Knowledgebase/FAQ, Canned Responses, Tasks, SLA, Banlist
- **Wave 2 (medium):** Dynamic Forms, Custom Lists, Priorities, Filters, Business Hours, Search, Export, Logs
- **Wave 3 (low/skip):** Queue query-builder internals, Sequences, Session/Lock internals, Plugin registration, Raw config bulk-write, Draft autosave, Email piping

---

## Key conventions

### Module structure (`app/modules/<domain>/`)
- `router.py` — routes + permission deps; thin, delegates to service
- `models.py` — Pydantic request/response schemas (and SQLModel tables for **local** FastAPI tables only — never osTicket tables)
- `service.py` — business logic, row→model mapping, `dispatch_command` calls, `register_event_handlers(registry)`
- New routers are mounted in `app/api/v1/router.py`.

Routers must not import from other routers. Cross-cutting logic goes in `app/core/` or `app/integration/`. (Importing shared *models* such as `app.modules.tickets.models.CommandResponse` is fine.)

### Identifiers
Use the osTicket internal int PK (`ticket_id`, `task_id`, …) as the path parameter. The public `number` string is display-only.

### Response shapes
- **Write/command** → HTTP **202**, `response_model=CommandResponse` → `{"command_id": int, "status": "pending", "idempotency_key": str | null}`
- **Reads** → top-level key matching the resource, e.g. `{"tickets": [...]}`; single resources return the detail model; 404 with `{"detail": "..."}` if missing
- **Errors** → FastAPI `{"detail": "..."}` with the right status code

---

## Testing

**Stack:** `pytest` + `pytest-asyncio` (auto mode) + in-memory SQLite (`aiosqlite`). Docker test env for true end-to-end against real osTicket.

```bash
make test              # all  (== .venv/bin/python -m pytest tests/ -v)
make test-unit         # fast, no I/O
make test-integration  # ASGITransport + SQLite
make test-cov
```

**Rules:**
- Write tests **in tandem** with implementation — not after.
- Every endpoint gets at least: happy path, missing auth (401), wrong permission (403), invalid input (422), and — for writes — duplicate `Idempotency-Key` returns the same `command_id` with a single inbox row.
- Reads: patch `app.modules.<domain>.service.repo.<fn>` with `AsyncMock`. Writes: assert the `IntegrationInbox` row (`command_type`, `aggregate_id`, `payload`, `requested_by_staff_id`).
- No test may depend on another's state (the autouse `setup_db` fixture rebuilds the schema).
- Security properties are tested explicitly: auth bypass, permission boundaries, malformed/expired tokens.

---

## Docker / dev environment

```bash
make start        # generate .env (scripts/setup_env.sh) + build + boot full stack
make logs         # follow osTicket logs
make reset        # nuke volumes + ost-config.php for a clean reinstall
make install      # uv sync --extra dev
make dev          # local uvicorn with reload (needs external MySQL)
make test-docker  # isolated test suite in Docker
```

FastAPI: http://localhost:8000 (`/docs`) · osTicket: http://localhost:8080 (`/scp`) · MySQL on host port 13306.

---

## Commit discipline

- Atomic commits: one logical change per commit.
- Format: `<type>(<scope>): <summary>` — types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `security`.
  - `feat(tickets): add team-assign command + permission check`
  - `fix(bridge): handle 503 from osTicket auth endpoint`
  - `security(rate-limit): tighten auth endpoint to 10 req/min`
- Do not commit: `fitsm.zip`, `__pycache__/`, `.env`, extracted archive contents, compiled PHP caches.

---

## What NOT to do

- No `UPDATE`/`INSERT`/`DELETE` against any `ost_*` table except `ost_integration_inbox` / `ost_integration_outbox`.
- No business logic in `read_repository.py` — it is a dumb query layer.
- No osTicket-schema models or SQL outside `app/integration/`.
- Don't expose queue query-builder, sequences, sessions, draft autosave, or raw config bulk-write.
- Don't skip the idempotency check in `dispatch_command`.
- Don't merge a route without `require_perm(...)` / `require_admin()` (or an explicit `# no permission required: <why>` comment).
- No `Any` in request schemas — every field explicitly typed and constrained.
- Don't add a command type in Python without the matching bridge `case` + `commandToEventType` entry (and vice versa).
- Don't build a frontend — the API and its test suite are the deliverable.
