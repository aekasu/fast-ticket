"""The agent directory and the assign-form choices."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.integration.test_staff import _auth

ROW = {"id": 7, "firstname": "Ada", "lastname": "Lovelace", "email": "ada@x.test", "phone": "", "phone_ext": "",
       "mobile": "", "dept_id": 1, "dept_name": "Support"}
REPO = "app.modules.agents.service.repo"


async def _get(path, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(path, headers=headers or {})


@pytest.mark.asyncio
async def test_directory_formats_names_and_passes_the_filters_and_access():
    listing = AsyncMock(return_value=([ROW], 1))
    with patch(f"{REPO}.get_agent_name_format", new=AsyncMock(return_value="lastfirst")), \
            patch(f"{REPO}.list_directory_agents", new=listing):
        resp = await _get("/api/v1/agents?q=ada&dept_id=1&sort=email&descending=true", _auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and body["items"][0]["name"] == "Lovelace, Ada"
    kwargs = listing.call_args.kwargs
    assert (kwargs["q"], kwargs["dept_id"], kwargs["sort"], kwargs["descending"]) == ("ada", 1, "email", True)
    assert kwargs["name_format"] == "lastfirst" and kwargs["access"].staff_id == 5


@pytest.mark.asyncio
async def test_directory_needs_a_login_and_rejects_an_unknown_sort():
    assert (await _get("/api/v1/agents")).status_code == 401
    assert (await _get("/api/v1/agents?sort=password", _auth())).status_code == 422


def _patch_assign(row_target, row):
    return (
        patch(row_target, new=AsyncMock(return_value=row)),
        patch(f"{REPO}.get_agent_name_format", new=AsyncMock(return_value="full")),
        patch(f"{REPO}.list_assignable_agents", new=AsyncMock(return_value=[{"id": 7, "firstname": "Ada", "lastname": "L"}])),
        patch(f"{REPO}.list_assignable_teams", new=AsyncMock(return_value=[{"id": 3, "name": "Tier 1"}])),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,perm,target", [
    ("tickets", "ticket.assign", "app.modules.tickets.service.repo.get_ticket"),
    ("tasks", "task.assign", "app.modules.tasks.service.repo.get_task"),
])
async def test_assignees_for_an_object_the_agent_may_assign(kind, perm, target):
    patches = _patch_assign(target, {"dept_id": 1})
    with patches[0], patches[1], patches[2] as agents, patches[3]:
        resp = await _get(f"/api/v1/{kind}/9/assignees", _auth([perm]))
    assert resp.status_code == 200
    assert resp.json() == {"agents": [{"id": 7, "name": "Ada L"}], "teams": [{"id": 3, "name": "Tier 1"}]}
    assert agents.call_args.kwargs["dept_id"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,perm,target", [
    ("tickets", "ticket.assign", "app.modules.tickets.service.repo.get_ticket"),
    ("tasks", "task.assign", "app.modules.tasks.service.repo.get_task"),
])
async def test_assignees_are_404_when_the_object_is_not_visible_and_403_without_the_permission(kind, perm, target):
    with patch(target, new=AsyncMock(return_value=None)):
        assert (await _get(f"/api/v1/{kind}/9/assignees", _auth([perm]))).status_code == 404
    with patch(target, new=AsyncMock(return_value={"dept_id": 1})):
        assert (await _get(f"/api/v1/{kind}/9/assignees", _auth([]))).status_code == 403
    assert (await _get(f"/api/v1/{kind}/9/assignees")).status_code == 401


@pytest.mark.asyncio
async def test_a_role_that_assigns_only_in_another_department_is_refused_for_this_ticket():
    """The route gate is "any role"; the exact answer is the role in the object's department."""
    with patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value={"dept_id": 2})):
        resp = await _get("/api/v1/tickets/9/assignees", _auth(["ticket.assign"]))   # role granted in department 1 only
    assert resp.status_code == 403
