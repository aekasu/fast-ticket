"""Agent directory.

Permission map — ``GET /agents`` is osTicket's staff directory (scp/directory.php): any agent may open it, and it
lists the agents of the departments the caller can see (``Staff::getDeptAgents``), unless they hold
``visibility.agents``. The assign-form choices live beside the objects they belong to
(``GET /tickets/{id}/assignees``, ``GET /tasks/{id}/assignees``).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_staff
from app.modules.agents import service
from app.modules.agents.models import AgentDirectoryResponse, DirectorySort

router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=AgentDirectoryResponse, summary="Agent directory")
async def list_agents(
    q: str | None = Query(default=None, max_length=100, description="Name or email (contains), a phone number, or an exact email"),
    dept_id: int | None = Query(default=None, ge=1, description="Primary department"),
    sort: DirectorySort = Query(default="name"),
    descending: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_directory(
        session, access=current_user["access"], q=q, dept_id=dept_id, sort=sort, descending=descending,
        page=page, page_size=page_size,
    )
