"""The Admin Panel settings pages against the live stack. They change the real configuration, so every test puts
back what it changed and checks that nothing else moved."""

import pytest

pytestmark = pytest.mark.e2e

SECTIONS = ["system", "tickets", "tasks", "agents", "users", "kb", "emails", "pages"]


def _snapshot(bridge) -> dict:
    return {s: bridge.read(f"/api/v1/settings/{s}") for s in SECTIONS}


def test_writing_a_section_back_unchanged_changes_nothing(bridge):
    """osTicket's pages rewrite every key; the overlay must reproduce the stored state exactly."""
    before = _snapshot(bridge)
    for section in SECTIONS:
        bridge.command("PATCH", f"/api/v1/settings/{section}", {})
    assert _snapshot(bridge) == before


def test_one_setting_per_section_changes_and_is_restored(bridge):
    before = _snapshot(bridge)
    flips = {
        "system": ("autolock_minutes", before["system"]["autolock_minutes"] + 1),
        "tickets": ("max_open_tickets", before["tickets"]["max_open_tickets"] + 7),
        "tasks": ("task_alert_admin", not before["tasks"]["task_alert_admin"]),
        "agents": ("staff_session_timeout", before["agents"]["staff_session_timeout"] + 1),
        "users": ("client_session_timeout", before["users"]["client_session_timeout"] + 1),
        "kb": ("enable_premade", not before["kb"]["enable_premade"]),
        "emails": ("use_email_priority", not before["emails"]["use_email_priority"]),
    }
    try:
        for section, (key, value) in flips.items():
            bridge.command("PATCH", f"/api/v1/settings/{section}", {key: value})
            now = bridge.read(f"/api/v1/settings/{section}")
            assert now[key] == value, (section, key)
            assert {k: v for k, v in now.items() if k != key} == {k: v for k, v in before[section].items() if k != key}, section
    finally:
        for section, (key, _) in flips.items():
            bridge.command("PATCH", f"/api/v1/settings/{section}", {key: before[section][key]})
    assert _snapshot(bridge) == before


def test_osticket_refuses_bad_values_with_its_own_words_and_changes_nothing(bridge):
    before = _snapshot(bridge)
    bridge.refuses("PATCH", "/api/v1/settings/tickets", {"ticket_number_format": "no hash here"}, because="hash")
    bridge.refuses("PATCH", "/api/v1/settings/system", {"helpdesk_title": ""}, because="title")
    bridge.refuses("PATCH", "/api/v1/settings/tasks", {"task_alert_active": True, "task_alert_admin": False,
                                                       "task_alert_dept_manager": False, "task_alert_dept_members": False},
                   because="recipient")
    assert _snapshot(bridge) == before


def test_the_dropdown_choices_come_from_osticket_and_include_every_stored_value(bridge):
    """The options are enumerated by osTicket's PHP; whatever the instance has stored must be one of them."""
    options = bridge.read("/api/v1/settings/options")["options"]
    stored = {"agents": ["agent_name_format", "agent_avatar", "agent_passwd_policy"],
              "users": ["client_name_format", "client_avatar", "client_passwd_policy", "client_registration"],
              "system": ["log_level", "acl_backend"], "tickets": ["ticket_lock", "default_ticket_queue"]}
    for section, keys in stored.items():
        got = bridge.read(f"/api/v1/settings/{section}")
        for key in keys:
            values = [o["value"] for o in options[key]]
            assert options[key] and all(o["label"] for o in options[key]), key
            assert str(got[key]) in values or got[key] in ("", 0), (key, got[key], values)
    assert [o["value"] for o in options["client_registration"]] == ["disabled", "public", "closed"]
    assert [o["value"] for o in options["log_level"]] == ["0", "3", "2", "1"]
