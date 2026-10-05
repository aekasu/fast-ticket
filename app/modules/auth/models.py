"""Response schemas of the auth routes (so API clients get typed shapes from the OpenAPI schema)."""

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")


class Me(BaseModel):
    """The agent's identity and current access, read from osTicket on every request."""

    staff_id: int
    username: str
    name: str
    email: str
    is_admin: bool
    assigned_only: bool
    global_permissions: list[str] = Field(description="Global permission keys (user.*, org.*, faq.manage, ...)")
    roles: dict[str, list[str]] = Field(description="Department id -> the role permission keys held there")
    department_ids: list[int] = Field(description="Departments the agent can see")
    team_ids: list[int]
