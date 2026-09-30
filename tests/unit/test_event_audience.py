"""Who may hear about an event — app/modules/events/visibility.py."""

import pytest

from app.core.ws_manager import Client
from app.modules.events import visibility
from tests.conftest import make_access


def _client(staff_id, permissions=(), *, is_admin=False):
    return Client(ws=None, access=make_access(staff_id, list(permissions), is_admin=is_admin), expires_at=9e12)


def _event(event_type, aggregate_type, aggregate_id="7"):
    return {"type": event_type, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "payload": {}}


@pytest.fixture
def repo_calls(monkeypatch):
    """Make ticket/task visibility a lookup table and count the queries it costs."""
    calls = []
    visible = {"ticket": set(), "task": set()}

    async def ticket_is_visible(session, ticket_id, *, access):
        calls.append(("ticket", ticket_id, access.staff_id))
        return access.staff_id in visible["ticket"]

    async def task_is_visible(session, task_id, *, access):
        calls.append(("task", task_id, access.staff_id))
        return access.staff_id in visible["task"]

    monkeypatch.setattr(visibility.repo, "ticket_is_visible", ticket_is_visible)
    monkeypatch.setattr(visibility.repo, "task_is_visible", task_is_visible)
    return calls, visible


async def _who(event, clients):
    return sorted(c.staff_id for c in await visibility.recipients(None, event, clients))


# ── tickets and tasks: the ones in the agent's own list ──────────────────────


@pytest.mark.asyncio
async def test_a_ticket_event_reaches_only_agents_who_may_see_that_ticket(repo_calls):
    calls, visible = repo_calls
    visible["ticket"] = {1, 3}
    clients = [_client(1), _client(2), _client(3)]
    assert await _who(_event("ticket.replied", "ticket"), clients) == [1, 3]


@pytest.mark.asyncio
async def test_two_sockets_of_one_agent_cost_one_query(repo_calls):
    calls, visible = repo_calls
    visible["ticket"] = {1}
    assert await _who(_event("ticket.replied", "ticket"), [_client(1), _client(1), _client(2)]) == [1, 1]
    assert [c for c in calls if c[2] == 1] == [("ticket", 7, 1)]


@pytest.mark.asyncio
async def test_a_task_event_uses_the_task_rule(repo_calls):
    calls, visible = repo_calls
    visible["task"] = {2}
    assert await _who(_event("task.assigned", "task"), [_client(1), _client(2)]) == [2]
    assert {c[0] for c in calls} == {"task"}


@pytest.mark.asyncio
async def test_an_admin_is_not_exempt_from_ticket_visibility(repo_calls):
    """osTicket has no admin bypass for what shows in a list, and neither does the event stream."""
    assert await _who(_event("ticket.created", "ticket"), [_client(1, is_admin=True)]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ticket", "task"])
async def test_deletions_go_to_administrators_only(repo_calls, kind):
    """The row is gone, so nobody 'can see' it; only administrators are told."""
    calls, visible = repo_calls
    visible[kind] = {1, 2}
    clients = [_client(1), _client(2, is_admin=True)]
    assert await _who(_event(f"{kind}.deleted", kind), clients) == [2]
    assert calls == []                                     # and no query was needed


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate_id", ["", "abc", None])
async def test_an_event_whose_subject_cannot_be_identified_goes_to_administrators_only(repo_calls, aggregate_id):
    clients = [_client(1), _client(2, is_admin=True)]
    assert await _who(_event("ticket.created", "ticket", aggregate_id), clients) == [2]


# ── directory, knowledge base, canned responses ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["user", "org"])
async def test_directory_events_need_user_dir(kind):
    clients = [_client(1, ["user.dir"]), _client(2, ["user.create"]), _client(3)]
    assert await _who(_event(f"{kind}.updated", kind), clients) == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["kb.faq", "kb_faq", "kb.category", "kb_category"])
async def test_knowledge_base_events_reach_every_agent(kind):
    assert await _who(_event("kb.faq.updated", kind), [_client(1), _client(2)]) == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["kb.canned", "kb_canned"])          # a signal's spelling and a command result's
async def test_canned_events_reach_canned_managers_only(kind):
    clients = [_client(1, ["canned.manage"]), _client(2, ["ticket.reply"]), _client(3)]
    assert await _who(_event("kb.canned.created", kind), clients) == [1]


# ── admin panel objects, and anything unknown ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["staff", "dept", "team", "topic", "sla", "priority", "form", "banlist"])
async def test_admin_panel_object_events_reach_administrators_only(kind):
    everything = ["user.dir", "canned.manage", "ticket.create", "faq.manage"]
    clients = [_client(1, everything), _client(2, is_admin=True)]
    assert await _who(_event(f"{kind}.updated", kind), clients) == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["galaxy", "", "ticket.thread"])
async def test_an_unknown_kind_of_event_fails_closed(kind):
    clients = [_client(1, ["user.dir", "canned.manage"]), _client(2, is_admin=True)]
    assert await _who(_event("x.y", kind), clients) == [2]


def test_aggregate_kinds_normalise_underscores():
    assert visibility.aggregate_kind("kb_canned") == visibility.aggregate_kind("kb.canned") == "kb.canned"
    assert visibility.aggregate_kind(None) == ""


# ── finding the subject of an event ───────────────────────────────────────────


@pytest.mark.parametrize(
    "aggregate_type, aggregate_id, payload, expected",
    [
        ("ticket", "7", {"ticket_id": 99}, "7"),                       # a set aggregate id wins
        ("ticket", "", {"ticket_id": 42}, "42"),                       # a create: the new id is in the result
        ("task", "", {"task_id": 5, "ticket_id": 9}, "5"),             # the aggregate's own key, not another id
        ("kb_canned", "", {"canned_id": 3}, "3"),
        ("kb.faq", "", {"faq_id": 8}, "8"),
        ("user", "", {"id": 12}, "12"),                                # a model signal's payload
        ("banlist", "", {"ban_id": 4}, "4"),                           # no key named after the kind: the only *_id
        ("ticket", "", {"unrelated": 1}, ""),
        ("ticket", "", {"a_id": 1, "b_id": 2}, ""),                    # ambiguous: refuse to guess
        ("ticket", "", {"ticket_id": True}, ""),                       # booleans are not ids
        ("ticket", "", {"ticket_id": 0}, ""),
        ("ticket", None, None, ""),
    ],
)
def test_resolve_aggregate_id(aggregate_type, aggregate_id, payload, expected):
    assert visibility.resolve_aggregate_id(aggregate_type, aggregate_id, payload) == expected


@pytest.mark.asyncio
async def test_a_create_event_is_judged_by_the_ticket_it_created(repo_calls):
    """The event of a ticket.create has no aggregate_id; the audience must still be worked out from the result."""
    calls, visible = repo_calls
    visible["ticket"] = {1}
    event = {"type": "ticket.created", "aggregate_type": "ticket", "aggregate_id": "", "payload": {"ticket_id": 42}}
    assert await _who(event, [_client(1), _client(2)]) == [1]
    assert calls[0][1] == 42
