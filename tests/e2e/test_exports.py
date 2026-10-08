"""CSV exports against the live stack: they are the lists, as CSV, scoped like the lists."""

import csv
import io

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, login_as

pytestmark = pytest.mark.e2e

PASSWORD = "E2e-Pass-9x!Qz"


def _rows(response) -> list[list[str]]:
    assert response.status_code == 200, f"{response.status_code} {response.text[:200]}"
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")                                 # the byte-order mark osTicket writes
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


def test_the_ticket_and_task_lists_export_as_csv_scoped_like_the_lists(bridge, uniq):
    subject = f"e2e export, \"quoted\" {uniq}"
    tid = bridge.create("/api/v1/tickets/", {"subject": subject, "message": "m", "email": f"ex.{uniq}@{DELIVERABLE_DOMAIN}",
                                             "name": "Export Person", "dept_id": 1, "topic_id": 1})
    holder = bridge.read(f"/api/v1/tickets/{tid}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tid}"))
    task = bridge.create("/api/v1/tasks/", {"title": f"e2e export task {uniq}", "description": "d", "dept_id": 1},
                         cleanup="/api/v1/tasks/{id}")

    table = _rows(bridge.get(f"/api/v1/tickets/export?q={uniq}"))
    assert table[0][:4] == ["Ticket #", "Subject", "From", "Status"] and len(table) == 2
    row = dict(zip(table[0], table[1]))
    assert row["Ticket #"] == holder["number"] and row["From"] == "Export Person" and row["Status"] == "Open"
    assert row["Subject"] == subject                                  # commas and quotes survive the CSV round trip
    assert row["Answered"] == "No" and row["Created"].endswith("Z")

    picked = _rows(bridge.get(f"/api/v1/tickets/export?q={uniq}&columns=number,subject"))
    assert picked[0] == ["Ticket #", "Subject"] and picked[1][0] == holder["number"]
    assert bridge.get("/api/v1/tickets/export?columns=number,nonsense").status_code == 422
    assert _rows(bridge.get(f"/api/v1/tickets/export?q={uniq}&queue=closed")) == [table[0]]      # filters apply: header only
    total = bridge.read("/api/v1/tickets/?page_size=1")["total"]
    assert len(_rows(bridge.get("/api/v1/tickets/export"))) - 1 == total                          # every page, not just the first

    tasks = _rows(bridge.get(f"/api/v1/tasks/export?q={uniq}"))
    assert tasks[0][:3] == ["Number", "Title", "Status"] and tasks[1][1] == f"e2e export task {uniq}" and len(tasks) == 2

    # an agent who cannot see them exports nothing of them
    username = f"eex{uniq}"
    bridge.create("/api/v1/staff", {"username": username, "firstname": "E2E", "lastname": "Ex", "email": f"{username}@{DELIVERABLE_DOMAIN}",
                                    "dept_id": 2, "role_id": 3, "password": PASSWORD, "is_active": True, "is_admin": False},
                  cleanup="/api/v1/staff/{id}")
    outsider = login_as(username, PASSWORD)
    assert len(_rows(httpx.get(f"{BASE_URL}/api/v1/tickets/export?q={uniq}", headers=outsider, timeout=60))) == 1
    assert len(_rows(httpx.get(f"{BASE_URL}/api/v1/tasks/export?q={uniq}", headers=outsider, timeout=60))) == 1
    assert httpx.get(f"{BASE_URL}/api/v1/tickets/export").status_code == 401


def test_the_directories_export_as_csv(bridge, uniq):
    org = bridge.create("/api/v1/orgs", {"name": f"e2e export org {uniq}"}, cleanup="/api/v1/orgs/{id}")
    user = bridge.create("/api/v1/users", {"name": f"E2E Export {uniq}", "email": f"exu.{uniq}@{DELIVERABLE_DOMAIN}", "org_id": org},
                         cleanup="/api/v1/users/{id}")
    users = _rows(bridge.get(f"/api/v1/users/export?q=E2E Export {uniq}"))
    assert users[0][:3] == ["Name", "Email", "Organization"] and len(users) == 2
    assert users[1][:3] == [f"E2E Export {uniq}", f"exu.{uniq}@{DELIVERABLE_DOMAIN}", f"e2e export org {uniq}"]
    orgs = _rows(bridge.get(f"/api/v1/orgs/export?q=e2e export org {uniq}&columns=name,users"))
    assert orgs == [["Name", "Users"], [f"e2e export org {uniq}", "1"]]
    assert bridge.get("/api/v1/users/export?columns=bad").status_code == 422
    assert bridge.read(f"/api/v1/users/{user}")["name"] == f"E2E Export {uniq}"

    # without the directory permission there is no export, as there is no list
    username = f"eexd{uniq}"
    bridge.create("/api/v1/staff", {"username": username, "firstname": "E2E", "lastname": "Dir", "email": f"{username}@{DELIVERABLE_DOMAIN}",
                                    "dept_id": 2, "role_id": 3, "password": PASSWORD, "is_active": True, "is_admin": False},
                  cleanup="/api/v1/staff/{id}")
    agent = login_as(username, PASSWORD)
    assert httpx.get(f"{BASE_URL}/api/v1/users/export", headers=agent).status_code == 403
    assert httpx.get(f"{BASE_URL}/api/v1/users", headers=agent).status_code == 403
