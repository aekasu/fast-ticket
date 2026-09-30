# Known limits

Things fast-ticket does not do yet, or does only partly, that someone relying on it should know.
This is a **live list, not a changelog**: when a limit is fixed its entry is *deleted* (git history keeps
it) and the commit that fixes it says so. Rules for keeping it honest are in `AGENTS.md` → "Known limits".

Last reviewed: 2026-09-30 (after S5: WebSocket audience, per-process event cursors).

Each entry: what is true today · what it means for you · what will resolve it (a phase from the plan, or
"by design" if it is a deliberate trade-off).

## Authorization (security overhaul S2–S5)

| Limit | Impact | Resolved by |
|---|---|---|
| **Teams, the agent directory, SLA plans, forms, and the *detail* of departments, topics and priorities are admin-only.** The lists an agent needs to fill in a ticket form (departments, help topics, priorities) and the canned-response picker are open, scoped as osTicket scopes them. | A non-admin agent cannot list teams or agents to build an *assign* form, or read SLAs and forms. Kept strict (never looser than before) until each osTicket rule (the agent directory, the assignable agents and teams) has been read and ported. | Next reference-data pass, as needed |
| **Help topics are sorted alphabetically by Python's case-folding for agents** (osTicket's default `help_topic_sort_mode`). | An instance set to manual order, or using a non-English collation, lists them in a different order than osTicket's own form. | Read the instance's sort mode |
| **Organization reads require `user.dir`**, which is stricter than osTicket (it does not gate organization pages by URL). | An agent without the user-directory permission cannot list organizations here though osTicket's page would open for them. | By design (nearest real gate) |
| **Role permissions are checked as "any of the agent's roles"** (`hasPerm($perm, false)`) by the API *and* by the bridge's registry check; the exact per-department answer (`getRole($dept)`) is applied only by the ticket and task handlers. | An agent whose role in *another* department grants `canned.manage` can manage canned responses of any department; the same any-role reading applies to `ticket.create`. Ticket and task commands are exact. | Later (per-department checks in the canned handlers) |
| **A command's secrets stay in the inbox until it finishes.** A `staff.create` password is in the payload while the command is pending or held by a run that was killed; the bridge overwrites it once the command is done or failed, and the API masks it whenever it shows a payload. | Anyone with database access can read a password during that window. | Encrypting sensitive payload fields at rest (with the HMAC work below) |
| **The same `Idempotency-Key` with a different request body silently returns the first command** (keys are per agent, but the body is not compared). | A client that reuses a key by mistake is told its second, different request succeeded when it was ignored. | Later (Stripe-style mismatch error) |
| **The bridge's permission check is proven live by hand, not by an automated test.** Forged inbox rows from an agent with no permissions were all refused (admin, `user.create`, `ticket.create`, `faq.manage`, `canned.manage`), and a static test checks every command's permission against the API's route guard, but no automated test can insert an inbox row. | A regression in the PHP check would show only in that manual run. | A PHP test runner, or a test-only endpoint |
| **The inbox is unsigned.** A row carries `requested_by_staff_id` and nothing proves the API wrote it. | Anyone with write access to the inbox table can run commands as any agent, including an admin. | Later (optional HMAC) |
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
| **WebSocket delivery is best-effort and not replayed.** Each API process starts at the end of the outbox and pushes what arrives after; an event written while a process was down, or before a client connected, is not delivered, and there are no event ids to resume from. | A client that reconnects must refetch what it cares about. | By design (the REST reads are the source of truth) |
| **Ticket and task events follow the agent's *list* visibility**, and deletions go to administrators only (the row is gone, so no one "can see" it). | An agent who can open a closed ticket only through a referral (page yes, list no) is not told about its changes; an agent is not told when a ticket they could see is deleted. | By design (narrower is safer) |
| **Each ticket or task event costs one query per connected agent** (`ticket_is_visible`). | Many connected agents and a busy helpdesk mean many small queries per event. | Phase 4 (cache) or a set-based check |
| **Open connections notice a disabled account or an expired token within `WS_RECHECK_SECONDS` (15 s)**, not instantly. | A disabled agent can keep receiving for up to that long. | Tunable; an event-driven close on `staff.*` events would make it immediate |
| **WebSocket connection attempts are not rate limited** (the HTTP middleware does not cover them); an agent is capped at `WS_MAX_CONNECTIONS_PER_STAFF`. | An unauthenticated client can open connections at will, each costing a token check. | Later (a connection-rate limit or a proxy rule) |
| **Every agent login sends administrators a `staff.updated` event** (osTicket's last-login update fires the model signal). | Noise on the admin stream. | Later (drop signal events that change only `lastlogin`) |
| **The outbox's `status`, `retry_count` and `locked_at` columns are unused.** The API reads the outbox by cursor and never claims or retries a row. | Dead schema in `bridge/schema.php`. | Later (drop them in a schema upgrade) |
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
