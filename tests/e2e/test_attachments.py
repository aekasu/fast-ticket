"""Files on thread entries, against the live stack.

The API has no upload yet, so the file is planted the way osTicket stores one (a row in ost_file, its chunks, and an
ost_attachment of type 'H' on the entry) with SQL, and removed afterwards. Needs MYSQL_PASSWORD, like the oracle.
"""

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, login_as

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "E2e-Pass-9x!Qz"


def sql(statement: str) -> list[list[str]]:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-e", f"MYSQL_PWD={os.environ['MYSQL_PASSWORD']}", "db",
         "mariadb", "-uosticket", "osticket", "-N", "-B", "-e", statement],
        cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, f"SQL failed: {result.stderr[:300]}"
    return [line.split("\t") for line in result.stdout.strip().splitlines() if line]


@pytest.fixture(autouse=True)
def _needs_database():
    if not os.environ.get("MYSQL_PASSWORD"):
        pytest.skip("needs MYSQL_PASSWORD to plant a file")


def _plant(bridge, entry_id: int, uniq: str, *, backend="D", name="hello world.txt", mime="text/plain") -> tuple[int, int]:
    rows = sql(
        f"INSERT INTO ost_file (ft, bk, type, size, `key`, signature, name, created) VALUES "
        f"('T', '{backend}', '{mime}', 11, 'e2ekey{uniq}{backend}', 'e2esig{uniq}{backend}', '{name}', NOW()); SET @f = LAST_INSERT_ID(); "
        "INSERT INTO ost_file_chunk (file_id, chunk_id, filedata) VALUES (@f, 0, 'hello '), (@f, 1, 'world'); "
        f"INSERT INTO ost_attachment (object_id, type, file_id, name, inline) VALUES ({entry_id}, 'H', @f, NULL, 0); "
        "SELECT @f, LAST_INSERT_ID();")
    file_id, attachment_id = int(rows[-1][0]), int(rows[-1][1])
    bridge._cleanups.append(lambda: sql(
        f"DELETE FROM ost_attachment WHERE id = {attachment_id}; DELETE FROM ost_file_chunk WHERE file_id = {file_id}; "
        f"DELETE FROM ost_file WHERE id = {file_id}"))
    return file_id, attachment_id


def test_an_attached_file_is_listed_on_its_entry_and_downloads_intact(bridge, uniq):
    email = f"att.{uniq}@{DELIVERABLE_DOMAIN}"
    tid = bridge.create("/api/v1/tickets/", {"subject": f"e2e attach {uniq}", "message": "see file", "email": email,
                                             "name": "Attach", "dept_id": 1, "topic_id": 1})
    holder = bridge.read(f"/api/v1/tickets/{tid}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tid}"))
    entry = holder["thread"][0]
    assert entry["attachments"] == []

    _, attachment = _plant(bridge, entry["id"], uniq)
    got = bridge.read(f"/api/v1/tickets/{tid}")["thread"][0]["attachments"]
    assert got == [{"id": attachment, "name": "hello world.txt", "size": 11, "content_type": "text/plain", "inline": False}]

    resp = bridge.get(f"/api/v1/tickets/{tid}/attachments/{attachment}")
    assert resp.status_code == 200 and resp.content == b"hello world"
    assert resp.headers["content-disposition"].startswith("attachment;") and "hello%20world.txt" in resp.headers["content-disposition"]
    assert resp.headers["content-length"] == "11"

    # not attached to this ticket / not there at all / not a task's
    other = bridge.create("/api/v1/tickets/", {"subject": f"e2e attach2 {uniq}", "message": "m", "email": email,
                                               "name": "Attach", "dept_id": 1, "topic_id": 1})
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{other}"))
    assert bridge.get(f"/api/v1/tickets/{other}/attachments/{attachment}").status_code == 404
    assert bridge.get(f"/api/v1/tickets/{tid}/attachments/99999999").status_code == 404
    assert bridge.get(f"/api/v1/tasks/{tid}/attachments/{attachment}").status_code == 404

    # an agent who cannot open the ticket gets a 404 too: a Sales-only agent, assigned-only
    username = f"eatt{uniq}"
    agent = bridge.create("/api/v1/staff", {"username": username, "firstname": "E2E", "lastname": "Att",
                                            "email": f"{username}@{DELIVERABLE_DOMAIN}", "dept_id": 2, "role_id": 3,
                                            "password": PASSWORD, "is_active": True, "is_admin": False},
                          cleanup="/api/v1/staff/{id}")
    outsider = login_as(username, PASSWORD)
    assert httpx.get(f"{BASE_URL}/api/v1/tickets/{tid}/attachments/{attachment}", headers=outsider).status_code == 404
    assert httpx.get(f"{BASE_URL}/api/v1/tickets/{tid}/attachments/{attachment}").status_code == 401


def test_a_file_kept_by_a_storage_plugin_is_refused_not_guessed(bridge, uniq):
    email = f"att2.{uniq}@{DELIVERABLE_DOMAIN}"
    tid = bridge.create("/api/v1/tickets/", {"subject": f"e2e attach3 {uniq}", "message": "m", "email": email,
                                             "name": "Attach", "dept_id": 1, "topic_id": 1})
    holder = bridge.read(f"/api/v1/tickets/{tid}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tid}"))
    _, attachment = _plant(bridge, holder["thread"][0]["id"], uniq, backend="F", name="elsewhere.bin", mime="application/zip")
    assert bridge.read(f"/api/v1/tickets/{tid}")["thread"][0]["attachments"][0]["name"] == "elsewhere.bin"   # still listed
    assert bridge.get(f"/api/v1/tickets/{tid}/attachments/{attachment}").status_code == 501
