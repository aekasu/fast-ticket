"""An agent's own profile and password, against the live stack. Uses a throwaway agent — never the admin."""

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, Bridge, login_as, login_raw

pytestmark = pytest.mark.e2e

PASSWORD = "E2e-Pass-9x!Qz"
NEW_PASSWORD = "E2e-New-7y!Rw"


def _agent(bridge, uniq):
    username = f"eprof{uniq}"
    bridge.create("/api/v1/staff", {"username": username, "firstname": "Pro", "lastname": "File",
                                    "email": f"{username}@{DELIVERABLE_DOMAIN}", "dept_id": 2, "role_id": 3,
                                    "password": PASSWORD, "is_active": True, "is_admin": False},
                  cleanup="/api/v1/staff/{id}")
    headers = login_as(username, PASSWORD)
    return username, Bridge(httpx.Client(base_url=BASE_URL, timeout=30, headers=headers))


def test_an_agent_edits_their_own_profile_and_what_they_do_not_send_stays(bridge, uniq):
    username, me = _agent(bridge, uniq)
    before = me.read("/api/v1/profile")
    assert (before["username"], before["firstname"], before["onvacation"]) == (username, "Pro", False)

    me.command("PATCH", "/api/v1/profile", {"phone": "5551234", "signature": "regards", "max_page_size": 25,
                                           "auto_refresh_rate": 5, "default_signature_type": "mine",
                                           "thread_view_order": "asc", "reply_redirect": "Queue", "onvacation": True})
    got = me.read("/api/v1/profile")
    assert (got["max_page_size"], got["auto_refresh_rate"], got["default_signature_type"], got["onvacation"]) == (25, 5, "mine", True)
    assert (got["thread_view_order"], got["reply_redirect"], got["signature"]) == ("asc", "Queue", "regards")
    assert got["phone"].replace("-", "").replace(" ", "").endswith("5551234")

    me.command("PATCH", "/api/v1/profile", {"lastname": "Renamed"})                 # the rest is preserved
    again = me.read("/api/v1/profile")
    assert (again["lastname"], again["max_page_size"], again["thread_view_order"], again["onvacation"]) == ("Renamed", 25, "asc", True)
    assert again["firstname"] == "Pro" and again["email"] == before["email"]
    me.command("PATCH", "/api/v1/profile", {"onvacation": False})
    assert me.read("/api/v1/profile")["onvacation"] is False

    other = f"eprofx{uniq}@{DELIVERABLE_DOMAIN}"
    bridge.create("/api/v1/staff", {"username": f"eprofx{uniq}", "firstname": "Other", "lastname": "Agent", "email": other,
                                    "dept_id": 2, "role_id": 3, "password": PASSWORD, "is_active": True, "is_admin": False},
                  cleanup="/api/v1/staff/{id}")
    me.refuses("PATCH", "/api/v1/profile", {"email": other}, because="already in use")
    me.refuses("PATCH", "/api/v1/profile", {"email": "nobody@no-such-domain.invalid"}, because="valid email")
    me.refuses("PATCH", "/api/v1/profile", {"default_signature_type": "mine", "signature": ""}, because="signature")
    assert bridge.read(f"/api/v1/staff/{[s for s in bridge.read('/api/v1/staff?page_size=200')['items'] if s['username'] == username][0]['id']}")["lastname"] == "Renamed"


def test_changing_the_password_needs_the_current_one_and_ends_old_tokens(bridge, uniq):
    username, me = _agent(bridge, uniq)
    me.refuses("POST", "/api/v1/profile/password", {"current_password": "wrong", "new_password": NEW_PASSWORD}, because="incorrect")
    assert login_raw(username, PASSWORD).status_code == 200                        # still the old one
    me.command("POST", "/api/v1/profile/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert login_raw(username, PASSWORD).status_code != 200
    assert login_raw(username, NEW_PASSWORD).status_code == 200
    assert me.get("/api/v1/profile").status_code == 401                            # the token from before the change is void
