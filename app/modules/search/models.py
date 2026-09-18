"""Search domain models."""

from pydantic import BaseModel

class SearchResultItem(BaseModel):
    id: int
    type: str  # "ticket", "user", "organization"
    title: str
    subtitle: str
    url_path: str  # e.g., "/tickets/123"

class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
    total: int
