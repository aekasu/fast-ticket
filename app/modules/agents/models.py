from typing import Literal, Optional

from pydantic import BaseModel


class AgentDirectoryItem(BaseModel):
    id: int
    name: str                      # in the instance's agent name format
    firstname: str
    lastname: str
    email: str
    phone: str = ""
    phone_ext: str = ""
    mobile: str = ""
    dept_id: Optional[int] = None
    dept_name: str = ""


class AgentDirectoryResponse(BaseModel):
    items: list[AgentDirectoryItem]
    total: int
    page: int
    page_size: int


class Assignee(BaseModel):
    id: int
    name: str


class AssigneeOptions(BaseModel):
    """What osTicket's assign form offers for one ticket or task."""

    agents: list[Assignee]
    teams: list[Assignee]


DirectorySort = Literal["name", "email", "dept", "phone", "mobile", "ext", "created", "login"]
