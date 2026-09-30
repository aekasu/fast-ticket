"""Search endpoints.

Permissions:
  search.all (osTicket's "search everything"), or an administrator. Until read scoping (S3) can narrow
  results to what an agent may see, search is limited to those who may see everything anyway.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_perm
from app.core.db import get_session
from app.modules.search import service
from app.modules.search.models import SearchResponse

router = APIRouter(tags=["search"])

@router.get("/search", response_model=SearchResponse, summary="Global search")
async def global_search(
    q: str = Query(..., min_length=2, description="Search query string"),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = require_perm("search.all", admin_ok=True),
    session: AsyncSession = Depends(get_session),
):
    """
    Search across tickets, users, and organizations.
    """
    return await service.global_search(session, query=q, limit=limit)
