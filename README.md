# fast-ticket

A REST and WebSocket API for [osTicket](https://osticket.com) 1.18, written in FastAPI.

osTicket's business rules (permissions, SLAs, filters, notifications, audit trail) live in its PHP model layer, which has
no usable API. fast-ticket exposes that layer as a typed, permission-checked HTTP API without re-implementing it:

- **Writes** go through a PHP plugin installed in osTicket (the *bridge*). The API queues a command in a database
  inbox; the plugin runs it with osTicket's own classes, as the requesting agent, and reports the result through an
  outbox. Nothing writes to osTicket's tables directly.
- **Reads** are read-only SQL against osTicket's schema, scoped in the query to what osTicket would show that agent.
- **Live updates** reach clients over a WebSocket, filtered per agent.

The API is the product. There is no UI in this repository; clients consume the contract in `notes/CLIENTS.md` and
`notes/EVENTS.md`.

```
client ──HTTP/WS──> FastAPI ──inbox──> osTicket + bridge plugin ──outbox──> FastAPI ──WS──> clients
                       └────── read-only SQL ──────> osTicket's MySQL
```

## Status

Covers osTicket's agent workflow (tickets, tasks, threads and attachments, users, organizations, knowledge base,
canned responses) and most of the Admin Panel. Tested against osTicket 1.18.1 on MariaDB 10.11 only. What is missing
or partial is listed in `notes/KNOWN_LIMITS.md`; read it before promising anything to anyone.

## Setup

Requires Docker with Compose, and Make. For local development also [uv](https://docs.astral.sh/uv/).

```bash
make start      # creates .env with generated secrets, builds and boots the whole stack, installs osTicket headlessly
```

| Service | URL |
|---|---|
| API (Swagger UI at `/docs`) | http://localhost:8000 |
| osTicket (Admin Panel at `/scp`) | http://localhost:8080 |

Ports are bound to loopback. The dev admin login is `OSTICKET_ADMIN_USERNAME` / `OSTICKET_ADMIN_PASSWORD` from `.env`
(defaults `helpdesk` / `changeme-admin`; change them before first boot, and never use the defaults anywhere reachable).

Smoke test:

```bash
TOKEN=$(curl -s -d username=helpdesk -d password=changeme-admin localhost:8000/api/v1/auth/login | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -sL -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/tickets | head -c 400
```

Other commands: `make logs`, `make reset` (deletes all data), `make doctor` (checks the osTicket instance, schema,
plugin handshake and DB grants), `make prod-up` (production shape, see `notes/DEPLOYMENT.md`). To attach the plugin
to an existing osTicket instead, see `notes/install-on-an-existing-osticket.md`.

## Tests

```bash
make install        # uv sync --extra dev
make test           # unit + integration (SQLite, bridge mocked)
make test-docker    # the same suite on the image's pinned dependencies
set -a; . ./.env; set +a; make test-e2e    # live, against the running stack; about 9 minutes
```

The e2e suite is the one that matters for anything touching `bridge/` or `app/integration/osticket/read_repository.py`:
the mocked tests cannot see a wrong SQL column or a PHP call that does not exist. Rebuild the stack
(`docker compose up -d --build`) before running it, because `bridge/` is baked into the image.

## Layout

| Path | Contents |
|---|---|
| `app/` | FastAPI application, one package per domain under `app/modules/` |
| `app/integration/osticket/` | The only code that touches osTicket: command dispatch, read-only SQL, bridge client |
| `bridge/` | The PHP plugin: command registry, handlers, inbox worker, read mode |
| `tests/` | `unit/`, `integration/`, and `e2e/` (live stack) |
| `notes/` | Contract for clients, security model, deployment, known limits, handoff |

## Handoff notes

Read in this order: `AGENTS.md` (architecture and the rules), `notes/KNOWN_LIMITS.md`, `notes/SECURITY.md`,
`notes/NEXT.md` (where work stopped). Things that will bite:

- **Never write to osTicket's tables from the API.** Add a command and a bridge handler instead. A handler is written
  from osTicket's own admin page and verified live; guessing its PHP API has broken every handler at least once.
- **Every route needs an entry in the authorization matrix** (`tests/integration/test_authz_matrix.py`) and a gate
  (`require_perm`, `require_admin` or `require_staff`). A command type needs both a Python caller and a
  `bridge/registry.php` line. Tests fail the build when either is missing.
- **Reads must be scoped.** A new read of ticket, task or thread data takes a required `access=` argument; the rule
  for how is in `AGENTS.md`. A resource the agent may not open is a 404, not a 403.
- **The dev stack uses one all-privileges database user.** Production should use `docker-compose.prod.yml`, which gives
  the API a read-mostly user, no default admin password and no `/docs`. TLS is not included; put a proxy in front.
- **Accounts with two-factor authentication cannot log in** through the API.
- **After a host restart** (WSL does this) the `db` container can stay down and the inbox worker loops. Run
  `docker compose up -d db`, then `docker compose restart osticket-worker`.
- **Only osTicket 1.18.1 on MariaDB 10.11 has been tested.** Installing the plugin zip through osTicket's admin panel
  has not been tried; the Docker image installs it from the command line.
- **The dev/test fixtures create real data.** The e2e suite adds and removes its own records but must never modify
  seeded osTicket data; keep it that way.
