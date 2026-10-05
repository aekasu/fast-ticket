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


def _b64(raw: bytes) -> str:
    import base64
    return base64.b64encode(raw).decode()


def _sweep_uploaded(bridge, name_like: str):
    """Uploaded files are rows in ost_file (+ chunks); osTicket removes them only in a clean-up job, so remove ours."""
    bridge._cleanups.append(lambda: sql(
        f"DELETE c FROM ost_file_chunk c JOIN ost_file f ON f.id = c.file_id WHERE f.name LIKE '{name_like}'; "
        f"DELETE FROM ost_file WHERE name LIKE '{name_like}'"))


def test_files_sent_with_a_reply_or_note_are_stored_listed_and_download_intact(bridge, uniq):
    email = f"up.{uniq}@{DELIVERABLE_DOMAIN}"
    _sweep_uploaded(bridge, f"e2e-up-{uniq}%")
    tid = bridge.create("/api/v1/tickets/", {"subject": f"e2e upload {uniq}", "message": "m", "email": email,
                                             "name": "Up", "dept_id": 1, "topic_id": 1})
    holder = bridge.read(f"/api/v1/tickets/{tid}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tid}"))
    url = f"/api/v1/tickets/{tid}"

    limits = bridge.read("/api/v1/settings/attachments")
    assert limits["osticket_max_file_bytes"] > 0 and limits["max_file_bytes"] <= limits["osticket_max_file_bytes"]

    body = f"hello from the api {uniq}".encode()
    binary = bytes(range(256)) * 20                                        # not text: every byte value survives
    done = bridge.command("POST", f"{url}/reply", {"message": "reply with files", "attachments": [
        {"name": f"e2e-up-{uniq}.txt", "content_type": "text/plain", "data": _b64(body)},
        {"name": f"e2e-up-{uniq}.bin", "data": _b64(binary)}]})
    note = bridge.command("POST", f"{url}/note", {"title": "n", "message": "note with a file", "attachments": [
        {"name": f"e2e-up-{uniq}-note.txt", "content_type": "text/plain", "data": _b64(b"note file")}]})

    thread = bridge.read(url)["thread"]
    reply = next(e for e in thread if e["id"] == done["result"]["entry_id"])
    names = {a["name"]: a for a in reply["attachments"]}
    assert set(names) == {f"e2e-up-{uniq}.txt", f"e2e-up-{uniq}.bin"}
    assert names[f"e2e-up-{uniq}.txt"]["size"] == len(body) and names[f"e2e-up-{uniq}.txt"]["content_type"] == "text/plain"
    assert names[f"e2e-up-{uniq}.bin"]["size"] == len(binary)
    for upload, expected in ((names[f"e2e-up-{uniq}.txt"], body), (names[f"e2e-up-{uniq}.bin"], binary)):
        got = bridge.get(f"{url}/attachments/{upload['id']}")
        assert got.status_code == 200 and got.content == expected          # the bytes are intact
    noted = next(e for e in thread if e["id"] == note["result"]["entry_id"])
    assert [a["name"] for a in noted["attachments"]] == [f"e2e-up-{uniq}-note.txt"]

    # osTicket's own maximum file size applies, with its words, and nothing is posted
    too_big = osticket_max = limits["osticket_max_file_bytes"] + 1
    before = len(bridge.read(url)["thread"])
    bridge.refuses("POST", f"{url}/reply", {"message": "too big", "attachments": [
        {"name": f"e2e-up-{uniq}-big.bin", "data": _b64(b"x" * too_big)}]}, because="too large")
    assert len(bridge.read(url)["thread"]) == before

    # the file bytes are not left in the inbox once the command is done (and never shown by the commands API)
    row = sql(f"SELECT payload FROM ost_integration_inbox WHERE id = {done['id']}")[0][0]
    assert _b64(body) not in row and "[redacted]" in row
    assert _b64(body) not in bridge.get(f"/api/v1/commands/{done['id']}").text


def test_files_sent_with_a_task_update_or_note_are_stored_and_download_intact(bridge, uniq):
    _sweep_uploaded(bridge, f"e2e-tup-{uniq}%")
    task = bridge.create("/api/v1/tasks/", {"title": f"e2e task upload {uniq}", "description": "d", "dept_id": 1},
                         cleanup="/api/v1/tasks/{id}")
    url = f"/api/v1/tasks/{task}"
    reply = bridge.command("POST", f"{url}/reply", {"message": "update with a file", "attachments": [
        {"name": f"e2e-tup-{uniq}.txt", "content_type": "text/plain", "data": _b64(b"task file one")}]})
    note = bridge.command("POST", f"{url}/note", {"note": "note with a file", "attachments": [
        {"name": f"e2e-tup-{uniq}-n.txt", "content_type": "text/plain", "data": _b64(b"task file two")}]})
    thread = bridge.read(url)["thread"]
    for done, expected in ((reply, b"task file one"), (note, b"task file two")):
        entry = next(e for e in thread if e["id"] == done["result"]["entry_id"])
        assert len(entry["attachments"]) == 1
        got = bridge.get(f"{url}/attachments/{entry['attachments'][0]['id']}")
        assert got.status_code == 200 and got.content == expected
