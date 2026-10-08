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


def test_team_alert_flags_round_trip_and_survive_other_edits(bridge, uniq):
    tid = bridge.create("/api/v1/teams", {"name": f"e2e-talert-{uniq}", "members": [1], "no_alerts": True,
                                          "member_alerts": {"1": False}}, cleanup="/api/v1/teams/{id}")
    url = f"/api/v1/teams/{tid}"
    got = bridge.read(url)
    assert got["no_alerts"] is True and got["member_alerts"] == {"1": False}

    bridge.command("PATCH", url, {"notes": "other edit"})                      # nothing else is reset
    got = bridge.read(url)
    assert got["no_alerts"] is True and got["member_alerts"] == {"1": False} and got["notes"] == "other edit"

    bridge.command("PATCH", url, {"no_alerts": False, "member_alerts": {"1": True}})
    got = bridge.read(url)
    assert got["no_alerts"] is False and got["member_alerts"] == {"1": True}

    bridge.command("PATCH", url, {"members": []})                                # removing the member drops the entry
    assert bridge.read(url)["member_alerts"] == {}
    bridge.refuses("PATCH", url, {"member_alerts": {"1": True}}, because="not a member")


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


def test_an_idempotency_key_reused_for_a_different_request_is_refused(bridge, uniq):
    ticket = _new_ticket(bridge, uniq, {}, "idem")
    url, key = f"/api/v1/tickets/{ticket}/note", {"Idempotency-Key": f"e2e-idem-{uniq}"}
    first = bridge.client.post(url, json={"title": "t", "message": "one"}, headers=key)
    again = bridge.client.post(url, json={"title": "t", "message": "one"}, headers=key)
    assert first.status_code == again.status_code == 202
    assert first.json()["command_id"] == again.json()["command_id"]
    clash = bridge.client.post(url, json={"title": "t", "message": "a different note"}, headers=key)
    assert clash.status_code == 422 and "different request" in clash.json()["detail"]
    other_ticket = bridge.client.post(f"/api/v1/tickets/{ticket + 1}/note", json={"title": "t", "message": "one"}, headers=key)
    assert other_ticket.status_code == 422                      # same body, different resource
    assert bridge.read(f"/api/v1/commands/{first.json()['command_id']}?wait=25")["status"] == "done"   # let it finish before cleanup


def test_changing_a_password_ends_the_sessions_opened_before_it(bridge, uniq):
    username, old, new = f"e2erev{uniq}", "E2e-Pass-9x!Qz", "E2e-New-7y!Wq"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": "Revoke", "email": _email(uniq, "rev"),
         "dept_id": 1, "role_id": 3, "password": old},
        cleanup="/api/v1/staff/{id}",
    )
    time.sleep(1.2)                                     # a token is dated to the second; osTicket stamps the change likewise
    token = login_raw(username, old).json()["access_token"]
    me = lambda t: httpx.get(f"{BASE_URL}/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"}, timeout=30)
    assert me(token).status_code == 200

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"firstname": "Renamed"})
    assert me(token).status_code == 200                 # an unrelated edit leaves the session alone

    time.sleep(1.2)
    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"password": new})
    refused = me(token)
    assert refused.status_code == 401 and "password changed" in refused.json()["detail"]
    assert login_raw(username, old).status_code == 401
    fresh = login_raw(username, new)
    assert fresh.status_code == 200 and me(fresh.json()["access_token"]).status_code == 200


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

    bridge.command("PATCH", f"/api/v1/tasks/{tid}", {"title": f"e2e renamed {uniq}", "note": "why I renamed it"})
    got = bridge.read(f"/api/v1/tasks/{tid}")
    assert got["title"] == f"e2e renamed {uniq}"
    assert any("why I renamed it" in e["body"] for e in got["thread"])      # osTicket's "reason for editing"

    bridge.command("POST", f"/api/v1/tasks/{tid}/assign", {"staff_id": 1, "note": "yours"})
    assert bridge.read(f"/api/v1/tasks/{tid}")["assignee_staff_id"] == 1

    bridge.command("POST", f"/api/v1/tasks/{tid}/note", {"title": "t", "note": "a task note"})
    assert any("a task note" in e["body"] for e in bridge.read(f"/api/v1/tasks/{tid}")["thread"])

    bridge.command("POST", f"/api/v1/tasks/{tid}/reply", {"message": "an update for collaborators"})
    assert any("an update for collaborators" in e["body"] for e in bridge.read(f"/api/v1/tasks/{tid}")["thread"])

    turl = f"/api/v1/tasks/{tid}"
    collab = bridge.create("/api/v1/users", {"name": "E2E TaskCollab", "email": _email(uniq, "tcollab")}, cleanup="/api/v1/users/{id}")
    bridge.command("POST", f"{turl}/collaborators", {"user_id": collab})
    listed = bridge.read(f"{turl}/collaborators")["collaborators"]
    assert [(c["user_id"], c["is_cc"]) for c in listed] == [(collab, True)]
    assert [c["user_id"] for c in bridge.read(turl)["collaborators"]] == [collab]
    bridge.refuses("POST", f"{turl}/collaborators", {"user_id": 999999}, because="Unknown user")
    update = next(e for e in bridge.read(turl)["thread"] if "an update for collaborators" in e["body"])
    done = bridge.command("PATCH", f"{turl}/thread/{update['id']}", {"body": "an edited update"})
    assert done["result"]["changed"] is True and done["result"]["new_entry_id"] != update["id"]
    thread = bridge.read(turl)["thread"]
    assert not any("an update for collaborators" in e["body"] for e in thread)       # superseded version hidden
    edited = next(e for e in thread if e["id"] == done["result"]["new_entry_id"])
    assert edited["is_edited"] is True and "an edited update" in edited["body"]
    assert any("a task note" in e["body"] for e in thread)                            # others untouched
    if edited["type"] == "R":
        bridge.command("POST", f"{turl}/thread/{edited['id']}/resend", {"signature": "none"})
    bridge.refuses("PATCH", f"{turl}/thread/999999", {"body": "x"}, because="not found")

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

    refs = bridge.read(f"{url}/referrals")["referrals"]
    assert [(r["referee_type"], r["referee_id"]) for r in refs] == [("department", 2), ("team", team)]
    assert refs[1]["name"] == f"e2e-claim-team-{uniq}" and refs[0]["name"]
    bridge.command("DELETE", f"{url}/referrals/{refs[0]['id']}")
    left = bridge.read(f"{url}/referrals")["referrals"]
    assert [(r["referee_type"], r["referee_id"]) for r in left] == [("team", team)]    # only that one went
    bridge.refuses("DELETE", f"{url}/referrals/{refs[0]['id']}", None, because="Referral not found")


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


def _answers(obj) -> dict:
    return {a["name"]: a for a in obj["custom_fields"]}


def test_ticket_custom_fields_from_a_help_topic_form(bridge, uniq):
    form = bridge.create("/api/v1/forms", {"title": f"e2e-tkform-{uniq}", "type": "G"}, cleanup="/api/v1/forms/{id}")
    furl = f"/api/v1/forms/{form}"
    bridge.command("POST", f"{furl}/fields", {"label": "Asset", "type": "text", "name": "asset", "mode": "required_for_agents"})
    bridge.command("POST", f"{furl}/fields", {"label": "Kind", "type": "choices", "name": "kind",
                                              "configuration": {"choices": "a:Alpha\nb:Beta"}})
    bridge.command("POST", f"{furl}/fields", {"label": "Urgent", "type": "bool", "name": "urgent"})
    bridge.command("POST", f"{furl}/fields", {"label": "Memo", "type": "memo", "name": "memo"})
    topic = bridge.create("/api/v1/topics", {"topic": f"e2e-tkt-{uniq}", "dept_id": 1, "form_ids": [2, form]},
                          cleanup="/api/v1/topics/{id}")

    email = _email(uniq, "cf")
    # a required custom field left out is refused by osTicket's own form validation
    bridge.refuses("POST", "/api/v1/tickets/", {"subject": f"cf {uniq}", "message": "m", "email": email, "name": "CF",
                                                 "dept_id": 1, "topic_id": topic}, because="required")
    tk = bridge.create("/api/v1/tickets/", {"subject": f"cf {uniq}", "message": "m", "email": email, "name": "CF",
                                            "dept_id": 1, "topic_id": topic,
                                            "custom_fields": {"asset": "A-100", "kind": "a", "urgent": True, "memo": "long text"}})
    holder = bridge.read(f"/api/v1/tickets/{tk}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk}"))
    got = _answers(holder)
    assert set(got) == {"asset", "kind", "urgent", "memo"}                 # subject / message / priority are not repeated
    assert got["asset"]["value"] == "A-100" and got["memo"]["value"] == "long text"
    assert "Alpha" in got["kind"]["value"]
    assert got["urgent"]["value"] in ("1", "true", "True")
    # typed: the client does not decode osTicket's JSON
    assert (got["kind"]["kind"], [(c["key"], c["label"]) for c in got["kind"]["choices"]]) == ("choices", [("a", "Alpha")])
    assert (got["urgent"]["kind"], got["urgent"]["bool_value"]) == ("bool", True)
    assert (got["asset"]["kind"], got["asset"]["choices"], got["asset"]["bool_value"]) == ("text", None, None)

    # the same answers as list columns: only the named fields, on every row, none unless asked
    listed = bridge.read(f"/api/v1/tickets/?q={uniq}&fields=asset,kind")["items"]
    assert [t["ticket_id"] for t in listed] == [tk]
    assert {a["name"]: a["value"] for a in listed[0]["custom_fields"]}["asset"] == "A-100"
    assert {a["name"] for a in listed[0]["custom_fields"]} == {"asset", "kind"}
    assert bridge.read(f"/api/v1/tickets/?q={uniq}")["items"][0]["custom_fields"] is None
    assert bridge.get("/api/v1/tickets/?fields=bad%20name").status_code == 422

    url = f"/api/v1/tickets/{tk}"
    bridge.command("PATCH", url, {"custom_fields": {"asset": "A-200", "kind": "b", "urgent": False, "memo": ""}})
    got = _answers(bridge.read(url))
    assert got["asset"]["value"] == "A-200" and "Beta" in got["kind"]["value"]
    assert got["urgent"]["value"] in (None, "", "0", "false", "False") and not got["memo"]["value"]
    assert got["kind"]["choices"][0]["key"] == "b" and got["urgent"]["bool_value"] is False

    bridge.command("PATCH", url, {"custom_fields": {"memo": "again"}, "source": "Phone"})   # others untouched
    got = _answers(bridge.read(url))
    assert got["memo"]["value"] == "again" and got["asset"]["value"] == "A-200"

    # a field added to the form after the ticket existed is listed without an answer, and can be answered
    bridge.command("POST", f"{furl}/fields", {"label": "Late", "type": "text", "name": "late"})
    late = _answers(bridge.read(url))["late"]
    assert (late["label"], late["value"], late["kind"]) == ("Late", None, "text")
    assert _answers(bridge.read(url))["asset"]["value"] == "A-200"
    bridge.command("PATCH", url, {"custom_fields": {"late": "answered"}})
    got = _answers(bridge.read(url))
    assert got["late"]["value"] == "answered" and got["asset"]["value"] == "A-200"

    # mass assignment: a custom field may not smuggle in a key that Ticket::create() acts on
    base = {"subject": f"ma {uniq}", "message": "m", "email": email, "name": "CF", "dept_id": 1, "topic_id": topic}
    for key in ("assignId", "staffId", "teamId", "slaId", "ccs", "statusId", "emailId", "autorespond"):
        bridge.refuses("POST", "/api/v1/tickets/", {**base, "custom_fields": {"asset": "A-1", key: "1"}},
                       because="Custom fields rejected")
    bridge.refuses("POST", "/api/v1/tickets/", {**base, "custom_fields": {"asset": "A-1", "nosuch": "x"}},
                   because="no 'nosuch' field")

    bridge.refuses("PATCH", url, {"custom_fields": {"nonexistent": "x"}}, because="no 'nonexistent' field")
    bridge.refuses("PATCH", url, {"custom_fields": {"kind": "zzz"}}, because="is not one of the choices")
    bridge.refuses("PATCH", url, {"custom_fields": {"asset": ""}}, because="required")
    assert _answers(bridge.read(url))["asset"]["value"] == "A-200"          # a refused edit changes nothing


def test_user_organization_and_task_custom_fields(bridge, uniq):
    # the user / organization / task forms are osTicket's own: add a field, use it, and always take it away
    spec = {1: ("users", "U"), 4: ("orgs", "O"), 5: ("tasks", "A")}
    field_ids = {}
    for form_id in spec:
        fid = bridge.command("POST", f"/api/v1/forms/{form_id}/fields",
                             {"label": "E2E probe", "type": "text", "name": f"e2eprobe{uniq[:6]}"})["result"]["field_id"]
        field_ids[form_id] = fid
        bridge._cleanups.append(("DELETE", f"/api/v1/forms/{form_id}/fields/{fid}?delete_data=true"))
    name = f"e2eprobe{uniq[:6]}"

    uid = bridge.create("/api/v1/users", {"name": "E2E CF User", "email": _email(uniq, "cfu"), "custom_fields": {name: "u1"}},
                        cleanup="/api/v1/users/{id}")
    assert _answers(bridge.read(f"/api/v1/users/{uid}"))[name]["value"] == "u1"
    bridge.command("PATCH", f"/api/v1/users/{uid}", {"custom_fields": {name: "u2"}})
    assert _answers(bridge.read(f"/api/v1/users/{uid}"))[name]["value"] == "u2"
    listed = bridge.read(f"/api/v1/users?q=E2E CF User&fields={name}")["items"]
    assert [a["value"] for u in listed if u["id"] == uid for a in u["custom_fields"]] == ["u2"]
    bridge.refuses("PATCH", f"/api/v1/users/{uid}", {"custom_fields": {"nope": "x"}}, because="no 'nope' field")
    got = bridge.read(f"/api/v1/users/{uid}")
    assert "phone" not in _answers(got) and "notes" not in _answers(got)       # built-ins live at the top level

    oid = bridge.create("/api/v1/orgs", {"name": f"e2e-cf-org-{uniq}", "custom_fields": {name: "o1"}}, cleanup="/api/v1/orgs/{id}")
    assert _answers(bridge.read(f"/api/v1/orgs/{oid}"))[name]["value"] == "o1"
    bridge.command("PATCH", f"/api/v1/orgs/{oid}", {"custom_fields": {name: "o2"}, "address": "kept"})
    got = bridge.read(f"/api/v1/orgs/{oid}")
    assert _answers(got)[name]["value"] == "o2" and got["address"] == "kept"
    listed = bridge.read(f"/api/v1/orgs?q=e2e-cf-org-{uniq}&fields={name}")["items"]
    assert [a["value"] for o in listed if o["id"] == oid for a in o["custom_fields"]] == ["o2"]

    tid = bridge.create("/api/v1/tasks/", {"title": f"e2e cf task {uniq}", "description": "d", "dept_id": 1,
                                           "custom_fields": {name: "t1"}}, cleanup="/api/v1/tasks/{id}")
    assert _answers(bridge.read(f"/api/v1/tasks/{tid}"))[name]["value"] == "t1"
    bridge.command("PATCH", f"/api/v1/tasks/{tid}", {"custom_fields": {name: "t2"}})
    got = bridge.read(f"/api/v1/tasks/{tid}")
    assert _answers(got)[name]["value"] == "t2" and got["title"] == f"e2e cf task {uniq}"
    listed = bridge.read(f"/api/v1/tasks/?q={uniq}&fields={name}")["items"]
    assert [a["value"] for t in listed if t["task_id"] == tid for a in t["custom_fields"]] == ["t2"]
    bridge.refuses("POST", "/api/v1/tasks/", {"title": "x", "description": "d", "custom_fields": {"title": "clash"}},
                   because="built-in field")

    # mass assignment: a custom field may not smuggle in a key that Task::create() acts on
    parent = bridge.create("/api/v1/tasks/", {"title": f"e2e ma task {uniq}", "description": "d", "dept_id": 1},
                           cleanup="/api/v1/tasks/{id}")
    for key in ("object_id", "object_type", "staffId", "internal_formdata"):
        bridge.refuses("POST", "/api/v1/tasks/", {"title": f"e2e ma {uniq}", "description": "d", "dept_id": 1,
                                                  "custom_fields": {key: "T" if key == "object_type" else "1"}},
                       because="Custom fields rejected")
    assert bridge.read(f"/api/v1/tasks/{parent}")["title"] == f"e2e ma task {uniq}"


def test_agent_directory_and_assign_form_choices(bridge, uniq):
    team = bridge.create("/api/v1/teams", {"name": f"e2e-dir-team-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    me = bridge.read("/api/v1/auth/me")
    got = bridge.read("/api/v1/agents?page_size=200")
    assert got["total"] >= 1 and me["staff_id"] in {a["id"] for a in got["items"]}
    mine = next(a for a in got["items"] if a["id"] == me["staff_id"])
    assert mine["name"] and mine["email"] and mine["dept_name"]

    first = mine["firstname"][:3]
    assert me["staff_id"] in {a["id"] for a in bridge.read(f"/api/v1/agents?q={first}")["items"]}
    assert bridge.read("/api/v1/agents?q=no-such-agent-zzz")["items"] == []
    assert me["staff_id"] in {a["id"] for a in bridge.read(f"/api/v1/agents?dept_id={mine['dept_id']}")["items"]}
    assert bridge.get("/api/v1/agents?sort=nonsense").status_code == 422
    asc = [a["id"] for a in bridge.read("/api/v1/agents?sort=email")["items"]]
    desc = [a["id"] for a in bridge.read("/api/v1/agents?sort=email&descending=true")["items"]]
    assert asc == desc[::-1]

    # the assign form for a task and a ticket: agents who can be assigned, and teams with an available member
    tid = bridge.create("/api/v1/tasks/", {"title": f"e2e assignees {uniq}", "description": "d", "dept_id": 1},
                        cleanup="/api/v1/tasks/{id}")
    options = bridge.read(f"/api/v1/tasks/{tid}/assignees")
    assert me["staff_id"] in {a["id"] for a in options["agents"]} and team in {t["id"] for t in options["teams"]}
    assert bridge.get("/api/v1/tasks/999999/assignees").status_code == 404

    holder = {}
    ticket = _new_ticket(bridge, uniq, holder, "dir")
    options = bridge.read(f"/api/v1/tickets/{ticket}/assignees")
    assert me["staff_id"] in {a["id"] for a in options["agents"]} and team in {t["id"] for t in options["teams"]}
    # an assignee the form offers is one the assign command accepts
    bridge.command("POST", f"/api/v1/tickets/{ticket}/assign", {"staff_id": options["agents"][0]["id"], "note": "offered"})


def test_ticket_and_task_lists_search_sort_count_and_queues_agree(bridge, uniq):
    holder = {}
    first = _new_ticket(bridge, uniq, holder, "qa")
    second = _new_ticket(bridge, uniq, {}, "qb")
    bridge.command("POST", f"/api/v1/tickets/{second}/close", {"note": "q"})

    # every queue's count equals the total its own list reports; children never exceed their parent
    queues = {q["key"]: q for q in bridge.read("/api/v1/tickets/queues")["queues"]}
    assert list(queues) == ["open", "unanswered", "answered", "overdue", "mine", "assigned_me", "assigned_teams", "closed"]
    for key, queue in queues.items():
        assert bridge.read(f"/api/v1/tickets/?queue={key}&page_size=1")["total"] == queue["count"], key
        if queue["parent"]:
            assert queue["count"] <= queues[queue["parent"]]["count"]
    assert queues["closed"]["count"] >= 1 and queues["open"]["count"] >= 1

    # search by subject text and by ticket number; the total counts across pages
    found = bridge.read(f"/api/v1/tickets/?q=e2e ticket {uniq}&page_size=1")
    assert found["total"] == 2 and len(found["items"]) == 1
    number = bridge.read(f"/api/v1/tickets/{first}")["number"]
    assert first in {t["ticket_id"] for t in bridge.read(f"/api/v1/tickets/?q={number}")["items"]}
    assert bridge.read("/api/v1/tickets/?q=zz-no-such-ticket-zz")["total"] == 0
    assert bridge.read(f"/api/v1/tickets/?q=e2e ticket {uniq}&queue=closed")["total"] == 1
    assert bridge.read("/api/v1/tickets/?q=100%25_")["total"] == 0                     # wildcards are literal

    # sorting
    asc = [t["ticket_id"] for t in bridge.read(f"/api/v1/tickets/?q={uniq}&sort=number&descending=false")["items"]]
    desc = [t["ticket_id"] for t in bridge.read(f"/api/v1/tickets/?q={uniq}&sort=number")["items"]]
    assert asc == desc[::-1] and set(asc) == {first, second}
    for column in ("priority", "assignee", "requester", "status", "dept", "subject", "created", "due_date", "updated"):
        assert bridge.get(f"/api/v1/tickets/?q={uniq}&sort={column}").status_code == 200, column
    # priority ties break by last activity first (osTicket's "Priority + Most Recently Updated" is its `lastupdate`), either direction
    for direction in ("false", "true"):
        rows = bridge.read(f"/api/v1/tickets/?sort=priority&descending={direction}&page_size=100")["items"]
        for prev, nxt in zip(rows, rows[1:]):
            if prev["priority_id"] == nxt["priority_id"]:
                assert (prev["last_activity"] or "") >= (nxt["last_activity"] or ""), (prev["ticket_id"], nxt["ticket_id"])

    # osTicket's own due date / overdue / last-updated, and the body format of a thread entry
    item = bridge.read(f"/api/v1/tickets/{first}")
    assert {"est_due_date", "is_overdue", "last_activity"} <= set(item) and item["last_activity"] is not None
    assert item["is_overdue"] is False
    assert all(e["format"] in ("text", "html") for e in item["thread"])
    by_activity = bridge.read("/api/v1/tickets/?sort=last_activity&page_size=100")["items"]
    stamps = [t["last_activity"] for t in by_activity if t["last_activity"]]
    assert stamps == sorted(stamps, reverse=True)

    # roles (Admin Panel) and the permission keys
    roles = {r["name"]: r for r in bridge.read("/api/v1/roles")["items"]}
    assert "ticket.edit" in roles["All Access"]["permissions"] and roles["All Access"]["enabled"] is True
    kinds = {p["key"]: p["kind"] for p in bridge.read("/api/v1/permissions")["permissions"]}
    assert kinds["user.dir"] == "global" and kinds["task.reply"] == "role"

    # login and /auth/me are typed in the schema (clients generate their types from it)
    schema = bridge.read("/openapi.json") if bridge.get("/openapi.json").status_code == 200 else None
    if schema:
        assert "global_permissions" in schema["components"]["schemas"]["Me"]["properties"]

    # tasks: search, sort and the real total
    t1 = bridge.create("/api/v1/tasks/", {"title": f"e2e qtask {uniq} alpha", "description": "d", "dept_id": 1}, cleanup="/api/v1/tasks/{id}")
    t2 = bridge.create("/api/v1/tasks/", {"title": f"e2e qtask {uniq} beta", "description": "d", "dept_id": 1}, cleanup="/api/v1/tasks/{id}")
    got = bridge.read(f"/api/v1/tasks/?q=qtask {uniq}&sort=title&descending=false&page_size=1")
    assert got["total"] == 2 and [t["task_id"] for t in got["items"]] == [t1]
    assert [t["task_id"] for t in bridge.read(f"/api/v1/tasks/?q=qtask {uniq}&sort=title")["items"]] == [t2, t1]
    assert bridge.read("/api/v1/tasks/?q=zz-no-such-task-zz")["total"] == 0


def test_ticket_merge_combines_children_into_the_parent_and_closes_them(bridge, uniq):
    holder = {}
    parent = _new_ticket(bridge, uniq, holder, "p")
    child = _new_ticket(bridge, uniq, {}, "c")

    bridge.command("POST", f"/api/v1/tickets/{parent}/merge", {"child_ids": [child]})
    assert bridge.read(f"/api/v1/tickets/{child}")["status"]["state"] == "closed"
    assert bridge.read(f"/api/v1/tickets/{parent}")["status"]["state"] == "open"
    related = bridge.read(f"/api/v1/tickets/{parent}/related")
    assert (related["relation"], related["merge_type"], related["is_parent"]) == ("merged", "combine", True)
    assert [c["ticket_id"] for c in related["children"]] == [child]
    assert bridge.read(f"/api/v1/tickets/{child}/related")["parent"]["ticket_id"] == parent

    # a merge is undone through un-merge, not unlink
    bridge.refuses("POST", f"/api/v1/tickets/{parent}/unlink", None, because="not linked")
    bridge.command("POST", f"/api/v1/tickets/{parent}/unmerge")
    assert bridge.read(f"/api/v1/tickets/{parent}/related")["relation"] == "none"
    assert bridge.read(f"/api/v1/tickets/{child}/related")["relation"] == "none"


def test_tickets_can_be_linked_and_unlinked_without_closing_anything(bridge, uniq):
    parent = _new_ticket(bridge, uniq, {}, "lp")
    one, two = _new_ticket(bridge, uniq, {}, "l1"), _new_ticket(bridge, uniq, {}, "l2")
    bridge.command("POST", f"/api/v1/tickets/{parent}/link", {"child_ids": [one, two]})
    rel = bridge.read(f"/api/v1/tickets/{parent}/related")
    assert (rel["relation"], rel["merge_type"], rel["is_parent"]) == ("linked", "visual", True)
    assert sorted(c["ticket_id"] for c in rel["children"]) == sorted([one, two])
    assert all(bridge.read(f"/api/v1/tickets/{t}")["status"]["state"] == "open" for t in (parent, one, two))
    assert bridge.read(f"/api/v1/tickets/{one}/related")["parent"]["ticket_id"] == parent
    bridge.refuses("POST", f"/api/v1/tickets/{parent}/unmerge", None, because="not part of a merge")
    bridge.refuses("POST", f"/api/v1/tickets/{one}/link", {"child_ids": [two]}, because="already linked")

    bridge.command("POST", f"/api/v1/tickets/{one}/unlink")           # a child releases itself only
    assert [c["ticket_id"] for c in bridge.read(f"/api/v1/tickets/{parent}/related")["children"]] == [two]
    bridge.command("POST", f"/api/v1/tickets/{parent}/unlink")        # a parent releases every child
    assert bridge.read(f"/api/v1/tickets/{parent}/related")["relation"] == "none"
    assert bridge.read(f"/api/v1/tickets/{two}/related")["relation"] == "none"


def test_a_reply_can_also_set_the_ticket_status(bridge, uniq):
    tk = _new_ticket(bridge, uniq, {})
    statuses = {s["name"]: s["id"] for s in bridge.read("/api/v1/ticket-statuses")["statuses"]}
    bridge.command("POST", f"/api/v1/tickets/{tk}/reply", {"message": "fixed", "status_id": statuses["Resolved"]})
    assert bridge.read(f"/api/v1/tickets/{tk}")["status"]["name"] == "Resolved"
    bridge.command("POST", f"/api/v1/tickets/{tk}/note", {"message": "reopen", "status_id": statuses["Open"]})
    assert bridge.read(f"/api/v1/tickets/{tk}")["status"]["name"] == "Open"
    bridge.refuses("POST", f"/api/v1/tickets/{tk}/reply", {"message": "x", "status_id": 99999}, because="not found")


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
    stamps = {"naive": [], "utc": 0}

    def scan(path, node):
        """Every timestamp leaves the API as an instant (UTC, Z), never as a bare osTicket datetime."""
        if isinstance(node, dict):
            for value in node.values():
                scan(path, value)
        elif isinstance(node, list):
            for value in node:
                scan(path, value)
        elif isinstance(node, str) and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?", node):
            if node.endswith("Z"):
                stamps["utc"] += 1
            else:
                stamps["naive"].append(f"{path}: {node}")

    for path in sorted(lists):
        resp = bridge.get(path)
        if resp.status_code != 200:
            failures.append(f"{path} -> {resp.status_code}")
            continue
        if "text/csv" in paths[path]["get"]["responses"]["200"].get("content", {}):     # an export is a file, not JSON
            if not resp.headers["content-type"].startswith("text/csv") or not resp.content.startswith(b"\xef\xbb\xbf"):
                failures.append(f"{path} is not a CSV file")
            continue
        body = resp.json()
        scan(path, body)
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
        else:
            scan(path, resp.json())

    assert not failures, failures
    assert not stamps["naive"], stamps["naive"][:10]
    assert stamps["utc"] > 50      # the walk really saw timestamps
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


def test_directory_lists_search_sort_count_and_total(bridge, uniq):
    """Users, organizations, the knowledge base and tasks: osTicket's search, sort, counts and totals."""
    holder = {}
    tk = _new_ticket(bridge, uniq, holder)
    uid = holder["user_id"]

    # global search finds a ticket by its requester (name or email), as the ticket list's own search does
    hits = bridge.read(f"/api/v1/search?q=cust.{uniq}")
    assert any(h["type"] == "ticket" and h["id"] == tk for h in hits["results"]), hits
    assert not bridge.read("/api/v1/search?q=100%25_")["results"]                       # wildcards are literal

    # users: search by name, by any email, total, ticket count, account status (a guest until registered)
    found = bridge.read(f"/api/v1/users?q=cust.{uniq}")
    assert found["total"] == 1 and found["items"][0]["id"] == uid
    assert found["items"][0]["ticket_count"] == 1 and found["items"][0]["account_status"] == "guest"
    assert bridge.read("/api/v1/users?q=zz-no-such-user-zz")["total"] == 0
    assert bridge.read("/api/v1/users?q=100%25_")["total"] == 0                          # wildcards are literal
    assert len({u["id"] for u in bridge.read("/api/v1/users?page_size=200")["items"]}) == len(
        bridge.read("/api/v1/users?page_size=200")["items"])                              # one row per user, whatever its emails
    for column in ("name", "email", "status", "created", "updated"):
        assert bridge.get(f"/api/v1/users?sort={column}&descending=true").status_code == 200, column
    assert bridge.get("/api/v1/users?sort=nonsense").status_code == 422
    names = [u["name"] for u in bridge.read("/api/v1/users?page_size=200")["items"]]
    assert names == sorted(names, key=str.lower) or len(names) < 2                       # default: by name, ascending
    bridge.command("POST", f"/api/v1/users/{uid}/account", {"username": f"e2edir{uniq}", "password": "Tmp-Pass-9x!Qz"})
    assert bridge.read(f"/api/v1/users?q=cust.{uniq}")["items"][0]["account_status"] == "active"
    bridge.command("PATCH", f"/api/v1/users/{uid}/account", {"is_locked": True})
    assert bridge.read(f"/api/v1/users?q=cust.{uniq}")["items"][0]["account_status"] == "locked"
    mine = bridge.read(f"/api/v1/tickets/?user_id={uid}")
    assert [t["ticket_id"] for t in mine["items"]] == [tk] and mine["total"] == 1       # the user's own tickets

    # organizations: member count, search, sort, total
    org = bridge.create("/api/v1/orgs", {"name": f"e2e-dir-org-{uniq}"}, cleanup="/api/v1/orgs/{id}")
    bridge.command("PATCH", f"/api/v1/users/{uid}", {"org_id": org})
    got = bridge.read(f"/api/v1/orgs?q=e2e-dir-org-{uniq}")
    assert got["total"] == 1 and got["items"][0]["id"] == org and got["items"][0]["user_count"] == 1
    assert bridge.read(f"/api/v1/users?org_id={org}")["total"] == 1
    assert bridge.read(f"/api/v1/users?q=e2e-dir-org-{uniq}")["total"] == 1             # a user is found by their organization
    for column in ("name", "users", "created", "updated"):
        assert bridge.get(f"/api/v1/orgs?sort={column}&descending=true").status_code == 200, column
    assert bridge.get("/api/v1/orgs?sort=nonsense").status_code == 422

    # knowledge base: FAQ search, category counts and parent, totals
    cat = bridge.create("/api/v1/kb/categories", {"name": f"e2e-kb-cat-{uniq}", "description": f"desc {uniq}"}, cleanup="/api/v1/kb/categories/{id}")
    faq = bridge.create("/api/v1/kb/faqs", {"category_id": cat, "question": f"How do I e2e-faq-{uniq}?", "answer": f"zebra-{uniq} answer"}, cleanup="/api/v1/kb/faqs/{id}")
    assert [f["id"] for f in bridge.read(f"/api/v1/kb/faqs?q=e2e-faq-{uniq}")["items"]] == [faq]
    assert bridge.read(f"/api/v1/kb/faqs?q=zebra-{uniq}")["total"] == 1                  # the answer is searched too
    assert bridge.read(f"/api/v1/kb/faqs?q=e2e-kb-cat-{uniq}")["total"] == 1             # and the category's name
    assert bridge.read("/api/v1/kb/faqs?q=zz-no-such-faq-zz")["total"] == 0
    assert bridge.read(f"/api/v1/kb/faqs?category_id={cat}")["total"] == 1
    cats = {c["id"]: c for c in bridge.read("/api/v1/kb/categories?page_size=200")["items"]}
    assert cats[cat]["faq_count"] == 1 and cats[cat]["parent_id"] is None
    assert bridge.read("/api/v1/kb/categories")["total"] >= 1

    # tasks: queues with counts, the linked ticket's number, the overdue flag
    task = bridge.create("/api/v1/tasks/", {"title": f"e2e dir task {uniq}", "description": "d", "dept_id": 1, "ticket_id": tk}, cleanup="/api/v1/tasks/{id}")
    queues = {q["key"]: q for q in bridge.read("/api/v1/tasks/queues")["queues"]}
    assert list(queues) == ["open", "overdue", "assigned", "closed"] and queues["closed"]["title"] == "Completed"
    for key in queues:
        listed = bridge.read(f"/api/v1/tasks/?queue={key}&page_size=1")
        assert listed["total"] == queues[key]["count"], key                              # a queue's count is its list's total
    item = next(t for t in bridge.read(f"/api/v1/tasks/?q=e2e dir task {uniq}")["items"] if t["task_id"] == task)
    ticket_number = bridge.read(f"/api/v1/tickets/{tk}")["number"]
    assert item["ticket_number"] == ticket_number and item["is_overdue"] is False
    assert bridge.get("/api/v1/tasks/?sort=ticket").status_code == 200 and bridge.get("/api/v1/tasks/?queue=nonsense").status_code == 422


def test_ticket_create_takes_what_osticket_new_ticket_form_takes(bridge, uniq):
    """source, SLA, due date, assignment, Cc, an existing requester, a first response and an internal note."""
    from datetime import datetime, timedelta, timezone

    requester = bridge.create("/api/v1/users", {"name": "E2E Requester", "email": _email(uniq, "req")}, cleanup="/api/v1/users/{id}")
    cc = bridge.create("/api/v1/users", {"name": "E2E Cc", "email": _email(uniq, "cc")}, cleanup="/api/v1/users/{id}")
    sla = bridge.create("/api/v1/slas", {"name": f"e2e-newtk-sla-{uniq}", "grace_period": 6, "is_active": True}, cleanup="/api/v1/slas/{id}")
    team = bridge.create("/api/v1/teams", {"name": f"e2e-newtk-team-{uniq}", "members": [1]}, cleanup="/api/v1/teams/{id}")
    due = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0, second=0)
    due_with_offset = due.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()    # an offset is converted

    # one ticket with everything, assigned to an agent: the note rides on the assignment, the response is posted
    tk = bridge.create("/api/v1/tickets/", {
        "subject": f"e2e new ticket {uniq}", "message": "first message", "user_id": requester, "cc_user_ids": [cc],
        "source": "Phone", "dept_id": 1, "topic_id": 1, "sla_id": sla, "due_date": due_with_offset,
        "assign_agent_id": 1, "response": f"first response {uniq}", "note": "an internal note", "signature": "none",
    })
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk}"))
    got = bridge.read(f"/api/v1/tickets/{tk}")
    assert got["user_id"] == requester and got["source"] == "Phone" and got["sla_id"] == sla
    assert got["assignee_staff_id"] == 1
    assert datetime.fromisoformat(got["due_date"].replace("Z", "+00:00")) == due          # stored as the same instant
    assert [c["user_id"] for c in got["collaborators"]] == [cc]
    assert any(e["type"] == "R" and f"first response {uniq}" in e["body"] for e in got["thread"])
    assert any(e["type"] == "M" and "first message" in e["body"] for e in got["thread"])

    # unassigned: the internal note is posted; defaults (source API, no assignee) hold
    tk2 = bridge.create("/api/v1/tickets/", {
        "subject": f"e2e new ticket b {uniq}", "message": "m", "name": "E2E Walk-in", "email": _email(uniq, "walk"),
        "topic_id": 1, "note": f"a new-ticket note {uniq}",
    })
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk2}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{bridge.read(f'/api/v1/tickets/{tk2}')['user_id']}"))
    got2 = bridge.read(f"/api/v1/tickets/{tk2}")
    assert got2["source"] == "API" and got2["assignee_staff_id"] is None and got2["team_id"] is None
    assert any(e["type"] == "N" and f"a new-ticket note {uniq}" in e["body"] for e in got2["thread"])

    # assigned to a team
    tk3 = bridge.create("/api/v1/tickets/", {
        "subject": f"e2e new ticket c {uniq}", "message": "m", "user_id": requester, "topic_id": 1, "assign_team_id": team})
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk3}"))
    assert bridge.read(f"/api/v1/tickets/{tk3}")["team_id"] == team

    # osTicket's refusals
    base = {"subject": f"e2e refused {uniq}", "message": "m", "user_id": requester, "topic_id": 1}
    bridge.refuses("POST", "/api/v1/tickets/", {k: v for k, v in base.items() if k != "topic_id"},
                   because="Help topic selection is required")                       # osTicket's form requires a topic
    bridge.refuses("POST", "/api/v1/tickets/", {**base, "user_id": 999999}, because="User not found")
    bridge.refuses("POST", "/api/v1/tickets/", {**base, "sla_id": 999999}, because="SLA plan not found")
    bridge.refuses("POST", "/api/v1/tickets/", {**base, "assign_agent_id": 999999}, because="Staff not found")
    bridge.refuses("POST", "/api/v1/tickets/", {**base, "cc_user_ids": [999999]}, because="User not found")
    bridge.refuses("POST", "/api/v1/tickets/", {**base, "due_date": "2020-01-01T00:00:00Z"}, because="future")
    assert bridge.get("/api/v1/tickets/?q=e2e refused").json()["total"] == 0                # nothing was half-created
    assert bridge.client.post("/api/v1/tickets/", json={"subject": "s", "message": "m"}).status_code == 422   # a requester is needed
    assert bridge.client.post("/api/v1/tickets/", json={**base, "source": "Carrier pigeon"}).status_code == 422
    assert bridge.client.post("/api/v1/tickets/", json={**base, "assign_agent_id": 1, "assign_team_id": 1}).status_code == 422


def test_forms_for_shows_agents_the_fields_osticket_shows_them(bridge, uniq):
    """Enabled, agent-visible fields only; a topic's forms follow the core ticket form; every kind has its form."""
    form = bridge.create("/api/v1/forms", {"title": f"e2e-forfor-{uniq}", "type": "G"}, cleanup="/api/v1/forms/{id}")
    furl = f"/api/v1/forms/{form}"
    for name, mode in (("seen", "optional"), ("internal", "internal_required"), ("customer_only", "users_only"),
                       ("gone", "optional")):
        bridge.command("POST", f"{furl}/fields", {"label": name, "type": "text", "name": name, "mode": mode})
    fields = {f["name"]: f["id"] for f in bridge.read(furl)["fields"]}
    bridge.command("PATCH", f"{furl}/fields/{fields['gone']}", {"is_enabled": False})
    topic = bridge.create("/api/v1/topics", {"topic": f"e2e-forfor-topic-{uniq}", "dept_id": 1, "form_ids": [form]},
                          cleanup="/api/v1/topics/{id}")

    def names(body):
        return [(f["title"].startswith("e2e-forfor"), [x["name"] for x in f["fields"]]) for f in body["forms"]]

    plain = bridge.read("/api/v1/forms/for/ticket")["forms"]
    assert [f["type"] for f in plain] == ["T"] and {"subject", "message", "priority"} <= {x["name"] for x in plain[0]["fields"]}
    with_topic = bridge.read(f"/api/v1/forms/for/ticket?topic_id={topic}")
    assert [f["type"] for f in with_topic["forms"]] == ["T", "G"]                          # core first, the topic's form after
    mine = {x["name"]: x for x in with_topic["forms"][1]["fields"]}
    assert set(mine) == {"seen", "internal"}                                               # disabled and customer-only are absent
    assert mine["seen"]["is_editable"] is True and mine["seen"]["is_required"] is False
    assert mine["internal"]["is_required"] is True and mine["internal"]["mode"] == "internal_required"
    assert [x["name"] for x in with_topic["forms"][1]["fields"]] == ["seen", "internal"]   # in the form's order

    user = bridge.read("/api/v1/forms/for/user")["forms"]
    assert [f["type"] for f in user] == ["U"] and {"name", "email"} <= {x["name"] for x in user[0]["fields"]}
    assert [f["type"] for f in bridge.read("/api/v1/forms/for/organization")["forms"]] == ["O"]
    assert [f["type"] for f in bridge.read("/api/v1/forms/for/task")["forms"]] == ["A"]
    assert bridge.get("/api/v1/forms/for/widget").status_code == 422
    assert bridge.get(f"/api/v1/forms/for/user?topic_id={topic}").status_code == 200       # a topic only matters for tickets
    assert [f["type"] for f in bridge.read(f"/api/v1/forms/for/user?topic_id={topic}")["forms"]] == ["U"]


def test_user_csv_import_is_osticket_import_all_or_nothing(bridge, uniq):
    def user_by(email):
        found = bridge.read(f"/api/v1/users?q={email}")["items"]
        return found[0]["id"] if found else None

    emails = [_email(uniq, f"imp{i}") for i in range(3)]
    header = "name,email,phone\n" + "".join(f"E2E Import {i},{e},555010{i}\n" for i, e in enumerate(emails))
    done = bridge.command("POST", "/api/v1/users/import", {"csv": header})
    assert done["result"]["imported"] == 3
    ids = [user_by(e) for e in emails]
    assert all(ids)
    for uid in ids:
        bridge._cleanups.append(("DELETE", f"/api/v1/users/{uid}"))
    got = bridge.read(f"/api/v1/users/{ids[1]}")
    assert (got["name"], got["email"], got["phone"]) == ("E2E Import 1", emails[1], "5550101")   # phone stored as digits

    # the pasted box: "name, email" per line, no header
    pasted_email = _email(uniq, "pasted")
    done = bridge.command("POST", "/api/v1/users/import", {"csv": f"E2E Pasted, {pasted_email}", "pasted": True})
    assert done["result"]["imported"] == 1 and user_by(pasted_email)
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{user_by(pasted_email)}"))

    # a bad row refuses the whole file: nothing is imported, in osTicket's words
    good = _email(uniq, "good")
    bridge.refuses("POST", "/api/v1/users/import", {"csv": f"name,email\nE2E Good,{good}\nE2E Bad,not-an-email\n"},
                   because="Both `name` and `email` fields are required")
    assert user_by(good) is None
    bridge.refuses("POST", "/api/v1/users/import", {"csv": "name,email\nE2E X\n"}, because="import")
    bridge.refuses("POST", "/api/v1/users/import", {"csv": "bogus_header,other\n1,2\n"}, because="header")


def test_admin_lists_carry_osticket_columns(bridge):
    staff = bridge.read("/api/v1/staff?page_size=200")["items"]
    assert all("last_login" in s for s in staff)
    depts = bridge.read("/api/v1/departments?page_size=200")["items"]
    assert all(d["status"] in ("active", "disabled", "archived") and d["agents_count"] >= 0 for d in depts)
    assert any(d["agents_count"] > 0 for d in depts)
    teams = bridge.read("/api/v1/teams?page_size=200")["items"]
    assert all(t["members_count"] >= 0 for t in teams)
    topics = bridge.read("/api/v1/topics?page_size=200")["items"]
    assert all(t["path"].endswith(t["topic"]) for t in topics)
    forms = bridge.read("/api/v1/forms?page_size=200")["items"]
    assert all(f["field_count"] >= 0 for f in forms) and any(f["field_count"] > 0 for f in forms)


def test_role_lifecycle_keeps_what_an_update_does_not_send(bridge, uniq):
    rid = bridge.create(
        "/api/v1/roles", {"name": f"e2e-role-{uniq}", "notes": "first", "permissions": ["ticket.reply", "ticket.assign"]},
        cleanup="/api/v1/roles/{id}")
    got = bridge.read(f"/api/v1/roles/{rid}")
    assert (got["name"], got["notes"], got["enabled"], got["in_use"]) == (f"e2e-role-{uniq}", "first", True, 0)
    assert got["permissions"] == ["ticket.assign", "ticket.reply"] and got["created"]

    bridge.command("PATCH", f"/api/v1/roles/{rid}", {"notes": "changed"})            # permissions and name preserved
    got = bridge.read(f"/api/v1/roles/{rid}")
    assert (got["name"], got["notes"], got["permissions"]) == (f"e2e-role-{uniq}", "changed", ["ticket.assign", "ticket.reply"])

    bridge.command("PATCH", f"/api/v1/roles/{rid}", {"permissions": ["ticket.close"], "enabled": False})
    got = bridge.read(f"/api/v1/roles/{rid}")
    assert (got["permissions"], got["enabled"], got["notes"]) == (["ticket.close"], False, "changed")

    bridge.refuses("POST", "/api/v1/roles", {"name": f"e2e-role-{uniq}", "permissions": ["ticket.reply"]}, because="already in use")
    bridge.command("DELETE", f"/api/v1/roles/{rid}")
    assert bridge.get(f"/api/v1/roles/{rid}").status_code == 404


def test_a_role_in_use_cannot_be_deleted(bridge):
    in_use = next(r for r in bridge.read("/api/v1/roles?page_size=200")["items"] if r["in_use"])
    bridge.refuses("DELETE", f"/api/v1/roles/{in_use['id']}", None, because="in use")


def test_api_key_lifecycle_keeps_what_an_update_does_not_send(bridge, uniq):
    kid = bridge.create("/api/v1/api-keys", {"ip_address": "203.0.113.7", "can_exec_cron": True, "notes": f"e2e {uniq}"},
                        cleanup="/api/v1/api-keys/{id}")
    got = bridge.read(f"/api/v1/api-keys/{kid}")
    assert len(got["key"]) >= 16 and got["ip_address"] == "203.0.113.7"
    assert (got["is_active"], got["can_create_tickets"], got["can_exec_cron"], got["notes"]) == (True, True, True, f"e2e {uniq}")
    bridge.command("PATCH", f"/api/v1/api-keys/{kid}", {"is_active": False})
    after = bridge.read(f"/api/v1/api-keys/{kid}")
    assert (after["is_active"], after["can_exec_cron"], after["notes"], after["key"]) == (False, True, f"e2e {uniq}", got["key"])
    assert any(k["id"] == kid for k in bridge.read("/api/v1/api-keys?page_size=200")["items"])
    bridge.refuses("POST", "/api/v1/api-keys", {"ip_address": "not an address"}, because="valid ip")
    bridge.command("DELETE", f"/api/v1/api-keys/{kid}")
    assert bridge.get(f"/api/v1/api-keys/{kid}").status_code == 404


def test_page_lifecycle_and_pages_in_use_are_protected(bridge, uniq):
    pid = bridge.create("/api/v1/pages", {"name": f"e2e page {uniq}", "type": "other", "body": "<p>hello</p>", "notes": "n"},
                        cleanup="/api/v1/pages/{id}")
    got = bridge.read(f"/api/v1/pages/{pid}")
    assert (got["name"], got["type"], got["is_active"], got["in_use"], got["notes"]) == (f"e2e page {uniq}", "other", True, False, "n")
    assert "hello" in got["body"]
    bridge.command("PATCH", f"/api/v1/pages/{pid}", {"body": "<p>changed</p>"})          # the rest is preserved
    after = bridge.read(f"/api/v1/pages/{pid}")
    assert "changed" in after["body"] and (after["name"], after["notes"], after["is_active"]) == (f"e2e page {uniq}", "n", True)
    bridge.refuses("POST", "/api/v1/pages", {"name": f"e2e page {uniq}", "type": "other", "body": "x"}, because="already exists")

    default = next(p for p in bridge.read("/api/v1/pages?page_size=200")["items"] if p["type"] == "landing")
    assert default["in_use"] is True
    bridge.refuses("DELETE", f"/api/v1/pages/{default['id']}", None, because="in use")
    bridge.refuses("PATCH", f"/api/v1/pages/{default['id']}", {"is_active": False}, because="in-use")
    assert all(p["type"] in ("landing", "offline", "thank-you", "other") for p in bridge.read("/api/v1/pages?page_size=200")["items"])
    bridge.command("DELETE", f"/api/v1/pages/{pid}")
    assert bridge.get(f"/api/v1/pages/{pid}").status_code == 404


def test_custom_list_and_items_lifecycle(bridge, uniq):
    lid = bridge.create("/api/v1/lists", {"name": f"e2e list {uniq}", "name_plural": f"e2e lists {uniq}", "sort_mode": "SortCol", "notes": "n"},
                        cleanup="/api/v1/lists/{id}")
    got = bridge.read(f"/api/v1/lists/{lid}")
    assert (got["name"], got["sort_mode"], got["notes"], got["item_count"], got["handled_by"]) == (f"e2e list {uniq}", "SortCol", "n", 0, None)
    assert got["is_editable"] and got["allow_add"] and got["is_deletable"]

    bridge.command("PATCH", f"/api/v1/lists/{lid}", {"notes": "changed"})                       # name and sort mode preserved
    got = bridge.read(f"/api/v1/lists/{lid}")
    assert (got["name"], got["name_plural"], got["sort_mode"], got["notes"]) == (f"e2e list {uniq}", f"e2e lists {uniq}", "SortCol", "changed")

    red = bridge.create(f"/api/v1/lists/{lid}/items", {"value": "Red", "abbrev": "R", "sort": 2})
    blue = bridge.create(f"/api/v1/lists/{lid}/items", {"value": "Blue", "sort": 1, "is_enabled": False})
    items = bridge.read(f"/api/v1/lists/{lid}/items")
    assert items["total"] == 2 and [i["value"] for i in items["items"]] == ["Blue", "Red"]       # the manual order
    by_value = {i["value"]: i for i in items["items"]}
    assert (by_value["Red"]["abbrev"], by_value["Red"]["is_enabled"], by_value["Blue"]["is_enabled"]) == ("R", True, False)
    bridge.refuses("POST", f"/api/v1/lists/{lid}/items", {"value": "Red"}, because="already has")

    bridge.command("PATCH", f"/api/v1/lists/{lid}/items/{blue}", {"is_enabled": True, "sort": 3})
    bridge.command("PATCH", f"/api/v1/lists/{lid}/items/{red}", {"value": "Crimson"})            # abbreviation and sort kept
    after = {i["id"]: i for i in bridge.read(f"/api/v1/lists/{lid}/items")["items"]}
    assert (after[red]["value"], after[red]["abbrev"], after[red]["sort"]) == ("Crimson", "R", 2)
    assert (after[blue]["is_enabled"], after[blue]["sort"]) == (True, 3)
    bridge.command("DELETE", f"/api/v1/lists/{lid}/items/{red}")
    assert [i["value"] for i in bridge.read(f"/api/v1/lists/{lid}/items")["items"]] == ["Blue"]
    bridge.refuses("DELETE", f"/api/v1/lists/{lid}/items/{red}", None, because="not found")

    statuses = next(l for l in bridge.read("/api/v1/lists?page_size=200")["items"] if l["handled_by"] == "ticket-status")
    assert not statuses["is_editable"] and not statuses["is_deletable"]
    bridge.refuses("PATCH", f"/api/v1/lists/{statuses['id']}", {"notes": "x"}, because="managed by osTicket")
    bridge.command("DELETE", f"/api/v1/lists/{lid}")
    assert bridge.get(f"/api/v1/lists/{lid}").status_code == 404


def test_a_custom_list_field_takes_item_ids_as_answers(bridge, uniq):
    lid = bridge.create("/api/v1/lists", {"name": f"e2e answers {uniq}", "sort_mode": "SortCol"}, cleanup="/api/v1/lists/{id}")
    red = bridge.create(f"/api/v1/lists/{lid}/items", {"value": "Red", "sort": 1})
    blue = bridge.create(f"/api/v1/lists/{lid}/items", {"value": "Blue", "sort": 2})
    name = f"e2elist{uniq[:6]}"
    fid = bridge.command("POST", "/api/v1/forms/1/fields", {"label": "E2E list", "type": f"list-{lid}", "name": name})["result"]["field_id"]
    bridge._cleanups.append(("DELETE", f"/api/v1/forms/1/fields/{fid}?delete_data=true"))
    bridge._cleanups.append(("DELETE", f"/api/v1/lists/{lid}"))     # runs after the field is gone (newest first)

    uid = bridge.create("/api/v1/users", {"name": "E2E List User", "email": _email(uniq, "lst"), "custom_fields": {name: red}},
                        cleanup="/api/v1/users/{id}")
    answer = _answers(bridge.read(f"/api/v1/users/{uid}"))[name]
    assert (answer["kind"], [(c["key"], c["label"]) for c in answer["choices"]]) == ("choices", [(str(red), "Red")])
    bridge.command("PATCH", f"/api/v1/users/{uid}", {"custom_fields": {name: blue}})
    assert [c["label"] for c in _answers(bridge.read(f"/api/v1/users/{uid}"))[name]["choices"]] == ["Blue"]
    bridge.refuses("PATCH", f"/api/v1/users/{uid}", {"custom_fields": {name: 999999}}, because="not a valid value")
    bridge.command("PATCH", f"/api/v1/users/{uid}", {"custom_fields": {name: ""}})
    assert _answers(bridge.read(f"/api/v1/users/{uid}"))[name]["choices"] in ([], None)


def test_email_template_set_lifecycle_and_messages(bridge, uniq):
    default = next(s for s in bridge.read("/api/v1/email-templates?page_size=200")["items"] if s["in_use"])
    full = bridge.read(f"/api/v1/email-templates/{default['id']}")
    assert full["message_count"] == 19 and all(m["defined"] for m in full["messages"])

    sid = bridge.create("/api/v1/email-templates", {"name": f"e2e{uniq[:20]}", "notes": "empty"}, cleanup="/api/v1/email-templates/{id}")
    empty = bridge.read(f"/api/v1/email-templates/{sid}")
    assert (empty["name"], empty["is_active"], empty["in_use"], empty["message_count"]) == (f"e2e{uniq[:20]}", True, False, 0)
    assert len(empty["messages"]) == 19 and not any(m["defined"] for m in empty["messages"])

    done = bridge.command("PUT", f"/api/v1/email-templates/{sid}/messages/ticket.autoresp", {"subject": "Hello %{ticket.number}", "body": "<p>Text</p>"})
    assert done["result"]["created"] is True                                     # the set had no copy: it is added
    msg = bridge.read(f"/api/v1/email-templates/{sid}/messages/ticket.autoresp")
    assert (msg["defined"], msg["subject"], msg["name"]) == (True, "Hello %{ticket.number}", "New Ticket Auto-response") and "Text" in msg["body"]
    done = bridge.command("PUT", f"/api/v1/email-templates/{sid}/messages/ticket.autoresp", {"subject": "Changed", "body": "<p>Again</p>"})
    assert done["result"]["created"] is False
    assert bridge.read(f"/api/v1/email-templates/{sid}")["message_count"] == 1
    assert bridge.get(f"/api/v1/email-templates/{sid}/messages/not.a.message").status_code == 404

    bridge.command("PATCH", f"/api/v1/email-templates/{sid}", {"notes": "changed"})            # name and status kept
    after = bridge.read(f"/api/v1/email-templates/{sid}")
    assert (after["name"], after["notes"], after["is_active"]) == (f"e2e{uniq[:20]}", "changed", True)
    bridge.command("PATCH", f"/api/v1/email-templates/{sid}", {"is_active": False})
    assert bridge.read(f"/api/v1/email-templates/{sid}")["is_active"] is False
    bridge.refuses("PATCH", f"/api/v1/email-templates/{default['id']}", {"is_active": False}, because="in-use")
    bridge.refuses("DELETE", f"/api/v1/email-templates/{default['id']}", None, because="in use")
    bridge.refuses("POST", "/api/v1/email-templates", {"name": f"e2e{uniq[:20]}"}, because="already exists")

    copy = bridge.create("/api/v1/email-templates", {"name": f"e2c{uniq[:20]}", "copy_from_id": default["id"]}, cleanup="/api/v1/email-templates/{id}")
    assert bridge.read(f"/api/v1/email-templates/{copy}")["message_count"] == 19
    bridge.command("DELETE", f"/api/v1/email-templates/{sid}")
    assert bridge.get(f"/api/v1/email-templates/{sid}").status_code == 404


def test_email_address_lifecycle_and_the_default_ones_are_protected(bridge, uniq):
    addr = f"e2e.{uniq}@{DELIVERABLE_DOMAIN}"
    eid = bridge.create("/api/v1/emails", {"email": addr, "name": "E2E Address", "dept_id": 1, "priority_id": 2, "notes": "n"},
                        cleanup="/api/v1/emails/{id}")
    got = bridge.read(f"/api/v1/emails/{eid}")
    assert (got["email"], got["name"], got["dept_id"], got["priority_id"], got["notes"]) == (addr, "E2E Address", 1, 2, "n")
    assert (got["no_autoresponse"], got["is_default"], got["has_mailbox"], got["has_smtp"], got["accounts"]) == (False, False, False, False, [])
    assert got["dept_name"] and got["priority_name"]

    bridge.command("PATCH", f"/api/v1/emails/{eid}", {"no_autoresponse": True})                 # the rest is kept
    after = bridge.read(f"/api/v1/emails/{eid}")
    assert (after["no_autoresponse"], after["name"], after["dept_id"], after["notes"], after["email"]) == (True, "E2E Address", 1, "n", addr)
    assert any(e["id"] == eid for e in bridge.read("/api/v1/emails?page_size=200")["items"])
    bridge.refuses("POST", "/api/v1/emails", {"email": addr, "name": "dup"}, because="already exists")

    default = next(e for e in bridge.read("/api/v1/emails?page_size=200")["items"] if e["is_default"])
    bridge.refuses("DELETE", f"/api/v1/emails/{default['id']}", None, because="cannot be deleted")
    bridge.command("DELETE", f"/api/v1/emails/{eid}")
    assert bridge.get(f"/api/v1/emails/{eid}").status_code == 404


def test_ticket_filter_lifecycle_rules_and_actions(bridge, uniq):
    opts = bridge.read("/api/v1/filters/options")
    assert any(f["key"] == "name" for g in opts["matches"] for f in g["fields"])
    assert {a["type"] for a in opts["actions"]} >= {"dept", "pri", "reject", "email"} and len(opts["match_types"]) == 8

    fid = bridge.create("/api/v1/filters", {
        "name": f"e2e f{uniq[:20]}", "execorder": 50, "target": "Web", "notes": "n",
        "rules": [{"what": "name", "how": "contains", "val": f"zz{uniq}"}, {"what": "email", "how": "ends", "val": "@example.org"}],
        "actions": [{"type": "pri", "priority_id": 3}, {"type": "dept", "dept_id": 1}]},
        cleanup="/api/v1/filters/{id}")
    got = bridge.read(f"/api/v1/filters/{fid}")
    assert (got["name"], got["execorder"], got["target"], got["is_active"], got["match_all_rules"], got["notes"]) == (f"e2e f{uniq[:20]}", 50, "Web", True, False, "n")
    assert [(r["what"], r["how"], r["val"]) for r in got["rules"]] == [("name", "contains", f"zz{uniq}"), ("email", "ends", "@example.org")]
    assert [(a["type"], a["priority_id"], a["dept_id"]) for a in got["actions"]] == [("pri", 3, None), ("dept", None, 1)]
    assert any(f["id"] == fid for f in bridge.read("/api/v1/filters?page_size=200")["items"])
    assert all(f["name"] != "SYSTEM BAN LIST" for f in bridge.read("/api/v1/filters?page_size=200")["items"])

    bridge.command("PATCH", f"/api/v1/filters/{fid}", {"is_active": False, "match_all_rules": True})   # rules and actions kept
    after = bridge.read(f"/api/v1/filters/{fid}")
    assert (after["is_active"], after["match_all_rules"], after["name"], len(after["rules"]), len(after["actions"])) == (False, True, got["name"], 2, 2)

    bridge.command("PATCH", f"/api/v1/filters/{fid}", {"rules": [{"what": "email", "how": "equal", "val": "someone@gmail.com"}], "actions": [{"type": "reject"}]})
    replaced = bridge.read(f"/api/v1/filters/{fid}")
    assert [(r["what"], r["val"]) for r in replaced["rules"]] == [("email", "someone@gmail.com")] and [a["type"] for a in replaced["actions"]] == ["reject"]
    assert replaced["is_active"] is False                                                             # the flags survived the replace

    bridge.refuses("PATCH", f"/api/v1/filters/{fid}", {"rules": [{"what": "name", "how": "match", "val": "(unclosed"}]}, because="regex")
    bridge.refuses("PATCH", f"/api/v1/filters/{fid}", {"actions": [{"type": "dept", "dept_id": 999999}]}, because="active department")
    bridge.refuses("POST", "/api/v1/filters", {"name": got["name"], "rules": [{"what": "name", "how": "equal", "val": "x"}]}, because="already in use")
    ban = bridge.read("/api/v1/banlist?page_size=1")                                                   # the ban list filter is not reachable here
    assert bridge.get("/api/v1/filters/1").status_code == 404 and ban is not None
    bridge.command("DELETE", f"/api/v1/filters/{fid}")
    assert bridge.get(f"/api/v1/filters/{fid}").status_code == 404


def test_schedule_and_entries_lifecycle(bridge, uniq):
    seeded = {s["name"]: s for s in bridge.read("/api/v1/schedules?page_size=200")["items"]}
    office = next(s for s in seeded.values() if s["type"] == "business_hours" and s["entry_count"] > 2)
    holidays = next(s for s in seeded.values() if s["type"] == "holidays")
    seeded_detail = bridge.read(f"/api/v1/schedules/{office['id']}")
    assert seeded_detail["holiday_schedule_ids"] and all(e["description"] for e in seeded_detail["entries"])
    assert any(e["repeats"] == "weekly" and e["day"] == 1 and e["starts_at"] == "08:00:00" for e in seeded_detail["entries"])

    sid = bridge.create("/api/v1/schedules", {"name": f"e2e hours {uniq}", "type": "business_hours", "timezone": "UTC",
                                              "description": "d", "holiday_schedule_ids": [holidays["id"]]},
                        cleanup="/api/v1/schedules/{id}")
    got = bridge.read(f"/api/v1/schedules/{sid}")
    assert (got["type"], got["timezone"], got["description"], got["holiday_schedule_ids"], got["entries"]) == ("business_hours", "UTC", "d", [holidays["id"]], [])

    mon = bridge.create(f"/api/v1/schedules/{sid}/entries", {"name": "Monday", "repeats": "weekly", "starts_on": "2026-01-05",
                                                              "starts_at": "08:00", "ends_at": "17:00", "day": 1})
    bridge.create(f"/api/v1/schedules/{sid}/entries", {"name": "Weekends off", "repeats": "weekends", "starts_on": "2026-01-03", "all_day": True})
    entries = {e["name"]: e for e in bridge.read(f"/api/v1/schedules/{sid}")["entries"]}
    assert (entries["Monday"]["repeats"], entries["Monday"]["day"], entries["Monday"]["starts_at"], entries["Monday"]["ends_at"]) == ("weekly", 1, "08:00:00", "17:00:00")
    assert entries["Monday"]["all_day"] is False and "Monday" in entries["Monday"]["description"]
    assert entries["Weekends off"]["all_day"] is True and entries["Weekends off"]["repeats"] == "weekends"

    bridge.refuses("POST", f"/api/v1/schedules/{sid}/entries", {"name": "Monday", "repeats": "weekly", "starts_on": "2026-01-05",
                                                                  "starts_at": "09:00", "ends_at": "10:00", "day": 2}, because="unique")
    bridge.refuses("POST", f"/api/v1/schedules/{sid}/entries", {"name": "Backwards", "repeats": "weekly", "starts_on": "2026-01-06",
                                                                  "starts_at": "17:00", "ends_at": "08:00", "day": 2}, because="time span")
    bridge.command("PUT", f"/api/v1/schedules/{sid}/entries/{mon}", {"name": "Monday", "repeats": "weekly", "starts_on": "2026-01-05",
                                                                       "starts_at": "09:00", "ends_at": "18:00", "day": 1})
    assert next(e for e in bridge.read(f"/api/v1/schedules/{sid}")["entries"] if e["id"] == mon)["starts_at"] == "09:00:00"

    bridge.command("PATCH", f"/api/v1/schedules/{sid}", {"description": "changed"})              # name, timezone, holidays kept
    after = bridge.read(f"/api/v1/schedules/{sid}")
    assert (after["name"], after["timezone"], after["description"], after["holiday_schedule_ids"], len(after["entries"])) == (f"e2e hours {uniq}", "UTC", "changed", [holidays["id"]], 2)
    bridge.command("DELETE", f"/api/v1/schedules/{sid}/entries/{mon}")
    assert len(bridge.read(f"/api/v1/schedules/{sid}")["entries"]) == 1

    copy = bridge.create("/api/v1/schedules", {"name": f"e2e copy {uniq}", "type": "business_hours", "copy_from_id": sid}, cleanup="/api/v1/schedules/{id}")
    assert len(bridge.read(f"/api/v1/schedules/{copy}")["entries"]) == 1
    bridge.refuses("POST", "/api/v1/schedules", {"name": f"e2e copy {uniq}", "type": "business_hours"}, because="already in use")
    bridge.refuses("POST", "/api/v1/schedules", {"name": f"e2e wrong {uniq}", "type": "holidays", "copy_from_id": sid}, because="Type must be")
    bridge.refuses("DELETE", f"/api/v1/schedules/{office['id']}", None, because="in use")
    bridge.refuses("DELETE", f"/api/v1/schedules/{holidays['id']}", None, because="in use")      # the seeded business hours list it
    bridge.command("DELETE", f"/api/v1/schedules/{copy}")
    bridge.command("DELETE", f"/api/v1/schedules/{sid}")
    assert bridge.get(f"/api/v1/schedules/{sid}").status_code == 404


def test_a_field_added_after_the_record_existed_is_listed_without_an_answer(bridge, uniq):
    """osTicket shows every field of the form; the API used to list only the fields with a stored answer."""
    uid = bridge.create("/api/v1/users", {"name": "E2E Late Field", "email": _email(uniq, "late")}, cleanup="/api/v1/users/{id}")
    name = f"e2elate{uniq[:6]}"
    fid = bridge.command("POST", "/api/v1/forms/1/fields", {"label": "E2E late", "type": "text", "name": name})["result"]["field_id"]
    bridge._cleanups.append(("DELETE", f"/api/v1/forms/1/fields/{fid}?delete_data=true"))
    late = _answers(bridge.read(f"/api/v1/users/{uid}"))[name]
    assert (late["label"], late["value"], late["kind"]) == ("E2E late", None, "text")
    listed = bridge.read(f"/api/v1/users?q=E2E Late Field&fields={name}")["items"]
    assert [a["value"] for u in listed if u["id"] == uid for a in u["custom_fields"]] == [None]
    bridge.command("PATCH", f"/api/v1/users/{uid}", {"custom_fields": {name: "now answered"}})
    assert _answers(bridge.read(f"/api/v1/users/{uid}"))[name]["value"] == "now answered"


def test_ticket_status_lifecycle_and_osticket_refusals(bridge, uniq):
    name = f"e2e status {uniq}"
    sid = bridge.create("/api/v1/ticket-statuses", {"name": name, "state": "closed", "description": "Parked for good",
                                                    "allow_reopen": True, "reopen_status_id": 1}, cleanup="/api/v1/ticket-statuses/{id}")
    url = f"/api/v1/ticket-statuses/{sid}"
    got = bridge.read(url)
    assert (got["name"], got["state"], got["description"], got["allow_reopen"], got["reopen_status_id"]) == (name, "closed", "Parked for good", True, 1)
    assert (got["is_enabled"], got["is_internal"], got["is_default"], got["is_deletable"], got["ticket_count"]) == (True, False, False, True, 0)
    assert any(s["id"] == sid for s in bridge.read("/api/v1/ticket-statuses")["statuses"])           # agents are offered it
    assert any(s["id"] == sid for s in bridge.read("/api/v1/ticket-statuses/manage")["items"])

    # a partial update keeps everything else
    bridge.command("PATCH", url, {"description": "Parked, again"})
    got = bridge.read(url)
    assert (got["description"], got["state"], got["allow_reopen"], got["reopen_status_id"], got["name"]) == ("Parked, again", "closed", True, 1, name)
    bridge.command("PATCH", url, {"name": name + " b", "allow_reopen": False})
    got = bridge.read(url)
    assert (got["name"], got["allow_reopen"], got["description"]) == (name + " b", False, "Parked, again")

    # disabled: gone from the agents' list, still in the Admin Panel's; enabled again: back
    bridge.command("PATCH", url, {"is_enabled": False})
    assert bridge.read(url)["is_enabled"] is False
    assert not any(s["id"] == sid for s in bridge.read("/api/v1/ticket-statuses")["statuses"])
    bridge.command("PATCH", url, {"is_enabled": True})
    assert any(s["id"] == sid for s in bridge.read("/api/v1/ticket-statuses")["statuses"])

    # osTicket's rules
    bridge.refuses("POST", "/api/v1/ticket-statuses", {"name": name + " b", "state": "open"}, because="already a ticket status")
    bridge.refuses("PATCH", url, {"reopen_status_id": sid}, because="enabled open status")           # a closed status is no target
    bridge.refuses("PATCH", url, {"reopen_status_id": 999999}, because="enabled open status")
    bridge.refuses("PATCH", "/api/v1/ticket-statuses/999999", {"name": "x"}, because="not found")
    internal = bridge.read("/api/v1/ticket-statuses/1")
    assert internal["is_internal"] and not internal["is_deletable"]
    bridge.refuses("PATCH", "/api/v1/ticket-statuses/1", {"name": "Opened"}, because="name cannot change")
    bridge.refuses("PATCH", "/api/v1/ticket-statuses/1", {"state": "closed"}, because="state cannot change")
    bridge.refuses("PATCH", "/api/v1/ticket-statuses/1", {"is_enabled": False}, because="cannot be disabled")
    bridge.refuses("DELETE", "/api/v1/ticket-statuses/1", None, because="cannot be deleted")
    assert bridge.read("/api/v1/ticket-statuses/1")["name"] == internal["name"]                          # nothing changed

    # a status that tickets use cannot be deleted
    open_sid = bridge.create("/api/v1/ticket-statuses", {"name": f"e2e open status {uniq}", "state": "open"},
                             cleanup="/api/v1/ticket-statuses/{id}")
    tk = bridge.create("/api/v1/tickets/", {"subject": f"e2e status use {uniq}", "message": "m", "email": _email(uniq, "st"),
                                            "name": "St", "dept_id": 1, "topic_id": 1, "status_id": open_sid})
    holder = bridge.read(f"/api/v1/tickets/{tk}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk}"))
    assert bridge.read(f"/api/v1/ticket-statuses/{open_sid}")["ticket_count"] == 1
    bridge.refuses("DELETE", f"/api/v1/ticket-statuses/{open_sid}", None, because="has tickets")
    bridge.command("PATCH", url, {"reopen_status_id": open_sid, "allow_reopen": True})
    assert bridge.read(url)["reopen_status_id"] == open_sid

    bridge.command("DELETE", url)
    assert bridge.get(url).status_code == 404
