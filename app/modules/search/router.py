"""Search endpoints.

Permissions:
  Any enabled agent may search; what they find is narrowed to what osTicket lets them see.
    tickets                 only those the agent may open (the ticket-list visibility rule), unless they
                            hold ``search.all`` ("search everything"), which lifts that scoping
    users, organizations    only with ``user.dir`` (the directory permission)
  There is no admin exemption: osTicket has none.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_staff
from app.modules.search import service
from app.modules.search.models import SearchResponse

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse, summary="Global search")
async def global_search(
    q: str = Query(..., min_length=2, max_length=100, description="Search query string"),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    """
    Search tickets (that you may see), and users and organizations (if you may use the directory).
    """
    return await service.global_search(session, query=q, limit=limit, access=current_user["access"])
