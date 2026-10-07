# Next steps — handoff

Last updated 2026-10-07 (end of session). Branch: `feat/bridge-worker-and-portability` (stay on it; the user does not
want more branches). Overwrite this file when the direction changes; delete finished items. A fresh session starts here
(see `CLAUDE.md` → "Starting a session").

## Do first

Nothing is open. **The "ready for a UI demo" milestone is met** (below) and `KNOWN_LIMITS.md` was trimmed and reviewed
at the end of it. The user said they will tell you what to do about the **frontend** next — wait for that; do not start
a UI or more API breadth on your own. If the user instead asks for API work, pick from "Direction" below.

## Where things stand

- **All committed, tree clean** (except the user's own `.claude/settings.json` — never commit it). Last commit
  `b3b9d41`. Verified at that commit: `make test` 800, `make test-docker` 800, `make test-e2e` 72 on a rebuilt stack
  (includes the oracle against osTicket's own PHP), `make doctor` 0 failed (one expected warning: the compose stack
  uses one all-privileges DB user). Security review of the new code: nothing at high confidence.
- Built, in order: bridge worker + portability, security overhaul S0–S6, access cache, ticket lifecycle (PATCH, claim,
  release, refer, collaborators, thread-entry edit/resend), depth pass over staff / departments / topics / SLAs / orgs /
  users / tasks / teams (alert flags) / dynamic forms and custom-field answers, the `custom_fields` mass-assignment fix,
  the agent directory and assign-form choices, attachment listing and download, ticket search / default queues with
  counts / sorting / real totals, session revocation on password change, idempotency-key mismatch, help-topic sort mode.
- Things worth knowing: the e2e suite takes ~6.5 min (run it from a shell with `.env` sourced); the oracle test needs
  `MYSQL_PASSWORD` too; `dispatch_command` commits the row itself (FastAPI commits the session only after answering);
  the oracle flushes osTicket's ORM cache between agents (`getDeptAgents` leaves partial objects in it).
- The raw `flags` integer was removed from the department, help-topic and SLA APIs (explicit fields instead).
- Read first: `AGENTS.md`, `notes/KNOWN_LIMITS.md`, `notes/SECURITY.md`.


## What needs the user (ask, do not guess)

- **The frontend**: decided 2026-10-04 — it lives in its **own repository**, nothing of it here. The backend side is
  done: `notes/CLIENTS.md` (auth, 202 pattern, idempotency, CORS, deployment), `notes/EVENTS.md` (WebSocket), AGENTS.md
  reworded, and CORS now exposes `X-Login-Error` / `Retry-After` / `Content-Disposition` to browsers. The user will
  come back to **frontend planning** (stack, token storage, where the OpenAPI schema is taken from) — wait for that.
  Open question for it: production turns `API_DOCS_ENABLED` off, so the frontend needs its schema from a dev instance
  or a copy published with a release.
- ~~Attachment upload and user CSV import~~ decided 2026-10-05: size-capped payload (files as base64 in the command payload,
  CSV as text). Done for replies/notes and `POST /users/import`; see KNOWN_LIMITS "Attachments" for what is not covered.

## Goal reminder

Replicate osTicket's interface on FastAPI first (reads scoped exactly as osTicket scopes them), custom features
after. fast-ticket should work against any osTicket instance out of the box.

## Milestone: "ready for a UI demo" — MET (2026-10-04)

The API now covers osTicket's core agent workflow end to end: login, scoped ticket and task queues with search, sort,
counts and totals; ticket and task detail with threads and downloadable attachments; reply, note, assign (with the
assign-form choices), transfer, claim, release, refer, merge, close; collaborators; tasks; users and organizations
(with accounts and custom fields); the agent directory; KB and canned replies; and the whole Admin Panel (staff, teams,
departments, topics, SLAs, priorities, forms and their fields, banlist) — with changes pushed live over the WebSocket.
All verified by `make test-e2e` (61) and the oracle test against osTicket's own PHP. **Tell the user a UI can start.**

Known holes a UI will meet (all in KNOWN_LIMITS): no file upload, no ticket-export, no custom queues, list search does
not read message bodies, team/SLA lists are admin-only, 2FA accounts cannot log in. Pick the next work from "Direction"
below based on what the UI needs.

## Frontend requests (from `../fast-ticket-frontend/notes/BACKEND_REQUESTS.md`)

**Everything in the frontend's list has an endpoint now** (2026-10-07; every change is in `notes/CHANGES_FOR_FRONTEND.md`,
newest first — the frontend should `npm run api:pull` and adopt them): priority tie-break by last activity, "Completed"
tasks, line breaks in messages, `GET /ticket-statuses`, status and files on ticket create, status on reply/note, link /
unlink / un-merge + `GET /tickets/{id}/related`, roles CRUD, extra admin-list columns, `GET/PATCH /profile` +
`POST /profile/password`, `?fields=` custom columns on lists, system logs + info, `GET /dashboard/report`, FAQ and canned
attachments, API keys, site pages, custom lists (+ list-field answers), the eight settings pages, email template sets,
email addresses, ticket filters, schedules, read-only plugins.

**Not done, by decision or cost** (all in `KNOWN_LIMITS.md` where they are limits): mailbox / SMTP account setup and the
email diagnostic (credentials, OAuth, live connections); plugin install / enable (AGENTS.md: not exposed); company
profile, logos and the default storage backend in settings; manual queue sort; ticket-status create/edit/delete (a
custom status is a list item osTicket manages itself: the list is read-only here); a list's per-item property fields;
CSV export of the dashboard report (the frontend builds it from `table`).

Verified at the end of this session: `make test` 911, `make test-docker` 911, `make test-e2e` 96 on a rebuilt stack
(oracle included), `make doctor` 0 failed. Security review of the session's code: nothing at high confidence.

## Direction, in order

### Depth audit (started 2026-10-03) — do before more breadth
Field-by-field comparison of each existing create/PATCH against osTicket's own edit page (`include/staff/*.inc.php`).
Done: tickets (PATCH), staff, departments, help topics, SLAs, organizations, users, tasks. **Gaps found, still open:**
- ~~Department~~ done: parent, SLA, schedule, template, auto-response email, signature, status, assignment, auto-claim/reopen flags, alert recipients, auto-responses, extended members (the raw `flags` integer is gone from the API).
- ~~Help topic~~ done: status, parent, default ticket status, forms (ordered; core ticket form never detached), number format, no-autoresponse, auto-assign. Not yet: switching individual form fields off per topic (`fields[]`) — they are preserved as stored.
- ~~SLA~~ done: schedule, transient, disable-overdue-alerts (priority escalation flag is preserved, not editable).
- ~~Dynamic forms cannot define fields~~ done: `POST/PATCH/DELETE /forms/{id}/fields` (type, name, label, mode, hint, order, enabled, typed configuration). **Answering them is done too** (`custom_fields` on create/PATCH and in the detail reads of tickets, users, organizations and tasks).
- ~~Team~~ done (alert flags: team `no_alerts`, per-member `member_alerts`). ~~User / org / task~~ compared 2026-10-04: users and organizations already cover every field of osTicket's pages; tasks gained the "reason for editing" `note` on PATCH.

### 0. Replicate osTicket — gap audit (2026-09-30)
Compared `notes/OSTICKET_CAPABILITIES.md` §2 against the routers and `bridge/registry.php` (66 command types).
Audited: which operations exist. **Not audited:** how completely each PATCH covers the fields osTicket's own
edit page has, and whether each handler matches the page — check that when you touch one.
Work in this order; for every item follow the "Adding a scoped read" rule in AGENTS.md and the add-endpoint skill.

**A. Ticket lifecycle gaps (agents live here) — do first**
- ~~Edit ticket fields~~ done: `PATCH /tickets/{id}` (subject, priority, topic, SLA, source, due date, note). Not yet: other custom form-field answers (see KNOWN_LIMITS).
- ~~Claim, release, unassign, refer~~ done (`/claim`, `/release` with staff/team flags = unassign, `/refer`). Referral list/remove done (`GET /tickets/{id}/referrals`, `DELETE .../referrals/{referral_id}`).
- ~~Collaborators~~ done: `GET/PATCH/DELETE /tickets/{id}/collaborators[/{user_id}]` (active/CC flags, remove).
- Thread: ~~entry edit/resend~~ done (`PATCH /tickets/{id}/thread/{entry_id}`, `POST .../resend`; the thread read now hides superseded versions). Still open: attachments (upload/download), "post client message".
- Ticket export (PDF/ZIP), edit lock: skip until needed (low).

**B. Tasks / users / orgs / staff depth**
- Tasks: ~~assign to a team, claim, transfer, post update, thread-entry edit/resend, collaborators (add + list; osTicket's task page has no change/remove)~~ done. Still open: attachments, custom task-form fields.
- Users: ~~lock/unlock, register account, password-reset and activation emails~~ done (`/users/{id}/account`, `user.manage`), organizations' account manager/domain/sharing/auto-collaboration/primary contacts done. Still open: CSV import.
- Organizations: check add/remove members (may ride on user update).
- Staff: ~~permissions, extra-department access, assigned_only, vacation, visibility, phone_ext, notes~~ now on create/PATCH + detail read. Still open: password reset email, per-team alert flags, auth backend, CSV import.
- Dynamic-form answers as a sub-resource (fill/validate).

**C. Non-admin reference data**: ~~agent directory, assignable agents/teams~~ done. Still admin-only: team list/detail,
SLAs, forms (see item 1 below) — port when a screen needs them.

**D. Wave 2, one domain at a time (mostly Admin Panel, light on scoping):** filters + rules, custom lists,
business hours/schedules, roles, API keys, email accounts (config only, not piping), pages, export, logs/audit.
Search exists but is basic.

**E. Deliberately not exposed (AGENTS.md "What NOT to do"):** queue query-builder, sequences, sessions,
draft autosave, raw config bulk-write, plugin registration, email piping, cron.

### 1. Deferred authorization work (KNOWN_LIMITS → "Authorization")
Pick per need; each needs osTicket's rule read first (class + `scp/*.php` page), never guessed:
- Team list/detail, SLA plans, forms and detail of departments/topics/priorities are admin-only (the agent directory
  and assign-form choices are done).
- Per-department canned-response checks in the bridge handlers (currently "any role").
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
- The bridge read mode exists (`bridge/read.php`; canned render and file streaming). The *full* "no direct DB access" mode, where every read goes through the bridge, does not: reads still query MySQL directly.

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
