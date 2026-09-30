# Next steps — handoff

Written 2026-09-30 at the end of security step S6 (commit `59c8098`) so a fresh session can start cold.
Branch: `feat/bridge-worker-and-portability` (stay on it; the user does not want more branches).
Overwrite this file when the direction changes; delete finished items.

## Where things stand

- Done and committed: bridge worker + portability (Phases 0–3), security overhaul S0–S6 (auth, per-request
  `AccessContext`, scoped reads that mirror osTicket, bridge-side permission checks, WebSocket audiences,
  authorization matrix, read-scoping guard, hardening headers, oracle differential test).
- Phase 4 (access cache) done on top of S6; see KNOWN_LIMITS for what it can lag. Still to run by a human: `make test-e2e`. Green at S6:
  the compose DB user has write access to osTicket tables).
- `.claude/settings.json` has an uncommitted user change — never commit it.
- Read first: `AGENTS.md`, `notes/KNOWN_LIMITS.md`, `notes/SECURITY.md`.

## Goal reminder

Replicate osTicket's interface on FastAPI first (reads scoped exactly as osTicket scopes them), custom features
after. fast-ticket should work against any osTicket instance out of the box.

## Direction, in order

### 1. Deferred authorization work (KNOWN_LIMITS → "Authorization")
Pick per need; each needs osTicket's rule read first (class + `scp/*.php` page), never guessed:
- Teams, agent directory, SLA plans, forms and detail of departments/topics/priorities are admin-only. Non-admin
  agents cannot build an assign form. Port the agent-directory and assignable agents/teams rules.
- Per-department canned-response checks in the bridge handlers (currently "any role").
- Idempotency-Key reused with a different body should return a mismatch error.
- Optional HMAC on inbox rows (the inbox is unsigned) and encrypting payload secrets at rest.

### 2. Extend the oracle (`tests/e2e/oracle/oracle.php`, `tests/e2e/test_oracle.py`)
Reads outside its coverage rest on SQL-semantics tests only: user/org directories, KB, thread bodies, search over
users. Add them one read at a time. Needs `MYSQL_PASSWORD` exported besides the admin login.

### 3. Deployment hardening (KNOWN_LIMITS → "Hardening and deployment security")
- Non-root API container, production compose profile (localhost-bound ports, TLS proxy note).
- Add `pip-audit` (or equivalent) to CI.
- Least-privilege DB users split in `docker-compose.yml` (`make doctor` prints the grants).

### 4. Portability still unproven
- Install the plugin zip through osTicket's admin panel on a real instance (only the CLI installer was run).
- Run the e2e suite against another osTicket/MySQL version, and a non-`ost_` prefix stack
  (`docker compose -p other up`).
- HTTP fallback read mode (user said "later") for setups where direct DB access is not acceptable.

### 5. Smaller cleanups worth doing when nearby
- WebSocket: connection-attempt rate limit; event-driven close on `staff.*` events instead of the 15 s recheck.
- Drop signal events that change only `lastlogin` (admin stream noise).
- Drop the unused outbox `status`/`retry_count`/`locked_at` columns in a schema upgrade.

## How to verify any change

```bash
make test && make test-docker
set -a; . ./.env; set +a; make test-e2e     # rebuild the stack first if bridge/ changed; includes the oracle
make doctor
```
Then `/security-review` if auth, permissions or the bridge were touched. At the end of the phase, review
`notes/KNOWN_LIMITS.md` entry by entry and update `Last reviewed`.

## Conventions to keep

- Commits: `<type>(<scope>): <summary>`, ending with the two trailers
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01JX4MmxEBKvennGPkCwFqmp`.
- Add a KNOWN_LIMITS entry when knowingly leaving a gap; delete it in the commit that fixes it.
- Never print `.env`; source it only inside a command to export variables.
- Scratch files go in `notes/.scratch/` (gitignored).
