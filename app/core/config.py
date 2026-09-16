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
        default=True,
        description="Create our integration tables at startup. Disable in production.",
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
        description=(
            "HS256 signing key. If unset a random key is generated per-process "
            "(dev-only; set explicitly in production)."
        ),
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: Annotated[int, Field(default=60, gt=0, le=1440)]

    REQUIRE_JWT_SECRET: bool = Field(
        default=False,
        description="Set true to abort startup if JWT_SECRET_KEY is not explicitly configured.",
    )

    # ── Security ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = Field(
        default="",
        description="Comma-separated allowed CORS origins. Empty = none allowed.",
    )

    # ── Outbox worker ─────────────────────────────────────────────────────────
    EVENT_POLL_INTERVAL_SECONDS: float = Field(default=3.0, gt=0)
    EVENT_BATCH_SIZE: Annotated[int, Field(default=100, gt=0, le=1000)]
    EVENT_MAX_RETRIES: Annotated[int, Field(default=3, ge=0)]
    EVENT_RETENTION_DAYS: Annotated[int, Field(default=30, gt=0)]

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
    if s.REQUIRE_JWT_SECRET and s.JWT_SECRET_KEY == "replace-with-48-byte-hex-secret":
        print(
            "FATAL: REQUIRE_JWT_SECRET is true but JWT_SECRET_KEY is not explicitly set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return s


settings = _load_settings()
