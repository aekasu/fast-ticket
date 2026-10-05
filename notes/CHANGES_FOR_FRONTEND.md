# Backend changes the frontend should adopt

A running log of API changes made in answer to the frontend's `notes/BACKEND_REQUESTS.md` (and a few made on the
way), newest first. For each: what changed, what the frontend should do, and what to delete from its own workarounds.
After a backend change run `npm run api:pull && npm run api:types`, read the snapshot diff, then work through the
list below and remove the met entries from `BACKEND_REQUESTS.md`. This file is the backend's side of that handshake;
delete an entry once the frontend has adopted it.

## 2026-10-05 — timestamps carry a timezone (BREAKING for any code that parses them)

**Changed.** Every timestamp in every response is now ISO 8601 **in UTC with a `Z`** (`"2026-10-03T18:18:02Z"`),
not a naive `"2026-10-03T18:18:02"`. This covers `created`, `updated`, `closed`, `due_date`, `est_due_date`,
`last_activity`, `registered` and the commands' `created_at` / `processed_at`, on tickets, tasks, users, organizations,
staff, teams, departments, topics, SLAs, priorities, forms, KB, banlist and commands. The OpenAPI schema is unchanged
(`type: string, format: date-time`), so regenerated types do not change.

**Why it is right.** osTicket writes its DATETIME columns in the *database server's* timezone, not necessarily UTC. The
API now finds that timezone at startup (`DB_TIMEZONE` if set, else osTicket's own `db_timezone`, else the server's)
and converts, so the `Z` is true on any installation. `make doctor` prints which source it used.

**Frontend to do.**
- Parse every timestamp as an instant (`new Date(value)` is now correct) and render in the viewer's timezone or in
  osTicket's configured one. Delete the "treat naive strings as UTC" shim in `src/lib/format.ts`; keep a test that a
  `Z` string round-trips.
- Sending: `due_date` (ticket PATCH, task create/edit) accepts an offset (`2026-10-10T10:00:00+02:00`, converted to UTC)
  or `Z`; a value with neither is still taken as UTC. Prefer sending `toISOString()`.
- Date-only comparisons (e.g. "overdue", "due today") must now use instants, not string prefixes.

**Not changed.** Sort order and filters work on the stored values and are unaffected. WebSocket events carry no
timestamps. A due date typed in a *task* form is still interpreted by osTicket in the agent's configured timezone
(see `KNOWN_LIMITS.md`).

## 2026-10-05 — earlier today (already in the schema snapshot you should pull)

| Change | Frontend to do |
|---|---|
| `GET /auth/me` and `POST /auth/login` have response schemas (`Me`, `TokenResponse`) | Replace the hand-written `Me` in `src/lib/api/me.ts` with the generated type. |
| Every 429 now has `Retry-After` (seconds) and the body `{"detail": ...}` (was `{"error": ...}`) | Login screen: say "try again in N seconds"; read `detail`, not `error`. |
| `sort=priority` breaks ties by most recently updated first, in both directions | Remove the `test.fixme` for the default order; the list default `sort=priority&descending=false` now matches osTicket's first page. |
| Thread entries carry `format` (`text` \| `html`) | Render by `format` instead of guessing by shape in `safe-html.tsx` (still sanitise `html`). Note: osTicket stores agent notes as `html` too. |
| Tickets (list and detail) carry `est_due_date` (manual due date, else the SLA estimate), `is_overdue` and `last_activity`; `sort=last_activity` | Show `est_due_date` as "Due Date"; red rows and the Overdue marker from `is_overdue`; use `last_activity` for "Last Updated" and sort by it. |
| `GET /roles` (admin only): `{items:[{id,name,notes,enabled,permissions}], page, page_size}` | Offer a role picker in the agent form and the department-access editors instead of typed ids. |
| `GET /permissions` (any agent): `{permissions:[{key, kind: global\|role}]}` | Build the permission editors from it instead of the hard-coded `PERMISSIONS`. |
| `GET /tickets/{id}/referrals` and `DELETE /tickets/{id}/referrals/{referral_id}` (needs `ticket.assign`) | Show the referral list with a remove action; delete the "referrals cannot be listed or removed" note. Referee `type` is `agent` \| `team` \| `department`. |
| Tasks: `GET /tasks/{id}/collaborators`, `POST /tasks/{id}/collaborators {user_id}` (needs `ticket.edit` — osTicket's task page checks the *ticket* permission), and `collaborators` on the task detail | Add the Collaborators row and an "add" action to the task view. There is no change or remove for tasks (osTicket has none). |
| Tasks: `PATCH /tasks/{id}/thread/{entry_id}` and `POST /tasks/{id}/thread/{entry_id}/resend` (needs `task.reply`; osTicket's rule decides) | Same edit/resend actions the ticket thread has; the entry id changes after an edit, as on tickets. |
