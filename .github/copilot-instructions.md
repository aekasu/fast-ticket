# Copilot instructions — fast-ticket

The full, canonical agent guide is [`AGENTS.md`](../AGENTS.md) at the repo root. Task playbooks live in [`.claude/skills/`](../.claude/skills/) (Copilot agent mode loads them as skills too). This file repeats only the rules that matter most for completions and code review.

## Architecture in one paragraph

FastAPI (Python 3.12, uv) in front of osTicket. **Reads** are `SELECT`-only SQL in `app/integration/osticket/read_repository.py`. **Writes** never touch osTicket tables: routes call `dispatch_command(...)`, which queues a row in `ost_integration_inbox`; the PHP bridge plugin (`bridge/plugin.php` + `bridge/handlers/*.php`) executes it via osTicket's own models (from `bridge/worker.php`, or osTicket's cron as fallback) and writes an event to `ost_integration_outbox`; `app/workers/events.py` relays events over WebSocket.

## Review / generation rules

- Every route has `current_user: dict = require_perm("<osTicket key>")`, `require_admin()` or `require_staff()` (from `app/core/security.py`), or an explicit `# no permission required: <why>` comment. Keys must be real osTicket permissions (`app/core/permissions.py`); there is no admin bypass; access is read from osTicket on every request, not from the token.
- Request bodies are Pydantic models with explicit, constrained fields. No `Any`, no raw `dict` bodies.
- Write routes: `status_code=202`, `response_model=CommandResponse`, accept `Idempotency-Key` header and pass it to `dispatch_command`.
- No `INSERT`/`UPDATE`/`DELETE` against `ost_*` tables other than `ost_integration_inbox` / `ost_integration_outbox`.
- osTicket-schema SQL only in `read_repository.py`; no business logic there.
- A new `command_type` needs: Python `dispatch_command` call, and one entry in `bridge/registry.php` (handler + event).
- PHP handlers throw on any failure; they never write raw SQL to osTicket tables.
- Routers don't import other routers. Path params are internal int PKs (`ticket_id`), not display numbers.
- Every endpoint ships with tests: happy path, 401, 403, 422, and idempotency for writes (`tests/integration/test_<domain>.py`).
- No hardcoded secrets; new env vars go in `.env.example`.
- Never commit `fitsm.zip` contents, `.env`, or `__pycache__/`.
- Commit/PR titles: `<type>(<scope>): <summary>` (`feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `security`).
