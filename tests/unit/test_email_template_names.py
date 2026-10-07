"""The message names the API shows are osTicket's own (EmailTemplateGroup::$all_names), kept in a data file."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_message_has_a_group_a_name_and_a_description():
    names = json.loads((ROOT / "app" / "modules" / "emailtemplates" / "names.json").read_text())
    assert len(names) == 19 and "ticket.autoresp" in names and "task.overdue.alert" in names
    for code, info in names.items():
        assert set(info) == {"group", "name", "description"} and all(info.values()), code
    assert {i["group"] for i in names.values()} <= {"a.ticket.user", "b.ticket.staff", "c.task", "sys"}
