"""The pure parts of `make doctor`, plus the checks that can run against SQLite."""

import pytest

from app.integration.osticket import doctor
from app.integration.osticket import read_repository as repo


def test_tested_versions():
    assert doctor.is_tested_version("v1.18.1")
    assert doctor.is_tested_version("v1.18.3")
    assert not doctor.is_tested_version("v1.17.4")
    assert not doctor.is_tested_version("v1.19.0")
    assert not doctor.is_tested_version(None)


def test_handshake_url_is_derived_from_the_auth_url():
    assert doctor.handshake_url("http://osticket/api/auth/integration") == "http://osticket/api/fast-ticket/handshake"
    assert doctor.handshake_url("https://h.example/sub/api/auth/integration/") == "https://h.example/sub/api/fast-ticket/handshake"


def test_every_read_function_can_be_exercised():
    """A new read function whose arguments the doctor cannot guess would silently go unchecked."""
    import inspect

    unknown = [
        name
        for name, func in inspect.getmembers(repo, inspect.iscoroutinefunction)
        if not name.startswith("_") and func.__module__ == repo.__name__
        and doctor.read_call_kwargs(func) is None
    ]
    assert not unknown, f"teach doctor.read_call_kwargs about: {unknown}"


def test_read_call_kwargs_uses_harmless_values():
    assert doctor.read_call_kwargs(repo.list_departments) == {"limit": 1, "offset": 0, "access": doctor.DOCTOR_ACCESS}
    assert doctor.read_call_kwargs(repo.get_ticket) == {"ticket_id": 0, "access": doctor.DOCTOR_ACCESS}
    assert doctor.read_call_kwargs(repo.search_all) == {
        "query": "doctor", "limit": 1, "access": doctor.DOCTOR_ACCESS, "include_directory": True,
    }


@pytest.mark.parametrize(
    "grants, broad",
    [
        (["GRANT ALL PRIVILEGES ON `osticket`.* TO `osticket`@`%`"], True),
        (["GRANT ALL PRIVILEGES ON *.* TO `root`@`%`"], True),
        (["GRANT SELECT, INSERT, UPDATE, DELETE ON `osticket`.* TO `u`@`%`"], True),
        (["GRANT USAGE ON *.* TO `u`@`%`", "GRANT SELECT ON `osticket`.* TO `u`@`%`"], False),
        (
            [
                "GRANT SELECT ON `osticket`.* TO `u`@`%`",
                "GRANT INSERT, DELETE ON `osticket`.`ost_integration_inbox` TO `u`@`%`",
                "GRANT UPDATE, DELETE ON `osticket`.`ost_integration_outbox` TO `u`@`%`",
            ],
            False,
        ),
    ],
)
def test_broad_grants_are_detected(grants, broad):
    assert doctor.grants_are_broad(grants) is broad


def test_least_privilege_advice_names_the_prefixed_queue_tables():
    grants = doctor.least_privilege_grants("osticket", "api", "tk_")
    assert any("`tk_integration_inbox`" in g and "INSERT" in g for g in grants)
    assert any("`tk_integration_outbox`" in g and "UPDATE" in g for g in grants)
    assert not doctor.grants_are_broad(grants)


@pytest.mark.asyncio
async def test_handshake_ok_and_version_warning():
    async def fetch(url, key):
        return {"plugin_version": "1.0", "php_version": "8.2", "osticket_version": "v1.17.4",
                "table_prefix": "ost_", "tables_ready": True, "worker_available": True}

    checks = {c.name: c for c in await doctor.check_handshake(fetch)}
    assert checks["bridge plugin handshake"].status == doctor.OK
    assert checks["osTicket version"].status == doctor.WARN


@pytest.mark.asyncio
async def test_handshake_flags_a_prefix_mismatch_and_missing_tables():
    async def fetch(url, key):
        return {"plugin_version": "1.0", "osticket_version": "v1.18.1", "table_prefix": "tk_",
                "tables_ready": False, "worker_available": False}

    checks = {c.name: c for c in await doctor.check_handshake(fetch)}
    assert checks["table prefix agreement"].status == doctor.FAIL
    assert checks["plugin tables"].status == doctor.FAIL
    assert checks["inbox worker"].status == doctor.WARN


@pytest.mark.asyncio
async def test_handshake_unreachable_is_a_failure():
    async def fetch(url, key):
        raise RuntimeError("403 — FASTAPI_INTEGRATION_KEY does not match the plugin's key")

    [check] = await doctor.check_handshake(fetch)
    assert check.status == doctor.FAIL and "403" in check.detail


@pytest.mark.asyncio
async def test_a_database_without_osticket_fails_the_schema_check():
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:   # SQLite: no osTicket tables at all
        assert (await doctor.check_database(session)).status == doctor.OK
        assert (await doctor.check_osticket_schema(session)).status == doctor.FAIL
        checks = await doctor.check_read_queries(session)
    assert checks[0].status == doctor.FAIL and "list_tickets" in checks[0].detail


@pytest.mark.parametrize(
    "peak, limit, verdict",
    [(5, 151, doctor.OK), (120, 151, doctor.OK), (121, 151, doctor.WARN), (150, 151, doctor.WARN),
     (151, 151, doctor.FAIL), (152, 151, doctor.FAIL), (10, 0, doctor.OK)],
)
def test_connection_pressure(peak, limit, verdict):
    """The high-water mark against max_connections: 80% warns, reaching the limit fails (a leaked
    connection per worker command once did exactly that and made logins answer 500)."""
    assert doctor.connection_pressure(peak, limit) == verdict
