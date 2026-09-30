"""Shared test fixtures.

Design:
  - In-memory SQLite for speed and isolation.
  - Each test gets a fresh schema via ``setup_db`` (autouse).
  - Auth is overridden via ``auth_user`` fixture — accepts a ``permissions`` list
    so individual tests can specify exactly what rights the test agent has.
  - ``client`` provides an async HTTP client with the auth override active.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.main import app
from app.core.auth import get_current_user
from app.core.db import get_session

# ── In-memory SQLite engine ────────────────────────────────────────────────────

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, future=True)
TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _override_session():
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_session] = _override_session


# ── Schema lifecycle ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create all SQLModel tables before each test, drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


# ── Auth override ──────────────────────────────────────────────────────────────


def make_access(
    staff_id: int = 1,
    permissions: list[str] | None = None,
    is_admin: bool = False,
    *,
    username: str = "test-agent",
    name: str = "Test Agent",
    email: str = "agent@example.local",
    is_active: bool = True,
    dept_ids: frozenset[int] = frozenset({1}),
    assigned_only: bool = False,
):
    """An AccessContext for a test, from a flat permission list.

    Global permissions (user.*, org.*, faq.manage, ...) go on the staff member, role permissions
    (ticket.*, task.*, canned.manage) on their role in department 1. A key osTicket does not have raises,
    so a test cannot quietly grant something that does not exist.
    """
    from app.core.access import AccessContext
    from app.core.permissions import ALL_PERMISSIONS, GLOBAL_PERMISSIONS, ROLE_PERMISSIONS

    wanted = set(permissions or [])
    unknown = wanted - ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"not osTicket permission keys: {sorted(unknown)}")
    return AccessContext(
        staff_id=staff_id,
        username=username,
        name=name,
        email=email,
        is_active=is_active,
        is_admin=is_admin,
        global_perms=frozenset(wanted & GLOBAL_PERMISSIONS),
        primary_dept_id=1,
        dept_roles={1: frozenset(wanted & ROLE_PERMISSIONS)},
        dept_ids=dept_ids,
        assigned_only=assigned_only,
    )


@pytest.fixture(autouse=True)
def _access_from_test_token(monkeypatch):
    """Stand in for osTicket's tables when a test uses a real signed token.

    Production tokens carry identity only; ``get_current_user`` then loads the staff member's access
    from osTicket. There is no osTicket database here, so this reads the ``permissions`` and
    ``is_admin`` claims a *test* put in its token instead — which is why test tokens carry them and
    real ones do not. Tests of revocation or of a changing role patch ``app.core.auth.load_access_context``
    themselves.
    """
    from app.core import auth

    seen: dict = {}
    real_decode = auth.decode_access_token

    def spy(token):
        claims = real_decode(token)
        seen["claims"] = claims
        return claims

    async def fake_load(session, staff_id):
        claims = seen.get("claims", {})
        return make_access(
            staff_id,
            claims.get("permissions"),
            bool(claims.get("is_admin")),
            username=claims.get("username", "test-agent"),
            name=claims.get("name", "Test Agent"),
            email=claims.get("email", "agent@example.local"),
        )

    monkeypatch.setattr(auth, "decode_access_token", spy)
    monkeypatch.setattr(auth, "load_access_context", fake_load)


@pytest.fixture(autouse=True)
def _fresh_login_throttle():
    from app.modules.auth.router import login_throttle

    login_throttle.reset()
    yield
    login_throttle.reset()



@pytest.fixture
def auth_user():
    """Return a factory that installs a fake current_user with given permissions.

    Usage in a test:
        def test_needs_perm(auth_user):
            auth_user(permissions=["ticket.create"])
    """
    def _install(
        staff_id: int = 1,
        username: str = "test-agent",
        name: str = "Test Agent",
        email: str = "agent@example.local",
        is_admin: bool = False,
        permissions: list[str] | None = None,
    ):
        access = make_access(
            staff_id, permissions, is_admin, username=username, name=name, email=email
        )

        async def _fake_user():
            return {
                "staff_id": staff_id,
                "username": username,
                "name": name,
                "email": email,
                "is_admin": is_admin,
                "permissions": access.all_permissions(),
                "access": access,
            }

        app.dependency_overrides[get_current_user] = _fake_user

    yield _install
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def admin_user(auth_user):
    """An administrator with every permission switched on — like the account osTicket's installer
    creates. (There is no admin bypass in ``require_perm``: osTicket has none.)"""
    from app.core.permissions import ALL_PERMISSIONS

    auth_user(is_admin=True, permissions=sorted(ALL_PERMISSIONS))


# ── HTTP client ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """Async HTTP client wired to the test ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
