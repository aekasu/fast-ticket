"""The agent directory and the assign-form choices (read-only, scoped as osTicket scopes them)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.names import agent_name
from app.integration.osticket import read_repository as repo
from app.modules.agents.models import AgentDirectoryItem, AgentDirectoryResponse, Assignee, AssigneeOptions


async def list_directory(
    session: AsyncSession, *, access, q: str | None, dept_id: int | None, sort: str, descending: bool,
    page: int, page_size: int,
) -> AgentDirectoryResponse:
    fmt = await repo.get_agent_name_format(session)
    rows, total = await repo.list_directory_agents(
        session, access=access, q=q, dept_id=dept_id, sort=sort, descending=descending, name_format=fmt,
        limit=page_size, offset=(page - 1) * page_size,
    )
    items = [
        AgentDirectoryItem(**{**row, "name": agent_name(row["firstname"], row["lastname"], fmt)})
        for row in rows
    ]
    return AgentDirectoryResponse(items=items, total=total, page=page, page_size=page_size)


async def assignee_options(session: AsyncSession, *, access, dept_id: int) -> AssigneeOptions:
    """Agents and teams the caller may pick when assigning an object of department ``dept_id``."""
    fmt = await repo.get_agent_name_format(session)
    agents = await repo.list_assignable_agents(session, access=access, dept_id=dept_id, name_format=fmt)
    teams = await repo.list_assignable_teams(session, access=access)
    return AssigneeOptions(
        agents=[Assignee(id=a["id"], name=agent_name(a["firstname"], a["lastname"], fmt)) for a in agents],
        teams=[Assignee(id=t["id"], name=t["name"]) for t in teams],
    )
