# Known limits

Things fast-ticket does not do yet, or does only partly, that someone relying on it should know.
This is a **live list, not a changelog**: when a limit is fixed its entry is *deleted* (git history keeps
it) and the commit that fixes it says so. Rules for keeping it honest are in `AGENTS.md` → "Known limits".

Last reviewed: 2026-09-30 (after S3, first half: ticket/task/search scoping).

Each entry: what is true today · what it means for you · what will resolve it (a phase from the plan, or
"by design" if it is a deliberate trade-off).

## Authorization (security overhaul S2–S5)

| Limit | Impact | Resolved by |
|---|---|---|
| **Reference data and canned responses are not yet scoped for non-admins.** Tickets, tasks, their threads and collaborators, and search are narrowed to what osTicket shows the agent. Departments, teams, the agent directory, help topics, SLAs and priorities are still `require_admin()` although osTicket shows agents scoped versions of them, and canned responses (department-specific in osTicket) are shown in full to anyone holding `canned.manage`. | A non-admin agent cannot fetch the topics, priorities, departments or teams to build a "new ticket" form; an agent with `canned.manage` sees other departments' canned responses. Deliberately kept strict (never looser than before) until they are scoped. | S3, second half |
| **Organization reads require `user.dir`**, which is stricter than osTicket (it does not gate organization pages by URL). | An agent without the user-directory permission cannot list organizations here though osTicket's page would open for them. | By design (nearest real gate) |
| **The API checks a role permission as "any of the agent's roles" (`hasPerm($perm, false)`)**; the exact per-department answer is enforced only by the ticket and task bridge handlers. | An agent whose *other* department's role grants a permission passes the API gate for an action in a department where they lack it; the bridge then refuses ticket and task commands, but not the other kinds. | S4 |
| **Idempotency keys are global; non-admins cannot see their own commands** (`GET /commands` is admin-only), so they cannot poll a write's result; **command payloads are stored in plain text** (a `staff.create` password sits in the inbox and is readable by admins and by anyone with database access). | One agent reusing another's key gets that agent's command back; payload secrets are exposed. | S4 |
| **Only ticket and task bridge handlers check the requesting agent's permission in PHP.** Users, organizations, KB, staff, departments, teams, topics, SLAs, priorities, forms and banlist run whatever the API queues. | The Python layer is the only guard for those; a bug there, or anyone able to insert an inbox row, acts as any agent. | S4 |
| **The inbox is unsigned.** A row carries `requested_by_staff_id` and nothing proves the API wrote it. | Anyone with write access to the inbox table can run commands as any agent, including an admin. | S4 (optional HMAC) |
| **WebSocket broadcasts go to every connected client unfiltered**, and the token is only checked when the connection opens. | Any connected agent sees every event; a disabled agent's open connection keeps receiving until it drops. | S5 |
| **A password change does not revoke tokens already issued** (they live until they expire). | Up to 30 minutes of access after a password reset. (A disabled account, a role change and a demotion *do* apply on the next request.) | By design for now (no revocation list) |

## Login and abuse protection

| Limit | Impact | Resolved by |
|---|---|---|
| **Accounts with two-factor authentication — or that osTicket requires to enrol — cannot use the API.** They are refused with `two_factor_required`. | Use a non-2FA agent account for API access. | By design until a second-step login endpoint exists |
| **The login lockout is in memory and per process.** The default rate limit is per process too unless `RATE_LIMIT_STORAGE_URI` points at Redis. | With N API workers the effective allowance is N times higher, and a restart clears lockouts. | A shared store (Redis) if you run several workers |
| **No refresh tokens.** | Clients log in again every `ACCESS_TOKEN_EXPIRE_MINUTES`. | By design (cost/benefit) |
| **The 2FA and forced-password-change login refusals are verified by hand, not by an automated test.** | No e2e path can put an agent into those states. | — (a PHP test runner would fix it) |

## Events, latency and caching (Phases 3–4)

| Limit | Impact | Resolved by |
|---|---|---|
| **Each outbox event is claimed by one API process.** | With more than one API worker, only the clients connected to the claiming worker are notified. | Phase 3 (cursor-based consumption) |
| **No Python event handlers are registered and nothing is cached.** | Every authenticated request runs five small queries to load the agent's `AccessContext` (staff row, extra-department roles, role permissions, teams, visible departments), on top of the request's own. | Phase 4 |
| **Without PHP's `pcntl` extension the worker exits after each command** and the supervisor restarts it. | Commands still run, but slower and subject to restart backoff; `make doctor` warns. | Install `pcntl` |
| **osTicket's cron fallback runs about once a minute.** | If no worker runs, a command can wait up to ~60 s. | Run the worker |

## Compatibility and deployment

| Limit | Impact | Resolved by |
|---|---|---|
| **Only osTicket 1.18.1 and MariaDB 10.11 have been tested.** `schema.php` is written for MySQL 5.6+ and the queries for the 1.18 schema, but nothing else has run. | `make doctor` warns on other versions and names any read query that breaks; the bridge handlers may still differ. | Running the e2e suite on each target |
| **Installing the plugin zip through osTicket's admin panel is untested.** Only the CLI installer (used by the Docker image) has been run. | Expect to check the plugin appears, enables and creates its tables. | Trying it on a real instance |
| **The compose stack uses one all-privileges database user** for osTicket, the plugin and the API. | `make doctor` warns; production should give the API the least-privilege grants it prints. | Splitting users in `docker-compose.yml` |
| **Parts of the access and visibility rules are proven by unit tests only, not against a real osTicket.** The API cannot set an agent's *global* permissions or `assigned_only`, cannot create referrals, and the seed data has no sub-departments or managed departments. Live tests cover roles, department and team membership, open/closed assignment, osTicket's automatic referral when a ticket closes, and the closed-task rule; the SQL-semantics tests (`tests/unit/test_visibility_sql.py`) cover the rest of the branches. | Sub-department inheritance, managed departments, extra-department access, a non-admin holding `user.dir`/`faq.manage`, limited-access agents, and direct referrals to an agent, department or team (merged-child threads included) rest on osTicket's source as read, not on a run against it. | A test instance with those features set up (by hand or through a fixture) |
