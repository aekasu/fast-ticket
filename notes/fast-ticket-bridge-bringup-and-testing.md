# Getting the bridge running bidirectionally — and testing it without
# getting lost in network debugging

This assumes PR 1–4 from the critical-fixes checklist are done (bridge
actually handles all command types, inbox claim is atomic). If they aren't,
do those first — otherwise you'll be debugging "why didn't my command run"
issues that are actually just missing `case` statements, and you'll burn
time chasing a network problem that isn't there.

The core idea for testing: **the two hardest things to debug over HTTP are
"did the other side receive it" and "did the other side finish it yet."**
Both directions of this bridge go through a MySQL table before anything
else happens, so for every test, check the table first, the HTTP response
second. That one habit avoids most of the wasted time.

---

## Part 1 — Known footguns to clear *before* you start debugging

These aren't bugs to fix necessarily (some are just how the repo is wired),
but if you hit them mid-debug without knowing they exist, they look exactly
like "the bridge is broken" when it's actually a setup detail.

1. **Always run `make start`, never bare `docker compose up`, on first boot.**
   `docker-compose.yml` bind-mounts a single file:
   `./docker/osticket/ost-config.php` → `/var/www/html/include/ost-config.php`.
   That file doesn't exist in git. If Docker tries to bind-mount a
   non-existent file, it silently creates a *directory* at that path on the
   host instead of a file, which breaks the container's config-file logic
   in ways that are annoying to diagnose. `make start` runs
   `scripts/setup_env.sh` first, which `touch`es an empty
   `docker/osticket/ost-config.php` before compose ever runs. If you ever
   `rm` that file by mistake, re-run `./scripts/setup_env.sh` (it's a no-op
   on the `.env` part if `.env` already exists, but it will recreate the
   config file).

2. **`OSTICKET_AUTO_SETUP` defaults to `false` after `setup_env.sh` runs**,
   even though `docker-compose.yml`'s inline default is `true`.
   `scripts/setup_env.sh` appends `OSTICKET_AUTO_SETUP=false` to `.env`
   because `.env.example` doesn't define it at all. `.env` always wins over
   the compose-file default. Practically: **after `make start`, osTicket
   will NOT auto-install** — you'll need the manual web wizard at
   `http://localhost:8080`, and you'll need to run `install.php` yourself
   inside the container afterward. For local bring-up/testing, it's much
   faster to flip this before first boot:
   ```bash
   ./scripts/setup_env.sh        # generates .env if missing
   sed -i 's/OSTICKET_AUTO_SETUP=false/OSTICKET_AUTO_SETUP=true/' .env
   make start
   ```
   With `true`, the entrypoint runs osTicket's CLI installer *and*
   `bridge/install.php` automatically — no browser needed.

3. **Cron only runs in the `osticket-cron` service, not `osticket`.**
   The main `osticket` service sets `START_CRON: "0"` (it just serves
   Apache). Command draining (`processInbox()`) only happens in the
   separate `osticket-cron` container, which runs `cron -f` and fires
   `api/cron.php` once a minute. If `osticket-cron` isn't up and healthy,
   every command you dispatch will sit at `status='pending'` forever, and
   it will look exactly like a network/auth failure between FastAPI and
   osTicket even though FastAPI did its job correctly (it only ever writes
   to `ost_integration_inbox`, in the same DB — no network call at all).
   Check with `docker compose ps` that `osticket-cron` is `Up`.

4. **Expect ~1 minute of latency on writes, not instant.** Cron fires every
   60s (`docker/osticket/crontab`); the Python outbox poller fires every
   `EVENT_POLL_INTERVAL_SECONDS` (default 3s, `app/core/config.py`). So the
   realistic round-trip for "POST a reply → see it via GET" is: FastAPI
   write (instant) → up to 60s for cron to pick it up → PHP execution
   (fast) → up to 3s for the outbox poller to notice the result. **Don't
   conclude something's broken from a GET issued immediately after a POST.**
   Poll, don't assume.

5. **Permission keys likely don't match yet.** `bridge/auth.php` forwards
   whatever keys are stored in `ost_role.permissions` verbatim:
   ```php
   $permissions[] = $key;   // raw osTicket role permission key
   ```
   But `app/core/security.py`'s `require_perm("ticket.reply")` etc. expects
   the dot-namespaced convention documented there. osTicket's native role
   permission keys are very unlikely to already be spelled `"ticket.reply"`
   — there's presumably meant to be a translation layer (rules.md points at
   `notes/INTEGRATION_CAPABILITIES.md` §8.1.4 for the mapping) that doesn't
   exist in `auth.php` yet. **Expect every write to 403 for a non-admin
   test login until this mapping is built.** For first-pass bring-up
   testing, log in as the osTicket admin account (`is_admin` bypasses
   `require_perm` entirely in `app/core/security.py`) so you're not
   fighting two problems (connectivity + permission mapping) at once. Once
   connectivity is proven, tackle the permission mapping as its own task.

6. **Two things register `/api/auth/integration` — make sure you know which
   one actually answers.** `bridge/install.php` copies `auth.php` to
   `api/auth/integration.php` so Apache serves it as a static file. Separately,
   `plugin.php`'s `registerApiEndpoints()` also registers a dispatcher route
   at `^/auth/integration` via osTicket's `Signal::connect('api', ...)`.
   Depending on osTicket's routing order, either could win. If login behaves
   strangely (wrong error format, unexpected status codes, headers you didn't
   write), add a one-line distinguishing `error_log()` at the top of each
   path temporarily and check `docker compose logs osticket` to see which one
   actually ran. Don't assume — check.

7. **WebSocket auth uses the `Sec-WebSocket-Protocol` header, not a query
   string.** `app/api/v1/router.py`'s `/ws` endpoint expects
   `protocols: ["bearer", "<token>"]`. Generic WS testing tools (browser
   dev console `new WebSocket(url)`, some CLI tools) default to no
   subprotocol or a query-string token, which will get you a clean-looking
   `403`/connection-close that has nothing to do with your actual token
   being valid. Use `websocat` or `wscat` with explicit subprotocol support,
   or a short Python script (see Part 3).

---

## Part 2 — Bring-up order

Bring each hop up and verify it in isolation before chaining them. Don't
jump straight to "call the FastAPI endpoint and see if a ticket appears" —
if that fails, you won't know which of four hops broke.

```bash
# 1. Generate .env, flip auto-setup on (see footgun #2)
./scripts/setup_env.sh
sed -i 's/OSTICKET_AUTO_SETUP=false/OSTICKET_AUTO_SETUP=true/' .env

# 2. Bring up db first, wait for healthy
docker compose up -d db
docker compose ps db    # wait for "healthy"

# 3. Bring up osticket (runs the installer + bridge/install.php on first boot)
docker compose up -d osticket
docker compose logs -f osticket    # watch for "[install.php] ... installed successfully"

# 4. Bring up the cron worker
docker compose up -d osticket-cron
docker compose ps osticket-cron    # confirm "Up"

# 5. Bring up the API last
docker compose up -d api
curl -f http://localhost:8000/health   # {"status": "ok"}
```

At this point, verify each hop **directly**, not through the full chain:

```bash
# Hop A: can you reach osTicket's auth endpoint at all, bypassing FastAPI?
curl -s -X POST http://localhost:8080/api/auth/integration \
  -H "X-Integration-Key: $(grep FASTAPI_INTEGRATION_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<OSTICKET_ADMIN_PASSWORD from .env>"}'
# Expect: 200 + JSON with staff_id, permissions, etc.
# If this fails, the problem is entirely inside osTicket/the bridge —
# FastAPI hasn't even been involved yet.

# Hop B: can FastAPI reach osTicket internally? (from inside the api container,
# since OSTICKET_AUTH_URL uses the compose network hostname "osticket", not localhost)
docker compose exec api curl -s -X POST http://osticket/api/auth/integration \
  -H "X-Integration-Key: $FASTAPI_INTEGRATION_KEY" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}'
# If Hop A works but Hop B doesn't, it's a Docker networking / hostname
# issue, not an application bug — check both services are on the same
# compose network (they are, by default, since no custom networks are
# declared) and that "osticket" resolves inside the api container.

# Hop C: full login through FastAPI
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=admin&password=..."
# Expect: {"access_token": "...", "token_type": "bearer", ...}
```

Only once all three of those work do you move on to testing an actual
write. This bisection (osTicket-alone → api-container-to-osTicket → full
chain) is the fastest way to localize a failure — each hop either works or
doesn't, and you never have to guess which of 3 services is at fault from a
single failed `curl`.

---

## Part 3 — Testing writes and events without staring at HTTP calls

**Don't poll the API in a loop to see if your write "took."** Query the
tables directly — it's faster, it tells you exactly which stage the command
is stuck at, and it doesn't depend on the read path (`read_repository.py`)
also being correct.

```bash
# Connect straight to the DB (exposed on host port 13306 per docker-compose.yml)
mysql -h 127.0.0.1 -P 13306 -u osticket -p osticket
```

### 3a. Test the write direction (FastAPI → PHP) in isolation

```sql
-- After POSTing e.g. a ticket create via curl/httpie:
SELECT id, command_type, status, error_message, created_at, processed_at
FROM ost_integration_inbox
ORDER BY id DESC LIMIT 5;
```
- `status='pending'` and staying that way past ~70s → cron isn't draining
  it. Check `docker compose ps osticket-cron` and
  `docker compose exec osticket-cron cat /var/log/osticket-cron.log`.
- `status='failed'` → read `error_message` directly, it's the PHP
  exception text. This is almost always more informative than anything
  you'll get from the HTTP response, since the API only ever returns
  `{"status": "accepted", "command_id": ...}` — it doesn't wait for or know
  the outcome.
- `status='done'` → the write succeeded on osTicket's side. If your `GET`
  still doesn't show it, the problem is in `read_repository.py`'s query,
  not in the bridge — a completely different debugging path (see 3c).

You can also **skip FastAPI entirely** and insert a row by hand to test
just the PHP side:
```sql
INSERT INTO ost_integration_inbox
  (command_type, aggregate_type, aggregate_id, payload, requested_by_staff_id)
VALUES
  ('ticket.create', 'ticket', '',
   '{"subject":"test","message":"hello","email":"test@example.com","name":"Test User","dept_id":1,"topic_id":1}',
   1);
```
Wait up to 60s, re-run the `SELECT` above. This isolates PHP-handler bugs
from FastAPI-layer bugs completely — no HTTP client involved on either end.

### 3b. Test the event direction (PHP → Python worker → WebSocket) in isolation

```sql
SELECT id, event_type, status, retry_count, error_message, created_at
FROM ost_integration_outbox
ORDER BY id DESC LIMIT 5;
```
Same idea: `status` tells you exactly where it's stuck (`pending` → worker
isn't running or isn't polling; `failed` → check `error_message` and the
FastAPI container logs, `docker compose logs api`, for the handler
exception — `app/workers/events.py` logs failures there).

You can also skip the PHP side and insert an outbox row by hand to test
just the Python worker + WebSocket broadcast path:
```sql
INSERT INTO ost_integration_outbox
  (event_type, aggregate_type, aggregate_id, payload, status)
VALUES
  ('ticket.created', 'ticket', '999', '{"ticket_id":999,"number":"TEST999"}', 'pending');
```
Within `EVENT_POLL_INTERVAL_SECONDS` (default 3s) it should flip to
`processed` and, if you have a WebSocket client connected, you should see
it pushed. This isolates "is my WS client/auth broken" from "is the worker
broken" from "did PHP actually emit the event."

### 3c. Minimal WebSocket test client (handles the subprotocol footgun from Part 1 #7)

```python
# ws_smoke_test.py
import asyncio, websockets

TOKEN = "..."  # paste an access_token from /auth/login

async def main():
    async with websockets.connect(
        "ws://localhost:8000/api/v1/ws",
        subprotocols=["bearer", TOKEN],
    ) as ws:
        print("connected, waiting for events...")
        async for message in ws:
            print("EVENT:", message)

asyncio.run(main())
```
Run this, then trigger 3b's manual outbox insert in another terminal — you
should see the event printed within a few seconds. If you get an immediate
close instead, the problem is almost certainly the subprotocol format
(footgun #7), not the worker or the network.

### 3d. Testing `read_repository.py` SQL against real data, without going through the API at all

The pytest suite (SQLite in-memory) **mocks every `read_repository`
function** for exactly this reason — those queries use MySQL-specific
syntax (`CONCAT`, `IF()`, etc.) against osTicket's real schema, which
doesn't exist in SQLite. That means the automated test suite gives you
zero confidence that the actual SQL in `read_repository.py` runs
correctly. The only way to validate it is against the real MySQL
container — but you don't need FastAPI or HTTP for that either:

```python
# read_repo_smoke_test.py — run with: uv run python read_repo_smoke_test.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.integration.osticket import read_repository as repo

engine = create_async_engine("mysql+aiomysql://osticket:<pw>@localhost:13306/osticket")
Session = async_sessionmaker(engine, class_=AsyncSession)

async def main():
    async with Session() as session:
        result = await repo.list_tickets(session, page=1, page_size=5)
        print(result)

asyncio.run(main())
```
This exercises the real SQL against the real schema, with a full
traceback on syntax/column errors, and no FastAPI routing, auth, or HTTP
in the loop at all. Run it once for each `read_repository` function you
touch — it's the fastest feedback loop there is for that file, much faster
than round-tripping through `curl` + the API + reading a JSON error body.

---

## Part 4 — A repeatable end-to-end smoke test

Once the individual hops all check out, wire them together into one script
you can re-run after every change, instead of re-deriving the manual steps
above each time:

```bash
#!/usr/bin/env bash
# smoke_test.sh — full bidirectional round trip, asserting at every hop.
set -euo pipefail

API=http://localhost:8000/api/v1
DB="mysql -h 127.0.0.1 -P 13306 -u osticket -p<pw> osticket -N -B"

TOKEN=$(curl -sf -X POST "$API/auth/login" -d "username=admin&password=<pw>" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
echo "1. Got token."

CMD_ID=$(curl -sf -X POST "$API/tickets/" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-$(date +%s)" \
  -d '{"subject":"smoke test","message":"hi","email":"smoke@example.com","name":"Smoke Test","dept_id":1,"topic_id":1}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["command_id"])')
echo "2. Dispatched command_id=$CMD_ID"

echo "3. Waiting for PHP bridge to process it (up to 70s)..."
for i in $(seq 1 14); do
  STATUS=$(echo "SELECT status FROM ost_integration_inbox WHERE id=$CMD_ID;" | $DB)
  [ "$STATUS" = "done" ] && break
  [ "$STATUS" = "failed" ] && { echo "FAILED, error:"; echo "SELECT error_message FROM ost_integration_inbox WHERE id=$CMD_ID;" | $DB; exit 1; }
  sleep 5
done
[ "$STATUS" = "done" ] || { echo "TIMED OUT at status=$STATUS"; exit 1; }
echo "4. Inbox command done."

TICKET_ID=$(echo "SELECT aggregate_id FROM ost_integration_inbox WHERE id=$CMD_ID;" | $DB)
# (aggregate_id for create commands is populated in result_payload, not aggregate_id —
#  adjust based on your actual PHP handler's result shape)

echo "5. Checking outbox emitted an event..."
echo "SELECT event_type, status FROM ost_integration_outbox WHERE aggregate_type='ticket' ORDER BY id DESC LIMIT 1;" | $DB

echo "6. Confirming read path sees it via the API..."
curl -sf "$API/tickets/?page=1" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -20

echo "Smoke test complete."
```

Adjust the ticket-ID extraction once you've confirmed the actual shape of
`FastApiTicketHandler::create()`'s return payload (`bridge/handlers/tickets.php`).
The point of the script isn't to be a polished test — it's to give you one
command that checks every hop and tells you exactly which one failed,
instead of a bare `curl` against `/tickets/` that just says "empty list"
and leaves you guessing whether the write failed, the cron hasn't run yet,
or the read query is wrong.

---

## Summary: the debugging order that avoids network rabbit holes

1. Check the table (`ost_integration_inbox` / `ost_integration_outbox`)
   before checking the HTTP response — the table tells you the real status;
   the HTTP response only tells you whether FastAPI accepted the request.
2. Test each hop in isolation (osTicket alone → api-container-to-osTicket →
   full chain; PHP-side alone via a manual `INSERT` → Python-worker-side
   alone via a manual `INSERT`) before testing the full round trip.
3. Use admin auth first to rule out the permission-mapping gap before
   assuming connectivity is broken.
4. Give it up to ~70s before concluding something didn't run — cron is
   once-a-minute.
5. For `read_repository.py`, test SQL directly against the real MySQL
   container in a standalone script — the pytest suite mocks this file
   entirely, so it gives you no signal on real query correctness.
