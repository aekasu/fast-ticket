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
│   ├── integration/osticket/         # Bridge boundary only: inbox model, dispatch, outbox, read-only SQL, doctor.py
│   ├── doctor.py                     # `make doctor`: preflight of the osTicket instance
│   ├── modules/<domain>/             # One sub-package per API domain (router / models / service)
│   └── workers/events.py             # Outbox poller → event handlers → WebSocket
├── bridge/                           # PHP integration bridge plugin (installed into osTicket)
│   ├── plugin.php                    # Signal hooks, inbox claim + dispatch, outbox emit, handshake route, settings (integration key)
│   ├── schema.php                    # the ONLY definition of the plugin's two tables (created on enable, prefix-aware)
│   ├── registry.php                  # command type → [handler file, class, method, outbox event] (one line each)
│   ├── worker.php                    # optional long-running inbox worker (~1 s latency); cron is the fallback
│   └── handlers/<domain>.php         # FastApi<Domain>Handler classes — one static method per command (+ common.php helpers)
├── tests/
│   ├── conftest.py                   # In-memory SQLite, auth_user/admin_user/client fixtures
│   ├── unit/                         # Pure function tests (no I/O)
│   ├── integration/                  # ASGITransport tests against the full app
│   └── e2e/                          # live tests of every command + read against the running stack (opt-in)
├── docker/                           # osticket / fastapi / mysql images + init SQL
├── docker-compose.yml                # Local stack: osTicket + MySQL + FastAPI (+ inbox worker + cron fallback)
├── docker-compose.test.yml           # Isolated test env on the image's pinned deps (no real osTicket)
├── uv.lock                           # committed: the image, the venv and CI must run the same versions
├── notes/                            # Research + runbooks (tracked)
└── pyproject.toml                    # uv-managed dependencies
```

> `fitsm.zip` is a reference snapshot of the messy predecessor. Extract to a temp dir when you need it, then discard. **Never commit its contents.**

---

## Architecture

### Write path — the integration bridge

All mutation operations use a **command inbox** pattern:

1. FastAPI validates + authorizes the request and writes a row to `ost_integration_inbox` via `dispatch_command(...)`.
2. The PHP plugin claims `pending` rows and calls the handler `bridge/registry.php` names, which uses osTicket's PHP models. Two drivers share one atomic claim: `bridge/worker.php` (a CLI loop, the `osticket-worker` service, ~1 s latency) and osTicket's own `cron` signal (once a minute — the fallback if no worker runs).
3. On success/failure the plugin writes a result event to `ost_integration_outbox` (event name from the registry entry).
4. The asyncio worker (`app/workers/events.py`) reads the outbox by a **per-process cursor** — every API process sees every event, so the clients of any worker are told and every process runs the registered handlers — and pushes each event only to the WebSocket clients allowed to hear it (`app/modules/events/visibility.py`). The outbox is a log, not a queue: rows are not claimed or retried.

Clients needn't poll: `GET /api/v1/commands/{id}?wait=<seconds ≤ 30>` holds the request until the command is `done`/`failed`.

The plugin also hooks osTicket's `model.*` / `object.*` signals so changes made in the osTicket UI emit outbox events too.

Dispatcher guarantees (`bridge/plugin.php`, keep them): each command first checks the requesting staff member **holds the permission the registry names for it** (`assertPermitted`: `admin`, `global:<key>` or `role:<key>` — what osTicket's own page requires, no admin bypass; a refusal fails the row with the reason); each command runs **as the requesting staff member** (global `$thisstaff` — osTicket's permission checks and authorship read it), in a **fresh PHP process per command** (osTicket keeps process-wide static state — `TaskForm::$instance` holds the previous task's answers — so the worker re-execs itself after each command and cron runs `worker.php --one` per command; the ORM cache is flushed too). The worker closes its database connection before every re-exec (`pcntl_exec` keeps open descriptors — an unclosed connection per command once exhausted `max_connections` and made logins answer 500) and recycles after `FASTAPI_WORKER_MAX_COMMANDS` commands (default 100), because each re-exec also leaks one file descriptor; `make doctor` warns when the connection peak nears the server's limit; any `Throwable` fails the row with osTicket's reason (never leaves it `processing`); a row held > 5 min is **failed, not retried**; secrets in a payload (`password`, `secret`, `token` keys) are **overwritten in the stored row once the command finishes**; a `*.create` handler that returns no id is **failed** (osTicket's `Model::create()` only instantiates, so "success" without an id means nothing was stored).

**Writing a handler is not guessing osTicket's API.** It is written for osTicket's web UI, and every handler here was once broken by assumption. Read the class *and* the admin page that does the same job (`/var/www/html/scp/*.php`) before writing one, build updates from the stored state overlaid with the payload (osTicket's `update()` overwrites every field and deletes members/permissions/links when keys are absent), and verify against the live stack. Details and the list of traps: `.claude/skills/add-bridge-command`.

**Never bypass the bridge.** Direct writes to osTicket's own tables are forbidden — they skip PHP validation, filters, SLA computation, email notifications, and audit trails.

### Read path

`app/integration/osticket/read_repository.py` — raw `SELECT`-only SQL against osTicket tables. This is the only file that may contain osTicket-schema SQL. Services map rows → Pydantic models.

**Scoped reads.** What an agent may see is decided in the query, mirroring osTicket's own rules (`ticket_visibility`, `task_visibility` in `read_repository.py`: `Staff::getTicketsVisibility` / `Ticket::checkStaffPerm`, and the task list block / `Task::checkStaffPerm` — the list and the detail rules genuinely differ and are reproduced as they are). Every scoped read takes a **required keyword `access=`** — the caller's `AccessContext`, or `scope.UNRESTRICTED` (only `search.all` uses it) — with no default, so a new read cannot forget. Thread and collaborator queries carry their own visibility check. A resource the agent may not open is a **404, never a 403**. Reads need no permission where osTicket needs none (`require_staff()`); do not invent one. The predicates are tested as real SQL over every state in `tests/unit/test_visibility_sql.py`. The lists an agent needs for a ticket form follow osTicket's own name-list procedures — `Staff::getDepartmentNames`, `Staff::getTopicNames` (ported literally in `app/modules/lookups/topic_visibility.py`), `Priority::getPriorities` — and canned responses follow `Canned::getCannedResponses` (the reply picker) and the management page (`canned.manage`); administrators get the Admin Panel's full lists. Details and the other reference data stay admin-only until their rule is ported.

Queries are written against osTicket's default `ost_` prefix and run through `_sql()`, which rewrites the prefix to `OSTICKET_TABLE_PREFIX` — **never call `text()` directly there**, or that instance's table names break. `make doctor` runs every function in this file against the live schema, so a new read function must be callable with `limit`/`offset`/`*_id` arguments only (a test enforces it).

Queries must match the **real osTicket 1.18 schema** (`DESCRIBE` it; don't assume): booleans such as active/enabled/deletable are usually bits in `flags`; dynamic-form values (ticket subject, task title, organization/user phone & notes) are in `ost_*__cdata`; threads are `ost_thread` by `object_type` + `object_id`. And they must be **NULL-safe** (`COALESCE`, `> 0` for "featured" 2) — osTicket stores NULL text and zero dates, and the mocked unit tests cannot see a row that breaks response validation.

### Command dispatch

`app/integration/osticket/commands_service.py::dispatch_command` is the single entry point for writing to the inbox. Every command carries:
- `command_type` — dot-namespaced, e.g. `ticket.reply`, `task.assign`, `kb.faq.create`
- `aggregate_type` / `aggregate_id` — the resource (`aggregate_id=""` for creates)
- `payload` — JSON dict built from a typed Pydantic body (`body.model_dump()`), never a raw dict from the client
- `requested_by_staff_id` — `current_user["staff_id"]`
- `idempotency_key` — from the `Idempotency-Key` header (≤ 200 characters, else 422); a repeat key **from the same staff member** returns the original inbox row instead of creating a new one (stored as `<staff id>:<key>`, so another agent's identical key is a different command)

### Command type registry — source of truth

A command type exists when **both** agree:
1. A Python service calls `dispatch_command(command_type="<x>", ...)`
2. `bridge/registry.php` has one line `'<x>' => ['<handlers file>', 'FastApi…Handler', '<method>', '<past-tense event>', '<permission>']` — the permission is `admin`, `global:<key>` or `role:<key>` and must equal the guard of the API route that queues it (a test derives that from the routers and fails on any mismatch) (`ticket.close` → `ticket.closed`)

Domain prefixes in use: `ticket.*`, `task.*`, `user.*`, `org.*`, `staff.*`, `dept.*`, `team.*`, `topic.*`, `sla.*`, `priority.*`, `kb.category.*`, `kb.faq.*`, `kb.canned.*`, plus banlist/forms. List them with:
```bash
grep -oP "^\s*'\K[a-z_.]+(?=' +=>)" bridge/registry.php
```

---

## Security model

Security is implemented **in tandem** with each domain, not as a later layer.

### Authentication
- Staff authenticate via the osTicket bridge auth endpoint (`OSTICKET_AUTH_URL`, `bridge/auth.php`), which asks osTicket's own registered staff backends to verify the password (so LDAP/SSO backends and the password policy apply) — never `StaffAuthenticationBackend::process()`, which writes a PHP session and emails 2FA codes.
- Refusals carry a code (`X-Login-Error` header): `invalid_credentials`, `account_disabled`, `two_factor_required`, `password_change_required`. **Accounts with 2FA — or that osTicket requires to enrol in 2FA — are refused loudly**, not let through: the API cannot complete a second factor yet.
- Failed logins are locked out by the API (`LOGIN_MAX_ATTEMPTS` per username + client address, `app/core/login_throttle.py`): osTicket's own strike counter is stored in the PHP session, which an API client can drop.
- On success, FastAPI mints a short-lived JWT (HS256, `ACCESS_TOKEN_EXPIRE_MINUTES`, default 30) with `iss`, `iat` and a unique `jti`. `get_current_user` also checks on **every request** that the staff member still exists and is enabled, so disabling an agent in osTicket ends their API access immediately, not at token expiry.
- All routes require `Bearer` except `GET /health` and `POST /api/v1/auth/login`.
- `JWT_SECRET_KEY` must be set in production, at least 32 characters (startup fails if missing and `REQUIRE_JWT_SECRET=true` — the compose api sets it).

### Authorization
- The token carries identity only. What an agent may do is an `AccessContext` (`app/core/access.py`) **loaded from osTicket's tables on every request** and mirroring `Staff::hasPerm()`, `getRoles()`, `getDepartments()`, `getTeams()` and `isAccessLimited()`; `current_user["access"]` holds it and `GET /api/v1/auth/me` shows it. A role change, demotion or disabled account applies on the next request.
- osTicket has **two kinds of permission** (`app/core/permissions.py`): *global* ones stored on the staff member (`user.*`, `org.*`, `faq.manage`, `emails.banlist`, `visibility.*`, `search.all`, `stats.agents`) and *role* ones stored on their roles, per department (`ticket.*`, `task.*`, `thread.edit`, `canned.manage`). `require_perm` asks the right place for each.
- Every route declares its gate as a default-value dependency, chosen by reading the osTicket page that does the same job (`scp/*.php`), not by guessing:
  ```python
  current_user: dict = require_perm("user.create"),   # NOT Depends(require_perm(...)); a real osTicket key or it fails at import
  current_user: dict = require_admin(),               # Admin Panel pages (admin.inc.php -> isAdmin()): staff, departments, teams, topics, SLAs, priorities, forms, banlist
  current_user: dict = require_staff(),               # any enabled agent (KB reads; reads scoped in the query by S3)
  ```
- **Reads are scoped to what osTicket shows the agent** (see Read path): `require_staff()` says who may ask, `access=` says what comes back.
- `app/core/security.py` houses the dependencies. **There is no admin bypass in `require_perm`** — osTicket's `hasPerm()` has none; an admin's power is the Admin Panel (`require_admin`), and an admin holds the other permissions they were given.
- Document the module's permission map in the router's module docstring.
- The coarse "any authenticated staff can do anything" model from the predecessor is **not acceptable**.

### WebSocket
- `GET /api/v1/ws` takes the token in the `Sec-WebSocket-Protocol` header (`bearer, <token>`), verifies it and loads the agent's live `AccessContext` — a disabled or deleted agent is refused, as on REST.
- **What a client is sent is decided per agent**: `app/modules/events/visibility.py` maps each event's aggregate to an audience — tickets and tasks to agents who have them in their list (osTicket's list visibility), users/organizations to `user.dir`, the knowledge base to everyone, canned responses to `canned.manage`, and everything else (including any aggregate the table does not know) to administrators only. **A new kind of event must be added to that table**; the default fails closed.
- Connections do not outlive their authority: every `WS_RECHECK_SECONDS` the manager closes expired tokens and disabled accounts and refreshes each agent's access. An agent may hold `WS_MAX_CONNECTIONS_PER_STAFF` sockets; a client that cannot take a message within `WS_SEND_TIMEOUT_SECONDS` is dropped; frames over 64 KiB are refused (`--ws-max-size`).

### Rate limiting
- Global limit per client IP through `SlowAPIMiddleware` (`RATE_LIMIT_DEFAULT`, `app/core/rate_limit.py` — slowapi enforces default limits only when the middleware is installed); tighter limit on login. The client address is `client_ip()`: `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_COUNT` says how many proxies append to it. `RATE_LIMIT_STORAGE_URI` can point at Redis to share counters between workers.
- Request bodies over `MAX_REQUEST_BYTES` get 413 (`app/core/request_limits.py`); every list endpoint caps its page size.

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
- `notes/KNOWN_LIMITS.md` — what fast-ticket does not do yet or does only partly (see "Known limits" below).
- `notes/install-on-an-existing-osticket.md` — installing the plugin zip on an existing osTicket, worker/cron, least-privilege DB user, `make doctor`.

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

**Stack:** `pytest` + `pytest-asyncio` (auto mode) + in-memory SQLite (`aiosqlite`); plus live end-to-end tests (`tests/e2e/`) against the real stack.

```bash
make test              # all  (== .venv/bin/python -m pytest tests/ -v)
make test-unit         # fast, no I/O
make test-integration  # ASGITransport + SQLite
make test-docker       # same suite on the image's pinned deps (uv.lock) — run it too
make test-e2e          # live: every command + every read, ~4 min; needs `make start` and OSTICKET_ADMIN_USERNAME / OSTICKET_ADMIN_PASSWORD exported
make test-cov
```

The unit/integration tests **mock the read repository and the bridge**, so they cannot catch a wrong SQL column, a handler calling an osTicket method that doesn't exist, or a NULL that breaks response validation — which were the majority of real defects. `tests/e2e/` does (create → read back → partial update → delete, plus osTicket's refusals, plus every GET). Anything touching `bridge/` or `read_repository.py` is not verified until `make test-e2e` passes; the final check must follow a rebuild of the image (`bridge/` is baked in). E2E tests must never modify seeded osTicket data. The e2e login comes from the environment: a human runs `set -a; . ./.env; set +a` in their own shell — an agent must not read `.env` (see Secrets) and should ask for the two variables to be exported.

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
make reset        # nuke all volumes (DB + persisted ost-config.php) for a clean reinstall; `make start` is idempotent on its own
make install      # uv sync --extra dev
make dev          # local uvicorn with reload (needs external MySQL)
make test-docker  # isolated test suite in Docker
make doctor       # preflight the osTicket instance the API points at (prefix, plugin tables, every read query, handshake, DB grants)
make bridge-zip   # dist/fastapi_integration-<version>.zip — the plugin, for an existing osTicket
```

The stack takes `OSTICKET_TABLE_PREFIX` and the host ports (`API_PORT`, `OSTICKET_PORT`, `MYSQL_PORT`) from the environment, so a second stack can run beside the first (`docker compose -p other up`) — that is how a non-`ost_` prefix is tested end to end. The bridge plugin creates the integration tables itself (`bridge/schema.php`); the API does not (`AUTO_CREATE_SCHEMA=false`).

FastAPI: http://localhost:8000 (`/docs`) · osTicket: http://localhost:8080 (`/scp`) · MySQL on host port 13306.

---

## Known limits

`notes/KNOWN_LIMITS.md` is the single, honest list of things that are not done or only partly done. It is a live
list, not a changelog.

- **Record a limit when you knowingly leave one.** A shortcut, a deferred check, a "works only for X", an untested
  path, an accepted risk — add an entry in the same commit (or the one that introduces the gap). Say what is true
  today, what it means for someone using it, and what will resolve it (a phase or step, or "by design").
- **Remove a limit when it is fixed**, in the commit that fixes it, and say so in the commit message. Delete the
  entry; git history keeps it. Don't leave "fixed" entries behind.
- **Review the whole list at every major update** — the end of a phase or security step, before a PR or merge,
  after a release, or when the list has not been touched for a while. For each entry ask "is this still true?": check
  the code, and run the thing if you can. Delete what is solved, correct what changed, and update the
  `Last reviewed` date. Report what you removed or added.
- Never soften an entry to make the list look better, and never delete one that is merely inconvenient.

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
- Don't merge a route without `require_perm(...)` / `require_admin()` / `require_staff()` (or an explicit `# no permission required: <why>` comment), and don't invent a permission key — use osTicket's (`app/core/permissions.py`) or `require_admin()`.
- No `Any` in request schemas — every field explicitly typed and constrained.
- Don't define the integration tables anywhere but `bridge/schema.php` (the SQLModel classes must mirror it; a test compares the columns), and don't put a `DEFAULT` on a TEXT column there (MySQL < 8.0.13).
- Don't add a command type in Python without the matching `bridge/registry.php` entry (and vice versa) — `tests/unit/test_bridge_registry.py` enforces it.
- Don't report a bridge handler as working because it returned without error; prove it stored the right thing (read it back) and that a partial update preserved the other fields.
- Don't write osTicket SQL from memory or from `notes/OSTICKET_CAPABILITIES.md`; check the schema and the class source.
- Don't put throwaway scripts anywhere but `notes/.scratch/` (gitignored).
- Don't build a frontend — the API and its test suite are the deliverable.
