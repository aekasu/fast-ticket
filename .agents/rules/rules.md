# fast-ticket — Agent Guide

## Project purpose

**fast-ticket** wraps osTicket's PHP model layer with a clean, security-first FastAPI REST API through a **PHP integration bridge plugin** (installed inside osTicket itself). The goal is to expose osTicket's native capabilities as a well-typed, properly secured API — first mirroring every high-suitability operation the PHP model layer supports, then building custom functionality on top.

This is **not** a re-implementation of osTicket. All writes go through the bridge; reads query osTicket's MySQL tables directly (read-only). There is no frontend — the API itself is the product.

---

## Repository layout

```
fast-ticket/
├── .agents/
│   └── rules/
│       └── rules.md                  # ← you are here
├── app/                              # FastAPI application (Python 3.12, uv)
│   ├── core/                         # Config, DB, auth, security, events, WS manager
│   ├── integration/
│   │   └── osticket/                 # Bridge boundary only: commands, outbox events, read-only SQL
│   └── modules/                      # One sub-package per API domain
│       ├── tickets/
│       ├── tasks/
│       ├── users/
│       └── …
├── tests/
│   ├── conftest.py                   # Shared fixtures: in-memory DB, auth override
│   ├── unit/                         # Pure function tests (no I/O)
│   └── integration/                  # ASGITransport tests against full app
├── bridge/                           # PHP integration bridge plugin source
│   └── plugin.php                    # Installed into osTicket as a plugin
├── docker/
│   ├── osticket/                     # Custom osTicket Docker image + entrypoint
│   ├── fastapi/                      # Dockerfile for the FastAPI app
│   └── mysql/                        # Init SQL for our integration tables
├── docker-compose.yml                # One-command local dev: osTicket + MySQL + FastAPI
├── docker-compose.test.yml           # Isolated test environment (no real osTicket needed)
├── .env.example                      # All required env vars documented
├── notes/
│   └── INTEGRATION_CAPABILITIES.md  # Capability inventory — read before adding any endpoint
└── pyproject.toml                    # uv-managed dependencies
```

> `fitsm.zip` is a reference snapshot of the messy predecessor. Extract to a temp dir when you need it, then discard. **Never commit its contents** into this repository.

---

## Architecture

### Write path — the integration bridge

All mutation operations use a **command inbox** pattern:

1. FastAPI receives a validated, authenticated request and writes a row to `ost_integration_inbox` (our table, in the same MySQL DB as osTicket).
2. A cron-driven PHP plugin polls `pending` rows and calls the matching osTicket PHP model method.
3. On success/failure the plugin writes a result event to `ost_integration_outbox`.
4. An `asyncio` background worker (`app/workers/events.py`) polls the outbox, dispatches to registered Python handlers, and broadcasts events over WebSocket.

**Never bypass the bridge.** Direct MySQL writes to osTicket's own tables are forbidden — they skip PHP validation, filters, SLA computation, email notifications, and audit trails.

### Read path

`app/integration/osticket/read_repository.py` — raw SQL queries against osTicket tables. All `SELECT` only. This is the only file in the project that may contain osTicket-schema SQL.

### Command dispatch

`app/integration/osticket/commands_service.py` is the single entry point for writing to the inbox. Every command carries:
- `command_type` — dot-namespaced string, e.g. `ticket.reply`, `task.assign`
- `aggregate_type` / `aggregate_id` — the resource being acted on
- `payload` — JSON dict of operation-specific fields
- `requested_by_staff_id` — authenticated agent ID embedded at dispatch time
- `idempotency_key` — from the `Idempotency-Key` HTTP header; prevents duplicate execution on client retry

### Outbox events

`app/integration/osticket/models.py` defines `IntegrationOutbox`. Event types mirror osTicket's naming where possible (`ticket.created`, `user.created`, etc.).

---

## Security model

Security is implemented **in tandem** with each domain, not as a later layer.

### Authentication
- Staff authenticate via the osTicket PHP bridge auth endpoint (`OSTICKET_AUTH_URL`).
- On success, FastAPI mints a short-lived JWT (HS256, configurable expiry).
- All routes require `Bearer` token except `GET /health` and `POST /api/v1/auth/login`.
- `JWT_SECRET_KEY` must be set explicitly in production (startup fails if missing and `REQUIRE_JWT_SECRET=true`).

### Authorization — Role-based (implemented per domain)
- JWT claims carry `staff_id`, `username`, `roles` (a list of osTicket `PERM_*` keys the agent holds).
- Each route declares its required permission via a `require_perm("ticket.reply")` dependency.
- `app/core/security.py` houses all permission-check dependencies.
- The coarse "any authenticated staff can do anything" model from the predecessor is **not acceptable here**.

### Rate limiting
- Global rate limit applied at middleware level (token-bucket, `slowapi` or equivalent).
- Auth endpoint has a tighter limit to resist brute-force.

### Input validation
- All request bodies are Pydantic models with explicit field constraints.
- No raw `dict` or `Any` payloads passed through to the bridge.

### Secrets
- No secrets hardcoded anywhere. Missing required secrets = startup failure with a clear message.
- `.env.example` documents every variable.

### CORS
- `CORS_ORIGINS` env var, comma-separated. Empty = locked-down (no origins).
- `allow_credentials` only set to `True` if explicitly needed per route.

### Idempotency
- Every POST command accepts `Idempotency-Key` header. Re-submission with same key returns the original command record (HTTP 200), not a duplicate execution.

---

## osTicket capability reference

`notes/INTEGRATION_CAPABILITIES.md` is the authoritative map of every PHP model operation, its API suitability rating, permissions, and relationships. **Read the relevant section before implementing any new endpoint.** Summary matrix in §2; per-domain details from §3 onward.

Implementation priority order:

**Wave 1 — High suitability (implement first):**
Tickets (full lifecycle), Threads/Messages, Users, Organizations, Staff, Teams, Departments, Help Topics, Knowledgebase/FAQ, Canned Responses, Tasks, SLA, Banlist

**Wave 2 — Medium suitability:**
Dynamic Forms (introspection + answer submission), Custom Lists, Priorities, Filters, Business Hours, Search, Export, Logs

**Wave 3 — Low/skip:**
Queue query-builder internals, Sequences, Session/Lock internals, Plugin registration, Raw config bulk-write, Draft autosave, Email piping

---

## Key conventions

### Command types (established)
| Command type | Aggregate | Notes |
|---|---|---|
| `ticket.create` | ticket | aggregate_id = "" (pre-creation) |
| `ticket.reply` | ticket | |
| `ticket.assign` | ticket | `staff_id` in payload |
| `ticket.assign_team` | ticket | `team_id` in payload |
| `ticket.close` | ticket | |
| `ticket.reopen` | ticket | |
| `ticket.note` | ticket | `title` + `message` |
| `ticket.answered` / `ticket.unanswered` | ticket | |
| `ticket.collaborator.add` | ticket | `user_id` |
| `ticket.transfer` | ticket | `dept_id` + `note` |
| `ticket.change_owner` | ticket | `user_id` |
| `ticket.delete` | ticket | `comments` |
| `ticket.merge` | ticket | `child_ids` list |
| `task.create` | task | |
| `task.assign` | task | `staff_id` |
| `task.close` | task | |
| `task.delete` | task | |
| `user.create` | user | |
| `user.update` | user | |
| `user.set_org` | user | `org_id` (0 = remove) |
| `user.delete` | user | |
| `staff.create` | staff | |
| `staff.update` | staff | |

New command types must also be handled in the PHP bridge plugin.

### Module structure

Each domain at `app/modules/<domain>/`:
- `router.py` — FastAPI routes, Pydantic schemas, route-level permission dependencies
- `models.py` — SQLModel ORM models (local FastAPI tables only, never osTicket tables)
- `service.py` — business logic, `register_event_handlers(registry)` function
- `schemas.py` — shared Pydantic models if router.py grows large

Routers must not import from other routers. Cross-cutting logic goes in `app/core/` or `app/integration/`.

### Ticket identifier
Use **osTicket `ticket_id`** (internal int PK) as the URL path parameter. The public `number` string is for display. Reference §8.1.3 of the capabilities doc.

### Response shapes
- **Write/command** → `{"status": "accepted", "command_id": <int>}` (async; success = queued)
- **Reads** → top-level key matching the resource, e.g. `{"tickets": [...]}`
- **Errors** → standard FastAPI `{"detail": "..."}` with appropriate HTTP status

---

## Testing

**Stack**: `pytest` + `pytest-asyncio` + SQLite in-memory (`aiosqlite`) for unit/integration; a separate Docker test environment for true end-to-end tests against a real osTicket instance.

**Run tests:**
```bash
uv run pytest tests/ -v
uv run pytest tests/unit/ -v           # fast, no I/O
uv run pytest tests/integration/ -v   # ASGITransport, SQLite in-memory
```

**Rules:**
- Write tests **in tandem** with implementation — not after.
- Every endpoint gets at least: happy path, auth missing (401), auth present but wrong permission (403), invalid input (422), idempotency (duplicate key).
- Integration tests use `ASGITransport` + SQLite in-memory — no real server or real osTicket.
- Unit tests cover: service functions, event handlers, permission checks, command dispatch logic.
- No test may rely on state from another test (always isolated schema via `setup_db` autouse fixture).
- Security properties are tested explicitly: auth bypass attempts, permission boundary crossings, malformed tokens.

---

## Docker / dev environment

```bash
# Full local stack: osTicket + MySQL + FastAPI (one command)
docker compose up

# Run test suite (isolated, no real osTicket)
docker compose -f docker-compose.test.yml run --rm tests

# Python-only dev (requires external MySQL)
uv sync
uv run fastapi dev app/main.py
```

**Required env vars** (all documented in `.env.example`):
```
DATABASE_URL              # mysql+aiomysql://<user>:<pass>@<host>/<db>
FASTAPI_INTEGRATION_KEY   # Must match PHP plugin config
JWT_SECRET_KEY            # Required in production (REQUIRE_JWT_SECRET=true)
CORS_ORIGINS              # Comma-separated allowed origins
```

---

## Commit discipline

- Atomic commits: one logical change per commit.
- Format: `<type>(<scope>): <summary>` — e.g.:
  - `feat(tickets): add team-assign command + permission check`
  - `test(auth): cover token expiry and invalid signature`
  - `fix(bridge): handle 503 from osTicket auth endpoint`
  - `security(rate-limit): tighten auth endpoint to 10 req/min`
- Do not commit: `fitsm.zip`, `__pycache__/`, `.env`, extracted archive contents, compiled PHP caches.

---

## What NOT to do

- Do not write `UPDATE`/`INSERT`/`DELETE` against any `ost_*` table that isn't `ost_integration_inbox` or `ost_integration_outbox`.
- Do not add business logic to `read_repository.py` — it is a dumb query layer.
- Do not import osTicket-schema models outside `app/integration/`.
- Do not expose osTicket's queue query-builder, sequences, sessions, draft autosave, or raw config bulk-write.
- Do not skip the idempotency check in `dispatch_command`.
- Do not merge a route that lacks a `require_perm(...)` dependency (or an explicit `# no permission required` comment with justification).
- Do not use `Any` type in request schemas — every field must be explicitly typed and constrained.
- Do not build a frontend — the API and its test suite are the deliverable.
