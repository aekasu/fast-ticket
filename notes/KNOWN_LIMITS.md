# Known limits

Things fast-ticket does not do yet, or does only partly, that someone relying on it should know.
This is a **live list, not a changelog**: when a limit is fixed its entry is *deleted* (git history keeps
it) and the commit that fixes it says so. Rules for keeping it honest are in `AGENTS.md` → "Known limits".

Last reviewed: 2026-09-30 (after security steps S0–S1).

Each entry: what is true today · what it means for you · what will resolve it (a phase from the plan, or
"by design" if it is a deliberate trade-off).

## Authorization (security overhaul S2–S5)

| Limit | Impact | Resolved by |
|---|---|---|
| **Reads are not scoped by department, team or assignment.** Any agent holding the permission a read route uses sees *every* ticket, task, user, organization and thread — osTicket itself limits an agent to their departments, their assignments and referrals, and to assigned-only for limited agents. | An agent restricted to one department can list another department's tickets through the API. Writes are safer: ticket and task handlers ask osTicket. | S3 |
| **Read routes are guarded by write permissions as stand-ins** (`GET /tickets` needs `ticket.create`, `GET /staff` needs `staff.create`), and about a third of the permission keys the API checks (`kb.manage`, `banlist.manage`, `topic.manage`, `sla.manage`, `dept.*`, `team.*`, `staff.*`, `commands.read`, …) are not osTicket permissions at all. | Those routes are effectively admin-only (fails closed), and agents holding the real permissions (`faq.manage`, `emails.banlist`, `user.dir`, …) cannot use them. | S2 |
| **Permissions and the admin flag are read at login and carried in the token** (up to `ACCESS_TOKEN_EXPIRE_MINUTES`, default 30), and only from the agent's *primary* role — osTicket roles are per department. | A demotion or role change takes effect at the next login; per-department roles are ignored. (Disabling or deleting an agent *is* enforced on every request.) | S2 |
| **`GET /search` accepts any valid token** with no permission check and no scoping. | Any signed-in agent can search every ticket, user and organization. | S2 / S3 |
| **Idempotency keys are global, `GET /commands` is not limited to your own commands, and command payloads are stored in plain text** (a `staff.create` password sits in the inbox and is readable with `commands.read`). | One agent reusing another's key gets that agent's command back; payload secrets are exposed to anyone who can read commands or the database. | S4 |
| **Only ticket and task bridge handlers check the requesting agent's permission in PHP.** Users, organizations, KB, staff, departments, teams, topics, SLAs, priorities, forms and banlist run whatever the API queues. | The Python layer is the only guard for those; a bug there, or anyone able to insert an inbox row, acts as any agent. | S4 |
| **The inbox is unsigned.** A row carries `requested_by_staff_id` and nothing proves the API wrote it. | Anyone with write access to the inbox table can run commands as any agent, including an admin. | S4 (optional HMAC) |
| **WebSocket broadcasts go to every connected client unfiltered**, and the token is only checked when the connection opens. | Any connected agent sees every event; a disabled agent's open connection keeps receiving until it drops. | S5 |
| **A password change does not revoke tokens already issued** (they live until they expire). | Up to 30 minutes of access after a password reset. | By design for now (no refresh/revocation list); revisit with S2 |

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
| **No Python event handlers are registered and nothing is cached.** | Every request queries the database, including the per-request "is this agent active" check. | Phase 4 |
| **Without PHP's `pcntl` extension the worker exits after each command** and the supervisor restarts it. | Commands still run, but slower and subject to restart backoff; `make doctor` warns. | Install `pcntl` |
| **osTicket's cron fallback runs about once a minute.** | If no worker runs, a command can wait up to ~60 s. | Run the worker |

## Compatibility and deployment

| Limit | Impact | Resolved by |
|---|---|---|
| **Only osTicket 1.18.1 and MariaDB 10.11 have been tested.** `schema.php` is written for MySQL 5.6+ and the queries for the 1.18 schema, but nothing else has run. | `make doctor` warns on other versions and names any read query that breaks; the bridge handlers may still differ. | Running the e2e suite on each target |
| **Installing the plugin zip through osTicket's admin panel is untested.** Only the CLI installer (used by the Docker image) has been run. | Expect to check the plugin appears, enables and creates its tables. | Trying it on a real instance |
| **The compose stack uses one all-privileges database user** for osTicket, the plugin and the API. | `make doctor` warns; production should give the API the least-privilege grants it prints. | Splitting users in `docker-compose.yml` |
