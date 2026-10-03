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
import time
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


def test_sla_settings_round_trip_and_partial_updates_keep_them(bridge, uniq):
    sid = bridge.create(
        "/api/v1/slas",
        {"name": f"e2e-slaset-{uniq}", "grace_period": 6, "schedule_id": 2, "transient": True,
         "disable_overdue_alerts": True, "notes": "n"},
        cleanup="/api/v1/slas/{id}",
    )
    url = f"/api/v1/slas/{sid}"
    want = {"grace_period": 6, "schedule_id": 2, "transient": True, "disable_overdue_alerts": True,
            "is_active": True, "notes": "n"}

    def check(expected):
        got = bridge.read(url)
        assert {k: got[k] for k in expected} == expected, got

    check(want)
    bridge.command("PATCH", url, {"grace_period": 12})                 # nothing else may move
    check({**want, "grace_period": 12})
    bridge.command("PATCH", url, {"transient": False, "schedule_id": 0, "is_active": False})
    check({**want, "grace_period": 12, "transient": False, "schedule_id": 0, "is_active": False})
    bridge.refuses("PATCH", url, {"schedule_id": 999999}, because="Schedule not found")
    bridge.refuses("POST", "/api/v1/slas", {"name": f"e2e-slazero-{uniq}", "grace_period": 0}, because="Grace period required")


def test_help_topic_lifecycle(bridge, uniq):
    tid = bridge.create(
        "/api/v1/topics",
        {"topic": f"e2e-topic-{uniq}", "status": "active", "is_public": True, "dept_id": 1, "priority_id": 2},
        cleanup="/api/v1/topics/{id}",
    )
    got = bridge.read(f"/api/v1/topics/{tid}")
    assert (got["topic"], got["dept_id"], got["priority_id"], got["is_active"]) == (f"e2e-topic-{uniq}", 1, 2, True)

    bridge.command("PATCH", f"/api/v1/topics/{tid}", {"topic": f"e2e-renamed-{uniq}"})
    got = bridge.read(f"/api/v1/topics/{tid}")
    assert (got["topic"], got["dept_id"], got["priority_id"]) == (f"e2e-renamed-{uniq}", 1, 2)

    bridge.command("PATCH", f"/api/v1/topics/{tid}", {"status": "disabled"})
    assert bridge.read(f"/api/v1/topics/{tid}")["is_active"] is False

    bridge.command("DELETE", f"/api/v1/topics/{tid}")
    assert bridge.get(f"/api/v1/topics/{tid}").status_code == 404


def test_help_topic_settings_round_trip_and_partial_updates_keep_them(bridge, uniq):
    form_a = bridge.create("/api/v1/forms", {"title": f"e2e-tf-a-{uniq}", "type": "G"}, cleanup="/api/v1/forms/{id}")
    form_b = bridge.create("/api/v1/forms", {"title": f"e2e-tf-b-{uniq}", "type": "G"}, cleanup="/api/v1/forms/{id}")
    team = bridge.create("/api/v1/teams", {"name": f"e2e-topic-team-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    parent = bridge.create("/api/v1/topics", {"topic": f"e2e-parent-{uniq}", "dept_id": 1}, cleanup="/api/v1/topics/{id}")
    tid = bridge.create(
        "/api/v1/topics",
        {"topic": f"e2e-tset-{uniq}", "dept_id": 1, "priority_id": 2, "sla_id": 1, "parent_id": parent,
         "no_auto_response": True, "custom_numbers": True, "number_format": "E2E-####", "sequence_id": 1,
         "assign_staff_id": 1, "notes": "n", "status_id": 1, "form_ids": [form_a, form_b]},
        cleanup="/api/v1/topics/{id}",
    )
    url = f"/api/v1/topics/{tid}"
    want = {"parent_id": parent, "sla_id": 1, "priority_id": 2, "no_auto_response": True, "custom_numbers": True,
            "number_format": "E2E-####", "assign_staff_id": 1, "assign_team_id": 0, "status": "active",
            "notes": "n", "form_ids": [form_a, form_b]}

    def check(expected):
        got = bridge.read(url)
        assert {k: got[k] for k in expected} == expected, got

    check(want)
    bridge.command("PATCH", url, {"notes": "changed"})                  # nothing else may move (forms included)
    check({**want, "notes": "changed"})

    bridge.command("PATCH", url, {"form_ids": [form_b, form_a], "assign_team_id": team, "assign_staff_id": 0})
    check({**want, "notes": "changed", "form_ids": [form_b, form_a], "assign_staff_id": 0, "assign_team_id": team})
    bridge.command("PATCH", url, {"form_ids": [form_b], "no_auto_response": False, "status": "archived"})
    check({**want, "notes": "changed", "form_ids": [form_b], "assign_staff_id": 0, "assign_team_id": team,
           "no_auto_response": False, "status": "archived"})
    bridge.command("PATCH", url, {"form_ids": [], "assign_team_id": 0, "custom_numbers": False, "status": "active"})
    got = bridge.read(url)
    assert got["form_ids"] == [] and got["assign_team_id"] == 0 and got["custom_numbers"] is False

    # the core "Ticket Details" form (id 2, type T) can be attached but never detached
    bridge.command("PATCH", url, {"form_ids": [2, form_a]})
    assert bridge.read(url)["form_ids"] == [2, form_a]
    bridge.command("PATCH", url, {"form_ids": [form_a]})
    assert bridge.read(url)["form_ids"] == [2, form_a]

    bridge.refuses("PATCH", url, {"form_ids": [999999]}, because="Form not found")
    bridge.refuses("PATCH", url, {"assign_staff_id": 999999}, because="Staff not found")
    bridge.refuses("PATCH", url, {"custom_numbers": True, "number_format": "no-hash"}, because="hash character")


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


def test_form_fields_are_added_changed_and_removed(bridge, uniq):
    fid = bridge.create("/api/v1/forms", {"title": f"e2e-fields-{uniq}", "type": "G"}, cleanup="/api/v1/forms/{id}")
    url = f"/api/v1/forms/{fid}"

    def fields():
        return {f["name"]: f for f in bridge.read(url)["fields"]}

    asset = bridge.command("POST", f"{url}/fields", {
        "label": "Asset tag", "type": "text", "name": "asset", "mode": "required_for_agents", "hint": "scan it",
        "configuration": {"size": 20, "length": 40, "validator": "regex", "regex": "^A-", "validator_error": "bad tag"},
    })["result"]["field_id"]
    bridge.command("POST", f"{url}/fields", {"label": "Kind", "type": "choices", "name": "kind",
                                              "configuration": {"choices": "a:Alpha\nb:Beta", "default": "a"}})
    bridge.command("POST", f"{url}/fields", {"label": "Urgent", "type": "bool", "name": "urgent",
                                              "configuration": {"desc": "Needs attention"}})
    bridge.command("POST", f"{url}/fields", {"label": "Notes", "type": "memo", "name": "details", "mode": "internal_optional"})
    got = fields()
    assert list(got) == ["asset", "kind", "urgent", "details"]                     # added in order
    a = got["asset"]
    assert (a["mode"], a["hint"], a["is_required"], a["is_enabled"], a["is_deletable"]) == (
        "required_for_agents", "scan it", True, True, True)
    assert (a["configuration"]["size"], a["configuration"]["validator"], a["configuration"]["regex"],
            a["configuration"]["validator_error"]) == (20, "regex", "^A-", "bad tag")
    assert got["kind"]["configuration"]["choices"] == "a:Alpha\nb:Beta" and got["kind"]["mode"] == "optional"
    assert got["urgent"]["configuration"]["desc"] == "Needs attention"
    assert got["details"]["mode"] == "internal_optional"

    # partial updates: the configuration is merged, everything else stays
    bridge.command("PATCH", f"{url}/fields/{asset}", {"label": "Asset tag (new)", "configuration": {"size": 30}})
    a = fields()["asset"]
    assert (a["label"], a["mode"], a["hint"]) == ("Asset tag (new)", "required_for_agents", "scan it")
    assert (a["configuration"]["size"], a["configuration"]["length"], a["configuration"]["regex"]) == (30, 40, "^A-")
    bridge.command("PATCH", f"{url}/fields/{asset}", {"mode": "optional", "is_enabled": False, "sort": 99, "hint": ""})
    a = fields()["asset"]
    assert (a["mode"], a["is_enabled"], a["sort"], a["hint"]) == ("optional", False, 99, "")
    assert list(fields())[-1] == "asset"                                           # sort order applies
    bridge.command("PATCH", f"{url}/fields/{asset}", {"type": "memo", "is_enabled": True})
    a = fields()["asset"]
    assert a["type"] == "memo" and a["configuration"]["regex"] is None             # the old type's options are dropped

    # refusals, none of which may change anything
    bridge.refuses("POST", f"{url}/fields", {"label": "Dup", "type": "text", "name": "KIND"}, because="not unique")
    bridge.refuses("POST", f"{url}/fields", {"label": "Req", "type": "text", "mode": "required"}, because="Variable name is required")
    bridge.refuses("POST", f"{url}/fields", {"label": "Opt", "type": "bool", "name": "o", "configuration": {"size": 5}},
                   because="does not apply")
    bridge.refuses("POST", f"{url}/fields", {"label": "Rx", "type": "text", "name": "rx", "configuration": {"validator": "regex"}},
                   because="needs a regex")
    bridge.refuses("POST", f"{url}/fields", {"label": "Lst", "type": "list-99999", "name": "lst"}, because="Unknown field type")
    bridge.refuses("PATCH", f"{url}/fields/999999", {"label": "x"}, because="not a field of this form")
    assert list(fields()) == ["kind", "urgent", "details", "asset"]

    bridge.command("DELETE", f"{url}/fields/{asset}")
    assert "asset" not in fields()
    bridge.command("DELETE", f"{url}/fields/{fields()['kind']['id']}?delete_data=true")
    assert list(fields()) == ["urgent", "details"]


def test_core_form_fields_keep_their_locks(bridge):
    # form 2 is "Ticket Details": subject / message / priority are osTicket's own
    form = bridge.read("/api/v1/forms/2")
    subject = next(f for f in form["fields"] if f["name"] == "subject")
    assert subject["is_deletable"] is False and subject["is_name_locked"] is True
    url = f"/api/v1/forms/2/fields/{subject['id']}"
    bridge.refuses("DELETE", url, None, because="core form")
    bridge.refuses("PATCH", url, {"name": "headline"}, because="variable name")
    bridge.refuses("PATCH", url, {"type": "bool"}, because="cannot be changed")
    again = next(f for f in bridge.read("/api/v1/forms/2")["fields"] if f["name"] == "subject")
    assert (again["label"], again["type"], again["mode"]) == (subject["label"], subject["type"], subject["mode"])


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


def test_department_settings_round_trip_and_partial_updates_keep_them(bridge, uniq):
    did = bridge.create(
        "/api/v1/departments",
        {"name": f"e2e-dset-{uniq}", "is_public": False, "parent_id": 1, "sla_id": 1, "signature": "regards",
         "status": "disabled", "assignment": "members", "disable_auto_claim": True, "alerts_to": "admin_only",
         "ticket_auto_response": False, "message_auto_response": True,
         "members": [{"staff_id": 1, "role_id": 1, "alerts": False}]},
        cleanup="/api/v1/departments/{id}",
    )
    url = f"/api/v1/departments/{did}"
    want = {"is_public": False, "parent_id": 1, "sla_id": 1, "signature": "regards", "status": "disabled",
            "assignment": "members", "disable_auto_claim": True, "disable_reopen_auto_assign": False,
            "alerts_to": "admin_only", "ticket_auto_response": False, "message_auto_response": True,
            "members": [{"staff_id": 1, "role_id": 1, "alerts": False}]}

    def check(expected):
        got = bridge.read(url)
        assert {k: got[k] for k in expected} == expected, got

    check(want)

    bridge.command("PATCH", url, {"name": f"e2e-dset2-{uniq}"})          # nothing else may move
    check(want)

    bridge.command("PATCH", url, {"status": "active", "assignment": "primary", "alerts_to": "dept_and_extended",
                                  "disable_reopen_auto_assign": True, "sla_id": 0, "parent_id": 0})
    got = bridge.read(url)
    assert (got["status"], got["assignment"], got["alerts_to"], got["disable_reopen_auto_assign"]) == (
        "active", "primary", "dept_and_extended", True)
    assert not got["sla_id"] and got["parent_id"] is None
    assert got["disable_auto_claim"] is True and got["members"] == want["members"]   # untouched

    bridge.command("PATCH", url, {"members": []})
    assert bridge.read(url)["members"] == []
    bridge.refuses("PATCH", url, {"members": [{"staff_id": 999999, "role_id": 1}]}, because="Staff not found")
    bridge.refuses("PATCH", url, {"sla_id": 999999}, because="Invalid SLA")
    bridge.refuses("PATCH", url, {"parent_id": did}, because="cannot contain")


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


def test_organization_settings_round_trip_and_partial_updates_keep_them(bridge, uniq):
    oid = bridge.create("/api/v1/orgs", {"name": f"e2e-orgset-{uniq}", "address": "kept"}, cleanup="/api/v1/orgs/{id}")
    member = bridge.create("/api/v1/users", {"name": "E2E Member", "email": _email(uniq, "mem"), "org_id": oid},
                           cleanup="/api/v1/users/{id}")
    other = bridge.create("/api/v1/users", {"name": "E2E Member 2", "email": _email(uniq, "mem2"), "org_id": oid},
                          cleanup="/api/v1/users/{id}")
    team = bridge.create("/api/v1/teams", {"name": f"e2e-orgteam-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    url = f"/api/v1/orgs/{oid}"
    base = bridge.read(url)     # osTicket gives a new organization its own sharing / collaboration defaults
    assert (base["domain"], base["manager_staff_id"], base["primary_contact_ids"]) == ("", 0, [])

    bridge.command("PATCH", url, {"manager_staff_id": 1, "domain": "e2e-org.example", "sharing": "primary_contacts",
                                  "collab_primary_contacts": True, "assign_account_manager": True,
                                  "collab_all_members": False, "primary_contact_ids": [member]})
    want = {"manager_staff_id": 1, "manager_team_id": 0, "domain": "e2e-org.example", "sharing": "primary_contacts",
            "collab_primary_contacts": True, "collab_all_members": False, "assign_account_manager": True,
            "primary_contact_ids": [member], "address": "kept", "name": f"e2e-orgset-{uniq}"}

    def check(expected):
        got = bridge.read(url)
        assert {k: got[k] for k in expected} == expected, got

    check(want)
    bridge.command("PATCH", url, {"website": "https://w.example"})        # form edit: settings untouched
    check(want)
    bridge.command("PATCH", url, {"address": "moved"})
    check({**want, "address": "moved"})

    bridge.command("PATCH", url, {"manager_team_id": team, "sharing": "all_members", "collab_all_members": True,
                                  "collab_primary_contacts": False, "primary_contact_ids": [member, other]})
    check({**want, "address": "moved", "manager_staff_id": 0, "manager_team_id": team, "sharing": "all_members",
           "collab_all_members": True, "collab_primary_contacts": False, "primary_contact_ids": [member, other]})
    bridge.command("PATCH", url, {"sharing": "disabled", "domain": "", "manager_team_id": 0, "primary_contact_ids": []})
    got = bridge.read(url)
    assert (got["sharing"], got["domain"], got["manager_team_id"], got["primary_contact_ids"]) == ("disabled", "", 0, [])
    assert got["collab_all_members"] is True and got["assign_account_manager"] is True     # not mentioned: kept

    bridge.refuses("PATCH", url, {"manager_staff_id": 999999}, because="Staff not found")
    bridge.refuses("PATCH", url, {"manager_team_id": 999999}, because="Team not found")
    bridge.refuses("PATCH", url, {"domain": "not a domain"}, because="valid email domain")
    bridge.refuses("PATCH", url, {"primary_contact_ids": [1]}, because="not a member")


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


def test_user_account_lifecycle(bridge, uniq):
    uid = bridge.create("/api/v1/users", {"name": "E2E Acct", "email": _email(uniq, "acct")}, cleanup="/api/v1/users/{id}")
    url = f"/api/v1/users/{uid}/account"
    assert bridge.read(f"/api/v1/users/{uid}")["account"] is None
    bridge.refuses("PATCH", url, {"is_locked": True}, because="no account")
    bridge.refuses("POST", f"{url}/password-reset", None, because="no account")
    bridge.refuses("POST", url, {"password": "short"}, because="passwd1")        # osTicket's password policy

    bridge.command("POST", url, {"password": "Tmp-Pass-9x!Qz", "username": f"e2eacct{uniq}",
                                 "require_password_reset": True})
    acct = bridge.read(f"/api/v1/users/{uid}")["account"]
    assert (acct["status"], acct["is_confirmed"], acct["username"], acct["require_password_reset"],
            acct["forbid_password_reset"]) == ("active", True, f"e2eacct{uniq}", True, False)
    assert "password" not in acct and "passwd" not in acct
    bridge.refuses("POST", url, {"password": "Tmp-Pass-9x!Qz"}, because="already registered")

    bridge.command("PATCH", url, {"is_locked": True})
    acct = bridge.read(f"/api/v1/users/{uid}")["account"]
    assert (acct["status"], acct["is_locked"], acct["require_password_reset"]) == ("locked", True, True)   # flag kept
    bridge.command("PATCH", url, {"is_locked": False, "require_password_reset": False, "forbid_password_reset": True,
                                  "timezone": "UTC"})
    acct = bridge.read(f"/api/v1/users/{uid}")["account"]
    assert (acct["status"], acct["require_password_reset"], acct["forbid_password_reset"], acct["username"]) == (
        "active", False, True, f"e2eacct{uniq}")
    bridge.command("PATCH", url, {"password": "New-Pass-7y!Wq"})
    assert bridge.read(f"/api/v1/users/{uid}")["account"]["status"] == "active"
    bridge.refuses("PATCH", url, {"password": "x"}, because="passwd1")
    bridge.refuses("POST", f"{url}/confirmation", None, because="already confirmed")


def test_user_account_activation_email_flow(bridge, uniq):
    uid = bridge.create("/api/v1/users", {"name": "E2E Mail", "email": _email(uniq, "mail")}, cleanup="/api/v1/users/{id}")
    url = f"/api/v1/users/{uid}/account"
    bridge.command("POST", url, {"send_email": True})
    acct = bridge.read(f"/api/v1/users/{uid}")["account"]
    assert (acct["status"], acct["is_confirmed"]) == ("pending", False)          # waits for the user to activate
    bridge.command("POST", f"{url}/confirmation")
    bridge.command("POST", f"{url}/password-reset")


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


def test_staff_access_settings_are_written_read_back_and_take_effect(bridge, uniq):
    username, password = f"e2eacc{uniq}", "E2e-Pass-9x!Qz"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": "Access", "email": _email(uniq, "acc"),
         "dept_id": 1, "role_id": 3, "password": password, "phone_ext": "12", "notes": "start"},
        cleanup="/api/v1/staff/{id}",
    )
    url = f"/api/v1/staff/{sid}"
    got = bridge.read(url)
    assert (got["phone_ext"], got["notes"], got["permissions"], got["department_access"]) == ("12", "start", [], [])
    assert (got["is_visible"], got["on_vacation"], got["assigned_only"]) == (True, False, False)

    headers = {"Authorization": f"Bearer {login_raw(username, password).json()['access_token']}"}
    def me():
        return httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30).json()
    assert me()["global_permissions"] == [] and me()["department_ids"] == [1]

    bridge.command("PATCH", url, {"permissions": ["user.dir", "faq.manage"], "assigned_only": True,
                                  "on_vacation": True, "is_visible": False, "phone_ext": "34",
                                  "department_access": [{"dept_id": 2, "role_id": 1, "alerts": False}]})
    got = bridge.read(url)
    assert got["permissions"] == ["faq.manage", "user.dir"]
    assert (got["assigned_only"], got["on_vacation"], got["is_visible"], got["phone_ext"]) == (True, True, False, "34")
    assert got["department_access"] == [{"dept_id": 2, "role_id": 1, "alerts": False}]
    assert (got["firstname"], got["notes"], got["role_id"], got["dept_id"]) == ("E2E", "start", 3, 1)   # untouched
    time.sleep(6)                                              # the per-process access cache (5 s)
    seen = me()
    assert sorted(seen["global_permissions"]) == ["faq.manage", "user.dir"]
    assert 2 in seen["department_ids"]                         # the extra department is now visible

    bridge.command("PATCH", url, {"firstname": "Renamed"})     # an unrelated edit keeps all of it
    got = bridge.read(url)
    assert got["permissions"] == ["faq.manage", "user.dir"] and got["assigned_only"] is True
    assert got["department_access"] == [{"dept_id": 2, "role_id": 1, "alerts": False}]

    bridge.command("PATCH", url, {"permissions": [], "department_access": [], "assigned_only": False})
    got = bridge.read(url)
    assert got["permissions"] == [] and got["department_access"] == [] and got["assigned_only"] is False
    bridge.refuses("PATCH", url, {"department_access": [{"dept_id": 999999, "role_id": 1}]}, because="Department not found")
    bridge.refuses("PATCH", url, {"department_access": [{"dept_id": 2, "role_id": 999999}]}, because="Role not found")


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

    bridge.command("POST", f"/api/v1/tasks/{tid}/reply", {"message": "an update for collaborators"})
    assert any("an update for collaborators" in e["body"] for e in bridge.read(f"/api/v1/tasks/{tid}")["thread"])

    # claim (held by me already -> allowed), team assignment, transfer
    bridge.command("POST", f"/api/v1/tasks/{tid}/claim", {"note": "mine"})
    team = bridge.create("/api/v1/teams", {"name": f"e2e-task-team-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    bridge.command("POST", f"/api/v1/tasks/{tid}/assign", {"team_id": team})
    got = bridge.read(f"/api/v1/tasks/{tid}")
    assert got["team_id"] == team
    bridge.command("POST", f"/api/v1/tasks/{tid}/transfer", {"dept_id": 1, "note": "back"})
    assert bridge.read(f"/api/v1/tasks/{tid}")["dept_id"] == 1
    bridge.refuses("POST", f"/api/v1/tasks/{tid}/transfer", {"dept_id": 1}, because="already in the department")
    bridge.refuses("POST", f"/api/v1/tasks/{tid}/assign", {"team_id": team}, because="already assigned")
    bridge.refuses("POST", f"/api/v1/tasks/{tid}/assign", {"team_id": 999999}, because="Team not found")

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

    url = f"/api/v1/tickets/{tk}/collaborators"
    listed = bridge.read(url)["collaborators"]
    assert [(c["user_id"], c["is_active"], c["is_cc"]) for c in listed] == [(collab, True, True)]
    bridge.command("PATCH", f"{url}/{collab}", {"is_active": False})
    listed = bridge.read(url)["collaborators"]
    assert (listed[0]["is_active"], listed[0]["is_cc"]) == (False, True)     # only the one flag changed
    bridge.command("PATCH", f"{url}/{collab}", {"is_active": True, "is_cc": False})
    listed = bridge.read(url)["collaborators"]
    assert (listed[0]["is_active"], listed[0]["is_cc"]) == (True, False)
    bridge.refuses("PATCH", f"{url}/999999", {"is_active": False}, because="not a collaborator")
    bridge.command("DELETE", f"{url}/{collab}")
    assert bridge.read(url)["collaborators"] == []
    bridge.refuses("DELETE", f"{url}/{collab}", None, because="not a collaborator")
    bridge.command("POST", f"/api/v1/tickets/{tk}/collaborators", {"user_id": collab})   # back, as the rest of the test expects

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


def test_ticket_patch_edits_fields_and_keeps_the_rest(bridge, uniq):
    holder = {}
    tk = _new_ticket(bridge, uniq, holder)
    before = bridge.read(f"/api/v1/tickets/{tk}")

    bridge.command("PATCH", f"/api/v1/tickets/{tk}", {"subject": f"edited {uniq}", "priority_id": 3})
    got = bridge.read(f"/api/v1/tickets/{tk}")
    assert (got["subject"], got["priority_id"]) == (f"edited {uniq}", 3)
    assert (got["topic_id"], got["source"], got["user_id"]) == (before["topic_id"], before["source"], before["user_id"])

    bridge.command("PATCH", f"/api/v1/tickets/{tk}", {"due_date": "2031-03-04T05:06:07Z", "source": "Phone"})
    got = bridge.read(f"/api/v1/tickets/{tk}")
    assert got["due_date"].startswith("2031-03-04T05:06:07") and got["source"] == "Phone"
    assert (got["subject"], got["priority_id"]) == (f"edited {uniq}", 3)   # untouched by the second edit

    bridge.command("PATCH", f"/api/v1/tickets/{tk}", {"note": "only a note"})
    assert bridge.read(f"/api/v1/tickets/{tk}")["due_date"].startswith("2031-03-04T05:06:07")

    bridge.command("PATCH", f"/api/v1/tickets/{tk}", {"due_date": None})
    assert bridge.read(f"/api/v1/tickets/{tk}")["due_date"] is None

    bridge.refuses("PATCH", f"/api/v1/tickets/{tk}", {"priority_id": 9999}, because="Priority not found")
    bridge.refuses("PATCH", f"/api/v1/tickets/{tk}", {"topic_id": 9999}, because="topic not found")
    bridge.refuses("PATCH", f"/api/v1/tickets/{tk}", {"due_date": "2001-01-01T00:00:00Z"}, because="must be in the future")


def test_ticket_claim_release_and_refer(bridge, uniq):
    holder = {}
    tk = _new_ticket(bridge, uniq, holder)
    url = f"/api/v1/tickets/{tk}"
    bridge.refuses("POST", f"{url}/release", {"staff": True}, because="not assigned")

    bridge.command("POST", f"{url}/claim", {"note": "mine"})
    assert bridge.read(url)["assignee_staff_id"] == 1
    bridge.refuses("POST", f"{url}/claim", {}, because="already assigned")

    team = bridge.create("/api/v1/teams", {"name": f"e2e-claim-team-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    bridge.command("POST", f"{url}/assign-team", {"team_id": team})
    bridge.command("POST", f"{url}/release", {"team": True, "note": "team off"})
    got = bridge.read(url)
    assert got["team_id"] is None and got["assignee_staff_id"] == 1     # only the team was dropped
    bridge.refuses("POST", f"{url}/release", {"team": True}, because="not assigned to a team")

    bridge.command("POST", f"{url}/assign-team", {"team_id": team})
    bridge.command("POST", f"{url}/release", {"staff": True, "team": True})   # = unassign
    got = bridge.read(url)
    assert got["team_id"] is None and got["assignee_staff_id"] is None

    bridge.command("POST", f"{url}/refer", {"dept_id": 2, "note": "fyi"})
    bridge.command("POST", f"{url}/refer", {"team_id": team})
    bridge.refuses("POST", f"{url}/refer", {"dept_id": 1}, because="already in the department")
    bridge.refuses("POST", f"{url}/refer", {"agent_id": 99999}, because="Unknown selection")


def test_ticket_thread_entry_edit_and_resend(bridge, uniq):
    holder = {}
    tk = _new_ticket(bridge, uniq, holder)
    url = f"/api/v1/tickets/{tk}"
    bridge.command("POST", f"{url}/reply", {"message": f"first draft {uniq}"})
    bridge.command("POST", f"{url}/note", {"title": "nt", "message": f"a note {uniq}"})

    def entries():
        return bridge.read(url)["thread"]

    reply = next(e for e in entries() if e["type"] == "R")
    note = next(e for e in entries() if e["type"] == "N")
    assert reply["is_edited"] is False

    done = bridge.command("PATCH", f"{url}/thread/{reply['id']}", {"body": f"second draft {uniq}"})
    assert done["result"]["changed"] is True and done["result"]["new_entry_id"] != reply["id"]
    thread = entries()
    replies = [e for e in thread if e["type"] == "R"]
    assert len(replies) == 1                                   # the superseded version is hidden
    assert replies[0]["id"] == done["result"]["new_entry_id"] and replies[0]["is_edited"] is True
    assert f"second draft {uniq}" in replies[0]["body"]
    assert any(e["id"] == note["id"] for e in thread)          # other entries untouched

    same = bridge.command("PATCH", f"{url}/thread/{replies[0]['id']}", {"body": replies[0]["body"]})
    assert same["result"]["changed"] is False

    bridge.command("POST", f"{url}/thread/{replies[0]['id']}/resend", {"signature": "none"})
    bridge.refuses("POST", f"{url}/thread/{note['id']}/resend", {}, because="staff reply")
    bridge.refuses("PATCH", f"{url}/thread/{note['id']}", {"body": "x", "resend": True}, because="staff reply")
    bridge.refuses("PATCH", f"{url}/thread/999999", {"body": "x"}, because="not found")


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
