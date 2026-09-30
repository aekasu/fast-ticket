"""Search service layer."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope import UNRESTRICTED
from app.integration.osticket import read_repository as repo
from app.modules.search.models import SearchResponse, SearchResultItem


async def global_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    *,
    access,
) -> SearchResponse:
    """Search tickets the agent may see, plus users and organizations if they may use the directory.

    ``search.all`` ("search everything", osTicket's ``SearchBackend::PERM_EVERYTHING``) lifts the ticket
    scoping; ``user.dir`` opens the user/organization directory. There is no admin exemption: osTicket has none.
    """
    if not query or len(query) < 2:
        return SearchResponse(query=query, results=[], total=0)
        
    limit = min(limit, 50)
    rows = await repo.search_all(
        session,
        query=query,
        limit=limit,
        access=UNRESTRICTED if access.has_global("search.all") else access,
        include_directory=access.has_global("user.dir"),
    )
    
    results = []
    for row in rows:
        type_ = row["type"]
        path = ""
        if type_ == "ticket":
            path = f"/tickets/{row['id']}"
        elif type_ == "user":
            path = f"/users/{row['id']}"
        elif type_ == "organization":
            path = f"/organizations/{row['id']}"
            
        results.append(
            SearchResultItem(
                id=row["id"],
                type=type_,
                title=row["title"] or "",
                subtitle=row["subtitle"] or "",
                url_path=path,
            )
        )
        
    return SearchResponse(
        query=query,
        results=results,
        total=len(results),
    )
