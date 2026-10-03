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

### Depth audit (started 2026-10-03) — do before more breadth
Field-by-field comparison of each existing create/PATCH against osTicket's own edit page (`include/staff/*.inc.php`).
Done: tickets (PATCH), staff. **Gaps found, still open:**
- ~~Department~~ done: parent, SLA, schedule, template, auto-response email, signature, status, assignment, auto-claim/reopen flags, alert recipients, auto-responses, extended members (the raw `flags` integer is gone from the API).
- ~~Help topic~~ done: status, parent, default ticket status, forms (ordered; core ticket form never detached), number format, no-autoresponse, auto-assign. Not yet: switching individual form fields off per topic (`fields[]`) — they are preserved as stored.
- ~~SLA~~ done: schedule, transient, disable-overdue-alerts (priority escalation flag is preserved, not editable).
- **Dynamic forms cannot define fields** through the API (forms have title/instructions/notes only; fields are read-only). Needed to build custom ticket forms; do after user/org/task are compared.
- Team: complete except alert flags. User/org/task: not compared yet.

### 0. Replicate osTicket — gap audit (2026-09-30)
Compared `notes/OSTICKET_CAPABILITIES.md` §2 against the routers and `bridge/registry.php` (66 command types).
Audited: which operations exist. **Not audited:** how completely each PATCH covers the fields osTicket's own
edit page has, and whether each handler matches the page — check that when you touch one.
Work in this order; for every item follow the "Adding a scoped read" rule in AGENTS.md and the add-endpoint skill.

**A. Ticket lifecycle gaps (agents live here) — do first**
- ~~Edit ticket fields~~ done: `PATCH /tickets/{id}` (subject, priority, topic, SLA, source, due date, note). Not yet: other custom form-field answers (see KNOWN_LIMITS).
- ~~Claim, release, unassign, refer~~ done (`/claim`, `/release` with staff/team flags = unassign, `/refer`). Referral list/remove still open.
- ~~Collaborators~~ done: `GET/PATCH/DELETE /tickets/{id}/collaborators[/{user_id}]` (active/CC flags, remove).
- Thread: ~~entry edit/resend~~ done (`PATCH /tickets/{id}/thread/{entry_id}`, `POST .../resend`; the thread read now hides superseded versions). Still open: attachments (upload/download), "post client message".
- Ticket export (PDF/ZIP), edit lock: skip until needed (low).

**B. Tasks / users / orgs / staff depth**
- Tasks: create/assign/close/reopen/note/edit/delete exist; check transfer, thread attachments, task forms.
- Users: no lock/unlock, register account, password reset/confirm email, CSV import.
- Organizations: check add/remove members (may ride on user update).
- Staff: ~~permissions, extra-department access, assigned_only, vacation, visibility, phone_ext, notes~~ now on create/PATCH + detail read. Still open: password reset email, per-team alert flags, auth backend, CSV import.
- Dynamic-form answers as a sub-resource (fill/validate).

**C. Non-admin reference data** (see item 1 "Deferred authorization work" below): teams, agent directory,
assignable agents/teams, SLAs, forms for an ordinary agent building an assign form.

**D. Wave 2, one domain at a time (mostly Admin Panel, light on scoping):** filters + rules, custom lists,
business hours/schedules, roles, API keys, email accounts (config only, not piping), pages, export, logs/audit.
Search exists but is basic.

**E. Deliberately not exposed (AGENTS.md "What NOT to do"):** queue query-builder, sequences, sessions,
draft autosave, raw config bulk-write, plugin registration, email piping, cron.

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
