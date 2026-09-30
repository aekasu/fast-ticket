"""Root-level conftest: set required environment variables before any app import.

This file runs before pytest collects any tests, ensuring that the Settings
object (which reads from environment at import time) receives valid values.
Tests that need different values can override specific env vars using
monkeypatch or by reimporting config inside the test.
"""

import os

# ── Required env vars for testing (all safe dummy values) ─────────────────────
# DATABASE_URL: use SQLite in-memory so no MySQL is needed.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# FASTAPI_INTEGRATION_KEY: must be ≥32 chars. Dummy value is fine for tests.
os.environ.setdefault("FASTAPI_INTEGRATION_KEY", "test-integration-key-32-chars-long!!")

# JWT_SECRET_KEY: any string works for test token mint/verify.
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-jwt-secret-key-48-chars-long-for-testing!!"
)

# OSTICKET_AUTH_URL: not called in tests (auth is overridden).
os.environ.setdefault("OSTICKET_AUTH_URL", "http://localhost/not-used-in-tests")

# CORS_ORIGINS: empty for tests.
os.environ.setdefault("CORS_ORIGINS", "")

# AUTO_CREATE_SCHEMA: disabled — each test fixture creates tables directly.
os.environ.setdefault("AUTO_CREATE_SCHEMA", "false")
# The suite fires far more than the production default limit from one address.
os.environ.setdefault("RATE_LIMIT_DEFAULT", "100000/minute")
