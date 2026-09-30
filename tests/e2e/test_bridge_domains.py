"""Live end-to-end tests of every write command through the PHP bridge.

For each domain: create, read the result back through the API, partially update (proving
the stored fields survive), and delete. The refusal cases pin the bridge's failure
behaviour: a command osTicket rejects must end ``failed`` with osTicket's reason, never a
phantom ``done``.

What these guard, because each was a real defect: handlers calling osTicket methods that
don't exist, create() that only instantiated, update() that silently wiped fields, values
osTicket's ORM never writes, read queries against columns that don't exist.

Needs the running stack — see conftest.py.
"""

import json
import uuid

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, login_raw

pytestmark = pytest.mark.e2e


def _email(uniq: str, who: str = "e2e") -> str:
    return f"{who}.{uniq}@{DELIVERABLE_DOMAIN}"


# ── lookups ───────────────────────────────────────────────────────────────────


def test_priority_lifecycle(bridge, uniq):
    pid = bridge.create(
        "/api/v1/priorities",
        {"name": f"e2e-prio-{uniq}", "desc": "first", "urgency": 9, "is_public": True},
        cleanup="/api/v1/priorities/{id}",
    )
    got = bridge.read(f"/api/v1/priorities/{pid}")
    assert (got["name"], got["desc"], got["urgency"]) == (f"e2e-prio-{uniq}", "first", 9)

    bridge.command("PATCH", f"/api/v1/priorities/{pid}", {"desc": "changed"})
    got = bridge.read(f"/api/v1/priorities/{pid}")
    assert (got["name"], got["desc"], got["urgency"]) == (f"e2e-prio-{uniq}", "changed", 9)

    bridge.command("DELETE", f"/api/v1/priorities/{pid}")
    assert bridge.get(f"/api/v1/priorities/{pid}").status_code == 404


def test_priority_in_use_by_a_help_topic_cannot_be_deleted(bridge):
    # priority 2 ("normal") is the system default and the default of seeded help topics
    bridge.refuses("DELETE", "/api/v1/priorities/2", None, because="default")


def test_sla_lifecycle(bridge, uniq):
    sid = bridge.create(
        "/api/v1/slas", {"name": f"e2e-sla-{uniq}", "grace_period": 12, "is_active": True, "notes": "n"},
        cleanup="/api/v1/slas/{id}",
    )
    got = bridge.read(f"/api/v1/slas/{sid}")
    assert (got["name"], got["grace_period"], got["is_active"]) == (f"e2e-sla-{uniq}", 12, True)

    bridge.command("PATCH", f"/api/v1/slas/{sid}", {"grace_period": 24})
    got = bridge.read(f"/api/v1/slas/{sid}")
    assert (got["name"], got["grace_period"], got["is_active"]) == (f"e2e-sla-{uniq}", 24, True)

    bridge.command("PATCH", f"/api/v1/slas/{sid}", {"is_active": False})
    got = bridge.read(f"/api/v1/slas/{sid}")
    assert (got["grace_period"], got["is_active"]) == (24, False)

    bridge.command("DELETE", f"/api/v1/slas/{sid}")
    assert bridge.get(f"/api/v1/slas/{sid}").status_code == 404


def test_help_topic_lifecycle(bridge, uniq):
    tid = bridge.create(
        "/api/v1/topics",
        {"topic": f"e2e-topic-{uniq}", "is_active": True, "is_public": True, "dept_id": 1, "priority_id": 2},
        cleanup="/api/v1/topics/{id}",
    )
    got = bridge.read(f"/api/v1/topics/{tid}")
    assert (got["topic"], got["dept_id"], got["priority_id"], got["is_active"]) == (f"e2e-topic-{uniq}", 1, 2, True)

    bridge.command("PATCH", f"/api/v1/topics/{tid}", {"topic": f"e2e-renamed-{uniq}"})
    got = bridge.read(f"/api/v1/topics/{tid}")
    assert (got["topic"], got["dept_id"], got["priority_id"]) == (f"e2e-renamed-{uniq}", 1, 2)

    bridge.command("PATCH", f"/api/v1/topics/{tid}", {"is_active": False})
    assert bridge.read(f"/api/v1/topics/{tid}")["is_active"] is False

    bridge.command("DELETE", f"/api/v1/topics/{tid}")
    assert bridge.get(f"/api/v1/topics/{tid}").status_code == 404


def test_help_topic_name_must_be_at_least_five_characters(bridge):
    bridge.refuses("POST", "/api/v1/topics", {"topic": "abc", "dept_id": 1}, because="too short")


def test_banlist_lifecycle(bridge, uniq):
    val = _email(uniq, "spam")
    ban_id = bridge.create(
        "/api/v1/banlist", {"val": val, "is_active": True, "notes": "n"}, cleanup="/api/v1/banlist/{id}"
    )
    got = bridge.read(f"/api/v1/banlist/{ban_id}")
    assert (got["val"], got["is_active"], got["notes"]) == (val, True, "n")

    bridge.command("PATCH", f"/api/v1/banlist/{ban_id}", {"notes": "changed"})
    got = bridge.read(f"/api/v1/banlist/{ban_id}")
    assert (got["val"], got["is_active"], got["notes"]) == (val, True, "changed")

    bridge.command("PATCH", f"/api/v1/banlist/{ban_id}", {"is_active": False})
    assert bridge.read(f"/api/v1/banlist/{ban_id}")["is_active"] is False

    bridge.refuses("POST", "/api/v1/banlist", {"val": val}, because="already")
    bridge.command("DELETE", f"/api/v1/banlist/{ban_id}")
    assert bridge.get(f"/api/v1/banlist/{ban_id}").status_code == 404


def test_banlist_rejects_a_non_email(bridge):
    bridge.refuses("POST", "/api/v1/banlist", {"val": "not-an-email"}, because="valid email")


def test_form_lifecycle(bridge, uniq):
    fid = bridge.create(
        "/api/v1/forms", {"title": f"e2e-form-{uniq}", "type": "G", "instructions": "i", "notes": "n"},
        cleanup="/api/v1/forms/{id}",
    )
    got = bridge.read(f"/api/v1/forms/{fid}")
    assert (got["title"], got["instructions"], got["notes"]) == (f"e2e-form-{uniq}", "i", "n")

    bridge.command("PATCH", f"/api/v1/forms/{fid}", {"title": f"e2e-form-2-{uniq}"})
    got = bridge.read(f"/api/v1/forms/{fid}")
    assert (got["title"], got["instructions"]) == (f"e2e-form-2-{uniq}", "i")

    bridge.command("DELETE", f"/api/v1/forms/{fid}")
    assert bridge.get(f"/api/v1/forms/{fid}").status_code == 404   # soft-deleted rows are hidden


def test_a_core_osticket_form_cannot_be_deleted(bridge):
    # osTicket flags its own forms deletable; deleting "Contact Information" (type U) would break
    # user handling, so only custom (type G) forms may be removed through the API.
    bridge.refuses("DELETE", "/api/v1/forms/1", None, because="core osticket form")


# ── departments and teams ─────────────────────────────────────────────────────


def test_department_lifecycle_keeps_a_private_department_private(bridge, uniq):
    did = bridge.create(
        "/api/v1/departments", {"name": f"e2e-dept-{uniq}", "is_public": False}, cleanup="/api/v1/departments/{id}"
    )
    assert bridge.read(f"/api/v1/departments/{did}")["is_public"] is False   # osTicket's ORM defaults it to public

    bridge.command("PATCH", f"/api/v1/departments/{did}", {"name": f"e2e-dept-2-{uniq}"})
    got = bridge.read(f"/api/v1/departments/{did}")
    assert (got["name"], got["is_public"]) == (f"e2e-dept-2-{uniq}", False)

    bridge.command("DELETE", f"/api/v1/departments/{did}")
    assert bridge.get(f"/api/v1/departments/{did}").status_code == 404


def test_team_lifecycle_keeps_its_members(bridge, uniq):
    tid = bridge.create(
        "/api/v1/teams", {"name": f"e2e-team-{uniq}", "is_enabled": False, "notes": "n", "members": [1]},
        cleanup="/api/v1/teams/{id}",
    )
    got = bridge.read(f"/api/v1/teams/{tid}")
    assert (got["name"], got["is_enabled"], got["members"]) == (f"e2e-team-{uniq}", False, [1])

    # osTicket's Team::update() removes every member when the key is absent
    bridge.command("PATCH", f"/api/v1/teams/{tid}", {"name": f"e2e-team-2-{uniq}"})
    got = bridge.read(f"/api/v1/teams/{tid}")
    assert (got["name"], got["is_enabled"], got["members"], got["notes"]) == (f"e2e-team-2-{uniq}", False, [1], "n")

    bridge.command("PATCH", f"/api/v1/teams/{tid}", {"members": []})
    assert bridge.read(f"/api/v1/teams/{tid}")["members"] == []

    bridge.command("DELETE", f"/api/v1/teams/{tid}")
    assert bridge.get(f"/api/v1/teams/{tid}").status_code == 404


# ── organizations, users, staff ───────────────────────────────────────────────


def test_organization_lifecycle(bridge, uniq):
    oid = bridge.create(
        "/api/v1/orgs",
        {"name": f"e2e-org-{uniq}", "address": "1 Main St", "phone": "555-0100",
         "website": "https://e2e.example", "internal_notes": "n"},
        cleanup="/api/v1/orgs/{id}",
    )
    got = bridge.read(f"/api/v1/orgs/{oid}")
    assert (got["address"], got["website"], got["internal_notes"]) == ("1 Main St", "https://e2e.example", "n")
    assert got["phone"]   # osTicket normalises the phone number's punctuation away

    bridge.command("PATCH", f"/api/v1/orgs/{oid}", {"website": "https://changed.example"})
    got = bridge.read(f"/api/v1/orgs/{oid}")
    assert (got["name"], got["address"], got["website"]) == (f"e2e-org-{uniq}", "1 Main St", "https://changed.example")

    bridge.command("PATCH", f"/api/v1/orgs/{oid}", {"name": f"e2e-org-2-{uniq}", "phone": ""})
    got = bridge.read(f"/api/v1/orgs/{oid}")
    assert (got["name"], got["phone"], got["address"]) == (f"e2e-org-2-{uniq}", "", "1 Main St")

    bridge.command("DELETE", f"/api/v1/orgs/{oid}")
    assert bridge.get(f"/api/v1/orgs/{oid}").status_code == 404


def test_organization_rejects_a_duplicate_name_and_a_bad_phone(bridge, uniq):
    bridge.create("/api/v1/orgs", {"name": f"e2e-dup-{uniq}"}, cleanup="/api/v1/orgs/{id}")
    bridge.refuses("POST", "/api/v1/orgs", {"name": f"e2e-dup-{uniq}"}, because="already exists")
    bridge.refuses("POST", "/api/v1/orgs", {"name": f"e2e-badphone-{uniq}", "phone": "abc"}, because="phone")
    # the rejected create must not leave a half-populated organization behind
    names = [o["name"] for o in bridge.read("/api/v1/orgs?page_size=200")["items"]]
    assert f"e2e-badphone-{uniq}" not in names


def test_user_lifecycle_with_organization_membership(bridge, uniq):
    uid = bridge.create(
        "/api/v1/users",
        {"name": "E2E User", "email": _email(uniq), "org_id": 1, "phone": "555-0111", "internal_notes": "n"},
        cleanup="/api/v1/users/{id}",
    )
    got = bridge.read(f"/api/v1/users/{uid}")
    assert (got["name"], got["email"], got["org_id"], got["internal_notes"]) == ("E2E User", _email(uniq), 1, "n")
    assert got["phone"]

    bridge.command("PATCH", f"/api/v1/users/{uid}", {"name": "E2E User Two"})
    got = bridge.read(f"/api/v1/users/{uid}")
    assert (got["name"], got["email"], got["org_id"]) == ("E2E User Two", _email(uniq), 1)

    bridge.command("PATCH", f"/api/v1/users/{uid}", {"org_id": 0})
    assert not bridge.read(f"/api/v1/users/{uid}")["org_id"]

    bridge.command("DELETE", f"/api/v1/users/{uid}")
    assert bridge.get(f"/api/v1/users/{uid}").status_code == 404


def test_user_refusals(bridge, uniq):
    bridge.create("/api/v1/users", {"name": "E2E Dup", "email": _email(uniq)}, cleanup="/api/v1/users/{id}")
    bridge.refuses("POST", "/api/v1/users", {"name": "E2E Dup Again", "email": _email(uniq)}, because="already exists")
    bridge.refuses("POST", "/api/v1/users", {"name": "Bad", "email": "not-an-email"}, because="valid email")
    # osTicket's default user form has no mobile field: refuse rather than silently drop it
    bridge.refuses(
        "POST", "/api/v1/users", {"name": "E2E Mobile", "email": _email(uniq, "mobile"), "mobile": "555-0100"},
        because="mobile",
    )
    users = [u["email"] for u in bridge.read("/api/v1/users?page_size=200")["items"]]
    assert _email(uniq, "mobile") not in users


def test_staff_lifecycle_and_the_password_actually_works(bridge, uniq):
    username = f"e2estaff{uniq}"
    password = "E2e-Pass-9x!Qz"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": "Staff", "email": _email(uniq, "staff"),
         "dept_id": 1, "role_id": 3, "password": password, "is_active": True, "is_admin": False},
        cleanup="/api/v1/staff/{id}",
    )
    got = bridge.read(f"/api/v1/staff/{sid}")
    assert (got["username"], got["role_id"], got["dept_id"], got["is_admin"], got["is_active"]) == (username, 3, 1, False, True)

    def login(pw):
        return login_raw(username, pw)

    assert login(password).status_code == 200      # the password set through the bridge works
    assert login("wrong-password").status_code == 401

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"firstname": "Renamed"})
    got = bridge.read(f"/api/v1/staff/{sid}")
    assert (got["firstname"], got["lastname"], got["email"], got["role_id"]) == ("Renamed", "Staff", _email(uniq, "staff"), 3)
    assert login(password).status_code == 200      # an unrelated update keeps the password

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"is_active": False})
    assert bridge.read(f"/api/v1/staff/{sid}")["is_active"] is False
    assert login(password).status_code in (401, 403)   # a deactivated agent cannot sign in

    bridge.command("DELETE", f"/api/v1/staff/{sid}")
    assert bridge.get(f"/api/v1/staff/{sid}").status_code == 404


def test_an_agent_cannot_delete_themselves(bridge):
    # the acting staff member is the admin who is logged in; staff #1 is that admin
    bridge.refuses("DELETE", "/api/v1/staff/1", None, because="delete themselves")


# ── knowledgebase ─────────────────────────────────────────────────────────────


def test_knowledgebase_lifecycle(bridge, uniq):
    cid = bridge.create(
        "/api/v1/kb/categories",
        {"name": f"e2e-cat-{uniq}", "description": "d", "is_public": True, "notes": "n"},
        cleanup="/api/v1/kb/categories/{id}",
    )
    got = bridge.read(f"/api/v1/kb/categories/{cid}")
    assert (got["name"], got["description"], got["is_public"]) == (f"e2e-cat-{uniq}", "d", True)

    bridge.command("PATCH", f"/api/v1/kb/categories/{cid}", {"description": "changed"})
    got = bridge.read(f"/api/v1/kb/categories/{cid}")
    assert (got["name"], got["description"], got["is_public"], got["notes"]) == (f"e2e-cat-{uniq}", "changed", True, "n")

    fid = bridge.create(
        "/api/v1/kb/faqs",
        {"category_id": cid, "question": f"e2e question {uniq}?", "answer": "the answer",
         "is_published": False, "keywords": "alpha beta"},
        cleanup="/api/v1/kb/faqs/{id}",
    )
    got = bridge.read(f"/api/v1/kb/faqs/{fid}")
    assert (got["category_id"], got["question"], got["answer"], got["is_published"]) == (cid, f"e2e question {uniq}?", "the answer", False)
    assert got["keywords"] == "alpha beta"          # osTicket's FAQ::update() would blank this

    bridge.command("PATCH", f"/api/v1/kb/faqs/{fid}", {"answer": "changed answer"})
    got = bridge.read(f"/api/v1/kb/faqs/{fid}")
    assert (got["question"], got["answer"], got["is_published"], got["keywords"]) == (
        f"e2e question {uniq}?", "changed answer", False, "alpha beta")

    bridge.command("PATCH", f"/api/v1/kb/faqs/{fid}", {"is_published": True})
    assert bridge.read(f"/api/v1/kb/faqs/{fid}")["is_published"] is True

    bridge.command("DELETE", f"/api/v1/kb/faqs/{fid}")
    assert bridge.get(f"/api/v1/kb/faqs/{fid}").status_code == 404
    bridge.command("DELETE", f"/api/v1/kb/categories/{cid}")
    assert bridge.get(f"/api/v1/kb/categories/{cid}").status_code == 404


def test_knowledgebase_category_requires_a_description(bridge, uniq):
    bridge.refuses("POST", "/api/v1/kb/categories", {"name": f"e2e-nodesc-{uniq}"}, because="description")


# ── tasks ─────────────────────────────────────────────────────────────────────


def test_task_lifecycle(bridge, uniq):
    tid = bridge.create(
        "/api/v1/tasks/", {"title": f"e2e task {uniq}", "description": "do the thing", "dept_id": 1},
        cleanup="/api/v1/tasks/{id}",
    )
    got = bridge.read(f"/api/v1/tasks/{tid}")
    assert (got["title"], got["dept_id"], got["status"]["state"]) == (f"e2e task {uniq}", 1, "open")
    assert any("do the thing" in e["body"] for e in got["thread"])

    # description is the thread's original message; a department change is a transfer
    bridge.command("PATCH", f"/api/v1/tasks/{tid}",
                   {"description": "rewritten body", "dept_id": 2, "due_date": "2030-01-15T10:00:00"})
    got = bridge.read(f"/api/v1/tasks/{tid}")
    assert (got["title"], got["dept_id"]) == (f"e2e task {uniq}", 2)
    assert got["due_date"]
    assert any("rewritten body" in e["body"] for e in got["thread"])
    assert not any("do the thing" in e["body"] for e in got["thread"])

    bridge.command("PATCH", f"/api/v1/tasks/{tid}", {"title": f"e2e renamed {uniq}"})
    assert bridge.read(f"/api/v1/tasks/{tid}")["title"] == f"e2e renamed {uniq}"

    bridge.command("POST", f"/api/v1/tasks/{tid}/assign", {"staff_id": 1, "note": "yours"})
    assert bridge.read(f"/api/v1/tasks/{tid}")["assignee_staff_id"] == 1

    bridge.command("POST", f"/api/v1/tasks/{tid}/note", {"title": "t", "note": "a task note"})
    assert any("a task note" in e["body"] for e in bridge.read(f"/api/v1/tasks/{tid}")["thread"])

    bridge.command("POST", f"/api/v1/tasks/{tid}/close", {"note": "done"})
    assert bridge.read(f"/api/v1/tasks/{tid}")["status"]["state"] == "closed"
    bridge.refuses("POST", f"/api/v1/tasks/{tid}/close", {}, because="already closed")

    bridge.command("POST", f"/api/v1/tasks/{tid}/reopen", {"note": "again"})
    assert bridge.read(f"/api/v1/tasks/{tid}")["status"]["state"] == "open"
    bridge.refuses("POST", f"/api/v1/tasks/{tid}/reopen", {}, because="already open")

    bridge.command("DELETE", f"/api/v1/tasks/{tid}")
    assert bridge.get(f"/api/v1/tasks/{tid}").status_code == 404


def test_task_can_be_linked_to_a_ticket(bridge, uniq):
    uid_holder = {}
    tk = _new_ticket(bridge, uniq, uid_holder)
    tid = bridge.create(
        "/api/v1/tasks/", {"title": f"e2e linked {uniq}", "description": "linked task", "ticket_id": tk},
        cleanup="/api/v1/tasks/{id}",
    )
    assert bridge.read(f"/api/v1/tasks/{tid}")["ticket_id"] == tk


# ── tickets ───────────────────────────────────────────────────────────────────


def _new_ticket(bridge, uniq, holder, suffix=""):
    """Create a ticket for a fresh customer; cleanup deletes the ticket, then its customer."""
    email = _email(uniq, f"cust{suffix}")
    # registered first so it is cleaned up LAST: a user with tickets cannot be deleted
    tk = bridge.create(
        "/api/v1/tickets/",
        {"subject": f"e2e ticket {uniq}{suffix}", "message": "hello there", "email": email,
         "name": "E2E Customer", "dept_id": 1, "topic_id": 1},
    )
    user_id = bridge.read(f"/api/v1/tickets/{tk}")["user_id"]
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{user_id}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk}"))
    holder["user_id"] = user_id
    return tk


def test_ticket_lifecycle(bridge, uniq):
    holder = {}
    tk = _new_ticket(bridge, uniq, holder)
    got = bridge.read(f"/api/v1/tickets/{tk}")
    assert (got["subject"], got["dept_id"], got["topic_id"], got["user_name"]) == (
        f"e2e ticket {uniq}", 1, 1, "E2E Customer")
    assert got["status"]["state"] == "open"

    bridge.command("POST", f"/api/v1/tickets/{tk}/reply", {"message": "a staff reply"})
    bridge.command("POST", f"/api/v1/tickets/{tk}/note", {"title": "nt", "message": "internal note body"})
    thread = bridge.read(f"/api/v1/tickets/{tk}")["thread"]
    assert any("a staff reply" in e["body"] for e in thread)
    assert any("internal note body" in e["body"] for e in thread)

    bridge.command("POST", f"/api/v1/tickets/{tk}/assign", {"staff_id": 1, "note": "mine"})
    assert bridge.read(f"/api/v1/tickets/{tk}")["assignee_staff_id"] == 1

    team = bridge.create("/api/v1/teams", {"name": f"e2e-tix-team-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    bridge.command("POST", f"/api/v1/tickets/{tk}/assign-team", {"team_id": team, "note": "team"})
    assert bridge.read(f"/api/v1/tickets/{tk}")["team_id"] == team

    bridge.command("POST", f"/api/v1/tickets/{tk}/transfer", {"dept_id": 2, "note": "move"})
    assert bridge.read(f"/api/v1/tickets/{tk}")["dept_id"] == 2

    bridge.command("POST", f"/api/v1/tickets/{tk}/change-owner", {"user_id": 1})
    assert bridge.read(f"/api/v1/tickets/{tk}")["user_id"] == 1
    bridge.refuses("POST", f"/api/v1/tickets/{tk}/change-owner", {"user_id": 1}, because="already owns")

    collab = bridge.create("/api/v1/users", {"name": "E2E Collab", "email": _email(uniq, "collab")}, cleanup="/api/v1/users/{id}")
    bridge.command("POST", f"/api/v1/tickets/{tk}/collaborators", {"user_id": collab})
    assert any(c["user_id"] == collab for c in bridge.read(f"/api/v1/tickets/{tk}")["collaborators"])

    bridge.command("POST", f"/api/v1/tickets/{tk}/answered")
    assert bridge.read(f"/api/v1/tickets/{tk}")["is_answered"] is True
    bridge.command("POST", f"/api/v1/tickets/{tk}/unanswered")
    assert bridge.read(f"/api/v1/tickets/{tk}")["is_answered"] is False

    bridge.command("POST", f"/api/v1/tickets/{tk}/close", {"note": "resolved"})
    assert bridge.read(f"/api/v1/tickets/{tk}")["status"]["state"] == "closed"
    bridge.refuses("POST", f"/api/v1/tickets/{tk}/close", {}, because="already closed")
    bridge.command("POST", f"/api/v1/tickets/{tk}/reopen", {"note": "again"})
    assert bridge.read(f"/api/v1/tickets/{tk}")["status"]["state"] == "open"

    bridge.command("DELETE", f"/api/v1/tickets/{tk}")
    assert bridge.get(f"/api/v1/tickets/{tk}").status_code == 404


def test_ticket_merge_combines_children_into_the_parent_and_closes_them(bridge, uniq):
    holder = {}
    parent = _new_ticket(bridge, uniq, holder, "p")
    child = _new_ticket(bridge, uniq, {}, "c")

    bridge.command("POST", f"/api/v1/tickets/{parent}/merge", {"child_ids": [child]})
    assert bridge.read(f"/api/v1/tickets/{child}")["status"]["state"] == "closed"
    assert bridge.read(f"/api/v1/tickets/{parent}")["status"]["state"] == "open"


def test_assigning_a_ticket_to_an_empty_team_is_refused_with_the_real_reason(bridge, uniq):
    tk = _new_ticket(bridge, uniq, {})
    team = bridge.create("/api/v1/teams", {"name": f"e2e-empty-{uniq}"}, cleanup="/api/v1/teams/{id}")
    bridge.refuses("POST", f"/api/v1/tickets/{tk}/assign-team", {"team_id": team}, because="does not have members")


def test_a_command_for_a_missing_ticket_fails_and_does_not_wedge_the_inbox(bridge):
    bridge.refuses("POST", "/api/v1/tickets/999999/reply", {"message": "x"}, because="not found")
    # the inbox keeps draining afterwards
    assert bridge.command("POST", "/api/v1/canned", {"title": "e2e-after-failure", "response": "ok"})["status"] == "done"
    canned_id = next(i for i in bridge.read("/api/v1/canned?page_size=200")["items"] if i["title"] == "e2e-after-failure")["id"]
    bridge.command("DELETE", f"/api/v1/canned/{canned_id}")


# ── the read side ─────────────────────────────────────────────────────────────


def test_every_read_endpoint_answers_200(bridge):
    """Walk every GET in the OpenAPI spec: lists first, then one detail per list.

    Most reads were once broken (queries against columns and tables that don't exist in
    osTicket 1.18); the unit tests mock the repository, so only a live run catches that.
    """
    import re

    paths = bridge.read("/openapi.json")["paths"]
    getters = [p for p, ops in paths.items() if "get" in ops and "/auth/" not in p and p != "/health"]
    lists = [p for p in getters if "{" not in p and p != "/api/v1/search"]
    details = [p for p in getters if re.search(r"\{[^}/]+\}$", p)]

    failures, first_ids = [], {}
    for path in sorted(lists):
        resp = bridge.get(path)
        if resp.status_code != 200:
            failures.append(f"{path} -> {resp.status_code}")
            continue
        body = resp.json()
        rows = body.get("items") or next((v for v in body.values() if isinstance(v, list)), [])
        if rows:
            first_ids[path.rstrip("/")] = next(
                (rows[0][k] for k in ("id", "task_id", "ticket_id") if k in rows[0]), None
            )
    for path in sorted(details):
        base = re.sub(r"/\{[^}]+\}$", "", path).rstrip("/")
        if first_ids.get(base) is None:
            continue
        resp = bridge.get(re.sub(r"\{[^}]+\}$", str(first_ids[base]), path))
        if resp.status_code != 200:
            failures.append(f"{path} [{first_ids[base]}] -> {resp.status_code}")

    assert not failures, failures
    assert bridge.get("/api/v1/search?q=e2e").status_code == 200


def test_commands_queued_together_do_not_share_state(bridge, uniq):
    """osTicket keeps static form state per PHP process (TaskForm::$instance holds the previous
    task's answers); commands must each run in a clean process. Queue two creates at once so
    they are drained back to back, then check each task kept its own title."""
    titles = [f"e2e twin A {uniq}", f"e2e twin B {uniq}"]
    ids = []
    for title in titles:
        resp = bridge.client.post(
            "/api/v1/tasks/",
            json={"title": title, "description": "queued together", "dept_id": 1},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        assert resp.status_code == 202
        ids.append(resp.json()["command_id"])
    for command_id in ids:
        done = bridge._wait(command_id)
        assert done["status"] == "done", done["error_message"]
        task_id = json.loads(done["result_payload"])["task_id"]
        bridge._cleanups.append(("DELETE", f"/api/v1/tasks/{task_id}"))
        got = bridge.read(f"/api/v1/tasks/{task_id}")
        assert got["title"] == titles[ids.index(command_id)]
