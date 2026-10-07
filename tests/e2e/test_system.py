"""System logs and system information against the live stack. A log row is planted with SQL and deleted through the API."""

import os

import pytest

from tests.e2e.test_attachments import sql

pytestmark = pytest.mark.e2e


def test_system_information_comes_from_osticket(bridge):
    info = bridge.read("/api/v1/system/info")
    assert info["osticket_version"] and info["php_version"].count(".") >= 1 and info["mysql_version"]
    assert any(e["key"] == "json" and e["loaded"] for e in info["extensions"])
    assert info["schema_signature"] and info["space_used_mib"] > 0
    assert {s["name"] for s in info["php_settings"]} == {"cgi.fix_pathinfo", "date.timezone"}
    assert any(p["code"] == "en_US" for p in info["language_packs"])


def test_logs_are_listed_filtered_read_and_deleted(bridge, uniq):
    if not os.environ.get("MYSQL_PASSWORD"):
        pytest.skip("needs MYSQL_PASSWORD to plant a log entry")
    title = f"e2e log {uniq}"
    log_id = int(sql(f"INSERT INTO ost_syslog (log_type, title, log, logger, ip_address, created, updated) "
                     f"VALUES ('Debug', '{title}', 'planted text', 'e2e', '127.0.0.1', NOW(), NOW()); SELECT LAST_INSERT_ID();")[-1][0])
    bridge._cleanups.append(lambda: sql(f"DELETE FROM ost_syslog WHERE log_id = {log_id}"))

    listed = bridge.read("/api/v1/logs?type=Debug&page_size=200")
    assert listed["total"] >= 1 and all(i["type"] == "Debug" for i in listed["items"])
    assert any(i["id"] == log_id and i["title"] == title for i in listed["items"])
    assert bridge.read("/api/v1/logs?type=Error&page_size=1")["items"][0]["type"] == "Error"
    ascending = [i["id"] for i in bridge.read("/api/v1/logs?sort=id&descending=false&page_size=5")["items"]]
    assert ascending == sorted(ascending)
    got = bridge.read(f"/api/v1/logs/{log_id}")
    assert (got["log"], got["logger"], got["ip_address"]) == ("planted text", "e2e", "127.0.0.1")

    done = bridge.command("POST", "/api/v1/logs/delete", {"ids": [log_id, 99999999]})
    assert done["result"]["deleted"] == 1
    assert bridge.get(f"/api/v1/logs/{log_id}").status_code == 404
