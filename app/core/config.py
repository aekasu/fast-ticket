import secrets
import sys
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings are loaded purely from environment variables.
# No env_file is used — it blocks on network/overlay filesystems and is
# redundant in Docker where vars are injected via docker-compose.yml.
# See .env.example for documentation of all required variables.
# For local non-Docker dev: export vars manually or use `direnv`.


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        ...,
        description="Async SQLAlchemy URL for the osTicket database (mysql+aiomysql://…).",
    )
    AUTO_CREATE_SCHEMA: bool = Field(
        default=False,
        description=(
            "Create the integration tables from the SQLModel classes at startup. Off by default: "
            "the bridge plugin creates them (bridge/schema.php). For a local DB with no plugin."
        ),
    )

    OSTICKET_TABLE_PREFIX: str = Field(
        default="ost_",
        description="Table prefix chosen when osTicket was installed (TABLE_PREFIX in ost-config.php).",
        pattern=r"^[A-Za-z0-9_]{0,32}$",
    )

    # ── osTicket bridge ───────────────────────────────────────────────────────
    FASTAPI_INTEGRATION_KEY: str = Field(
        ...,
        description="Shared secret with the PHP bridge plugin. Must be at least 32 chars.",
        min_length=32,
    )
    OSTICKET_AUTH_URL: str = Field(
        default="http://osticket/api/auth/integration",
        description="URL of the PHP bridge authentication endpoint.",
    )
    OSTICKET_AUTH_TIMEOUT: float = Field(
        default=10.0,
        description="Timeout in seconds for calls to the osTicket auth endpoint.",
        gt=0,
    )

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = Field(
        default_factory=lambda: secrets.token_hex(48),
        min_length=32,
        description=(
            "HS256 signing key, at least 32 characters. If unset a random key is generated per "
            "process (dev-only: tokens die on restart and differ between workers)."
        ),
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: Annotated[int, Field(default=30, gt=0, le=1440)]

    REQUIRE_JWT_SECRET: bool = Field(
        default=False,
        description="Set true to abort startup if JWT_SECRET_KEY is not explicitly configured.",
    )

    # ── Login protection ──────────────────────────────────────────────────────
    LOGIN_MAX_ATTEMPTS: int = Field(
        default=5, ge=1,
        description="Failed logins per (username, client IP) before that pair is locked out.",
    )
    LOGIN_LOCKOUT_SECONDS: int = Field(default=300, ge=1, description="How long a lockout lasts.")

    # ── Rate limiting & request limits ────────────────────────────────────────
    RATE_LIMIT_DEFAULT: str = Field(
        default="300/minute",
        description="Default limit per client IP for every HTTP route (slowapi syntax).",
    )
    RATE_LIMIT_STORAGE_URI: str = Field(
        default="memory://",
        description="Where limit counters live. memory:// is per process; use redis://… to share.",
    )
    TRUSTED_PROXY_COUNT: int = Field(
        default=0, ge=0,
        description=(
            "Reverse proxies in front of the API that append to X-Forwarded-For. 0 = the header is "
            "ignored (clients can spoof it); N = the client is the Nth address from the right."
        ),
    )
    MAX_REQUEST_BYTES: int = Field(
        default=1_000_000, ge=1_000, description="Largest accepted request body; bigger gets 413."
    )

    # ── Security ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = Field(
        default="",
        description="Comma-separated allowed CORS origins. Empty = none allowed.",
    )

    # ── Outbox worker ─────────────────────────────────────────────────────────
    EVENT_POLL_INTERVAL_SECONDS: float = Field(default=1.0, gt=0)
    EVENT_BATCH_SIZE: Annotated[int, Field(default=100, gt=0, le=1000)]
    EVENT_RETENTION_DAYS: Annotated[int, Field(default=30, gt=0)]

    # ── WebSocket ─────────────────────────────────────────────────────────────
    WS_RECHECK_SECONDS: float = Field(
        default=15.0, gt=0,
        description="How often open connections are re-validated (token expiry, account disabled, role changes).",
    )
    WS_MAX_CONNECTIONS_PER_STAFF: int = Field(default=10, ge=1, description="Open sockets one agent may hold.")
    WS_SEND_TIMEOUT_SECONDS: float = Field(
        default=5.0, gt=0, description="A client that cannot take a message within this long is dropped."
    )

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _jwt_secret_not_placeholder(cls, v: str) -> str:
        if v.startswith("replace-with"):
            raise ValueError("JWT_SECRET_KEY looks like a placeholder — set a real secret.")
        return v

    @field_validator("FASTAPI_INTEGRATION_KEY")
    @classmethod
    def _key_not_placeholder(cls, v: str) -> str:
        if v.startswith("replace-with"):
            raise ValueError(
                "FASTAPI_INTEGRATION_KEY looks like a placeholder — set a real secret."
            )
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a list, stripping blanks."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


def _load_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]
    if "JWT_SECRET_KEY" not in s.model_fields_set:
        # The field has a random default, so "unset" is only visible in model_fields_set.
        if s.REQUIRE_JWT_SECRET:
            print(
                "FATAL: REQUIRE_JWT_SECRET is true but JWT_SECRET_KEY is not set.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            "WARNING: JWT_SECRET_KEY is not set — using a random key for this process. Tokens "
            "will not survive a restart and differ between workers. Set it in production.",
            file=sys.stderr,
        )
    return s


settings = _load_settings()
