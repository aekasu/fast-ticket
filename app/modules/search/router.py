"""Search endpoints.

Permissions:
  None specific. Requires basic valid token (any valid agent).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_session
from app.modules.search import service
from app.modules.search.models import SearchResponse

router = APIRouter(tags=["search"])

@router.get("/search", response_model=SearchResponse, summary="Global search")
async def global_search(
    q: str = Query(..., min_length=2, description="Search query string"),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),  # Any valid agent token works
    session: AsyncSession = Depends(get_session),
):
    """
    Search across tickets, users, and organizations.
    """
    return await service.global_search(session, query=q, limit=limit)
