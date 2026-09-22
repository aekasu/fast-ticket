# osTicket Capability Inventory

Research-only document. No application code was modified. Source analyzed: `osticket-src/` (osTicket, ~1,448 PHP files). Purpose: give a separate agent the factual basis to design a FastAPI wrapper without re-discovering osTicket internals.

**Method note:** this was produced by targeted reading of the core `include/class.*.php` domain models, the existing `api/` implementation, `scp/` (staff control panel) and top-level client controllers, plus grep-based method inventories across ~30 core classes — not a line-by-line read of all 1,448 files. Plugin internals (`include/plugins/*`), the full SCP/client template layer, and less-central subsystems were sampled, not exhaustively traced. Anything not directly verified is flagged in §8.

---

## 1. Executive Summary

osTicket is a PHP 7/8 web app (no existing general REST API) built around a **ticket + thread** core, with **dynamic forms** as the mechanism for custom fields everywhere (tickets, tasks, users, organizations). Two UIs sit on top of the same model layer: the **client portal** (top-level `*.php`) for end users, and the **staff control panel** (`scp/`) for agents/admins. Almost every capability described below is currently only reachable via session-authenticated HTML/AJAX controllers, not a stable API.

The **only existing API surface** (`api/`, `include/class.api.php`, `include/api.tickets.php`) is narrow: API-key + IP authenticated, and supports exactly one write operation — **create a ticket** (via XML/JSON POST or piped email) — plus a **cron trigger** endpoint. There is no API for reading, listing, updating, assigning, or closing tickets, nor for any other domain (users, staff, KB, tasks, etc.). A FastAPI wrapper is therefore not "wrapping a REST API" so much as **exposing the PHP model layer's capabilities as a new API**, which is consistent with the prompt's framing.

Core domains, roughly by centrality:
1. **Tickets** — the central object; creation, threading, status/lifecycle, assignment, transfer, merge, SLA, forms.
2. **Threads/messages** — every ticket (and task) has a `Thread` containing typed `ThreadEntry` items (message, response, note) plus attachments and collaborators.
3. **Users & organizations (client-side identity)** vs. **Staff, teams, departments** (agent-side identity) — two parallel people/permission models.
4. **Dynamic forms** — the generic custom-field engine used by tickets, tasks, users, organizations, and help topics.
5. **Knowledgebase** — FAQs, categories, canned responses.
6. **Tasks** — a ticket-like secondary work-item type sharing much of the Thread/Assignment machinery.
7. **Queues/Views, Filters, SLA, Business hours, Sequences** — operational/admin configuration that shapes ticket behavior.
8. **Email** — inbound (piping + IMAP/POP fetch), outbound (templates, notices), mailboxes/accounts.
9. **Auth & permissions** — pluggable backends, roles, department/team-scoped visibility, 2FA, API keys.
10. **Plugins, config/settings, logs, cron, import/export, search** — system/admin plumbing.

Roughly: **tickets, threads, users/orgs, staff/teams/departments, KB/FAQ/canned, tasks, help topics, and dynamic forms** are high-value, API-suitable domains. **Queues (advanced query builder), filters, plugins, low-level auth backend registration, and most SCP "system settings" screens** are internal/admin implementation detail better left to the native UI (medium/low suitability — see §7).

---

## 2. Capability / Domain Index (Compact Matrix)

| Domain | Capability | Operations | API Suitability | Implementation |
|---|---|---|---|---|
| Tickets | Create ticket | create | high | `include/class.ticket.php: Ticket::create/open`, `include/api.tickets.php` |
| Tickets | Retrieve / list / search tickets | retrieve, list, search | high | `include/class.ticket.php`, `include/class.search.php`, `include/class.queue.php` |
| Tickets | Update ticket fields | update | high | `Ticket::update/updateField` |
| Tickets | Status lifecycle (open/close/reopen) | update, close, reopen | high | `Ticket::setStatus/reopen/isCloseable` |
| Tickets | Assign to agent/team | assign, unassign | high | `Ticket::assign/assignToStaff/assignToTeam/unassign` |
| Tickets | Transfer department | update | high | `Ticket::transfer` |
| Tickets | Refer (share) ticket | create | medium | `Ticket::refer/systemReferral` |
| Tickets | Claim (self-assign) | update | high | `Ticket::claim` |
| Tickets | Release (unassign self) | update | medium | `Ticket::release` |
| Tickets | Merge / link tickets | update | medium | `Ticket::merge/manageMerge/unlinkChild` |
| Tickets | Delete ticket | delete | medium | `Ticket::delete` |
| Tickets | Reply (agent → client) | create (reply) | high | `Ticket::postReply` |
| Tickets | Post internal note | create (note) | high | `Ticket::postNote/logNote` |
| Tickets | Post client message | create (message) | high | `Ticket::postMessage`, `TicketApiController::createTicket` |
| Tickets | Collaborators (CC) mgmt | add, update, delete | medium | `Ticket::addCollaborator(s)/updateCollaborators` |
| Tickets | Export ticket (PDF/ZIP) | retrieve | medium | `Ticket::pdfExport/zipExport` |
| Tickets | Lock/unlock (edit lock) | update | low | `Ticket::acquireLock/releaseLock` |
| Threads | Read thread/entries | retrieve, list | high | `include/class.thread.php` |
| Threads | Attachments upload/download | create, retrieve, delete | high | `include/class.attachment.php`, `ThreadEntry::createAttachment(s)` |
| Threads | Thread actions (edit, resend, refer) | update | medium | `include/class.thread_actions.php` |
| Users | Create/retrieve/update/delete client | create, retrieve, update, delete | high | `include/class.user.php` |
| Users | Register client account | create | high | `User::register`, `UserAccount::register` |
| Users | Import users (CSV) | create (bulk) | medium | `User::importCsv/importFromPost` |
| Users | Lock/unlock account | update | medium | `UserAccount::lock/unlock` |
| Users | Password reset / confirm email | create | high | `UserAccount::sendResetEmail/sendConfirmEmail` |
| Organizations | CRUD organization | create, retrieve, update, delete | high | `include/class.organization.php` |
| Organizations | Add/remove members | update | high | `Organization::addForm... / removeUser` |
| Staff (agents) | CRUD staff account | create, retrieve, update, delete | high | `include/class.staff.php` |
| Staff | Update permissions/access | update | medium | `Staff::updatePerms/updateAccess` |
| Staff | Import staff (CSV) | create (bulk) | medium | `Staff::importCsv` |
| Staff | Password reset | create | high | `Staff::sendResetEmail/checkPassword` |
| Teams | CRUD team, manage membership | create, retrieve, update, delete | high | `include/class.team.php` |
| Departments | CRUD department, access/visibility | create, retrieve, update, delete | high | `include/class.dept.php` |
| Help Topics | CRUD help topic, form linkage | create, retrieve, update, delete | high | `include/class.topic.php` |
| Dynamic Forms | Define/manage custom field forms | create, retrieve, update, delete | medium | `include/class.forms.php`, `include/class.dynamic_forms.php` |
| Dynamic Forms | Fill/validate form answers | create, update | high (as sub-resource) | `DynamicFormEntry::saveAnswers/validateEntry` |
| Knowledgebase | CRUD FAQ, categories | create, retrieve, update, delete | high | `include/class.faq.php`, `include/class.knowledgebase.php` |
| Canned Responses | CRUD canned response, apply to ticket | create, retrieve, update, delete, apply | high | `include/class.canned.php`, `Ticket::postCannedReply` |
| Tasks | CRUD task, assign, thread, close | create, retrieve, update, delete, assign, close | high | `include/class.task.php` |
| Queues/Views | Define/list queues (query builder) | create, retrieve, update, delete | low | `include/class.queue.php` |
| Filters | CRUD email/ticket filters + rules | create, retrieve, update, delete | medium | `include/class.filter.php`, `include/class.filter_action.php` |
| SLA | CRUD SLA plans | create, retrieve, update, delete | high | `include/class.sla.php` |
| Business Hours | CRUD schedules | create, retrieve, update, delete | medium | `include/class.businesshours.php`, `include/class.schedule.php` |
| Priorities | CRUD priority levels | create, retrieve, update, delete | medium | `include/class.priority.php` |
| Sequences | Manage numbering sequences | create, retrieve, update | low | `include/class.sequence.php` |
| Email | CRUD mailbox accounts, fetch, pipe | create, retrieve, update, delete, process | medium | `include/class.email.php`, `include/class.mailfetch.php`, `include/class.mailparse.php`, `api/pipe.php` |
| Banlist | Add/remove banned emails | create, delete | high | `include/class.banlist.php` |
| Auth | Login/logout, password policy | create (session), update | medium | `include/class.auth.php` |
| 2FA | Enroll, send, validate OTP | create, update | medium | `include/class.2fa.php` |
| API Keys | CRUD API key | create, retrieve, update, delete | medium | `include/class.api.php` |
| Roles/Permissions | CRUD role, permission bitmasks | create, retrieve, update, delete | medium | `include/class.role.php` |
| Plugins | Enable/disable/configure plugin | update | low | `include/class.plugin.php` |
| Config/Settings | Read/update system settings | retrieve, update | low-medium | `include/class.config.php` |
| Search | Full-text search across entities | search | medium | `include/class.search.php` |
| Export | CSV/bulk export (tickets, users, orgs, tasks) | retrieve | medium | `include/class.export.php` |
| Logs/Audit | Read system logs, auth audit | retrieve | medium | `include/class.log.php`, `Auth::authAudit` |
| Cron | Trigger scheduled jobs | process | low | `include/class.cron.php`, `api/cron.php` |
| Import | Bulk import framework | create (bulk) | low | `include/class.import.php` |
| Pages (CMS) | CRUD static content pages | create, retrieve, update, delete | low-medium | `pages/`, `include/class.staff.php` (`Page` model referenced by SCP `pages.php`) |

---

## 3. Detailed Capability Inventory

### 3.1 Tickets (`include/class.ticket.php`, `Ticket` class)

**Domain:** Tickets — the central work-item entity.

**Capability:** Full lifecycle management of a support ticket: creation from web/email/API, threaded communication, status transitions, assignment, and closure.

**Operations:**
- `create` — `Ticket::create($vars, &$errors, $origin, $autorespond, $alertstaff)` (also `Ticket::open`). Origin is one of `web|staff|email|api|phone` etc.
- `retrieve` — via ORM (`Ticket::lookup`/`getId`-style access, `TicketModel::objects()`; not grepped directly but standard osTicket ORM pattern used throughout, see `class.orm.php`).
- `search` / `list` — `Ticket::getSearchableFields`, backed by `include/class.search.php` and the queue/query-builder (`class.queue.php`).
- `update` — `Ticket::update($vars, &$errors)`, `updateField($form, &$errors)`.
- `assign` / `unassign` — `assignToStaff`, `assignToTeam`, `assign(AssignmentForm)`, `unassign`, `release`.
- `transfer` (department) — `transfer(TransferForm $form, &$errors, $alert)`.
- `refer` — `refer(ReferralForm)`, `systemReferral($emails)`.
- `claim` — `claim(ClaimForm $form, &$errors)`.
- `merge` / `unlink` — `Ticket::merge(array $tickets)` (static), `manageMerge`, `unlinkChild`, `unlink`, `getRelatedTickets`.
- `close` / `reopen` — `setStatus($status, ...)`, `reopen()`, `isCloseable`, `isReopenable`.
- `delete` — `delete($comments)`.
- `reply` (agent → client) — `postReply($vars, &$errors, $alert, $claim)`; `postCannedReply`.
- `note` (internal) — `postNote`, `logNote`, `logActivity`.
- `message` (client → agent) — `postMessage($vars, $origin, $alerts)`; entry point for new client replies and for API/email ticket creation.
- `changeOwner` — reassigns the owning user.
- `export` — `pdfExport($psize, $notes, $events)`, `zipExport($notes, $tasks)`.
- `lock` / `unlock` (optimistic edit lock, not security) — `acquireLock`, `releaseLock`.

**Inputs:** `$vars` associative array combining ticket-form fields (subject, priority, help topic, department override), user-form fields (name/email/phone or `userId`), message body, attachments (name/type/data/encoding/size), `source`, `topicId`, `ip`, plus dynamic-form-field values discovered per help topic (see 3.4).

**Outputs:** `Ticket` object / `false` on failure with `$errors` populated; on create, `Ticket::getNumber()` (external ticket number, distinct from internal numeric `id`).

**Relationships:** belongs to one `User` (owner) and optionally `Organization` (via user); has one `Thread` (1:1) containing many `ThreadEntry` (messages/responses/notes); assigned to a `Staff` and/or `Team`; belongs to a `Dept`; has a `HelpTopic`; has a `Priority`, `SLA`, dynamic form data (`TicketForm`); can have collaborators (`Collaborator` → `User`); can have child tickets (merge parent/child via `pid`/merge flags); can spawn `Task`s.

**Permissions:** `Ticket::PERM_CREATE/EDIT/ASSIGN/RELEASE/TRANSFER/REFER/MERGE/LINK/REPLY/MARKANSWERED/CLOSE/DELETE` (all `ticket.*`), checked via `checkStaffPerm($staff, $perm)`; client access checked via `checkUserAccess($user)`; department-scoped visibility applies to staff (see §5).

**API suitability:** **high** for create/retrieve/list/update/status/assign/reply/note; **medium** for merge/refer/lock (workflow nuance, may need redesign); **low** for internal lock mechanics.

**Notes/caveats:**
- Ticket creation logic is deeply entangled with **filters/banlist/autoresponders** (`onNewTicket`) — a create endpoint must surface `errno=403` denial (filter/banlist rejection) distinctly from validation errors, as the existing `TicketApiController` does.
- "Ticket number" (external, used in subject lines/URLs) vs. internal DB `id` are different identifiers — API should standardize on one (recommend ticket number for public API, id for internal joins).
- Dynamic/custom fields make the ticket "schema" help-topic-dependent; a generic API needs a way to introspect required fields per topic (`getMissingRequiredFields`).
- SLA due-date, overdue-marking, and business-hours math are computed server-side (`getSLADueDate`, `markOverdue`) — expose as read-only computed fields, don't let API clients set them directly.

---

### 3.2 Threads, Messages, Attachments (`include/class.thread.php`, `include/class.attachment.php`)

**Domain:** Threads — the conversation container shared by tickets and tasks.

**Capability:** Store and render a typed sequence of communication entries (`M`essage, `R`esponse, `N`ote) plus file attachments, email metadata, and collaborators.

**Operations:** `create` (thread, entry, note, message, response — `Thread::create`, `ThreadEntry::add`, `Message::add`, `Response::add`, `Note::add`), `retrieve`/`list` (`getEntries`, `getMessages`, `getResponses`, `getNotes`, `getEntry($id)`), `delete` (`Thread::delete`, `deleteAttachments`), attachment `create`/`retrieve` (`createAttachment(s)`, `getAttachments`, `getAttachmentUrls`), collaborator `create`/`update`/`delete` (`addCollaborator`, `updateCollaborators`, `removeCollaborators`), `refer` (share thread), email-linked ingestion (`postEmail`, `lookupByEmailHeaders`).

**Inputs:** entry type, poster (staff/user/system), body (text/html), attachments, recipients (to/cc) for email-originated entries, email headers (Message-Id, References) for threading/deduplication.

**Outputs:** `ThreadEntry` object; rendered HTML/plain text (`render`); attachment records with download URLs.

**Relationships:** `Thread` belongs to exactly one `object` (a `Ticket` or `Task`, polymorphic via `object_type`/`object_id`); contains many `ThreadEntry`; entries have `Attachment`s and an `Editor`/`Poster`.

**Permissions:** `Thread::PERM_EDIT` (`thread.edit`); entry visibility gated by ticket/task permission checks upstream.

**API suitability:** **high** for reading a ticket's messages/notes/attachments and posting a reply/note (this is core "reply to ticket" functionality); **medium** for raw thread manipulation (editing/resending existing entries — `class.thread_actions.php`), which is more of a UI convenience layer.

**Notes/caveats:** Email deduplication relies on Message-Id/References header matching (`lookupByEmailHeaders`) — not relevant to a pure REST API but important if the wrapper also proxies email ingestion. Attachment upload has a separate validation path per form field (`FileField::uploadAttachment`) tied to the "message" field's config (max size/allowed types), not a generic global limit.

---

### 3.3 Users & Client Accounts (`include/class.user.php`)

**Domain:** Users (client-side identity), distinct from Staff.

**Capability:** Manage the people who submit tickets: profile data, organization membership, dynamic-form (custom field) data, and an optional self-service account (`UserAccount`) with password/2FA.

**Operations:** `create`/`register` (`User::register`, `UserAccount::register`/`createForUser`), `retrieve`, `update` (`updateInfo`, `UserAccount::update`), `delete` (`delete`, `deleteAllTickets`), `list`/`search` (directory), bulk `import` (`importCsv`, `importFromPost`), account `lock`/`unlock`, `sendResetEmail`, `sendConfirmEmail`, `checkPassword`.

**Inputs:** name, email(s) (a user can have multiple emails), phone, org linkage, dynamic form answers (`addDynamicData`, `addForm`).

**Outputs:** `User`/`UserAccount` objects; ticket-creation flows resolve/create a `User` from submitted name+email if no account exists.

**Relationships:** may belong to an `Organization`; owns many `Ticket`s; can be a `Collaborator` on others' tickets; has dynamic form data (`UserForm`).

**Permissions:** `User::PERM_CREATE/EDIT/DELETE/MANAGE/DIRECTORY` (`user.*`), staff-side; client-side self-service is self-scoped (a user manages only their own profile).

**API suitability:** **high**. This is a natural REST resource (`/users`), with a sub-resource for organization membership and ticket history.

**Notes/caveats:** osTicket allows tickets without a registered account (guest/"unregistered" users identified by email) — `User` and `UserAccount` are separate; not every `User` has an account. An API needs to decide whether to expose guest users as first-class resources or only "registered" ones.

---

### 3.4 Organizations (`include/class.organization.php`)

**Domain:** Organizations — group/company records that users belong to, useful for shared visibility and SLA/priority defaults.

**Capability:** CRUD for organizations, with dynamic form data and member (user) management.

**Operations:** `create` (`Organization::create`), `retrieve`, `update` (`updateProfile`, `update`), `delete`, `removeUser($user)`, dynamic data (`addForm`, `addDynamicData`), `check($flag)` (feature flags e.g. auto-assign-to-org-SLA).

**Relationships:** has many `User` members; can set default department/SLA/priority for its tickets; itself has dynamic form fields (`OrgForm`).

**Permissions:** `Organization::PERM_CREATE/EDIT/DELETE` (`org.*`).

**API suitability:** **high** — standard CRUD resource, `/organizations`, with a members sub-collection.

---

### 3.5 Staff / Agents (`include/class.staff.php`)

**Domain:** Staff — agent/admin accounts (distinct identity system from Users).

**Capability:** Agent account management: profile, credentials, role/permission assignment, team membership, department visibility, password reset, CSV import, stats.

**Operations:** `create` (`Staff::create`), `retrieve`, `update` (`update`, `updateProfile`, `updateConfig`), `delete`, `updatePerms`/`updateAccess` (per-department override of role permissions), `updateTeams` (membership), `importCsv`, `export` (static `Staff::export`), `sendResetEmail`, `cancelResetTokens`, `checkPassword`/`check_passwd`, `resetStats`.

**Inputs:** username, email, name, department, role, team memberships, permission overrides, password policy fields.

**Outputs:** `Staff` object; `applyVisibility`/`applyDeptVisibility` produce query filters used elsewhere to scope what an agent can see.

**Relationships:** belongs to a primary `Dept`; can belong to multiple `Team`s; has a `Role` (global) plus optional per-department permission overrides; owns/assigned tickets and tasks.

**Permissions:** `Staff::PERM_STAFF` (`visibility.agents`) plus the union of role-based permission checks used everywhere else (`checkStaffPerm`).

**API suitability:** **high** for CRUD/profile; **medium** for permission/role editing (security-sensitive, likely admin-only and worth restricting or omitting from a general API).

**Notes/caveats:** Staff authentication supports multiple backends (local, AD/LDAP planned via pluggable `StaffAuthenticationBackend`, see 3.15) — password reset/checkPassword behavior can differ by backend.

---

### 3.6 Teams (`include/class.team.php`)

**Domain:** Teams — groupings of staff for ticket assignment.

**Capability:** CRUD team, manage membership (with optional per-member "lead" flag), assignment target for tickets/tasks.

**Operations:** `create`, `update`, `updateMembers($access, &$errors)`, `save`, `delete`.

**Relationships:** has many `Staff` members; tickets/tasks can be `assignToTeam`.

**API suitability:** **high**.

---

### 3.7 Departments (`include/class.dept.php`)

**Domain:** Departments — organizational/routing unit for tickets and staff.

**Capability:** CRUD department; configure auto-assignment behavior, SLA/priority defaults, manager, and per-department access overrides for staff.

**Operations:** `create`, `update`, `updateAccess`, `assignMembersOnly`/`assignPrimaryOnly` (assignment policy toggles), `disableAutoClaim`, `disableReopenAutoAssign`, `export` (static), `delete`.

**Relationships:** has a manager (`Staff`); has member `Staff`/`Team`; tickets/help topics reference a department; ticket transfer moves between departments.

**Permissions:** `Dept::PERM_DEPT` (`visibility.departments`).

**API suitability:** **high** for read; **medium** for the assignment-policy toggles (admin config, but low risk to expose).

---

### 3.8 Help Topics (`include/class.topic.php`)

**Domain:** Help Topics — the client-facing category chosen when opening a ticket; drives which dynamic forms/department/priority apply.

**Capability:** CRUD help topic; link to one or more dynamic forms; set default department/priority/SLA/auto-response behavior; sort order.

**Operations:** `create`, `update`, `updateForms($vars, &$errors)` (attach/detach custom-field forms), `save`, `delete`, `updateSortOrder` (static).

**Relationships:** many-to-many with `DynamicForm`s (via `TicketForm`/topic-form links); a ticket references exactly one topic; topic can imply department/SLA/priority defaults.

**API suitability:** **high** — needed by any client wanting to know what fields to submit for ticket creation (introspection endpoint is valuable).

---

### 3.9 Dynamic Forms / Custom Fields (`include/class.forms.php`, `include/class.dynamic_forms.php`)

**Domain:** Dynamic Forms — osTicket's generic custom-field framework, reused across Ticket, Task, User, Organization, and Help Topic.

**Capability:** Define reusable field sets (`Form`/`DynamicForm`), attach them to entities, and store/validate per-instance answers (`DynamicFormEntry`).

**Operations:** `create`/`update`/`delete`/`disable` at the form-definition level (`DynamicForm::create/save/delete/disable/disableFields`); `save`/`saveAnswers`/`validateEntry`/`addMissingFields` at the entry (per-object answer set) level; `saveTranslations` for i18n of field labels.

**Inputs:** field type, label, name, validation rules, choices (for select/radio), required flag, visibility conditions.

**Outputs:** field metadata (for rendering a create/edit form) and stored answer values.

**Relationships:** `Form` has many `FormField`; an entity (Ticket/Task/User/Org) has one-or-more `DynamicFormEntry` linking it to a `Form` with concrete answers.

**API suitability:** **medium** for form/field *definition* management (admin-level schema editing — significant design surface for a clean API); **high** for *reading* the schema (to build a dynamic ticket-creation client) and for *submitting* answers as part of ticket/task/user/org create-update payloads.

**Notes/caveats:** This is the single largest and most structurally complex subsystem (450+ methods across the two files). A FastAPI design should almost certainly **not** try to expose full form-builder CRUD as a first-class resource-per-field-type API; instead expose (a) schema introspection per help topic/entity, and (b) generic "answers" as part of the owning entity's payload. Field types (text, memo, choices, department, priority, assignee-picker, etc.) each have their own widget/validation class — enumerate exact types as an open question (§8).

---

### 3.10 Knowledgebase, FAQ, Canned Responses (`include/class.faq.php`, `include/class.knowledgebase.php`, `include/class.canned.php`)

**Domain:** Self-service and agent-productivity content.

**Capability:**
- **FAQ**: CRUD article, category (`Category`) organization, link to help topics (`updateTopics`), attachments, translations.
- **Knowledgebase**: premise/config wrapper around FAQ + categories (`create`, `update`, `delete`, `validate`).
- **Canned Responses**: CRUD reusable reply text, apply directly to a ticket as a reply (`Ticket::postCannedReply`).

**Operations:** `create`, `retrieve`, `update`, `delete`, `list`/`search`, and for canned responses, `apply` (post as a reply).

**Relationships:** FAQ belongs to a `Category`, optionally linked to one or more `HelpTopic`s; Canned responses can be scoped to a department/topic.

**Permissions:** `Faq::PERM_MANAGE` (`faq.manage`), `Canned::PERM_MANAGE` (`canned.manage`).

**API suitability:** **high** for all three — clean, low-risk CRUD resources valuable for a self-service portal or agent tooling.

---

### 3.11 Tasks (`include/class.task.php`)

**Domain:** Tasks — internal work items, either standalone or attached to a ticket, sharing much of the Ticket/Thread/Assignment machinery.

**Capability:** Create/manage a task with its own thread, dynamic form data, assignment, and lifecycle.

**Operations:** `create` (`TaskModel::create` / `Task::create` for the description entry), `update`, `updateField`, `assign`/`assignToStaff`, `transfer`, `claim`, `addCollaborator`, `addDescription`, `delete`.

**Permissions:** `TaskModel::PERM_CREATE/EDIT/ASSIGN/TRANSFER/REPLY/CLOSE/DELETE` (`task.*`).

**Relationships:** may belong to a `Ticket` (`object_id`/`object_type` on its `Thread`) or stand alone; has its own `Thread`.

**API suitability:** **high** — same shape as tickets, reuse the same API design patterns.

---

### 3.12 Queues / Views (`include/class.queue.php`)

**Domain:** Saved, configurable ticket list views ("queues") used in the SCP — the query-builder behind the ticket list UI.

**Capability:** Define named queries over tickets with custom columns, sort order, filters, and access scoping (`checkAccess`, `checkOwnership`); export queue results to CSV.

**Operations:** `create`, `update`, `save`, `delete` (implicit via disable), `enable`/`disable`, `updateColumns`, `updateExports`, `export` (CsvExporter), `addColumn`.

**API suitability:** **low**. This is the single largest class in the codebase (222 methods) and is essentially a bespoke SQL/query-builder UI concept, not a resource clients need. A FastAPI wrapper should instead offer its own simple `list tickets with filters` endpoint (status, department, assignee, date range, search term) rather than exposing "queues" as a concept. Existing saved queues could optionally be exposed **read-only** (medium) to let a client reuse an admin-defined view.

---

### 3.13 Filters & Filter Actions (`include/class.filter.php`, `include/class.filter_action.php`)

**Domain:** Automated rule engine applied at ticket creation/update (e.g., auto-reject spam domains, auto-route by subject).

**Capability:** CRUD filter rules (match conditions) and filter actions (assign, reject, set field, etc.); apply filters during ticket create/update.

**Operations:** `create`, `update`, `delete`, `addRule`/`removeRule`, `validate_rules`/`save_rules`, `validate_actions`/`save_actions`, `apply($ticket, ...)`, `disableFilters`/`disableAlerts`.

**API suitability:** **medium**. Useful for admins to manage automation via API, but rule/action definitions are highly osTicket-internal (match types, action types are an open enumeration — §8) and this is a lower-priority resource than tickets/users.

---

### 3.14 SLA, Business Hours, Priorities, Sequences

- **SLA** (`class.sla.php`): CRUD SLA plan (grace period, business-hours-aware due date calc: `addGracePeriod`), `sendAlerts`. **High** suitability — simple, valuable resource (`/slas`).
- **Business Hours / Schedules** (`class.businesshours.php`, `class.schedule.php`): define operating windows used by SLA due-date math. **Medium** — mostly config, but useful read (and occasional write) resource.
- **Priorities** (`class.priority.php`): simple lookup/CRUD list (Low/Normal/High/Emergency, custom levels). **Medium** — small, mostly static reference data; likely exposed as a lookup endpoint rather than full CRUD.
- **Sequences** (`class.sequence.php`): internal numbering generators (ticket numbers, task numbers). **Low** — pure implementation detail, should not be exposed; ticket-number generation should stay server-side.

---

### 3.15 Email (`include/class.email.php`, `include/class.mailfetch.php`, `include/class.mailparse.php`, `api/pipe.php`)

**Domain:** Inbound/outbound email integration.

**Capability:**
- **Mailbox accounts**: CRUD an IMAP/POP/SMTP mailbox config (`class.email.php`), banlist toggle per mailbox (`PERM_BANLIST`).
- **Fetching**: `MailFetcher::processEmails()` polls a mailbox and turns messages into tickets/thread entries (cron-driven, `class.cron.php: MailFetcher()`).
- **Piping**: `api/pipe.php` + `PipeApiController` — local MTA pipes a raw email directly into osTicket (no API key; process-exit-code based response), used for `procmail`/`postfix` style local delivery instead of remote polling.
- **Outbound**: templated notices/alerts throughout (new ticket, new message, assignment, overdue, SLA alert, password reset, etc.) rendered via `class.template.php`/email templates, sent via `class.mailer.php`.

**API suitability:** **medium** for mailbox account CRUD (admin config); the actual email ingestion pipeline is **not** something a FastAPI wrapper should re-expose as a public write endpoint — inbound email already has its own dedicated paths (fetch cron + pipe). Outbound notification content/templates: **low** (internal, tied to `class.template.php` rendering).

---

### 3.16 Authentication & Authorization (`include/class.auth.php`, `class.2fa.php`, `class.role.php`, `class.session.php`, `class.ostsession.php`)

**Domain:** Identity verification and permission enforcement, for both staff and client users, with pluggable backends.

**Capability:**
- Pluggable authentication backends registered via `StaffAuthenticationBackend::register($bk)` / `UserAuthenticationBackend::register($bk)` — local DB backend is default; architecture supports others (e.g., external SSO) though only local backend classes were directly observed.
- `process($username, $password, &$errors)` — credential check; `processSignOn` — SSO/remote-auth continuation; `checkPolicies` — password policy (expiry, complexity) enforcement; `authTimeout`/`authstrike` — lockout after repeated failures; `authAudit` — log auth attempts.
- **2FA**: pluggable backends (`TwoFactorAuthentication::register($class)`), `send($user)` (deliver OTP), `validate($form, $user)`.
- **Roles/Permissions**: `Role` is a named bundle of boolean permission flags; `RolePermission::register($group, $perms)` is how each domain class (Ticket, Task, User, Org, Faq, Canned, Staff...) contributes its permission constants into the global role editor. Staff get a role plus optional per-department overrides (`Staff::updateAccess`).

**Permissions model summary (see §5 for full table):** two independent principal types — **Staff** (agents, permission-driven via Role + dept scoping) and **User** (clients, mostly self-scoped, no granular permission system — client capability is defined by what's *their* ticket/org). API keys are a third principal type, scoped by IP + two coarse flags (`can_create_tickets`, `can_exec_cron`), **not** by department/role.

**API suitability:** **medium**. Login/session issuance and 2FA validation are necessary for a real API (likely via a token/OAuth-style layer built *on top of*, not replacing, this), but exposing raw backend-registration or policy internals is **low**/not applicable.

**Notes/caveats:** The existing `API` key model (`class.api.php`) is much coarser than staff Role permissions — it has no concept of "which departments" or "which operations" beyond create-tickets/exec-cron. A new FastAPI layer will likely need its own richer authorization model (e.g., API key mapped to a Staff/Role, or OAuth2 scopes) rather than reusing `API_KEY_TABLE` as-is. **Flagged as an open design question, not a fact to assume.**

---

### 3.17 Plugins (`include/class.plugin.php`, `include/plugins/*`)

**Domain:** Extensibility — installable plugin bundles (bootstrap-registered) that can add auth backends, 2FA backends, storage backends, etc.

**Capability:** `create` (register plugin install record), `update`/`saveConfiguration`, `enable`/`disable` (`addInstance` for multi-instance plugins), `delete`.

**API suitability:** **low** — admin/installation-time concern, not a runtime API resource for end clients. Possibly **medium** if the wrapper's own operators need to toggle plugins programmatically, but out of scope for a typical helpdesk API consumer.

---

### 3.18 Configuration / System Settings (`include/class.config.php`)

**Domain:** Global system settings (219 methods) — everything from ticket numbering format to email notification toggles to KB visibility.

**Capability:** `create`/`update`/`updateAll` for individual keyed settings; grouped setters per SCP settings tab: `updateSystemSettings`, `updateAgentsSettings`, `updateUsersSettings`, `updateTicketsSettings`, `updateTasksSettings`, `updateEmailsSettings`, `updatePagesSettings`, `updateAutoresponderSettings`, `updateKBSettings`, `updateAlertsSettings`. Many boolean feature-flag getters (`enableStaffIPBinding`, `addCollabsViaEmail`, `notifyOnNewStaffTicket`, etc.).

**API suitability:** **low-medium**. Read-only exposure of a curated subset (e.g., "is registration required", "default department") could be **medium**/useful for a client app to adapt its UI; bulk raw settings CRUD is **low** — too broad/sensitive (touches mail credentials, security policy) for a general API and best left to SCP.

---

### 3.19 Search, Export, Logs, Cron, Import (system/admin plumbing)

- **Search** (`class.search.php`): pluggable search backend (`SearchBackend::register`), indexes tickets/users/etc. for full-text query; `PERM_EVERYTHING` (`search.all`) for cross-department search. **Medium** — a `/search` endpoint is useful but should wrap this, not expose backend internals.
- **Export** (`class.export.php`): CSV export helpers for tasks, users, organizations, queue results; ZIP/PDF export of ticket threads. **Medium** — useful as a bulk-download endpoint, but format/streaming design needs its own spec.
- **Logs** (`class.log.php`): system/audit log entries (errors, warnings, auth events). **Medium** — read-only admin visibility endpoint.
- **Cron** (`class.cron.php`, `api/cron.php`): scheduled jobs — mail fetch, ticket-overdue monitor, log/draft purge, orphaned-file cleanup, session cleanup, table optimization. Triggered externally via `api/cron.php` (needs a valid API key with `can_exec_cron`) or system cron. **Low** — this is infrastructure/ops, not a client-facing capability; a wrapper might need to *trigger* it (medium) but not expose it as a resource.
- **Import** (`class.import.php`): generic bulk-import framework underlying CSV imports for users/staff. **Low** — implementation detail behind the User/Staff import capabilities already listed.

---

### 3.20 Miscellaneous discovered subsystems

- **Banlist** (`class.banlist.php`): simple add/remove of banned email addresses, checked during ticket creation/filtering. **High** — small, clean, valuable (`/banlist`).
- **Collaborators** (`class.collaborator.php`): join entity between `User` and a `Thread`, with role (CC) — already covered under Tickets/Threads (3.1/3.2). **High** as a ticket sub-resource.
- **Company** (`class.company.php`): appears to be a legacy/alternate concept related to Organization — only 8 methods, low usage observed; **flagged for further investigation** (§8) rather than asserted as distinct from Organization.
- **Categories** (`class.category.php`): FAQ category tree — covered under Knowledgebase (3.10).
- **Pages/CMS** (`pages/`, referenced by `scp/pages.php`): static content pages (e.g., "offline" page, custom portal pages) — **low-medium**, a simple CRUD content resource but not central to a ticketing API.
- **Avatar** (`class.avatar.php`), **Note** (`class.note.php` — appears to overlap with Thread notes, needs disambiguation), **Draft** (`class.draft.php` — autosave drafts of in-progress replies/tickets, tied to session, **low** API value), **Lock** (`class.lock.php` — generic optimistic-lock primitive used by Ticket's `acquireLock`, **low**, internal), **Session** (`class.session.php`/`class.ostsession.php` — server-side session storage, **not applicable** to a stateless API), **Signal** (`class.signal.php`, 2 methods — appears to be a simple pub/sub event-dispatch hook used for extensibility, **not** a client-facing capability).

---

## 4. Cross-Domain Relationships (Summary Graph)

```
Organization ──< User ──< Ticket >── Dept
                   │         │         │
                   │         ├──< Thread ──< ThreadEntry ──< Attachment
                   │         │         │            └──< Collaborator >── User
                   │         ├── Staff (assignee)          
                   │         ├── Team (assignee)
                   │         ├── HelpTopic ──< DynamicForm ──< FormField
                   │         ├── Priority
                   │         ├── SLA ──< BusinessHoursSchedule
                   │         └──< Task ──< Thread (own)
                   └── DynamicForm (UserForm)

Staff ── Dept (primary)
Staff >──< Team
Staff ── Role ──< RolePermission (per-domain perm bits)
Staff ──< Staff/Dept access override

FAQ ── Category
FAQ >──< HelpTopic
Canned response (standalone, optionally dept/topic scoped)

ApiKey (IP + flags) ── independent of Staff/Role
```

Key structural facts an API design must respect:
- `Thread` is polymorphic (`object_type`/`object_id`) — shared by Ticket and Task. A generic "thread" API could serve both.
- `User` vs `UserAccount`, and `Staff` vs its `Role`, are separate objects each — profile data vs. credentials/permissions are modeled separately.
- Dynamic form data is not a fixed column set on Ticket/Task/User/Org — it's a separate `DynamicFormEntry` linkage. Any API schema for these entities needs a "custom fields" sub-object, not flat properties.
- Ticket **number** (public, string, e.g. `123456`) vs. **id** (internal int PK) are different fields both used as identifiers in different contexts.

---

## 5. Authentication and Authorization Model

**Principal types (three, independent):**
1. **Staff** (agents/admins) — authenticate via `StaffAuthenticationBackend`; authorized via `Role` (bundle of named permission booleans, e.g. `ticket.create`, `user.manage`) plus **department scoping** (a staff's primary dept, extra dept memberships via Teams, and per-dept permission overrides via `Staff::updateAccess`). `checkStaffPerm($staff, $perm)` is the universal gate used across Ticket/Task/User/Org/Faq/Canned actions.
2. **User** (clients) — authenticate via `UserAuthenticationBackend`; authorization is essentially self-scoped (a client can act on their own tickets/profile, plus org-shared tickets if the org enables shared visibility) rather than permission-flag driven. No `Role` concept on the client side.
3. **API Key** (`class.api.php`) — IP-address + shared-secret key, with exactly two authorization flags: `can_create_tickets`, `can_exec_cron`. No user/staff identity, no department scoping, no per-resource permission. This is the entire authorization model for the *existing* API.

**2FA** is optional per-account, pluggable backend (`TwoFactorAuthentication`), enforced at login (`_validate`/`onValidate`) with strike/timeout limits.

**Session** state is server-side (`class.session.php`), cookie-based — not directly reusable by a stateless FastAPI wrapper; a new API needs its own session/token issuance layer, most plausibly built by authenticating against Staff or User credentials via the same `Auth::process()` entry point and then minting its own tokens (recommendation, not an existing osTicket capability — flagged for design in the FastAPI project, not asserted as present).

**Implication for the wrapper:** the existing API-key model is too coarse to safely expose most of the "high suitability" write operations above (e.g., assign ticket, delete user) without building a proper authorization layer mapped to Staff/Role, since none of that granularity exists on `ApiKey` today.

---

## 6. Notable Implementation Constraints for an API Wrapper

- **No existing read/list/update API** for anything — every "retrieve/list/update" capability in this document is currently reachable only through session-authenticated AJAX/SCP controllers (`include/ajax.*.php`, `scp/*.php`), not a stable programmatic contract. Response shapes there are tuned for the jQuery UI (HTML fragments, not JSON) in many cases — expect to re-derive JSON shapes from the model layer directly (`asVar()` methods on Ticket/Thread/etc. are the closest thing to a "public field export" convention already in the codebase).
- **Custom fields are dynamic**, not fixed columns — any generic Ticket/Task/User/Org schema exposed by the API must include an open-ended custom-fields object, discoverable via the relevant `DynamicForm`.
- **Two identifier schemes for tickets** (number vs. id) and likely similarly for tasks — pick one as canonical for the public API and map internally.
- **PHP procedural/global-state patterns**: many classes rely on globals (`$ost`, `$thisstaff`, `$thisclient`) and static session state rather than pure dependency injection — a wrapper calling into this code (if done via shared PHP execution rather than DB access) needs to bootstrap that global context per request; if instead reimplementing against the database directly, the ORM's business rules (validation, filters, notifications) would need to be reproduced, which is a significant scope decision **not resolved by this document**.
- **Notifications/side-effects are baked into model methods** (e.g., `onNewTicket`, `onAssign`, `onResponse` fire emails) — API operations will trigger real emails unless explicitly suppressed via the `$alert`/`$autorespond` flags most methods already accept.
- **Filters/banlist can silently reject ticket creation** — must be surfaced as a distinct error type (already the pattern in `TicketApiController`).
- **File attachments** go through per-field validation tied to form config, not a flat global upload policy.
- **Existing API auth (IP+key) doesn't map to Staff identity** — actions like "assign" or "reply as agent X" have no existing API-level concept of *which* agent is acting.

---

## 7. Functionality That Should Probably NOT Be Exposed Directly

- **Queues/Views internal query-builder** (`class.queue.php`) — bespoke SQL/column/sort DSL; replace with a purpose-built filter/list API instead of exposing this class's shape.
- **Sequences** (ticket/task numbering) — must stay server-generated.
- **Session/Lock internals** (`class.session.php`, `class.ostsession.php`, `class.lock.php`) — server implementation detail, not a resource.
- **Plugin registration/config internals** — admin/installation-time, high blast-radius if exposed generally.
- **Auth backend / 2FA backend registration mechanics** (the `register($bk)` extensibility points) — these are PHP-level extension points for *developers*, not runtime API capabilities.
- **Raw system Config bulk-write** (`updateSystemSettings` etc.) — touches mail credentials and security policy; at most expose a small curated read-only subset.
- **Cron internals** — trigger via a narrow operational endpoint at most, never as a general resource.
- **Draft autosave** (`class.draft.php`) — tightly coupled to the web UI's autosave UX; low value as an API concept.
- **Email piping (`api/pipe.php`)** — this is a local-MTA integration mechanism (exit-code based), architecturally incompatible with a REST wrapper; don't try to unify it with a JSON create-ticket endpoint, keep them separate.
- **Thread "actions" replay/resend mechanics** (`class.thread_actions.php`) — UI-dialog-specific helper layer over more fundamental Thread/Ticket operations already covered.

---

## 8. Open Questions / Areas Requiring Further Investigation

These were not conclusively resolved from the code sampled and should not be assumed true without a follow-up pass. Status after the §8.1 follow-up pass is noted in brackets.

1. ~~**Exact set of dynamic form field types**~~ — **RESOLVED, see §8.1.1.** Full type enumeration (11 admin-selectable base types + 5 system-only types + the open-ended Custom List type) with validation model documented.
2. **`class.company.php` vs `class.organization.php`** — **still open.** Not in scope for this pass; only 8 methods were counted, not cross-referenced against callers.
3. **`class.note.php`** relationship to `Thread`'s built-in `Note` entry type — **still open**, not investigated this pass. (Note: §8.1.2 did newly confirm that Users and Organizations each have their *own* note stream via `ajax.users.php`/`ajax.orgs.php: createNote()`, separate from ticket-thread notes — this is additive information, not a resolution of the original class-level overlap question.)
4. **Auth backend plurality** — **still open.** Not investigated this pass; only the OAuth2 path for *mailbox* accounts was newly confirmed (§8.1.2), which is a different subsystem from staff/user login backends.
5. **Exact list of filter match-types and filter-action types** — **still open.** Not investigated this pass.
6. ~~**Client-side/staff-side/AJAX controller directories not inventoried**~~ — **RESOLVED, see §8.1.2.** All `include/ajax.*.php` handlers relevant to core entities, plus `scp/*.php` and top-level client controllers, were reviewed. New capabilities (bulk actions, Custom Lists, entity-level notes, post-hoc form attachment, mailbox OAuth2) are documented; most other AJAX endpoints confirmed as alternate interfaces to already-documented model-layer capabilities.
7. **Plugin bundles under `include/plugins/`** — **still open** for third-party/optional plugins in general. Narrowly confirmed: the only plugin directory present in this snapshot is `fastapi_integration` (the user's own project, out of scope, not core osTicket) — i.e., **no core/bundled osTicket plugins ship in this codebase**, so there is currently nothing to audit here beyond the mechanism itself (§8.1.4 confirms the permission-registration hook is generic and available to any future plugin).
8. **`pages/` (CMS) and `frontend/`** — **still open.** Not investigated this pass.
9. ~~**Exact permission bit list per Role**~~ — **RESOLVED, see §8.1.4.** Exhaustive `PERM_*`/`RolePermission::register` grep across the full `include/` tree; 32 permissions across 8 classes and 6 groups enumerated, with a noted gap that several domains (Lists, Forms, Teams, SLA, Filters, Plugins, Config, Roles) have no named permission at all.
10. **Database schema** — **partially resolved, see §8.1.3.** No baseline SQL dump exists in this codebase to review directly (a discovery in itself — flagged in 8.1.3), so this was addressed via exhaustive review of ORM `$meta` definitions instead, which is a reliable but not identical substitute for literal DDL. Table/column *types, lengths, and defaults* remain unverified.

---

## 8.1 Follow-up Verification

Second-pass, targeted investigation to resolve specific items from §8. Scope: dynamic form field types (`class.forms.php`, `class.dynamic_forms.php`, `class.list.php`), `include/ajax.*.php` + top-level client controllers + `scp/*.php`, ORM `$meta` definitions (used in place of a missing baseline SQL dump — see 8.1.3), and an exhaustive `PERM_*`/`RolePermission::register` grep. Nothing here contradicts the original inventory's domain/suitability judgments except where explicitly marked **CORRECTION**.

### 8.1.1 Dynamic Form Field Types

osTicket registers field types through `FormField::addFieldTypes($group, $callable)`. The base "Basic Fields" group (admin-selectable when building a custom form) plus a "Dynamic Fields" group (system-reserved, used for built-in Ticket/Task fields, not offered in the generic form builder) together give the complete type set found in this codebase:

| Key | Class | Human label | Admin-selectable? | Value type | Choices? | References entity? |
|---|---|---|---|---|---|---|
| `text` | `TextboxField` | Short Answer | yes | string | no | no |
| (subtype) `password` | `PasswordField` (extends `TextboxField`) | — | no (used internally, e.g. agent passwords) | string (masked) | no | no |
| `memo` | `TextareaField` | Long Answer | yes | string (multi-line, optional HTML) | no | no |
| `thread` | `ThreadEntryField` | Thread Entry | no (system; the ticket "message" field is this type) | string (message body + attachments) | no | no |
| `datetime` | `DatetimeField` | Date and Time | yes | date/datetime string | no | no |
| `timezone` | `TimezoneField` (extends `ChoiceField`) | Timezone | yes | string key | yes (fixed system TZ list) | no |
| `phone` | `PhoneField` | Phone Number | yes | string (digits + optional `X`-delimited extension) | no | no |
| `bool` | `BooleanField` | Checkbox | yes | boolean | no | no |
| `choices` | `ChoiceField` | Choices | yes | string key or array of keys (if multiselect) | yes, admin-typed `key:value` lines | no |
| `files` | `FileUploadField` | File Upload | yes | array of file refs (id/name/type/size or base64+encoding) | no | no (attachments are their own table, see 8.1.3) |
| `break` | `SectionBreakField` | Section Break | yes | none (layout-only, no answer) | no | no |
| `info` | `FreeTextField` | Information | yes | none (display-only static text/HTML) | no | no |
| `priority` | `PriorityField` (extends `ChoiceField`) | Priority Level | no (system; built into Ticket form) | string key | yes | **yes** — `Priority` |
| `department` | `DepartmentField` (extends `ChoiceField`) | Department | no (system) | int/string key | yes, dynamically populated | **yes** — `Dept` |
| `assignee` | `AssigneeField` (extends `ChoiceField`) | Assignee | no (system; used for assignment/transfer forms) | key | yes, dynamically populated (staff + teams) | **yes** — `Staff`/`Team` |
| `state` | `TicketStateField` (extends `ChoiceField`) | Ticket State | no (system, internal) | fixed key | yes, fixed set (open/closed/etc., see 8.1.3) | no |
| `flags` | `TicketFlagField` (extends `ChoiceField`) | Ticket Flags | no (system, internal) | key(s) | yes, fixed set (TODO comment in source: "move to configurable custom list") | no |
| `numeric` (registered ad hoc; class present but not confirmed under a public admin key — see below) | `NumericField` | Number | yes (used internally at minimum, e.g. search field types) | numeric | no | no |
| — | `ColorChoiceField` | Color Picker | not found registered via `addFieldTypes` in this snapshot | string (hex) | yes (fixed palette) | no |
| — | `TimeField` | Time | not found registered via `addFieldTypes`; used directly by business-hours/schedule config, not the generic form builder | time string | no | no |
| — | `InlineFormField` / `InlineDynamicFormField` | (embed one form inside another) | internal only | nested form data | n/a | **yes** — another `DynamicForm` |
| `list:<list_id>` (dynamic, per-list) | resolves to a `ChoiceField` subtype (`SelectionField` in `class.dynamic_forms.php`) driven by `DynamicList::getSelections()` | (custom list name, e.g. "Products") | yes, one per admin-defined Custom List | key from the list | yes, **entity-backed** (see below) | **yes** — a `DynamicList` (`ost_list`/`ost_list_items`) |

**Custom Lists are a distinct, previously-undocumented domain.** `include/class.list.php` (`DynamicList`, `DynamicListItem`) implements admin-managed named lists (e.g. "Products", "Office Locations") whose items become the choices for one or more Choice-type fields across any form. Each list has configurable masks (`MASK_EDIT`, `MASK_ADD`, `MASK_DELETE`, `MASK_ABBREV`) controlling end-user list manipulation, and items can carry an "abbreviation" and be individually disabled. This is registered into the form builder via `FormField::addFieldTypes('Custom Lists', array('DynamicList', 'getSelections'))` (`include/class.list.php:613`), meaning **every custom list an admin creates becomes its own selectable field type** at form-build time — an open-ended, admin-extensible field type set, not a fixed enum.

**Validation model (applies generically, confirmed from `TextboxField`/`PhoneField`/`ChoiceField` reads):** every field has `required` (bool), `default`, and a `configuration` array whose contents are type-specific (e.g., `size`/`length`/`validator` for text; `digits`/`ext`/`format` for phone; `choices`/`multiselect`/`prompt`/`default` for choice fields). Text fields additionally support a named sub-validator (`phone`, `email`, `ip`, `number`, or custom regex) independent of the base type. Choice-field "choices" are admin-authored as `key:value` newline-delimited text and stored/transported as JSON (`to_database`/`to_php` use `JsonDataEncoder`/`JsonDataParser`); values may also appear as comma-separated key lists for legacy/CDATA compatibility.

**How this could map to an API schema:** expose two orthogonal concepts — (1) a **field-type enum** (the `key` column above: `text`, `memo`, `phone`, `bool`, `choices`, `datetime`, `timezone`, `files`, `break`, `info`, plus the system-only `priority`/`department`/`assignee`/`state`/`flags` for built-in ticket fields, plus a generic `list` type parameterized by `list_id` for custom lists) and (2) a **field descriptor** object per field: `{name, type, label, required, default, configuration: {...type-specific...}, choices: [{key, value}] | null, references: "priority"|"dept"|"staff"|"team"|"list:<id>"|null}`. Layout-only types (`break`, `info`) should be flagged non-answerable (`presentation_only`, a real property already present on `FormField`) so a JSON API can omit them from "answers" payloads while still returning them for form-rendering/introspection use cases.

**Correction to §3.9:** the original inventory noted SLA/Topic "have their own dynamic-field-like classes"; verification shows `SLAField`/`TopicField` extend `ChoiceField` but are **not** registered via `addFieldTypes` (no admin-facing key found) — they exist to render ticket-form dropdowns internally, not as generally reusable custom-field types. Treat them as system/internal, not part of the admin-extensible type set.

---

### 8.1.2 Client / Staff / AJAX Functionality — Newly Discovered Capabilities

Reviewed: all `include/ajax.*.php` handlers relevant to core entities, `scp/*.php` top-level controllers, and top-level client controllers (`account.php`, `tickets.php`, etc.). Most AJAX endpoints are confirmed to be **alternate interfaces to already-documented capabilities** (e.g. `ajax.tickets.php`'s `assign`/`claim`/`transfer`/`refer` map 1:1 to `Ticket::assign/claim/transfer/refer`). The genuinely new items:

| Domain | Capability | Operation | Implementation | Inputs | Outputs | Relationships | Permissions | API suitability | New or existing? |
|---|---|---|---|---|---|---|---|---|---|
| Tickets | Bulk/mass ticket actions (assign, close, delete, change status, mark-as-read, etc. across a multi-select or saved-search result set) | update (bulk) | `include/ajax.tickets.php: massProcess($action, $w)`, `changeSelectedTicketsStatus`/`setSelectedTicketsStatus` | action name + list of ticket IDs (or a saved-search/queue reference) | per-item success/failure results | operates over N `Ticket` objects | same per-ticket perms as the underlying single-ticket action | **medium** | New — bulk variant not previously captured; matters for API design (batch endpoint vs. N calls) |
| Tickets | Mark thread/ticket read-state and answered-state toggle from the list view | update | `include/ajax.tickets.php: markAs($tid, $action)` | ticket id, action (`read`/`unread`/similar) | updated flag | per-ticket, per-staff read state | `ticket.edit`-level | low-medium | New (minor) — distinct from `Ticket::markAnswered` already documented |
| Tickets | Attach/detach an additional dynamic form to an *existing* ticket (post-creation) | update | `include/ajax.tickets.php: manageForms/updateForms($ticket_id)` | form id(s) | updated form/field set on ticket | ticket ↔ `DynamicForm` (many, not fixed at creation) | `ticket.edit` | medium | **Clarifies/extends §3.9** — dynamic forms are not fixed at ticket creation; forms can be added later. Same pattern confirmed for Users/Orgs (see below). |
| Tickets | Change which user-form data / owning user is associated with a ticket inline | update | `include/ajax.tickets.php: viewUser/updateUser/changeUserForm($tid)` | user id or user-form field values | updated ticket-user link | Ticket → User | `ticket.edit` | medium | New nuance on already-documented `Ticket::changeOwner` |
| Users | Create a note on a **User** record (not a ticket) | create | `include/ajax.users.php: createNote($id)` | note title/body | `Note` record scoped to the user | User has its own note/log stream | `user.edit`/`user.manage` | medium | **New** — a User-level note capability was not previously documented (only ticket/task notes were) |
| Users | Attach/manage dynamic forms on a User post-creation | update | `include/ajax.users.php: manageForms/updateForms($user_id)` | form id(s) | updated field set | User ↔ `DynamicForm` | `user.edit` | medium | Same "forms attached post-hoc" clarification as tickets |
| Users | Import a user directly from a remote/external auth backend into the local user table | create | `include/ajax.users.php: addRemoteUser($bk, $id)` | backend key + remote id | new local `User` | User sourced from `UserAuthenticationBackend` | `user.create` | medium | New — confirms remote-auth-backend-as-directory-source is a real capability, not just login |
| Users | Reassign a user's organization | update | `include/ajax.users.php: updateOrg($id, $orgId)` | org id | updated `User.org_id` | User → Organization | `user.edit` | high | New explicit op; previously only implied via Organization's `addForm`/membership methods |
| Users | Export a single user's tickets | retrieve | `include/ajax.users.php: exportTickets($id)` | user id | CSV/export file | User → Ticket(s) | `user.manage` | medium | New — a per-user scoped variant of `Export` |
| Organizations | Create a note on an Organization record | create | `include/ajax.orgs.php: createNote($id)` | note text | `Note` scoped to org | same pattern as User notes | `org.edit` | medium | New (same pattern as Users) |
| Organizations | Attach/manage dynamic forms on an Organization post-creation; export an org's tickets | update / retrieve | `include/ajax.orgs.php: manageForms/updateForms`, `exportTickets($org_id)` | — | — | Organization ↔ DynamicForm; Organization → Ticket(s) | `org.edit` | medium | Same clarification as above |
| Custom Lists | Full CRUD + bulk item management for admin-defined choice lists | create, retrieve, update, delete, list, bulk-disable/enable, bulk-delete, import | `include/ajax.forms.php: getListItem(s)/saveListItem/searchListItems/addListItem/importListItems/disableItems/undisableItems/deleteItems`; `scp/lists.php` | list id, item name/value/abbreviation/sort | list/item records | `DynamicList` ↔ `DynamicListItem`, referenced by Choice-type fields (see 8.1.1) | admin/forms-management permission (no dedicated `PERM_*` found — see 8.1.4 gap) | **high** | **New domain** — not present in the original inventory at all |
| Dynamic Forms | Clear/delete a single stored answer independent of a full entity update | delete | `include/ajax.forms.php: deleteAnswer($entry_id, $field_id)` | entry id, field id | removed answer row | `DynamicFormEntry` | matches owning entity's edit perm | medium | New (minor) — a scoped delete not covered by "update entity" |
| Knowledgebase | Restrict an FAQ article's visibility (department/help-topic scoped) | update | `include/ajax.kbase.php: manageFaqAccess($id)` | dept/topic id list | updated FAQ visibility scope | FAQ ↔ Topic (already modeled as `FaqTopic` join, `faq_id`+`topic_id`) | `faq.manage` | medium | New nuance on §3.10 — FAQ visibility is scoped, not always-public |
| Staff | View an individual agent's effective permission set; reset an agent's permissions to role defaults | retrieve, update | `include/ajax.staff.php: getAgentPerms($id)`, `resetPermissions()` | staff id | permission set (see 8.1.4) | Staff → Role (+ optional per-department Role via `StaffDeptAccess`, see 8.1.3) | `staff.manage`-equivalent | medium | New — confirms permission introspection is a real, separate operation from role CRUD |
| Staff | Set/change a staff member's avatar image | update | `include/ajax.staff.php: setAvatar($id)` | image file | stored avatar | Staff → file | self or `staff.manage` | low | New (minor, cosmetic) |
| Email | Configure OAuth2 authorization for a mailbox account; revoke a stored OAuth2 token | create, delete | `include/ajax.email.php: configureAuth($id, $type, $auth)`, `deleteToken($id, $type)`; backed by `include/class.oauth2.php` | OAuth2 provider/type + auth code | stored token | Email account ↔ OAuth2 token | mailbox-admin | medium | New nuance on §3.15 — mailbox auth isn't just IMAP/POP credentials, OAuth2 (e.g. Microsoft/Google) is a supported path |
| Email | Send a diagnostic test email through a configured mailbox account | process | `scp/emailtest.php` | mailbox id, recipient, subject | delivery attempt result | Email account | mailbox-admin | low | New (minor, diagnostic/ops) |
| Search/Queues | **Personal "Saved Search"** (per-agent private filter), distinct from admin-shared **Custom Queue** | create, retrieve, update, delete | `include/ajax.search.php: createSearch/saveSearch/editSearch`, vs. `class.queue.php`'s `CustomQueue` | search criteria | `SavedSearch` record, staff-scoped | Staff → SavedSearch (1:many, private) vs. shared `CustomQueue` (already documented, low suitability) | owner-only (implicit) | low-medium | Clarifies §3.12 — two distinct entities were conflated as "queues"; SavedSearch is smaller/simpler and closer to a legitimate per-user "saved filter" API resource than CustomQueue is |
| Admin | Bulk enable/disable/delete API keys; bulk delete/disable custom-list items; bulk-clear logs | update/delete (bulk) | `scp/apikeys.php: mass_process`, `scp/lists.php: mass_process`, `scp/logs.php: mass_process` | id list + action | — | — | admin | low | Confirms a recurring **bulk/mass-action pattern** used across many SCP controllers, not unique to tickets/tasks — worth a general design note (see §9) |

**Not new (confirmed as alternate interfaces to existing documented capabilities, no new facts):** ticket/task lock acquire/renew/release; ticket/task lookup/autocomplete by number/email; merge preview/confirm two-step flow; canned-response preview; thread collaborator add/update/view; thread-action triggers; per-field inline edit/view on a ticket; task add/edit/assign/transfer/claim/close/reopen/delete; advanced-search dialog building blocks (`addField`/`addCondition*`); quick-add dialogs for Dept/Team/Role/Staff (`ajax.admin.php`); SCP/client JS bootstrap config (`ajax.config.php`).

---

### 8.1.3 Database Schema Cross-Check

**Important caveat:** this codebase snapshot does **not** contain a single baseline schema dump (no `CREATE TABLE ost_ticket ...` file exists anywhere in the tree). Schema exists only as ~150+ incremental version-to-version patch files under `include/upgrader/streams/core/*.sql`, which together are not a practical source for reconstructing full current-state DDL by inspection. **This cross-check therefore uses the ORM `static $meta` definitions on each model class as the source of truth for structure**, since osTicket's own ORM (`class.orm.php`) relies on exactly these definitions to generate queries, and they are internally consistent with the class-method behavior already documented. This is a reasonable substitute but is not a literal DDL read — column types/lengths/defaults are not verified.

Findings relevant to API design:

- **Ticket** (`ost_ticket`, PK `ticket_id`): confirms two distinct identifiers exist — internal `ticket_id` (PK, used in all joins) vs. the public `number` field (used in URLs/emails); an API must pick one as its canonical resource identifier and document the mapping (recommend `number` externally, matching existing user-facing convention). `status` is a **foreign key to a `TicketStatus` lookup table**, not a fixed enum column — status values are data, not compiled constants (open question: whether the status set is effectively fixed by application logic or admin-extensible; not resolved here, flagged for a future pass). `dept_id`, `staff_id`, `team_id`, `sla_id`, and even `user_id` are all **nullable** at the join-definition level — an API schema should treat ticket owner/assignee/department/SLA as optional/nullable fields, not assume-present.
- **Thread polymorphism confirmed at the schema level**: `ost_thread.object_type` is a single-character discriminator (`'T'` = Ticket, `'A'` = Task) paired with `object_id`. `DynamicFormEntry` uses the **same polymorphic pattern** (`object_type`/`object_id`) to attach custom-field answers to *any* entity (Ticket uses `'T'`; Task, User, Organization each have their own discriminator, not individually re-verified here). API design implication: a generic "custom fields" or "thread" sub-resource pattern is schema-justified, not just a convention this document is inventing.
- **Two representations of custom-field data**: normalized answers live in `DynamicFormEntry`/`FORM_ANSWER_TABLE` (one row per field per entry); a **denormalized "cdata" table** (e.g. `ost_ticket__cdata`) also exists per entity type, populated for fast search/sort/listing. A wrapper reading "all custom field values for a ticket" should go through the normalized entries (authoritative) and not assume the cdata table is a complete or independently-writable source.
- **User model**: a `User` has many `UserEmailModel` rows (multiple email addresses per user is a real, modeled capability, with one marked `default_email`) plus an optional 1:1 reverse join to a separate **`ClientAccount`** table (this document previously called this "UserAccount" informally — the actual model/table is `ClientAccount`; **correction**, terminology only, no behavior change) for login credentials. `User.org_id` is nullable — organization membership is optional, confirming §3.3/3.4.
- **Team membership is a real join table** (`TeamMember`, many-to-many between `Staff` and `Team`), with a separate direct `lead_id` FK on `Team` for the team lead — distinct from generic membership.
- **Departments are hierarchical**: `Dept.pid` is a nullable self-referential FK (`parent` join to `Dept.id`). **This was not mentioned in the original inventory (§3.7) and is a material addition** — a Department API resource should support a parent/child tree, not a flat list. Dept also carries direct `sla_id` and `email_id` FKs (default SLA and default outgoing mailbox per department).
- **Role permissions are stored as a single JSON blob** on the `Role` record (`$this->permissions = $permissions->toJson()`), not as individual rows or a bitmask integer. An API exposing "role permissions" should model it as a flat key→boolean map, matching the internal representation, rather than inventing a relational permissions-per-role table that doesn't exist.
- **Per-department staff access is a distinct Role assignment, not a permission override**: `StaffDeptAccess` (table `ost_staff_dept_access`, composite PK `staff_id`+`dept_id`) joins a staff member, a department, **and its own `role_id`** — meaning an agent can hold a *different Role entirely* when acting in a secondary department, plus a per-department alerts on/off flag. **Correction to §5/§6**: the original document described this loosely as "per-dept permission overrides"; verification shows it is specifically "assign an additional department with its own (possibly different) Role," which is a cleaner and more API-friendly model than a bitmask override would be.
- **Collaborators are a Thread↔User join row with bit flags** (`ost_thread_collaborator`: `FLAG_ACTIVE`, `FLAG_CC`), not a separate collaborator identity — confirms §3.1/3.2's treatment of collaborators as a ticket sub-resource is structurally correct.
- **Custom Lists** (`ost_list`, `ost_list_items`) are confirmed as real, separate tables (see 8.1.1) — supports treating Custom Lists as a first-class new domain.

No outright contradictions of previously documented relationships were found; the additions above are refinements/corrections layered onto the existing §4 relationship graph, not replacements.

---

### 8.1.4 Permission Enumeration

Exhaustive `grep -rn "const PERM_"` across `include/*.php` (staff/client subfolders and the sole present plugin directory, `include/plugins/fastapi_integration/`, produced no additional matches — that directory is the user's own project scaffolding, not core osTicket, and was not analyzed further per the instruction to focus only on the osTicket codebase) plus every `RolePermission::register(...)` call site. Full permission taxonomy found in this codebase:

| Group (as registered) | Permission key | Constant | Defining class | Scope |
|---|---|---|---|---|
| Tickets | `ticket.create` | `Ticket::PERM_CREATE` | `class.ticket.php` | global (role), department-relevant via visibility |
| Tickets | `ticket.edit` | `Ticket::PERM_EDIT` | `class.ticket.php` | role + dept |
| Tickets | `ticket.assign` | `Ticket::PERM_ASSIGN` | `class.ticket.php` | role + dept |
| Tickets | `ticket.release` | `Ticket::PERM_RELEASE` | `class.ticket.php` | role + dept |
| Tickets | `ticket.transfer` | `Ticket::PERM_TRANSFER` | `class.ticket.php` | role + dept |
| Tickets | `ticket.refer` | `Ticket::PERM_REFER` | `class.ticket.php` | role + dept |
| Tickets | `ticket.merge` | `Ticket::PERM_MERGE` | `class.ticket.php` | role + dept |
| Tickets | `ticket.link` | `Ticket::PERM_LINK` | `class.ticket.php` | role + dept |
| Tickets | `ticket.reply` | `Ticket::PERM_REPLY` | `class.ticket.php` | role + dept |
| Tickets | `ticket.markanswered` | `Ticket::PERM_MARKANSWERED` | `class.ticket.php` | role + dept |
| Tickets | `ticket.close` | `Ticket::PERM_CLOSE` | `class.ticket.php` | role + dept |
| Tickets | `ticket.delete` | `Ticket::PERM_DELETE` | `class.ticket.php` | role + dept |
| Tickets | `thread.edit` | `ThreadEntry::PERM_EDIT` | `class.thread.php` | role + dept |
| Tasks | `task.create` | `TaskModel::PERM_CREATE` | `class.task.php` | role + dept |
| Tasks | `task.edit` | `TaskModel::PERM_EDIT` | `class.task.php` | role + dept |
| Tasks | `task.assign` | `TaskModel::PERM_ASSIGN` | `class.task.php` | role + dept |
| Tasks | `task.transfer` | `TaskModel::PERM_TRANSFER` | `class.task.php` | role + dept |
| Tasks | `task.reply` | `TaskModel::PERM_REPLY` | `class.task.php` | role + dept |
| Tasks | `task.close` | `TaskModel::PERM_CLOSE` | `class.task.php` | role + dept |
| Tasks | `task.delete` | `TaskModel::PERM_DELETE` | `class.task.php` | role + dept |
| Users | `user.create` | `UserModel::PERM_CREATE` | `class.user.php` | global |
| Users | `user.edit` | `UserModel::PERM_EDIT` | `class.user.php` | global |
| Users | `user.delete` | `UserModel::PERM_DELETE` | `class.user.php` | global |
| Users | `user.manage` | `UserModel::PERM_MANAGE` | `class.user.php` | global |
| Users | `user.dir` | `UserModel::PERM_DIRECTORY` | `class.user.php` | global (directory visibility) |
| Organizations | `org.create` | `Organization::PERM_CREATE` | `class.organization.php` | global |
| Organizations | `org.edit` | `Organization::PERM_EDIT` | `class.organization.php` | global |
| Organizations | `org.delete` | `Organization::PERM_DELETE` | `class.organization.php` | global |
| Knowledgebase | `canned.manage` | `Canned::PERM_MANAGE` | `class.canned.php` | global |
| Knowledgebase | `faq.manage` | `Faq::PERM_MANAGE` | `class.faq.php` | global |
| Miscellaneous | `visibility.departments` | `Dept::PERM_DEPT` | `class.dept.php` | governs cross-department visibility itself |
| Miscellaneous | `emails.banlist` | `Email::PERM_BANLIST` | `class.email.php` | global |
| Miscellaneous | `visibility.agents` | `Staff::PERM_STAFF` | `class.staff.php` | governs staff-directory visibility |
| Miscellaneous | `stats.agents` | `ReportModel::PERM_AGENTS` | `class.report.php` | global |
| Miscellaneous | `search.all` | `SearchBackend::PERM_EVERYTHING` | `class.search.php` | global (cross-department search override) |

**Totals:** 32 named permissions across 8 registering classes, grouped into 6 role-editor groups (Tickets, Tasks, Users, Organizations, Knowledgebase, Miscellaneous). No permissions were found registered for: Custom Lists, Dynamic Forms/field definitions, Teams, SLA, Business Hours, Priorities, Filters, Plugins, Config/Settings, or Roles themselves — these areas are gated by broader/implicit admin checks (e.g. simple staff-is-admin flags) rather than a named `PERM_*` in the Role permission-editor system. **This is a gap worth flagging for API design** (§9), not something this document resolves.

**Scope model (confirmed, refines §5):**
- Every permission above is granted **per Role**, and a Role is a flat JSON key→boolean map (8.1.3).
- **Department scoping** does not modify individual permission bits; instead a Staff member can be granted access to *additional* departments beyond their primary one, each such grant (`StaffDeptAccess`) carrying its own Role reference. So "department-scoped" in practice means "this Role, which may differ from the agent's primary Role, applies while the agent is acting in this specific department" — not a per-permission dept override.
- `visibility.departments` and `visibility.agents` are themselves permissions that broaden an otherwise dept-scoped agent's visibility across all departments/all agents respectively — i.e., they are escape hatches from the default department-scoping behavior, not separate features.
- **No plugin in this codebase contributes permissions** — the only permission sources are the 8 core classes above. (The `fastapi_integration` plugin directory belongs to the user's own project and was excluded per scope, but note for completeness: if it or any future plugin calls `RolePermission::register(...)`, it would extend this table identically to how core classes do — the mechanism is generic and open to any code that calls it.)
- **Client/User side has no equivalent permission system** — confirmed again here; Users are self-scoped by ownership, not by a Role/permission-bit model. Any API authorization design for client-facing endpoints should not attempt to reuse the Staff Role/permission taxonomy above.

---


---

## 9. Inventory Readiness Assessment

**Remaining unresolved areas** (carried over from §8, unaffected by this pass): Company vs. Organization distinction; `class.note.php` vs. Thread's built-in Note type; staff/user auth backend plurality beyond the local backend; exact filter match-type/action-type taxonomy; `pages/` CMS and `frontend/` content. All are narrow, self-contained questions — each is answerable by opening 1–3 specific files, not further broad exploration.

**Materiality to FastAPI design:**
- **Not material / safe to defer:** Company-vs-Organization (Organization is already confirmed as the real, actively-used entity via its ORM joins from `User`; Company appears vestigial), `class.note.php` disambiguation (the note-taking *capability* is already documented at the Ticket/Task/User/Org level regardless of which internal class backs it), auth backend plurality (only the local backend needs to be supported for a first API version; pluggability is an extension point, not a blocker), `pages/`/`frontend/` (already correctly rated low/medium suitability and peripheral to a ticketing API).
- **Somewhat material, low urgency:** filter match/action taxonomy — only matters once the wrapper actually decides to expose Filters as a resource (already rated medium suitability, not a first-wave domain).
- **Nothing found in this pass or remaining open materially threatens the core, high-suitability API surface** (tickets, threads, users, organizations, staff, teams, departments, help topics, KB/FAQ/canned responses, tasks, SLA, banlist). The two additions with real design weight — **Custom Lists as a new domain** and **department hierarchy** (`Dept.pid`) — are both now documented (§8.1.1, §8.1.3) and are additive, not disruptive: they extend the existing Department and Dynamic-Forms sections rather than invalidating anything already speced.
- One genuine **gap worth carrying into the API design phase**: several admin-facing domains (Custom Lists, Dynamic Form/field definitions, Teams, SLA, Business Hours, Filters, Plugins, Config, Roles themselves) have **no dedicated `PERM_*` permission** in osTicket's own Role system (§8.1.4) — they're gated by coarser "is this staff an admin" checks not enumerated here. A FastAPI authorization layer covering these domains will need to either introduce its own finer-grained scopes or accept the same coarse admin/non-admin split osTicket itself uses. This is a design decision to make consciously, not a fact this document can resolve further by reading more source.

**Recommendation:** the capability inventory (original document + this follow-up) is **sufficient to begin FastAPI API architecture/design** for the high- and medium-suitability domains identified — tickets, threads/messages, users, organizations, staff, teams, departments, help topics, dynamic-form-driven custom fields (including the newly-documented Custom Lists), knowledgebase/FAQ/canned responses, tasks, SLA, and banlist. The remaining open items (§8, items 2–5, 7 general case, 8) are narrow enough to resolve opportunistically while designing the specific endpoints that touch them, rather than requiring another dedicated research pass first. The one item worth resolving *before* finalizing the authorization model specifically is the permission-coverage gap noted above, since it affects a cross-cutting design decision rather than a single domain.

*End of document.*