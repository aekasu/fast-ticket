---
name: add-endpoint
description: End-to-end recipe for adding or extending a fast-ticket API endpoint (read or write) — capability lookup, Pydantic schemas, service, router with require_perm, bridge command, and tests. Use whenever implementing a new route or osTicket operation in app/modules/.
---

# Add an endpoint

Follow in order. Don't skip the capability lookup or the tests — they are part of the change, not follow-ups.

## 1. Look it up first

- Find the operation in `notes/OSTICKET_CAPABILITIES.md` (osTicket PHP method, suitability, required `PERM_*`, relationships).
- Check `notes/INTEGRATION_CAPABILITIES.md` §3 (commands) / §4 (events) / §5 (read-throughs) for the agreed name and payload.
- Wave 3 / "skip" items (queue builder, sequences, sessions, draft autosave, raw config writes) are **not** exposed. Stop and tell the user.
- Look at the closest existing module (`app/modules/tasks/` is the cleanest reference) and copy its shape.

Decide: **read** (SQL in `read_repository.py`) or **write** (command through the bridge)?

## 2. Schemas — `app/modules/<domain>/models.py`

- Request bodies: `BaseModel` with explicit, constrained fields (`Field(min_length=1, max_length=255)`, `ge=1`, `Literal[...]`). No `Any`, no bare `dict`.
- Read responses: list wrapper with a top-level resource key (`{"tasks": [...]}`, plus pagination fields if the neighbours have them) and a detail model.
- Writes return the shared `CommandResponse` from `app.modules.tickets.models`.

## 3a. Read path

1. Add a `SELECT`-only async function to `app/integration/osticket/read_repository.py` returning `list[dict]` / `dict | None`. Parameterized queries only; no business logic.
   **Check every table and column against the real schema first** (`docker compose exec db sh -c 'mariadb … -e "DESCRIBE ost_x"'`) — most of the original queries used columns that don't exist and 500ed. Known osTicket 1.18 facts: booleans like `isactive` / `isenabled` / `deletable` are usually **bits in `flags`**; a ticket's subject, a task's title, and an organization's/user's phone/notes live in the `ost_*__cdata` mirrors; threads are `ost_thread` keyed by `object_type` (`T` ticket, `A` task) + `object_id`; the ban list is the `SYSTEM BAN LIST` filter's rows in `ost_filter_rule`; tasks have no status table (open = `flags & 1`).
   **Make it NULL-safe**: osTicket stores `NULL` text and even `0000-00-00` dates, and response models are `str`/`datetime`, so wrap with `COALESCE(x, '')` and coerce "featured" visibility (2) with `> 0`. An unhandled NULL is a 500 for that row only, which the unit tests (mocked repo) can never catch.
2. In `service.py`, call it via `from app.integration.osticket import read_repository as repo` (tests patch `app.modules.<domain>.service.repo.<fn>`) and map rows with a `_row_to_*` helper.

## 3b. Write path

1. In `service.py`:
   ```python
   cmd = await dispatch_command(
       session=session,
       command_type="<domain>.<verb>",        # e.g. "task.assign"
       aggregate_type="<domain>",
       aggregate_id=str(<id>),                 # "" for creates
       payload=body.model_dump(exclude_none=True),  # plus path ids the handler needs
       requested_by_staff_id=staff_id,
       idempotency_key=idempotency_key,
   )
   return _cmd_resp(cmd, idempotency_key)
   ```
2. Implement the PHP side with the **`add-bridge-command`** skill (handler method + one `bridge/registry.php` entry). Payload keys must match exactly what the handler reads.
3. If Python should react to the resulting event, add a handler in `register_event_handlers(registry)` and make sure `_ensure_handlers_registered()` in `app/workers/events.py` imports it.

## 4. Router — `app/modules/<domain>/router.py`

```python
@router.post("/{task_id}/assign", response_model=CommandResponse, status_code=202, summary="...")
async def assign_task(
    task_id: int,
    body: TaskAssign,
    current_user: dict = require_perm("task.assign"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.assign_task(session, task_id, body, current_user["staff_id"], idempotency_key)
```

- Permission: `require_perm("<perm>")`, or `require_admin()` where osTicket has no finer `PERM_*`. Update the permission map in the router's module docstring.
- Path param is the internal int PK, never the display `number`.
- Router stays thin. No imports from other routers.
- New module → mount it in `app/api/v1/router.py`.

## 5. Tests

Use the **`endpoint-tests`** skill. Minimum: happy path, 401, 403, 422, and idempotency for writes. Those tests mock the repository and the bridge, so they prove routing and validation only — also extend `tests/e2e/test_bridge_domains.py` so the read query and the bridge handler are exercised against real osTicket (`test_every_read_endpoint_answers_200` picks up new GET routes automatically).

## 6. Verify and commit

```bash
make test           # local venv
make test-docker    # the image's pinned deps (uv.lock) — the versions that actually run
make test-e2e       # live stack; required when you touched bridge/ or read_repository.py
                    # (needs OSTICKET_ADMIN_USERNAME/PASSWORD exported; a human sources .env, agents must not read it)
```
Commit atomically: `feat(<domain>): <what>` (bridge changes can be a separate `feat(bridge): ...` commit).

## Checklist

- [ ] Capability docs consulted; not a Wave 3 item
- [ ] Typed, constrained request model; no `Any`
- [ ] `require_perm` / `require_admin` on every route; docstring permission map updated
- [ ] Writes: 202 + `CommandResponse`, `Idempotency-Key` passed through
- [ ] Handler + `bridge/registry.php` entry exist for every new command type
- [ ] osTicket SQL only in `read_repository.py`, SELECT-only
- [ ] Tests for 200/202, 401, 403, 422, idempotency; `make test` and `make test-docker` green
- [ ] Read query verified against the real schema and NULL-safe; write verified by an e2e test against the live bridge
