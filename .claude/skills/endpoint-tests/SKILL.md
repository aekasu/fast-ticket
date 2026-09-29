---
name: endpoint-tests
description: Write the mandatory pytest matrix for a fast-ticket endpoint — happy path, 401, 403, 422, and idempotency — using ASGITransport, in-memory SQLite, mocked read_repository, and IntegrationInbox assertions. Use when adding or changing any route, or when asked to add test coverage.
---

# Endpoint tests

Tests live in `tests/integration/test_<domain>.py` (HTTP-level) and `tests/unit/` (pure functions, no I/O). Mirror the structure of `tests/integration/test_tasks.py`.

## Required matrix per endpoint

| Case | Expect |
|---|---|
| Happy path | 200 (read) / 202 (write) with correct shape |
| No `Authorization` header | 401 |
| Valid token, missing permission | 403 (`"Permission required: <perm>"`) |
| Admin token without the perm | passes (admins bypass `require_perm`) — at least once per module |
| Invalid body / query (missing field, too long, `ge` violated) | 422 |
| Write + same `Idempotency-Key` twice | same `command_id`, exactly **one** inbox row |
| Read of missing resource | 404 |

Add explicit security cases where relevant: malformed token, expired token, `require_admin` routes rejecting non-admins.

## Building blocks

Auth — either the real-JWT helper used in module tests:
```python
def _token(permissions, is_admin=False):
    from datetime import timedelta
    from app.core.auth import create_access_token
    return create_access_token(
        {"staff_id": 5, "username": "agent", "name": "Test Agent",
         "email": "agent@test.local", "is_admin": is_admin, "permissions": permissions},
        expires_delta=timedelta(minutes=5),
    )

def _auth(perms=None, is_admin=False):
    return {"Authorization": f"Bearer {_token(perms or [], is_admin)}"}
```
…or the `auth_user(permissions=[...])` / `admin_user` fixtures plus the `client` fixture from `tests/conftest.py`. Use the JWT helper when the test must exercise real token decoding (401s, expiry).

Reads — never hit a real osTicket DB; patch the repo **as imported by the service**:
```python
with patch("app.modules.tasks.service.repo.list_tasks", new=AsyncMock(return_value=[MOCK_TASK])):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/tasks/", headers=_auth(["task.create"]))
assert resp.status_code == 200
assert resp.json()["tasks"][0]["task_id"] == 1
```

Writes — assert the queued command, not a side effect in osTicket:
```python
from sqlmodel import select
from app.integration.osticket.commands import IntegrationInbox
from tests.conftest import TestingSessionLocal

async def _inbox_rows():
    async with TestingSessionLocal() as s:
        return (await s.execute(select(IntegrationInbox))).scalars().all()

rows = await _inbox_rows()
assert len(rows) == 1
assert rows[0].command_type == "task.assign"
assert rows[0].aggregate_id == "1"
assert json.loads(rows[0].payload)["staff_id"] == 7
assert rows[0].requested_by_staff_id == 5
```

## Rules

- Isolation: the autouse `setup_db` fixture rebuilds the schema per test — never rely on order or shared state.
- Mock data dicts use the exact column names `read_repository` returns.
- Don't mock `dispatch_command` or `require_perm`; exercise them.
- One behaviour per test, named `test_<action>_<condition>` (e.g. `test_assign_task_without_perm_returns_403`).

Run: `.venv/bin/python -m pytest tests/integration/test_<domain>.py -v`, then `make test`.
