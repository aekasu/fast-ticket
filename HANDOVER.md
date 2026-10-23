# fast-ticket — Handover

Written 2026-10-23, from the repository as it stands on branch `main` (last commit `1da51f2`). It is the one document
to read first when you take over this project. It tells you what the system is, how a request travels through it, where
every kind of code lives, how to run and test it, which rules you must not break, and what is unfinished.

It does **not** replace the other documents; it points at them at the right moment. Where this file and the code
disagree, the code wins — and please fix this file.

> **How to read this:** Parts 1–4 give you the mental model (read all of them). Part 5 is the map of the repo. Parts 6–8
> are the day-to-day (run it, test it, change it). Parts 9–11 are the rules, the traps and the open work. Part 12 is a
> first-week plan. Part 13 is a glossary and a document index.

---

## Contents

1. [What this project is](#1-what-this-project-is)
2. [The architecture in one picture](#2-the-architecture-in-one-picture)
3. [Following one request end to end](#3-following-one-request-end-to-end)
4. [The five ideas that explain the whole design](#4-the-five-ideas-that-explain-the-whole-design)
5. [Repository map](#5-repository-map)
6. [Getting it running](#6-getting-it-running)
7. [Testing: the four layers](#7-testing-the-four-layers)
8. [How to change things (recipes)](#8-how-to-change-things-recipes)
9. [The rules that must not be broken](#9-the-rules-that-must-not-be-broken)
10. [Traps that have already bitten someone](#10-traps-that-have-already-bitten-someone)
11. [Current state and open work](#11-current-state-and-open-work)
12. [A suggested first week](#12-a-suggested-first-week)
13. [Reference: glossary, configuration, documents](#13-reference-glossary-configuration-documents)

---

## 1. What this project is

**fast-ticket** is a REST + WebSocket API for [osTicket](https://osticket.com) 1.18, written in Python 3.12 / FastAPI.

osTicket is a mature PHP helpdesk. All its business rules — who may see which ticket, SLA calculation, filters, email
notifications, audit trail — live in its PHP model layer, and that layer has no usable API. fast-ticket exposes
osTicket's capabilities as a typed, permission-checked HTTP API **without re-implementing osTicket**. osTicket stays the
system of record; fast-ticket is a well-behaved front door to it.

Facts worth holding in your head:

| | |
|---|---|
| **Deliverable** | The API and its test suite. **There is no frontend in this repo, and you must not add one.** A frontend lives in a separate repository (`../fast-ticket-frontend`) and talks only to the published contract (`notes/CLIENTS.md`, `notes/EVENTS.md`). |
| **Tested against** | osTicket **1.18.1** on **MariaDB 10.11** only. Nothing else has ever run. |
| **Size** | ~233 HTTP routes across 25 routers in 31 domain packages; 127 bridge command types; ~19.5k lines in the routers, services, `app/core`, `app/integration` and `bridge/` (models and tests not counted). |
| **Test suite** | 955 unit/integration tests (≈23 s) + 105 live end-to-end tests (≈7–9 min) including a differential "oracle" test against osTicket's own PHP. |
| **Status** | Feature-complete for osTicket's core agent workflow and most of the Admin Panel. The user (project owner) decides what comes next; the standing instruction is *depth over breadth* — do not add API breadth unprompted. |
| **Product goal** | "Replicate osTicket's interface on FastAPI first (reads scoped exactly as osTicket scopes them), custom features after. It should work against any osTicket instance out of the box." |

What it deliberately does **not** do (osTicket features judged unsuitable to expose): the queue query-builder, sequences,
sessions, draft autosave, raw config bulk-write, plugin registration/installation, email piping, cron. See
`AGENTS.md → What NOT to do`.

---

## 2. The architecture in one picture

```
                      ┌───────────────────────────── Docker network ─────────────────────────────┐
                      │                                                                          │
  client ──HTTP/WS──► │  api (FastAPI, port 8000)                                                │
  (web UI, CLI…)      │    │  ▲                                                                  │
                      │    │  │ reads: SELECT-only SQL, scoped to what the agent may see         │
                      │    │  └───────────────────────────────────────────────┐                  │
                      │    │ writes: INSERT one row                            │                  │
                      │    ▼                                                   │                  │
                      │  ┌──────────────────────────  MariaDB (one database)  ─┴──────────────┐   │
                      │  │  ost_*  (osTicket's own tables)                                    │   │
                      │  │  ost_integration_inbox   ← API writes commands, bridge claims them │   │
                      │  │  ost_integration_outbox  ← bridge writes result events, API reads  │   │
                      │  └───▲────────────────────────────────────────────────────┬───────────┘   │
                      │      │ claim + run                                        │ events         │
                      │  osticket-worker (PHP loop, ~1 s)  +  osticket-cron (fallback, ~60 s)     │
                      │      │   runs bridge handlers INSIDE osTicket, as the requesting agent    │
                      │  osticket (Apache+PHP, port 8080)  — osTicket + the bridge plugin         │
                      └──────────────────────────────────────────────────────────────────────────┘
                                         outbox ──► asyncio poller in the API ──► WebSocket (filtered per agent)
```

Five containers (`docker-compose.yml`): `db`, `osticket`, `osticket-worker`, `osticket-cron`, `api`. The three osTicket
containers are the **same image** with a different role switched by environment (`START_WORKER=1`, `START_CRON=1`).

The three data paths, which are the heart of the system:

| Path | Direction | Mechanism | Why it exists |
|---|---|---|---|
| **Write** | client → osTicket | API inserts a row in `ost_integration_inbox`; a PHP worker claims it and calls osTicket's own classes | Runs osTicket's validation, filters, SLA, notifications and audit trail. **Direct writes to `ost_*` tables are forbidden.** |
| **Read** | osTicket → client | Raw read-only SQL in `read_repository.py`, with the agent's visibility rules built into the query | Fast, simple lists. A few reads that SQL cannot answer go through the **bridge read mode** (synchronous HTTP call into PHP). |
| **Events** | osTicket → client | Bridge writes to `ost_integration_outbox`; an asyncio poller in each API process pushes to WebSocket clients, filtered per agent | Live updates without polling. |

---

## 3. Following one request end to end

The fastest way to understand the code is to trace **"close ticket 42"**. Every step names the file to open.

### 3.1 Login (once)

1. `POST /api/v1/auth/login` (form-encoded `username`, `password`) → `app/modules/auth/router.py`.
2. The API checks its own lockout (`app/core/login_throttle.py`: `LOGIN_MAX_ATTEMPTS` failures per username+IP).
3. `app/core/auth.py::authenticate_with_osticket` POSTs the credentials to the PHP endpoint `bridge/auth.php`
   (`OSTICKET_AUTH_URL`, guarded by the shared `X-Integration-Key`). PHP asks **osTicket's own registered staff
   backends** to verify the password — so LDAP/SSO backends and the password policy keep working.
4. On success the API mints a short-lived **HS256 JWT** (`iss`, `iat`, unique `jti`, `exp`, `staff_id`). **The token
   carries identity only — no permissions.**
5. Refusals carry a code in the `X-Login-Error` header: `invalid_credentials`, `account_disabled`,
   `two_factor_required`, `password_change_required`. Accounts with 2FA cannot use the API (a known limit).

### 3.2 `POST /api/v1/tickets/42/close`

1. **Router** — `app/modules/tickets/router.py::close_ticket`. Thin. Its gate is a default-value dependency:
   `current_user: dict = require_perm("ticket.close")`, plus `session = Depends(get_session)` and the
   `Idempotency-Key` header.
2. **Authentication & authorization** — `require_perm` depends on `get_current_user` (`app/core/auth.py`), which on
   **every request**: decodes the JWT strictly, confirms the staff member still exists and is enabled, rejects tokens
   issued before a password change, and loads a fresh **`AccessContext`** (`app/core/access.py`) from osTicket's own
   tables (staff row always; departments/roles/teams through a 5-second per-process cache). `require_perm` then asks
   that context whether the agent holds `ticket.close`. A demotion or disabled account applies on the next request.
3. **Service** — `app/modules/tickets/service.py::close_ticket` builds the payload from a typed Pydantic body
   (`TicketClose`) and calls `dispatch_command(command_type="ticket.close", aggregate_type="ticket",
   aggregate_id="42", payload=…, requested_by_staff_id=…, idempotency_key=…)`.
4. **Dispatch** — `app/integration/osticket/commands_service.py::dispatch_command`, the **only** way to write.
   It stores `<staff id>:<key>` as the idempotency key (so two agents never collide), returns the original row on a
   repeat (422 if the same key arrives with a different body), inserts an `IntegrationInbox` row and **commits
   immediately** (so a client that polls at once can see it).
5. **Response** — HTTP **202** with `{"command_id": 17, "status": "pending", "idempotency_key": …}`. 202 means *queued*,
   not *done*, and says nothing about whether osTicket will accept it.
6. **Claim** — within ~1 s `bridge/worker.php` (or ≤60 s osTicket's cron) atomically flips `pending → processing`
   (`FastApiIntegrationPlugin::drainInbox` in `bridge/plugin.php`). Each command runs in a **fresh PHP process**.
7. **Dispatch in PHP** — `plugin.php::dispatchCommand`: loads the requesting `Staff` and sets the global `$thisstaff`
   (osTicket's permission checks and authorship read it); looks the command up in `bridge/registry.php`
   (`'ticket.close' => ['tickets', 'FastApiTicketHandler', 'close', 'ticket.closed', 'role:ticket.close']`);
   **re-checks the permission in column 5 in PHP** (`assertPermitted`, no admin bypass) so security holds even if the API
   check were wrong; then calls the handler.
8. **Handler** — `bridge/handlers/tickets.php::FastApiTicketHandler::close` → `changeStatus(...)`, which uses osTicket's
   `Ticket` model and its exact per-ticket permission check (`checkStaffPerm`). Anything wrong **throws** with
   osTicket's own reason.
9. **Result** — success: inbox row → `done` with `result_payload` (JSON), and an outbox row `ticket.closed` is written.
   Failure: row → `failed` with `error_message`; **no event**. Either way any secret in the payload (`password`,
   `secret`, `token`, `file_data`) is overwritten in the stored row.
10. **Client learns the outcome** — `GET /api/v1/commands/17?wait=20` holds the request until `done`/`failed`
    (`app/modules/commands`). Then it re-reads `GET /tickets/42`.
11. **Everyone else learns** — each API process's poller (`app/workers/events.py`) reads the outbox by its own cursor and
    asks `app/modules/events/visibility.py` which connected agents may hear a `ticket.closed` for ticket 42 (those with it
    in their list). Those clients get `{"type":"ticket.closed","aggregate_type":"ticket","aggregate_id":"42",…}`.

### 3.3 `GET /api/v1/tickets/42` (a read)

Router (`require_staff()` — who may *ask*) → `service.get_ticket(session, 42, access=current_user["access"])` →
`read_repository.get_ticket(..., access=…)` which adds `ticket_visibility(access, detail=True)` to the SQL. If the agent
may not open it the answer is **404, never 403** (existence is not disclosed).

---

## 4. The five ideas that explain the whole design

1. **Writes go through osTicket's PHP, never around it.** The bridge plugin runs *inside* osTicket and uses its model
   classes, so validation, filters, SLAs, emails and audit trails happen exactly as in osTicket's UI. The bridge runs
   each command **as the requesting agent** (`$thisstaff`), so osTicket's own permission checks apply too.
2. **Reads are SQL, scoped in the query.** What an agent may see is part of the `WHERE` clause, copied from osTicket's
   own source (`Staff::getTicketsVisibility`, `Ticket::checkStaffPerm`, …). A wrong copy is a data leak, so it is
   defended by a structural guard, SQL-semantics tests over every state, and a live differential oracle (Part 7).
3. **Defence in depth.** Authorization is checked twice — once in the API route, once in the PHP bridge against the
   registry — and a test proves both columns agree. Tokens carry identity only; access is reloaded from osTicket on
   every request.
4. **Fail closed, and make failure structural.** Missing a scope argument is a `TypeError`; a route with no entry in the
   authorization matrix fails the build; an unknown event audience defaults to administrators-only; an unknown
   permission key fails at import. The project assumes people forget, and makes forgetting loud.
5. **Writes are asynchronous.** 202 + `command_id` + `GET /commands/{id}?wait=`. A client must understand this; so must
   you when writing a test.

---

## 5. Repository map

```
fast-ticket/
├── HANDOVER.md                ← you are here
├── README.md                  setup + the handoff pitfalls (short)
├── AGENTS.md                  CANONICAL engineering rules (architecture, security model, conventions, what NOT to do)
├── CLAUDE.md                  Claude Code entry point: imports AGENTS.md + session protocol (see 11.4)
├── Makefile                   all the commands (Part 6/7)
├── pyproject.toml / uv.lock   dependencies (uv). uv.lock is committed: image, venv and CI must match
├── docker-compose.yml         dev stack  · .override.yml (dev: mounted source, --reload, auto-loaded)
│                              .prod.yml (production shape) · .test.yml (isolated test run, no osTicket)
├── .env / .env.example        .env is gitignored and holds generated secrets. NEVER print it. Names are in .env.example
├── app/                       the FastAPI application
├── bridge/                    the PHP plugin (installed into osTicket)
├── tests/                     unit/ integration/ e2e/ (+ e2e/oracle/oracle.php)
├── docker/                    fastapi/Dockerfile · osticket/{Dockerfile,entrypoint.sh,apache.conf,crontab}
├── scripts/                   setup_env.sh (generates .env) · build_bridge_zip.py
├── notes/                     research, runbooks, contracts, the live handoff (NEXT.md) and limits (KNOWN_LIMITS.md)
├── .claude/skills/            task playbooks (add-endpoint, add-bridge-command, endpoint-tests, dev-stack, fitsm-reference)
├── .github/workflows/ci.yml   tests + PHP lint + pip-audit (also weekly)
├── dist/                      built plugin zips (make bridge-zip)
└── fitsm.zip                  snapshot of the messy predecessor. Reference only. NEVER commit its contents
```

### 5.1 `app/` — the Python side

```
app/
├── main.py                    app factory: middleware stack, lifespan starts 3 background tasks
│                              (outbox poller, hourly purge, WebSocket revalidation). Mounts everything under /api.
├── api/v1/router.py           mounts every module router under /api/v1; the /ws WebSocket endpoint is defined here
├── doctor.py                  `make doctor` entry point
├── core/                      cross-cutting, domain-free code
│   ├── config.py              Settings (pydantic-settings). Startup fails on bad/missing secrets
│   ├── db.py                  async engine + get_session
│   ├── auth.py                login against the bridge, JWT mint/decode, get_current_user, WebSocket auth
│   ├── access.py              AccessContext — mirrors Staff::hasPerm/getRoles/getDepartments/getTeams/isAccessLimited
│   ├── access_cache.py        5 s per-process cache for the slow-moving part of access
│   ├── security.py            require_perm / require_admin / require_staff  ← every route uses one
│   ├── permissions.py         osTicket's permission keys: GLOBAL vs ROLE (see 9.3). Unknown key → ValueError at import
│   ├── scope.py               UNRESTRICTED sentinel for "deliberately no visibility scoping"
│   ├── ws_manager.py          WebSocket connections, per-agent limits, revalidation
│   ├── events.py              event handler registry
│   ├── login_throttle.py · rate_limit.py · request_limits.py · security_headers.py   abuse protection / hardening
│   ├── attachments.py · uploads.py · csv_export.py · custom_fields.py · object_forms.py · redaction.py · timezone.py · names.py · message_text.py   shared helpers
├── integration/osticket/      THE BRIDGE BOUNDARY — the only code that knows osTicket's schema
│   ├── commands_service.py    dispatch_command() — sole entry point for writes (idempotency lives here)
│   ├── commands.py / models.py   SQLModel mirrors of the two integration tables (a test compares them with bridge/schema.php)
│   ├── read_repository.py     2,371 lines of SELECT-only SQL; the ONLY file allowed to contain osTicket-schema SQL
│   ├── bridge_read.py         synchronous reads through the PHP plugin (canned render, file bytes)
│   └── doctor.py              the preflight checks
├── modules/<domain>/          one package per API domain: router.py (thin) · models.py (Pydantic) · service.py (logic)
│   agents apikeys auth banlist commands dashboard emails emailtemplates events filters forms kb lists lookups
│   pages plugins profile roles schedules search settings staff system tasks tickets users
│   Some packages have no router of their own: departments and teams are routed by `staff`, SLAs, topics and
│   priorities by `lookups`, canned responses by `kb`.
└── workers/events.py          outbox poller: one cursor per API process, delivery via events/visibility.py
```

Module layout rule: `router.py` = routes + permission dependency, delegating; `models.py` = request/response schemas;
`service.py` = row→model mapping, `dispatch_command`, business logic. **Routers must not import other routers or
`read_repository`.** Cross-cutting code goes in `app/core/` or `app/integration/`.

Biggest modules (by route count, for orientation): tickets (40), tasks (21), users (21), lookups (21), settings (18),
kb (17), staff (15). `tasks` is called out in the project's own recipe as "the cleanest reference" to copy from.

### 5.2 `bridge/` — the PHP side

```
bridge/
├── plugin.php      the plugin class: hooks osTicket signals (cron, api, model.*/object.*), claims inbox rows,
│                   dispatches, enforces permissions, writes outbox events, scrubs secrets, exposes handshake/read/auth routes
├── registry.php    THE command table, one line per command (127):
│                   'type' => [handlers file, class, static method, outbox event, permission]
├── schema.php      the ONLY definition of the two integration tables (created when the plugin is enabled; prefix-aware)
├── worker.php      long-running inbox loop (~1 s); re-execs itself after every command (fresh process each time)
├── auth.php        POST /api/auth/integration — verifies a staff password via osTicket's own backends
├── read.php + reads.php   bridge read mode: POST /api/fast-ticket/read, operation → handler
├── install.php     idempotent CLI installer used by the Docker entrypoint (activates plugin, stores the key, creates tables)
└── handlers/       one FastApi<Domain>Handler class per domain, one static method per command
    tickets tasks users orgs staff departments teams lookups kb banlist forms lists filters emails emailtemplates
    pages roles schedules settings apikeys profile system reads, plus common.php (shared helpers: FastApiModel …)
```

Command families in the registry (count): ticket 27 · task 13 · user 9 · kb 9 · schedule 6 · list 6 · form 6 · org 4 ·
emailtemplate 4 · topic/ticket_status/team/staff/sla/role/priority/page/filter/email/dept/banlist/apikey 3 each ·
profile 2 · settings 1 · log 1. List them with:
`grep -oP "^\s*'\K[a-z_.]+(?=' +=>)" bridge/registry.php`

### 5.3 `tests/`

```
tests/
├── conftest.py            in-memory SQLite, per-test schema rebuild (autouse), auth_user/admin_user/client fixtures, make_access()
├── unit/                  pure + structural tests (no I/O) — includes the guards in 7.2
├── integration/           ASGITransport tests against the whole FastAPI app, repo and bridge mocked
└── e2e/                   live tests against the running stack (marker `e2e`, deselected by default)
    ├── conftest.py        login from env, the Bridge helper, cron fallback runner
    ├── oracle/oracle.php  read-only PHP that asks osTicket's own classes the visibility questions
    └── test_*.py          bridge_domains · bridge_roundtrip · bridge_reads · events · exports · attachments · oracle · …
```

### 5.4 `notes/` — what each document is for

| File | Read it when |
|---|---|
| `NEXT.md` | **Start of every session.** The live handoff: where things stand, what is open. (See 11.4 — its header is stale.) |
| `KNOWN_LIMITS.md` | Before promising anything. The honest list of everything incomplete. |
| `SECURITY.md` | Threat model, trust boundaries, how to run every check. |
| `CLIENTS.md` | The contract for anyone building a client: auth, the 202 pattern, idempotency, CORS, errors. |
| `EVENTS.md` | The WebSocket contract. |
| `CHANGES_FOR_FRONTEND.md` | Changelog of contract changes, newest first. **Update it whenever a response shape or event changes.** |
| `DEPLOYMENT.md` | Dev vs production differences, least-privilege DB user, TLS proxy, backups, upgrading, checklist. |
| `install-on-an-existing-osticket.md` | Attaching the plugin zip to a real osTicket. |
| `fast-ticket-bridge-bringup-and-testing.md` | Footguns and the hop-by-hop debugging runbook (auth hop A/B/C, write direction, event direction). |
| `OSTICKET_CAPABILITIES.md` | Inventory of osTicket PHP operations. **Use for orientation only — it has been wrong about method names; never trust it for signatures.** |
| `INTEGRATION_CAPABILITIES.md` | The API↔bridge matrix (commands, events, read-throughs). |

---

## 6. Getting it running

### 6.1 Prerequisites

Docker with Compose, `make`, and [`uv`](https://docs.astral.sh/uv/) for local Python. WSL2 is the environment this was
developed in.

### 6.2 First boot

```bash
make install     # uv sync --extra dev  → creates .venv for running tests locally
make start       # scripts/setup_env.sh generates .env with random secrets, then `docker compose up --build -d`
```

**Always use `make start` for the first boot**, not a bare `docker compose up`: it generates `.env` with the required
secrets (`MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `FASTAPI_INTEGRATION_KEY`, `JWT_SECRET_KEY`, …). The osTicket
container installs osTicket **headlessly** on first boot (`docker/osticket/entrypoint.sh`) and installs/enables the
bridge plugin (`bridge/install.php`). The install is idempotent and self-healing; only a half-finished install needs
`make reset`.

| Service | URL |
|---|---|
| API (Swagger UI at `/docs`) | http://localhost:8000 |
| osTicket (Admin Panel at `/scp`) | http://localhost:8080 |
| MariaDB | `localhost:13306` |

Everything binds to loopback by default (`API_BIND`, `OSTICKET_BIND`). Dev admin login: `OSTICKET_ADMIN_USERNAME` /
`OSTICKET_ADMIN_PASSWORD` from `.env` (defaults `helpdesk` / `changeme-admin`; osTicket's installer rejects the username
`admin`).

### 6.3 Smoke test

```bash
TOKEN=$(curl -s -d username=helpdesk -d password=changeme-admin localhost:8000/api/v1/auth/login \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -sL -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/tickets | head -c 400
make doctor      # preflight: prefix, plugin tables, every read query against the live schema, handshake, DB grants, stuck commands
```

### 6.4 Command cheat sheet

| Command | Does |
|---|---|
| `make start` / `make down` | boot / stop the stack |
| `make logs` | follow osTicket logs (`docker compose logs -f osticket-worker api` for the others) |
| `make reset` | **destroys all volumes** (DB + persisted osTicket config), then `make start` reinstalls |
| `make dev` | local `uvicorn --reload` (needs an external MySQL) |
| `make doctor` | preflight of the osTicket instance the API points at |
| `make bridge-zip` | `dist/fastapi_integration-<version>.zip`, the plugin for an existing osTicket |
| `make prod-up` | production shape (`docker-compose.prod.yml`) |
| `make audit` | `pip-audit` on the locked runtime dependencies |

### 6.5 The dev loop, and the single most common mistake

* `./app` is **bind-mounted with `--reload`** (via `docker-compose.override.yml`): Python edits are live.
* **`bridge/` is baked into the osTicket image.** After editing PHP you must `docker compose up -d --build` — or, for a
  fast inner loop, `docker cp bridge/. <container>:/var/www/html/include/plugins/fastapi_integration/` into both the
  `osticket` and `osticket-worker` containers and `chown -R www-data` them. The **final** verification must always
  follow a real rebuild.
* If the machine/WSL restarted: `docker compose ps`; if `db` exited, `docker compose up -d db`, then
  `docker compose restart osticket-worker` (the worker loops while the DB is down).

### 6.6 When something is off — debug hop by hop

Never start with the full chain. In order: `make doctor` → inbox/outbox rows in the DB → auth hop A (osTicket's bridge
auth directly) → hop B (from inside the `api` container) → hop C (login through FastAPI) → write direction in isolation
→ event direction in isolation → read SQL against real data. The commands are in `.claude/skills/dev-stack/SKILL.md`
and `notes/fast-ticket-bridge-bringup-and-testing.md`.

Symptoms to causes:

| Symptom | Look at |
|---|---|
| Command stays `pending` | worker and cron both down; plugin not loaded/enabled; command type missing from `registry.php` |
| Command `failed` | `error_message` on the inbox row is osTicket's real reason (SELECT `id, command_type, status, error_message FROM ost_integration_inbox ORDER BY id DESC LIMIT 10`) |
| Command stuck `processing` | a run died; after 5 min it is failed, **not retried** (it may have partly executed) |
| Unexpected 403 | the API route's guard vs `registry.php` column 5 vs the keys in `bridge/auth.php` |
| Login 500s | database `max_connections` exhausted (worker descriptor leak — see 10) |
| 401 on a just-created agent | see the unexplained flake in 11.3; grep `docker compose logs api` for `token refused` |
| Events do not arrive | WebSocket auth uses the `Sec-WebSocket-Protocol: bearer, <token>` header, **not** a query param |

---

## 7. Testing: the four layers

The project's central lesson: **mocked tests cannot see the majority of real defects** (a wrong SQL column, a PHP method
that does not exist, a NULL that breaks response validation). So there are four layers, and which you must run depends on
what you touched.

### 7.1 The commands

```bash
make test               # unit + integration (SQLite, bridge and read repo mocked)  ≈ 23 s, 955 tests
make test-docker        # same suite on the image's pinned deps (uv.lock) — run it too
set -a; . ./.env; set +a; make test-e2e    # live, ≈ 7–9 min, 105 tests. Needs a running, REBUILT stack
make test-cov           # coverage
```

Single file: `.venv/bin/python -m pytest tests/integration/test_tasks.py -v`.
Playing by the project's finishing rule, each piece of work ends: `make test` → `make test-docker` →
`docker compose up -d --build` → `make test-e2e` → atomic commit → update `NEXT.md`/`KNOWN_LIMITS.md`.

`make test-e2e` needs `OSTICKET_ADMIN_USERNAME` / `OSTICKET_ADMIN_PASSWORD` exported, and `MYSQL_PASSWORD` too for the
oracle. A human sources `.env` in their shell (`set -a; . ./.env; set +a`); an AI agent may source it *inside one command*
(the user permitted that) but must never print or write its values.

| Touched… | Must run |
|---|---|
| Python only (routes, services, models) | `make test`, `make test-docker` |
| `bridge/` or `read_repository.py` | all of the above **plus** rebuild **plus** `make test-e2e` |
| `app/core/access.py`, `scope.py`, visibility predicates | the e2e **oracle** (`tests/e2e/test_oracle.py`) |
| auth, permissions or the bridge | all of the above plus `/security-review` |

### 7.2 Structural guards (they fail the build — keep them passing)

| Guard | File | What it enforces |
|---|---|---|
| **Authorization matrix** | `tests/integration/test_authz_matrix.py` | A literal `POLICY` dict has a line for **every route** (`public\|authenticated\|staff\|admin\|perm:<key>\|self`). The test drives each route as each persona (no permissions / each single permission / all / admin with and without). A new route without a line fails; so does a stale line. Writes can never be `staff`/`public`. |
| **Read-scoping guard** | `tests/unit/test_read_scoping_guard.py` | Every read function touching ticket/task/thread data takes a required keyword-only `access`; every service call passes it; routers never import `read_repository`. Deliberate exceptions go in `UNSCOPED_BY_DESIGN` with a reason. |
| **Bridge registry** | `tests/unit/test_bridge_registry.py` | Every Python `dispatch_command` type has a `registry.php` line and vice versa; every registered class/method exists; events stay in their domain; **column 5 equals the guard of the API route that queues it**. Parses `registry.php` as text — one entry per line. |
| **Schema parity** | `tests/unit/test_bridge_schema.py` | The SQLModel integration tables match `bridge/schema.php` column for column. |
| **SQL semantics** | `tests/unit/test_visibility_sql.py` | The visibility predicates run as real SQL over every state. |
| **Doctor** | `tests/unit/test_doctor.py` | Every read function is callable with only `limit`/`offset`/`*_id` args (so `make doctor` can exercise it against a live schema). |
| **Event audience** | `tests/unit/test_event_audience.py` | Who hears which event; unknown aggregates fail closed. |

### 7.3 The oracle (the strongest check)

`tests/e2e/test_oracle.py` builds a throwaway scenario in the live stack (sub-departments, a managed department,
extra-department access, `assigned_only`, referrals, team assignment, merged-ticket-style threads…) and compares the
API's visibility answers with **osTicket's own PHP** (`tests/e2e/oracle/oracle.php`, read-only). It covers list/page
visibility, department and topic name lists, canned pickers, the default queues, the agent directory and assign-form
choices. Reads outside its list (user/org directories, KB, thread bodies, search over users, task lists) rest on
SQL-semantics tests and on having read osTicket's source — extending the oracle read by read is open work.

When you change a guard, **prove it can fail**: break it on purpose, confirm a test goes red, restore it.

### 7.4 The per-endpoint test matrix (mandatory)

Every endpoint gets at least: happy path · 401 (no auth) · 403 (wrong permission) · 422 (invalid input) · and for writes,
a duplicate `Idempotency-Key` returning the same `command_id` with a single inbox row. Reads: patch
`app.modules.<domain>.service.repo.<fn>` with `AsyncMock`. Writes: assert the `IntegrationInbox` row (`command_type`,
`aggregate_id`, `payload`, `requested_by_staff_id`). No test may depend on another's state. Use the `/endpoint-tests`
skill. **E2E tests must never modify seeded osTicket data** — they create and remove their own records. A bridge handler
is only "working" when an e2e test has created → read back → partially updated (other fields preserved) → deleted.

---

## 8. How to change things (recipes)

Each recipe has a full playbook in `.claude/skills/<name>/SKILL.md`. Those are written for Claude Code but are plain
markdown runbooks any engineer can follow.

### 8.1 Add or extend an endpoint — `add-endpoint`

1. **Look it up first**: `notes/OSTICKET_CAPABILITIES.md` (orientation only), then **osTicket's own source** — the class
   *and* the admin page that does the same job (`/var/www/html/scp/*.php`, `include/staff/*.inc.php` inside the
   container). Check the "do not expose" list before starting.
2. **Decide read or write.**
3. **Schemas** in `app/modules/<domain>/models.py`: explicit constrained fields, no `Any`, no bare `dict`. Writes return
   `CommandResponse` (from `app.modules.tickets.models`). Reads return a top-level key named for the resource
   (`{"tasks": [...]}`).
4. **Read path**: add a SELECT-only function to `read_repository.py` (check every column with `DESCRIBE` first; be
   NULL-safe), call it from `service.py` as `repo.<fn>(..., access=access)`, map rows with a `_row_to_*` helper.
5. **Write path**: `service.py` calls `dispatch_command(...)`; router returns `status_code=202`, accepts the
   `Idempotency-Key` header, and declares its gate. Then do 8.2 for the PHP side.
6. **Gate** — choose by reading the osTicket page, not by guessing (see 9.3). Add the module's permission map to the
   router docstring.
7. **Tests** — the full matrix (7.4), **plus a line in `POLICY`** in the authorization matrix, **plus** an e2e test if you
   touched SQL or PHP.
8. If a response shape or event changed: update `notes/CHANGES_FOR_FRONTEND.md` (and `CLIENTS.md`/`EVENTS.md` if the
   contract changed). If the event is a new kind of aggregate, add it to `app/modules/events/visibility.py`.

### 8.2 Add a bridge command — `add-bridge-command`

Two places, always in sync:

* `app/modules/<domain>/service.py` calls `dispatch_command(command_type="x.y", …)`.
* `bridge/registry.php` gets **one line**: `'x.y' => ['<handlers file>', 'FastApi…Handler', '<method>', '<past-tense event>', '<permission>']`,
  where permission is `admin`, `global:<key>`, `role:<key>` (or `self` for the profile self-service commands), and
  **equals the guard of the API route**.
* The handler: `public static function <verb>($payload, $requester_staff_id)` in `bridge/handlers/<domain>.php`. Return a
  small array (ids + key fields) — it becomes `result_payload` and the event payload. A `*.create` handler **must**
  return a non-zero id or the dispatcher fails the command. **Throw** on every failure with osTicket's own reason.
  Payload keys are the Pydantic field names, not osTicket's column names; map explicitly.

Then verify against the live stack (Part 7). The traps are in Part 10 — read them before writing the handler.

### 8.3 Add a scoped read — the rule from `AGENTS.md`

Writes keep osTicket's rules *in osTicket*; a read *copies* a rule into SQL, and a wrong copy is a leak. So:
1. Read osTicket's rule (the class method *and* the `scp/*.php` page that lists/opens it).
2. Simple or already ported (`ticket_visibility`, `task_visibility`, `AccessContext`, the topic/department name lists)?
   Write it in SQL, add a SQL-semantics test over every state, and add a case to the oracle if the rule is new.
3. Awkward to copy faithfully? **Do not approximate.** Either make the read admin-only until ported (add a
   `KNOWN_LIMITS` entry), or serve it through the **bridge read mode** (`bridge/reads.php` + `bridge/handlers/reads.php`,
   called via `bridge_json(...)` / `open_bridge_stream(...)` *after* the API has checked access). One extra HTTP round
   trip, so use it for what SQL cannot answer, not for lists.

Every scoped read takes a **required keyword `access=`** (the agent's `AccessContext`, or `scope.UNRESTRICTED` — used
only by `search.all`). Reads need no permission where osTicket needs none (`require_staff()`); don't invent one.
Queries are written against the default `ost_` prefix and go through `_sql()`, which rewrites the prefix — **never call
`text()` directly there.**

### 8.4 Commit

Atomic commits, `<type>(<scope>): <summary>` — types `feat fix test refactor docs chore security`; scope is the domain,
or `bridge` for `bridge/`. Example: `feat(tickets): add team-assign command + permission check`. Never commit `.env`,
`fitsm.zip`, `__pycache__`, extracted archives, or `.claude/settings.json` (it holds the project owner's own
uncommitted local change — it shows as modified in `git status` and should stay that way).

---

## 9. The rules that must not be broken

### 9.1 Architecture

* **Never write to osTicket's tables from the API.** No `INSERT/UPDATE/DELETE` on any `ost_*` table except
  `ost_integration_inbox` and `ost_integration_outbox`. Add a command and a handler.
* **All osTicket-schema SQL lives in `read_repository.py`.** No business logic there; no osTicket-schema models outside
  `app/integration/`. OSTicket-owned tables never become SQLModel tables; SQLModel is only for the two integration tables.
* **`dispatch_command` is the only write entry.** Never skip its idempotency check.
* **Bridge handlers are never written from memory.** Read the class and the admin page; verify live. Don't trust
  `OSTICKET_CAPABILITIES.md` for signatures.
* **Don't define the integration tables anywhere but `bridge/schema.php`**, and never put a `DEFAULT` on a TEXT column
  there (MySQL < 8.0.13).
* **Don't build a frontend here.** Changing a response shape or event is a *contract change*: update the notes with it.
* Throwaway scripts go in `notes/.scratch/` (gitignored), nowhere else.

### 9.2 Security model (summary — the full text is `AGENTS.md → Security model` and `notes/SECURITY.md`)

* **Auth**: osTicket's own backends verify passwords (never `StaffAuthenticationBackend::process()`, which writes a PHP
  session and emails 2FA codes). JWT HS256, strict decode (`exp`, `iss`, positive int `staff_id`). `get_current_user`
  re-checks the staff member on every request. All routes require Bearer except `GET /health` and `POST /auth/login`.
* **Authorization**: `AccessContext` reloaded from osTicket per request. **No admin bypass in `require_perm`** — osTicket's
  `hasPerm()` has none. An administrator's power is the Admin Panel (`require_admin`); for everything else they hold the
  permissions they were given.
* **404 not 403** for a resource the agent may not open.
* **Reads scoped in the query**; the scope argument has no default.
* **Idempotency** on every write route; keys are per staff member.
* **WebSocket**: token in `Sec-WebSocket-Protocol` (never the URL); per-agent audience; connections re-validated every
  `WS_RECHECK_SECONDS` (15 s); per-agent connection cap; slow clients dropped; frames > 64 KiB refused.
* **Hardening**: rate limit per client IP (`X-Forwarded-For` ignored unless `TRUSTED_PROXY_COUNT` says otherwise), login
  lockout, 413 above `MAX_REQUEST_BYTES`, security headers on every response, CORS locked by default, `/docs` can be
  switched off with `API_DOCS_ENABLED=false`.
* **Secrets**: nothing hardcoded; startup fails on a missing/short `JWT_SECRET_KEY` when `REQUIRE_JWT_SECRET=true`.
  **Never read, print or commit `.env`.**

### 9.3 Choosing a permission — the two kinds

osTicket has **two kinds of permission** (`app/core/permissions.py`), and confusing them is the classic mistake:

* **Global** — stored on the staff member: `user.create/edit/delete/manage/dir`, `org.create/edit/delete`, `faq.manage`,
  `emails.banlist`, `visibility.departments/agents`, `stats.agents`, `search.all`.
* **Role** — stored on the agent's roles, per department: `ticket.*`, `task.*`, `thread.edit`, `canned.manage`.

```python
current_user: dict = require_perm("user.create")   # NOT Depends(require_perm(...)); a real osTicket key or import fails
current_user: dict = require_admin()               # Admin Panel pages (osTicket's isAdmin())
current_user: dict = require_staff()               # any enabled agent; the query does the scoping
```

A route without one of these (or an explicit `# no permission required: <why>` comment) must not merge. In the registry
the equivalents are `admin`, `global:<key>`, `role:<key>`. Role permissions are checked in the bridge as "any of the
agent's roles"; ticket and task handlers additionally apply osTicket's exact per-department check.

---

## 10. Traps that have already bitten someone

These come from the project's own record of bugs. Each cost real time.

**Bridge / osTicket model traps** (full list: `.claude/skills/add-bridge-command`)

1. `Model::create()` **only instantiates**; nothing is saved until `->update($vars,$errors)`/`->save()`. Symptom: command
   `done` with a null id (the dispatcher now fails such a command).
2. `->update()` **overwrites every field** and web-form conventions apply (checkbox = "key present = on"); it *deletes*
   members/permissions/links when their key is absent. So build updates from **stored state overlaid with the payload**.
   A partial PATCH must never lose fields (`departments.php`, `teams.php`, `staff.php` are the reference).
3. osTicket's ORM **never writes a falsy value to a never-set field** — a new row meant to be "disabled" silently gets
   the column default. Use `FastApiModel::saveViaUpdate()`, which does a second pass.
4. **Dynamic-form data** (ticket subject, task title, user/org phone and notes) lives in form-entry *answers* and
   `ost_*__cdata`, not columns. Use `FastApiModel::applyAnswers()`.
5. `Ticket::assign/transfer` and the task equivalents take **forms** (`AssignmentForm`, `TransferForm`), not scalars; the
   transfer field is `dept`, not `dept_id`.
6. `$thisstaff` is set by the dispatcher — don't set it yourself.
7. **osTicket keeps process-wide static state** (`TaskForm::$instance` kept the previous task's answers: every task after
   the first got the first task's title). Hence **one fresh PHP process per command** — never reintroduce batching.
8. `DynamicForm::delete()` only flags the row, and osTicket marks even core forms deletable: guard destructive
   operations against seeded data.
9. osTicket checks **MX records** on email domains, so test data must use a deliverable domain (`gmail.com`), not
   `example.test`.
10. A call to a nonexistent method raises a PHP `Error`, not an `Exception`. The dispatcher catches `Throwable`; keep it so.

**Worker / infrastructure traps**

11. `pcntl_exec` keeps open descriptors: an unclosed DB connection per command once exhausted `max_connections` and made
    logins answer 500. The worker now closes its connection before each re-exec and recycles after
    `FASTAPI_WORKER_MAX_COMMANDS` (default 100) because each re-exec also leaks a file descriptor.
12. The plugin only runs if osTicket *loads* it: `ost_plugin.install_path` must be the plugin **directory**, an enabled
    `ost_plugin_instance` row must exist, signals must be connected in `bootstrap()` not the constructor. `install.php`
    does it all idempotently — if commands stay `pending`, check this first.
13. `ost-config.php` lives in the `osticket_config` named volume (not git, not host). `make reset` deletes it with the DB.
14. osTicket's `api/.htaccess` rewrites unknown `/api/*` URLs; the bridge auth URL works only because
    `docker/osticket/apache.conf` rewrites it first. The osTicket healthcheck is a keyless POST expecting **403**.
15. `dispatch_command` commits the row **itself** (FastAPI's session dependency commits only after the response is
    sent). Don't "tidy" this away.

**API traps**

16. **Timestamps**: osTicket stores naive datetimes in the database's timezone. The API answers every timestamp as UTC
    with a `Z`. Proven only against a UTC database (`DB_TIMEZONE` exists for others; `make doctor` shows the source).
17. **NULL-safety**: osTicket stores NULL text and zero dates (`0000-00-00`). One un-`COALESCE`d NULL is a 500 for that
    row only — mocked tests can never catch it, e2e/doctor can.
18. Booleans such as `isactive`/`isenabled`/`deletable` are usually **bits in `flags`**; "featured" visibility is `> 0`
    (value 2); threads are `ost_thread` by `object_type` (`T` ticket, `A` task) + `object_id`; tasks have no status
    table (open = `flags & 1`).
19. Ticket **list** visibility and ticket **detail** visibility genuinely differ in osTicket (a referral counts on the
    detail page in any state, but in a list only while open; a closed task opens for anyone but lists only in-department).
    Both are reproduced faithfully — do not "unify" them.
20. A **required custom field with no answer blocks every `PATCH /tickets/{id}`** (osTicket validates the whole form).
21. Adding/removing a field on a core form makes osTicket **rebuild that form's `__cdata` table** — fine, but slow on a
    huge ticket table.
22. `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_COUNT` is set; setting it wrong lets clients spoof their IP.

---

## 11. Current state and open work

### 11.1 Where it stands

* **All committed.** The only dirty file is the owner's `.claude/settings.json` — leave it alone.
* The "ready for a UI demo" milestone was met 2026-10-04. Later rounds added frontend-requested breadth (roles, API keys,
  site pages, custom lists, the eight settings pages, email template sets, filters, schedules, read-only plugins,
  profile, dashboard report, system logs), CSV export, ticket print (PDF) and ZIP export, ticket-status management,
  uploads via size-capped base64, and managing extra forms on tickets/users/organizations.
* Deployment hardening (2026-10-12): non-root read-only API image, loopback ports, `docker-compose.prod.yml`
  (least-privilege `fast_ticket` DB user, no default admin password, docs off), CI with `pip-audit`.
* Verified when this was written: `make test` → **955 passed**. (Run it yourself first thing: if it is not 955, find out
  why before touching anything.) Last recorded full runs: `make test-docker` 955, `make test-e2e` 105, `make doctor` 0
  failed with one expected dev-only warning (the dev stack uses one all-privileges DB user).

### 11.2 Open work — nothing is mandatory

The project owner directs what comes next, and the standing guidance is: **frontend planning is theirs; do not add API
breadth unprompted; depth over breadth; do only what is worth its cost.** If you do need work, in rough priority:

1. **Prove portability** (the largest unproven risk): install the plugin zip through osTicket's admin panel on a real
   instance (only the CLI installer has ever run); run e2e on another osTicket/MySQL version and on a non-UTC database.
   A non-`ost_` table prefix *is* proven (2026-10-12, `zz_`, 105/105).
2. **Extend the oracle** to the reads it does not cover (user/org directories, KB, thread bodies, task lists, search).
3. **Per-department canned-response checks** in the bridge handlers (today "any role").
4. **Optional HMAC on inbox rows** and encrypting payload secrets at rest (the inbox is unsigned).
5. **Small cleanups**: WebSocket connection-attempt rate limit; event-driven close on `staff.*` events (instead of the
   15 s recheck); drop signal events that change only `lastlogin` (admin-stream noise); drop the unused outbox
   `status`/`retry_count`/`locked_at` columns in a schema upgrade.
6. **Candidates the owner has floated, none started:** custom queues (read-only; the query builder stays unexposed),
   exporting agents/department members, canned files on task updates.
7. **Judged not worth doing:** staff CSV import; company profile/logo settings; mailbox/SMTP credential setup and the
   email diagnostic (stay out by decision).

### 11.3 What is knowingly incomplete (the headline items from `notes/KNOWN_LIMITS.md`)

That file is the single honest list (about 50 entries, reviewed 2026-10-12). Read it fully; the ones most likely to
surprise you:

* **2FA accounts cannot log in** through the API (refused with `two_factor_required`). No refresh tokens.
* **Writes are eventually consistent** (202 then poll/wait); there is no streaming upload — files are base64 in the
  command payload, size-capped (`ATTACHMENT_MAX_*`), and sit in the inbox row until the command finishes.
* **WebSocket delivery is best-effort and never replayed**; a reconnecting client must refetch.
* **Queues are osTicket's eight defaults, fixed in code**; custom queues/renamed queues are not reflected.
* **List search does not read message bodies** (ticket number, subject, requester only).
* **The inbox is unsigned**; anyone with DB write access can run commands as any agent.
* **The login lockout and default rate limit are per process** unless `RATE_LIMIT_STORAGE_URI` points at Redis.
* **Several reference-data reads are admin-only** (team list/detail, SLA plans, forms list/detail) until a screen needs them.
* **No TLS in the stack** — a proxy is required off a laptop; the osTicket containers run as root with the full DB user.
* **Only osTicket 1.18.1 + MariaDB 10.11 tested.**
* **Unexplained e2e flake (2026-10-08, never reproduced):** twice a just-created agent got a 401 on its second request.
  The API now logs why it refuses a token; if a run goes red, `docker compose logs api | grep "token refused"`. Suspected
  cause: a password-change timestamp one second after the token's `iat` (the comparison is to the second).

### 11.4 Known documentation drift (fix as you go)

* `notes/NEXT.md` header says "Branch: `feat/bridge-worker-and-portability`" and "Last updated 2026-10-07", and quotes
  older test counts (800 / 72). The repo is on **`main`**, and later sections of the same file report 955 / 105. Trust the
  later sections; correct the header.
* A local branch `feat/bridge-worker-and-portability` still exists alongside `main`; the owner's standing instruction is
  to **stay on the current branch, and not create branches or worktrees**.
* `README.md` says the e2e suite takes ~9 minutes; `NEXT.md` says ~6.5. Hardware-dependent.
* `fitsm.zip` (51 MB) is in the working tree for reference only — extract to a temp dir if you need the predecessor's
  code, then discard (`/fitsm-reference` skill). Never commit its contents.

### 11.5 Working agreements the project owner has set

If you are an AI agent (or a human working with one), these are standing instructions recorded in `CLAUDE.md`:

* Start each session by reading `notes/NEXT.md`, `notes/KNOWN_LIMITS.md`, `git log --oneline -15`, `git status`; then
  state the branch, what was done last, and what you will take first. Don't ask "what should I work on?" when `NEXT.md`
  says. Ask only for a genuine decision.
* Stay on the current branch; no branches or worktrees; never commit `.claude/settings.json`.
* Reading `.env` by sourcing it *inside a command* is allowed to run e2e; never print or write its values.
* Finish a piece with `make test` → `make test-docker` → rebuild → `make test-e2e` → atomic commit → update
  `NEXT.md`/`KNOWN_LIMITS.md` in the same change.
* Record a limit when you knowingly leave one (`KNOWN_LIMITS.md`), delete it in the commit that fixes it, and review the
  whole list at the end of any phase.
* Before ending a session: everything committed, `NEXT.md` current, and say whether it is safe to clear the conversation.
* Use `/security-review` before a PR touching auth, permissions or the bridge.

---

## 12. A suggested first week

**Day 1 — orient and run.**
1. Read this file, then `README.md`, then `AGENTS.md` front to back (it is long; it is the rulebook).
2. `make install && make start`, wait for health, run the smoke test (6.3) and `make doctor`.
3. Open http://localhost:8000/docs and http://localhost:8080/scp (log in as the dev admin). Create a ticket in osTicket's UI
   and watch it appear in `GET /api/v1/tickets`.
4. `make test` — expect 955 passed.

**Day 2 — trace the write path with your own eyes.**
1. Follow Part 3.2 in the code with the files open side by side.
2. Post a reply through Swagger with an `Idempotency-Key`; repeat it and confirm the same `command_id`.
3. Watch the rows: `SELECT id,command_type,status,error_message FROM ost_integration_inbox ORDER BY id DESC LIMIT 5;` and the
   outbox. Connect a WebSocket (`["bearer", token]` subprotocol) and watch the event arrive.
4. Read `bridge/plugin.php::dispatchCommand`, `registry.php`, and `handlers/tickets.php::close`.

**Day 3 — trace the read path and the safety nets.**
1. Read `read_repository.py::ticket_visibility` and `task_visibility`, then `tests/unit/test_visibility_sql.py`.
2. Read `app/core/access.py` and compare with `Staff::hasPerm` in the osTicket container (`docker compose exec osticket sed -n …
   /var/www/html/include/class.staff.php`).
3. Read `tests/integration/test_authz_matrix.py` and `tests/unit/test_read_scoping_guard.py`; then deliberately break a
   gate and watch a test fail.

**Day 4 — run the heavy checks.**
1. `make test-docker`, then `set -a; . ./.env; set +a; make test-e2e` (≈7–9 min). Read `tests/e2e/test_oracle.py` while it runs.
2. Read `notes/KNOWN_LIMITS.md` and `notes/SECURITY.md` in full.

**Day 5 — make one small, complete change** to prove the loop: pick a low-risk item (for example one of the small cleanups
in 11.2 item 5), follow the matching recipe in Part 8, finish it the project's way (Part 11.5), and update `NEXT.md`.
Fix the header drift in 11.4 while you are there.

Before taking a larger task, ask the owner what they want; the project's direction is theirs.

---

## 13. Reference

### 13.1 Glossary

| Term | Meaning |
|---|---|
| **osTicket** | The PHP open-source helpdesk (v1.18.1 here) that owns all data and business rules. |
| **Bridge / plugin** | `bridge/` — a PHP osTicket plugin (`fastapi_integration`) that runs commands and emits events from inside osTicket. |
| **Inbox / outbox** | The two plugin tables `ost_integration_inbox` (API → PHP commands) and `ost_integration_outbox` (PHP → API events). Defined only in `bridge/schema.php`. |
| **Command** | One row in the inbox: `command_type`, `aggregate_type`/`aggregate_id`, JSON `payload`, `requested_by_staff_id`, `idempotency_key`; status `pending → processing → done|failed`. |
| **Aggregate** | The resource a command/event is about (`ticket`, `task`, `user`, `org`, `staff`, `dept`, `kb.faq`, …). |
| **Agent / staff** | An osTicket staff member (the only API user type). "Client"/"user" in osTicket means an end customer. |
| **AccessContext** | `app/core/access.py` — what an agent may do right now, loaded from osTicket's tables. |
| **Scope / `access=`** | The required argument on scoped reads; an `AccessContext` or `scope.UNRESTRICTED`. |
| **Global vs role permission** | See 9.3. |
| **Bridge read mode** | `POST /api/fast-ticket/read` — a synchronous, read-only PHP call for what SQL can't answer (canned rendering, file bytes). |
| **Oracle** | The e2e test that diffs the API's visibility with osTicket's own PHP. |
| **Doctor** | `make doctor` — live preflight of schema, queries, handshake, grants and queue health. |
| **Worker vs cron** | `osticket-worker` drains the inbox ~1 s; `osticket-cron` is osTicket's once-a-minute fallback. Both use the same atomic claim, so they can run together. |

### 13.2 Configuration (names only — values live in `.env`; `.env.example` documents each)

* **Database**: `DATABASE_URL`, `OSTICKET_TABLE_PREFIX` (default `ost_`), `DB_TIMEZONE`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`
* **Bridge**: `FASTAPI_INTEGRATION_KEY` (shared secret API ↔ plugin), `OSTICKET_AUTH_URL`, `OSTICKET_AUTH_TIMEOUT`
* **JWT**: `JWT_SECRET_KEY`, `REQUIRE_JWT_SECRET`, `ACCESS_TOKEN_EXPIRE_MINUTES`
* **Abuse limits**: `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`, `RATE_LIMIT_DEFAULT`, `RATE_LIMIT_STORAGE_URI`,
  `TRUSTED_PROXY_COUNT`, `MAX_REQUEST_BYTES`
* **WebSocket / cache**: `ACCESS_CACHE_TTL_SECONDS`, `WS_RECHECK_SECONDS`, `WS_MAX_CONNECTIONS_PER_STAFF`, `WS_SEND_TIMEOUT_SECONDS`
* **Events**: `EVENT_POLL_INTERVAL_SECONDS`, `EVENT_BATCH_SIZE`, `EVENT_RETENTION_DAYS`
* **Hardening / exports / uploads**: `API_DOCS_ENABLED`, `CORS_ORIGINS`, `EXPORT_MAX_ROWS`, `ATTACHMENT_MAX_FILES|FILE_BYTES|TOTAL_BYTES`
* **Worker**: `FASTAPI_WORKER_MAX_COMMANDS`, `FASTAPI_WORKER_MAX_SECONDS`
* **Production**: `FASTAPI_DB_PASSWORD`, `API_BIND`, `OSTICKET_BIND`
* **Schema**: `AUTO_CREATE_SCHEMA` (keep `false`; the plugin creates the tables)

### 13.3 Response and error conventions

* Writes → **202** `{"command_id", "status": "pending", "idempotency_key"}`; outcome via `GET /commands/{id}?wait=≤30`.
* Reads → top-level key per resource (`{"tickets": [...]}`), detail models for single resources; lists are paginated and
  capped; a few use `items` (commands). Identifiers are osTicket's integer PKs; `number` is display-only.
* Errors → `{"detail": "..."}`; 401 auth, 403 permission, 404 missing-or-not-yours, 413 body too big, 422 validation or
  idempotency-key problems, 429 rate/lockout (`Retry-After`), 501 file backend the bridge cannot open, 502/503 bridge
  trouble.
* Login refusal reasons: `X-Login-Error` header.

### 13.4 Where to find the answer

| Question | Go to |
|---|---|
| "Why is it built this way?" | `AGENTS.md` (Architecture, Security model) |
| "Can I rely on X? What's missing?" | `notes/KNOWN_LIMITS.md` |
| "What am I supposed to do next?" | `notes/NEXT.md` (and ask the owner) |
| "What does a client need to know?" | `notes/CLIENTS.md`, `notes/EVENTS.md`, `notes/CHANGES_FOR_FRONTEND.md` |
| "How do I deploy / attach to an existing osTicket?" | `notes/DEPLOYMENT.md`, `notes/install-on-an-existing-osticket.md` |
| "The stack is broken." | `.claude/skills/dev-stack/SKILL.md`, `notes/fast-ticket-bridge-bringup-and-testing.md`, `make doctor` |
| "How does osTicket itself do this?" | The osTicket container's source: `docker compose exec osticket sed -n 1,120p /var/www/html/include/class.<x>.php` and `/var/www/html/scp/<x>.php` |
| "How did the old version do it?" | `/fitsm-reference` skill (`fitsm.zip`) — as a cautionary reference, not a model |
