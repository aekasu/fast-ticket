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


@pytest.fixture(autouse=True)
def _staff_is_active_by_default():
    """``get_current_user`` confirms the staff member is still enabled in osTicket; there is no
    osTicket database here, so every staff id is active unless a test says otherwise."""
    from unittest.mock import AsyncMock, patch

    with patch("app.core.auth.repo.get_staff_active", new=AsyncMock(return_value=True)):
        yield


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
        async def _fake_user():
            return {
                "staff_id": staff_id,
                "username": username,
                "name": name,
                "email": email,
                "is_admin": is_admin,
                "permissions": permissions or [],
            }

        app.dependency_overrides[get_current_user] = _fake_user

    yield _install
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def admin_user(auth_user):
    """Convenience: installs an admin auth override."""
    auth_user(is_admin=True)


# ── HTTP client ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """Async HTTP client wired to the test ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
