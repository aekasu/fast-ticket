# Backend changes the frontend should adopt

A running log of API changes made in answer to the frontend's `notes/BACKEND_REQUESTS.md` (and a few made on the
way), newest first. For each: what changed, what the frontend should do, and what to delete from its own workarounds.
After a backend change run `npm run api:pull && npm run api:types`, read the snapshot diff, then work through the
list below and remove the met entries from `BACKEND_REQUESTS.md`. This file is the backend's side of that handshake;
delete an entry once the frontend has adopted it.

## 2026-10-05 — `POST /tickets/` is osTicket's staff "Open a New Ticket" (BREAKING: a requester and a topic are required)

The create command now runs osTicket's own staff path (`Ticket::open`, what `scp/tickets.php?a=open` calls), so it
also posts the optional first response and note, assigns, adds collaborators and records the SLA, and it checks the
create permission in the **chosen department's** role (it was any-role before).

**New request fields** (all optional unless noted; the schema has them):

| Field | What it is | osTicket's form |
|---|---|---|
| `user_id` | An existing user as the requester | the User search/selection |
| `name` + `email` | A new (or by-email existing) requester | the name/email boxes |
| `cc_user_ids` | Users added as collaborators (Cc) | the Cc row |
| `source` | `Phone` \| `Email` \| `Other` \| `API` (default `API`; the staff form offers the first three, default Phone) | Ticket Source |
| `sla_id` | SLA plan (0 or absent: the topic's or default) | SLA Plan |
| `due_date` | UTC instant or with an offset; must be in the future | Due Date |
| `assign_agent_id` / `assign_team_id` | Assign on create (one of them; needs `ticket.assign`) | Assign To |
| `status_id` | Initial status | Ticket Status |
| `response` | A first reply to the requester (needs `ticket.reply` in the department) | Response |
| `note` | Internal note (posted when the ticket is not assigned; with an assignee it rides on the assignment) | Internal Note |
| `signature` | `none` \| `mine` \| `dept` for the emailed reply | Signature |
| `notify` | `none` (default) \| `user` \| `all`: who gets the new-ticket email. Default none: an API create sends no email | Ticket Notice |

**Breaking.** (1) A request must carry `user_id`, or both `name` and `email` (422 otherwise; before, an email-only or
empty-requester create was accepted and failed later). (2) osTicket's form requires a help topic: with no `topic_id`
and no default topic configured the command fails with "Help topic selection is required"; send `topic_id`.
(3) The command can now fail with osTicket's other form refusals (unknown user, SLA, agent or Cc, a past due date,
"Action Denied. You are not allowed to assign/reassign tickets", the required custom fields for agents).

**Frontend to do.** Build the New Ticket screen's missing controls from the table (source select, SLA plan, due date
picker, Assign To, Cc, Response with a signature choice, Internal Note); offer an existing-user picker (`GET /users?q=`)
beside the name/email boxes; show the command's `error_message` as osTicket words it. Not possible yet: attachments
(no upload) and the canned-response picker's variable expansion on the server side.

## 2026-10-05 — global search finds tickets by requester; reply `signature` is an enum

| Change | Frontend to do |
|---|---|
| `GET /search?q=` now matches a ticket's requester (name or any email address), as the ticket list's own search does; wildcards (`%`, `_`) in `q` are literal | Search page: take tickets from `/search` too and drop the second request to `GET /tickets/?q=`. Results stay limited to what the agent may open. |
| `POST /tickets/{id}/reply` `signature` is `none` \| `mine` \| `dept` (default `none`); anything else, including `""`, is a 422 | Optionally add osTicket's signature selector (None / My Signature / Department Signature). It changes the outgoing email only, not the stored thread; send `none` or omit it. |

## 2026-10-05 — lists: search, sort, totals, counts (users, organizations, KB, tasks, tickets)

All additive; nothing existing changes shape except a fix to the user list (below). Pull the schema and adopt:

| Endpoint | New | Frontend to do |
|---|---|---|
| `GET /users` | `q` (name, **any** email address, organization name or phone contains), `sort` (`name` \| `email` \| `status` \| `created` \| `updated`) + `descending`, a real `total`; each item has `account_status` (`guest` \| `active` \| `pending` \| `locked`, osTicket's directory Status: Guest / Active (Registered) / Locked (Pending Activation) / Locked (Administrative)) and `ticket_count` | Add the search box, sortable headers, the Status column and the "(n)" ticket count beside the name; page with `total`. **Fix:** a user with several email addresses used to appear once per address; now once. The old `status` integer stays (it is a primary-contact flag, not the account state). |
| `GET /orgs` | `q` (name contains, as osTicket), `sort` (`name` \| `users` \| `created` \| `updated`) + `descending`, `total`; each item has `user_count` | Add search, sort and the Users column. |
| `GET /tickets/` | `user_id` filter (the requester) | A user's Tickets table: `?user_id=<id>` instead of searching by email and filtering client-side. |
| `GET /kb/faqs` | `q` (question, answer, keywords, category name or description contains, as kb.php), `total` | Add the FAQ search box; stop counting on the client. |
| `GET /kb/categories` | each item has `faq_count` (own FAQs plus its sub-categories', as osTicket) and `parent_id`; `total` | Show "Category (n)"; nest by `parent_id`; the 200-item client counting can go. |
| `GET /tasks/` | `queue` (`open` \| `overdue` \| `assigned` \| `closed`); each item has `ticket_number` (the linked ticket's number) and `is_overdue`; `sort=ticket` and `sort=closed` | Ticket column from `ticket_number` (drop the per-ticket lookups); red rows from `is_overdue`; use `queue=` for the tabs. |
| `GET /tasks/queues` | **new**: `{queues:[{key,title,count}]}` in osTicket's order (Open, Overdue, My Tasks, Completed) | One call for the tab counts instead of three filtered lists. A queue's count equals that queue's list `total`. |

Notes. "My Tasks" is open tasks assigned to the *agent* only: osTicket's own task page does the same (its list filters
`staff_id`), so tasks assigned to the agent's team are in Open, not My Tasks; the request's point (3) is osTicket's
behaviour, not a gap. `ticket_count` counts all of the user's tickets, as osTicket's directory does, not only the ones
the agent can open. KB search is a plain "contains", as osTicket's.

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
