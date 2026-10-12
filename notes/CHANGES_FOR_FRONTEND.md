# Backend changes the frontend should adopt

A running log of API changes made in answer to the frontend's `notes/BACKEND_REQUESTS.md` (and a few made on the
way), newest first. For each: what changed, what the frontend should do, and what to delete from its own workarounds.
After a backend change run `npm run api:pull && npm run api:types`, read the snapshot diff, then work through the
list below and remove the met entries from `BACKEND_REQUESTS.md`. This file is the backend's side of that handshake;
delete an entry once the frontend has adopted it.

## 2026-10-12 — Manage Forms on a ticket, a user and an organization

- `GET /tickets/{id}/forms`, `GET /users/{id}/forms`, `GET /orgs/{id}/forms` → `{forms:[{entry_id, form_id, title, kind: built_in|extra, sort}], available:[{form_id, title}]}`: the forms the object carries in order, and the extra forms that can still be added (osTicket's Manage Forms dialog). Needs `ticket.edit` / `user.edit` / `org.edit`, as the dialog does (404 for a ticket the agent may not open).
- `PUT /tickets/{id}/forms`, `PUT /users/{id}/forms`, `PUT /orgs/{id}/forms` with `{form_ids: [..]}` → 202: **the whole ordered list**, built-in form(s) included (send the listed `built_in` ids back). A form left out is removed together with its answers (osTicket warns before saving — the UI should too); a form not yet there is added empty. The object's built-in form cannot be left out, only extra forms (`kind: extra`, from `available`) can be added, and an id twice or ≤ 0 is a 422. After it, the object's `custom_fields` list the new form's fields (empty) and drop the removed form's.

## 2026-10-12 — canned response `files` with `?plain=true` (fix)

- `GET /tickets/{id}/canned/{canned_id}?plain=true` returned `files: []` for a response with ordinary attachments: osTicket's own JSON lists the separate files for the HTML box but only the *inline* images for the plain-text box. `files` now lists the response's separate files whatever `plain` is (plain adds the inline images osTicket offers there), and `canned_attachment_ids` on a reply accepts any of them. Nothing to change in the UI; the workaround of asking for the HTML form to get the files can go.

## 2026-10-08 — files on a new task

- `POST /tasks/` takes `attachments: [{name, content_type?, data (base64)}]` like a ticket's first message: the files are stored on the task's description (its first thread entry) and listed there. Same caps as every upload (`GET /settings/attachments`); osTicket's own size limit refuses the whole create with its wording.

## 2026-10-08 — ticket statuses: create, edit, enable/disable, delete (Admin Panel, admin only)

- `GET /ticket-statuses/manage` → `{items:[...]}` every status, enabled or not; `GET /ticket-statuses/{id}`. An item: `{id, name, state: open|closed|archived|deleted, description, sort, is_enabled, is_internal, is_default, allow_reopen, reopen_status_id, ticket_count, is_deletable}`. `GET /ticket-statuses` stays the agents' list of enabled statuses (`{statuses:[{id,name,state}]}`) and is unchanged.
- `POST /ticket-statuses {name (≤60, unique), state, description? (≤100), sort? (default: last), allow_reopen?, reopen_status_id?, is_enabled?}`, `PATCH /ticket-statuses/{id}` (partial; what you leave out is kept), `DELETE /ticket-statuses/{id}`. `allow_reopen` and `reopen_status_id` only mean something for a closed status (end users may reopen their ticket; `reopen_status_id` must be an enabled open status, 0/absent = the system default).
- osTicket's rules come back as the command's error: an **internal** status (the five built-in ones, and whichever is the system default) keeps its name and state and cannot be disabled or deleted; a status that tickets use cannot be deleted; names are unique. Use `is_internal`, `is_deletable` and `ticket_count` to disable the controls before the agent tries.

## 2026-10-08 — CSV export of the lists

- `GET /tickets/export`, `GET /tasks/export` (any agent; the rows are the ones their list shows), `GET /users/export`, `GET /orgs/export` (`user.dir`, like the directory). They take the same filters as the list endpoints (`q`, `queue`, `status_id`, `dept_id`, `sort`, ..., no paging) and return `text/csv` (UTF-8 with a byte-order mark, comma-separated) as a download: all pages, at most `EXPORT_MAX_ROWS` (10000) rows. `?columns=a,b` picks columns (default all; ticket: number, subject, from, status, priority, department, topic, sla, source, assigned_agent, assigned_team, answered, overdue, created, last_activity, due_date, closed; the other lists name theirs in the OpenAPI description; an unknown name is a 422), `?fields=` adds custom-form columns as on the lists. Dates are ISO UTC, booleans Yes/No, and a cell starting with `= + - @` is quoted with a leading `'` so a spreadsheet will not run it. Pass the current list's filters straight through, fetch with the bearer token and save the blob.

## 2026-10-08 — Print and Export to ZIP

- `GET /tickets/{id}/print?paper_size=Letter|Legal|A4|A3&notes=bool&events=bool` → the ticket as a PDF (osTicket's Print: the thread, with the internal notes and the event history when asked; the paper size defaults to the agent's own setting in osTicket). `GET /tasks/{id}/print?paper_size=` → the task as a PDF. `GET /tickets/{id}/zip?notes=bool&tasks=bool` → a ZIP with the ticket's PDF and every file attached to its thread (and its tasks, only those the agent may open, with `tasks=true`). All three answer a file download (`Content-Disposition: attachment`, filename `Ticket-<number>.pdf`, `Task-<number>.pdf`, `ticket-<number>.zip`), any agent who can open the object may ask, and a ticket or task they may not open is a 404. Fetch with the bearer token and save the blob (a plain link cannot send the header); this replaces printing the browser page. 501 if the osTicket server lacks the `mbstring` (PDF) or `zip` extension.

## 2026-10-08 — a canned response's files on the reply it fills

- `POST /tickets/{id}/reply` takes `canned_id` and `canned_attachment_ids` (the `id`s of `files` in `GET /tickets/{id}/canned/{canned_id}`). The listed files are attached to the reply, as osTicket's reply box does when an agent picks a premade reply; they can be mixed with `attachments` uploads. `canned_attachment_ids` without `canned_id` is a 422; a response the agent may not use is a 404; a file that is not one of that response's own files fails the command ("not attached"). Let the agent untick a file before sending and send only the ticked ids. Replies only: the task update and the notes have no premade-reply picker here.

## 2026-10-08 — a field added to a form after the record existed

- A ticket's, task's, user's or organization's `custom_fields` now lists **every** enabled, agent-visible field of its forms, as osTicket's own page does. A field that was added after the record was made, or never answered, comes back with `value: null` (and `value_id: null`) instead of being missing; the same holds for `?fields=` columns on the lists. Answering it with `PATCH {custom_fields: {name: value}}` now stores the answer (before, the write was silently lost for such a field). A bool field never answered still reads `bool_value: false`. Nothing to change if the UI already renders a field with no value; delete any workaround that hid or re-fetched such fields.

## 2026-10-07 — the settings drop-down choices

- `GET /settings/options` (admin): `{options: {<setting key>: [{value, label}]}}`, listed by osTicket itself, for `agent_name_format`, `client_name_format`, `agent_avatar`, `client_avatar`, `agent_passwd_policy`, `client_passwd_policy` (a blank policy, "All Active Policies", has the value `" "` — a single space, as osTicket posts it), `client_registration` (`disabled|public|closed`), `log_level` (`"0"|"3"|"2"|"1"`), `ticket_lock`, `acl_backend` and `default_ticket_queue`. Send `value` back in the section's `PATCH`. Turn those settings' text/number inputs into selects. The default priority/status/department/SLA/help-topic ids are not here: they come from `/priorities`, `/ticket-statuses`, `/departments`, `/slas`, `/help-topics`.

## 2026-10-07 — plugins (read-only)

- `GET /plugins` (admin): `{plugins:[{id, name, version, install_path, is_phar, is_active, installed, instances:[{id, name, is_enabled}]}]}`. Installing, enabling and configuring a plugin runs its code and is not exposed.

## 2026-10-07 — schedules (Admin Panel, admin only)

- `GET /schedules`, `GET /schedules/{id}` (`type: business_hours|holidays`, `timezone` (empty = floating), `description`, `holiday_schedule_ids` for business hours, and `entries[]` with osTicket's own sentence in `description`), `POST /schedules {name, type, timezone?, description?, copy_from_id?, holiday_schedule_ids?}` (a copy must be the same type), `PATCH /schedules/{id}` (partial; the type is fixed), `DELETE /schedules/{id}` (refused while an SLA plan, a department, the system settings or a business-hours schedule uses it).
- Entries: `POST /schedules/{id}/entries`, `PUT /schedules/{id}/entries/{entry_id}` (replaced whole), `DELETE`. An entry is `{name, repeats: never|daily|weekly|weekdays|weekends|monthly|yearly, starts_on, all_day?, starts_at?, ends_at? (HH:MM, 24-hour), stops_on?, day?, week?, month?}`: weekly takes `day` 1-7 (Mon-Sun); monthly and yearly repeat on the date of `starts_on`, or on the n-th weekday (`week` 1-5 or -1 for the last, `day` 1-7, and `month` for yearly). osTicket's rules apply (unique names; one daily entry excludes others; no overlapping weekday entries; valid time span) with its wording.

## 2026-10-07 — ticket filters (Admin Panel, admin only)

- `GET /filters` (without the system ban list, which keeps `/banlist`), `GET /filters/{id}` (adds `notes`, `rules[{id, what, how, val}]`, `actions[{id, sort, type, ...}]`), `GET /filters/options` (the fields a rule can match, grouped as osTicket groups them — ticket-form fields appear as `field.<id>` — plus the 8 match types, the targets and the action types with the fields each takes).
- `POST /filters {name, execorder?, is_active?, target: Any|Web|Email|API, email_id?, match_all_rules?, stop_onmatch?, notes?, rules[≥1], actions[]}`, `PATCH /filters/{id}` (partial; `rules` / `actions`, when sent, **replace** the whole list), `DELETE /filters/{id}`. A rule is `{what, how, val}`; an action is `{type, ...its fields}`: `reject`, `replyto`, `noresp` (no fields), `canned` (`canned_id`), `dept` (`dept_id`), `pri` (`priority_id`), `sla` (`sla_id`), `team` (`team_id`), `agent` (`staff_id`), `topic` (`topic_id`), `status` (`status_id`), `email` (`recipients`, `subject`, `message`, `from_email_id?`). osTicket's own validation applies (regex compile, active department / help topic, valid email for an `email equal` rule, unique name) and its wording comes back on the command.

## 2026-10-07 — the system's email addresses (Admin Panel, admin only)

- `GET /emails` → `{items:[{id, email, name, dept_id/name, priority_id/name, topic_id/name, no_autoresponse, is_default, has_mailbox, has_smtp, created, updated}], total, ...}`; `GET /emails/{id}` adds `notes` and `accounts[]` (mailbox / SMTP: active, host, port, protocol, encryption, folder, fetch settings, error count and last error; **never credentials**). `POST /emails {email, name, dept_id?, priority_id?, topic_id?, no_autoresponse?, notes?}`, `PATCH /emails/{id}` (partial), `DELETE /emails/{id}` (not the default or alert address). Setting up a mailbox or SMTP account (host, login, OAuth) stays in osTicket.

## 2026-10-07 — email template sets and their messages (Admin Panel, admin only)

- `GET /email-templates` → `{items:[{id, name, lang, notes, is_active, in_use, message_count, created, updated}], total, ...}`; `GET /email-templates/{id}` adds `messages[]` for all 19 messages osTicket knows (`code_name`, `name`, `group`, `description`, `defined`, `subject`, `updated`; `defined=false` means the set has no copy yet and osTicket uses its built-in text). `GET /email-templates/{id}/messages/{code_name}` adds the `body`.
- `POST /email-templates {name, notes?, is_active?, lang?, copy_from_id?}` (a copy of another set's messages, or empty), `PATCH /email-templates/{id}`, `DELETE /email-templates/{id}`; the default set and a department's are `in_use`: not disabled or deleted. `PUT /email-templates/{id}/messages/{code_name} {subject, body}` sets a message (adds the copy when the set has none).

## 2026-10-07 — Admin Panel settings pages (admin only)

- `GET /settings/{section}` and `PATCH /settings/{section}` (202, partial) for `system`, `tickets`, `tasks`, `agents`, `users`, `kb`, `emails` and `pages` (landing / offline / thank-you page ids). The body is osTicket's own keys, flat and typed: numbers, text, booleans (checkboxes and the 1/0 switches such as `isonline` and the `*_alert_active` flags) and, for `system`, `secondary_langs` as a list of codes. `tickets` carries the auto-response, notice and alert switches that osTicket keeps on that page.
- osTicket validates the whole section exactly as its page does and refuses with its own wording on the command (`error_message` is a JSON object of field → message, e.g. `{"ticket_number_format": "…requires at least one hash character (#)"}`); a refused save changes nothing. A key that is not in the section is refused. Select-like values (`client_registration`, `agent_name_format`, password policies, …) are osTicket's own strings: take the options from its pages' values (`public`/`private`/`disabled` etc.); the API does not enumerate them yet.
- Not here: company profile and logos, the default storage backend, manual queue sort (see `KNOWN_LIMITS.md`).

## 2026-10-07 — custom lists (Admin Panel, admin only) and custom-list answers

- `GET /lists`, `GET /lists/{id}` (`{id, name, name_plural, sort_mode: Alpha|-Alpha|SortCol, notes, item_count, handled_by, is_editable, allow_add, is_deletable}`), `POST /lists`, `PATCH /lists/{id}`, `DELETE /lists/{id}` (refused while a form field uses it). A list osTicket manages itself (ticket statuses: `handled_by` set) is read-only here.
- Items: `GET /lists/{id}/items` (in the list's own sort mode; `{id, value, abbrev, sort, is_enabled, is_internal}`), `POST /lists/{id}/items {value, abbrev?, sort?, is_enabled?}`, `PATCH /lists/{id}/items/{item_id}` (partial), `DELETE /lists/{id}/items/{item_id}` (un-links the item, as osTicket does; internal items cannot be disabled or deleted).
- A custom-list form field (`list-<id>`) takes the **item id** as its answer (a list of ids if it is multiple-select), on tickets, tasks, users and organizations, and `GET .../form` options list the enabled items. **Do:** a list-field picker can now be filled from `/lists/{id}/items` and written back.

## 2026-10-07 — API keys and site pages (Admin Panel, admin only)

- `GET /api-keys`, `GET /api-keys/{id}`, `POST /api-keys {ip_address, is_active?, can_create_tickets?, can_exec_cron?, notes?}`, `PATCH /api-keys/{id}` (flags and notes; the address is fixed), `DELETE /api-keys/{id}`. These are **osTicket's** HTTP-API keys, not this API's login; the key is shown to administrators, as osTicket's page does.
- `GET /pages`, `GET /pages/{id}`, `POST /pages {name, type: landing|offline|thank-you|other, body, is_active?, notes?}`, `PATCH`, `DELETE`. `in_use` is true for the three default pages and a help topic's page: those cannot be disabled or deleted (osTicket's refusal comes back on the command). Email and registration templates that live in the same table are not listed (osTicket's page doesn't either).

## 2026-10-07 — knowledge-base and canned-response attachments

- `FAQDetail` and `CannedDetail` carry `attachments: [{id, name, size, content_type, inline}]` (separate files; inline images stay in the text). Download: `GET /kb/faqs/{faq_id}/attachments/{attachment_id}` (any agent) and `GET /canned/{canned_id}/attachments/{attachment_id}` (an agent who may see the response: 404 otherwise). Always sent as a download.
- `POST/PATCH /kb/faqs` (`faq.manage`) and `POST/PATCH /canned` (`canned.manage`) take `attachments: [{name, content_type?, data}]` (base64, the same caps as replies, `GET /settings/attachments`) to add files; PATCH also takes `remove_attachment_ids` (the ids from the record; an id that is not on it is refused). A PATCH with neither keeps the files. **Do:** show the files on the FAQ and canned screens, add the picker and the remove action.

## 2026-10-07 — the Dashboard activity report

- `GET /dashboard/report?start=YYYY-MM-DD&period=until_today|one_week|two_weeks|one_month|one_quarter&group=department|topic|agent` (any agent) → `{start, period, group, plot:{days[], series:[{event, counts[]}]}, table:{columns[], rows[][]}}`: osTicket's own report class run as the agent, so which departments, topics and agents the rows cover is osTicket's rule (an agent sees their departments; "agent" shows themselves, plus the agents they manage or all with `stats.agents`). Table cells are strings as osTicket prints them (hours with one decimal). `start` omitted = a month ago. No CSV export route: build it from `table`.

## 2026-10-07 — system logs and system information (Admin Panel, admin only)

- `GET /logs?type=Debug|Warning|Error&since=&until=&sort=id|title|type|ip|created|updated&descending=&page=&page_size=` → `{items:[{id,type,title,logger,ip_address,created,updated}], total, page, page_size}`; `GET /logs/{id}` adds `log` (the text). `POST /logs/delete {ids}` (202; the result's `deleted` counts the rows that existed).
- `GET /system/info`: osTicket and PHP/MySQL versions, web server, the PHP extensions osTicket looks for (`loaded`), the two PHP settings it checks, schema name and signature, space used, attachment space, database timezone, installed language packs. Not included: osTicket's "latest version" check (it phones home).

## 2026-10-07 — custom answers as list columns: `?fields=`

- `GET /tickets/`, `/tasks/`, `/users`, `/orgs` take `?fields=name1,name2` (variable names, at most 20; see `GET /forms/for/{kind}`): every row then carries `custom_fields` with just those answers (typed, as on the detail reads), an empty list when the row has none, and `null` when `fields` was not asked. One extra query per page, not one per row; tickets and tasks are scoped as the list is. An invalid name list is a 422. **Do:** show custom columns from it and drop the "one request per row" workaround.

## 2026-10-07 — My Profile (self-service)

- `GET /profile` (any agent): their own profile: `username, firstname, lastname, email, phone, phone_ext, mobile, signature, timezone, lang, max_page_size (0 = system), auto_refresh_rate (minutes, 0 = off), default_signature_type, default_paper_size, onvacation`, and the per-agent settings `datetime_format, default_from_name, thread_view_order, default_ticket_queue_id, reply_redirect, img_att_view, editor_spacing` (empty = the system default).
- `PATCH /profile` (202, any agent, partial: what is absent keeps its stored value; osTicket's own refusals such as "Email already in use by another agent" come back on the command). `POST /profile/password {current_password, new_password}` (202): the current password must be right and osTicket's password policy applies. **A password change voids the agent's existing tokens: send them to the login screen.** Neither takes a staff id: the bridge acts on the requesting agent only.
- **Do:** replace the read-only My Profile with a form on these; nothing about 2FA is exposed (accounts with 2FA still cannot log in).

## 2026-10-07 — roles CRUD and the missing admin-list columns

- Roles: `GET /roles/{id}`, `POST /roles {name, notes?, permissions[], enabled?}`, `PATCH /roles/{id}` (partial: what is absent keeps its stored value, permissions included), `DELETE /roles/{id}` (refused with "in use" while an agent or department-access entry uses it). All admin only. A role (list and detail) now has `created`, `updated` and `in_use`. osTicket needs at least one permission and a unique name; its refusals are shown as worded. Permission keys must be role keys from `GET /permissions`.
- List items gained (all optional, never required): agents `last_login`; departments `status`, `manager_id`, `manager_name`, `email_address`, `agents_count` (administrators only; other agents see null); teams `lead_id`, `lead_name`, `members_count`; help topics `path` ("Parent / Child"), `priority_name`, `dept_name`; forms `field_count`. **Do:** fill the Admin list columns from these and drop the per-row requests and the "lists carry less" notes.

## 2026-10-07 — link / unlink / un-merge, the related-tickets read, and a status on a reply or note

- `GET /tickets/{id}/related` (any agent who can open the ticket): `{relation: none|linked|merged, merge_type: visual|combine|separate|null, is_parent, parent, children[]}`; each related ticket is `{ticket_id, number, subject, status_name}`. A related ticket the agent may not open is left out.
- `POST /tickets/{id}/link {child_ids}` (`ticket.link`): osTicket's Link Tickets (threads stay apart, nothing is closed). A merged ticket cannot be linked, nor a ticket already linked elsewhere (osTicket's refusals are shown as worded).
- `POST /tickets/{id}/unlink` (`ticket.link`): from a parent every child is released, from a child only itself. `POST /tickets/{id}/unmerge` (`ticket.merge`): the same for a merge; children keep the status the merge gave them (osTicket does not reopen them). Each refuses a ticket of the other kind.
- `POST /tickets/{id}/reply` and `/note` take `status_id` (osTicket's Ticket Status select, from `GET /ticket-statuses`). Closing needs `ticket.close`, deleting `ticket.delete`; an archived status is refused. **Do:** add Link / Unlink / Un-merge to the More menu, a Related Tickets panel, and the status select on the reply and note boxes.

## 2026-10-07 — ticket statuses and files on a new ticket

- `GET /ticket-statuses` (any agent): `{statuses:[{id, name, state}]}`, the enabled statuses in osTicket's order. The New
  Ticket form offers those with `state == "open"` (osTicket's staff form does the same); `status_id` on `POST /tickets/`
  is refused with osTicket's wording for a closed or disabled status.
- `POST /tickets/` takes `attachments: [{name, content_type?, data}]` (base64, same caps as replies, see
  `GET /settings/attachments`): the files land on the ticket's first message. **Do:** add the file picker to the New
  Ticket screen and the status select; drop the "status and attachments are not offered" note.

## 2026-10-07 — three small fixes from BACKEND_REQUESTS.md

- `sort=priority` now breaks ties by `last_activity` (osTicket's `lastupdate`), not the row's `updated`: the first page
  matches osTicket's default queue. **Do:** re-enable the `test.fixme` for the exact order.
- A closed task's `status_name` is `Completed` (osTicket's word); `status_id` 2 / `status_state` `closed` are unchanged.
  **Do:** drop the frontend's rename.
- A plain-text `message` / `note` / `response` / entry `body` with newlines (and no HTML tags of its own) is stored with
  `<br>` for each line break; a body that already carries markup is stored as sent. **Do:** nothing, line breaks now survive.

## 2026-10-05 — canned responses with the ticket's variables filled in: `GET /tickets/{id}/canned/{canned_id}`

**New.** Returns `{id, title, response, format, files}`: the canned response as osTicket's own reply form would insert
it, with `%{ticket.name.first}`, `%{ticket.number}`, `%{ticket.subject}` and the rest of osTicket's variables expanded by
osTicket itself (`format` is `html`, or `text` with `?plain=true`, which strips the HTML). `files` lists the response's
attached files by name only. 404 when the agent may not open the ticket or may not use that response (or it is
disabled). Any agent can ask (osTicket gates it on opening the ticket, not on a permission).

**Frontend to do.** When an agent picks a canned response in the reply or note box, call this instead of `GET /canned/{id}`
plus the local expansion, insert `response`, and delete the variable table in `src/lib/canned.ts` (and its unit tests).
Also new and invisible to clients: downloads of files kept by a storage plugin now go through osTicket, so the 501
for them mostly disappears.

## 2026-10-05 — user CSV import: `POST /users/import`

**New.** `POST /users/import` with `{csv, pasted?}` (needs `user.create`, like osTicket's Import dialog) queues osTicket's own
import. `csv` is CSV text up to `CSV_IMPORT_MAX_BYTES` (1 MB): a header row naming user-form fields (`name`, `email`,
`phone`, ...), or, with `pasted: true`, one `name, email` per line with no header (the dialog's "paste" box). The finished
command's `result.imported` is the number of users. It is **all-or-nothing**: the first bad row (no valid email, no name,
an unknown column) refuses the whole file, nothing is imported, and `error_message` carries osTicket's reason
("Both `name` and `email` fields are required"). Existing emails are matched, not duplicated (osTicket's behaviour).

**Frontend to do.** An Import button on the User Directory (visible with `user.create`): a file picker (read it as text)
and a paste box, a result line "Imported N users", the refusal text on failure; refetch the list (a `user.created`-style
event may arrive per user).

## 2026-10-05 — attachment upload on replies and notes (size-capped base64)

**New.** `POST /tickets/{id}/reply`, `POST /tickets/{id}/note`, `POST /tasks/{id}/reply` and `POST /tasks/{id}/note` accept
`attachments: [{name, content_type?, data}]`: `data` is the file's bytes in base64 (no `data:` prefix), `name` has no path,
`content_type` is optional (osTicket detects it). Caps: `GET /settings/attachments` (any agent) returns
`{max_file_bytes, osticket_max_file_bytes, api_max_file_bytes, max_files, max_total_bytes}`; `max_file_bytes` is the
effective per-file limit (osTicket's admin setting `max_file_size`, 1 MB by default, or the API's cap if smaller).
Over a cap on the API side is a 422 before anything is queued; osTicket's own size refusal comes back as the
command's `error_message` ("File size is too large").
The files appear on the new thread entry's `attachments` and download with the existing
`GET /tickets/{id}/attachments/{attachment_id}` (tasks: `/tasks/...`). The request body limit is raised on these four
routes only.

**Frontend to do.** Add a file picker to the reply and note forms (ticket and task): read each file with `FileReader`
/ `arrayBuffer`, base64 it, refuse a file over `max_file_bytes` or too many files client-side using the limits
endpoint, and send them with the message in one request (one `Idempotency-Key`). Show osTicket's `error_message` on a
refusal. Not available yet: attachments on ticket or task *create*, on canned responses or FAQs.

## 2026-10-05 — custom answers are typed (additive)

Every `custom_fields[]` answer (tickets, tasks, users, organizations) now has `kind` and the decoded value beside the
stored `value` / `value_id`: `kind: "text"` (use `value`), `kind: "bool"` with `bool_value`, `kind: "choices"` with
`choices: [{key, label}]` (empty when nothing is selected) for choice fields and custom-list fields (`list-<id>`; the
key is the item id). Nothing was removed.

**Frontend to do.** Replace the JSON decoding in `src/lib/custom-fields.ts` with these fields (keep sending answers
as before: a choice key or list of keys, a boolean, text). Datetime and number answers stay text. Writing a custom-list
answer is still unsupported.

## 2026-10-05 — form definitions for every agent: `GET /forms/for/{kind}`

New, open to any agent (`GET /forms` and `GET /forms/{id}` stay admin-only). `kind` is `ticket`, `user`,
`organization` or `task`; for tickets `?topic_id=` adds the help topic's forms. Response `{forms: [{id, title, type,
instructions, fields: [...]}]}`, the core form first and then the topic's forms in order, each field as `FormFieldItem`
(`name`, `label`, `type`, `hint`, `mode`, `sort`, `configuration`: choices, defaults, validators...) plus:

- `is_required`: required **for agents** (osTicket's agent-required flag, not the customer one);
- `is_editable`: the agent may type an answer; when false show the value read-only;
- `list_items`: for a custom-list field (`list-<id>`) the enabled items `[{id, value}]`; the answer is the item id.

Only fields osTicket shows agents are returned: enabled and agent-visible (customer-only fields are absent), and for a
ticket the fields the help topic switches off are removed. The core fields (`subject`, `message`, `priority` on tickets;
`name`, `email`, `phone`, `notes` on users...) come back too: skip the ones your screen already has as dedicated inputs.

**Frontend to do.** Non-admin agents can now type custom answers: build the inputs from this instead of `GET /forms/{id}`
(drop the admin-only path and the "no inputs for non-admins" note), re-fetch when the topic changes on New Ticket, decode
nothing new (answers still use the formats in `src/lib/custom-fields.ts`). A custom-list field's answer cannot be written
yet (see KNOWN_LIMITS), so show it read-only.

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
