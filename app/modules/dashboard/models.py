import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Period = Literal["until_today", "one_week", "two_weeks", "one_month", "one_quarter"]
ReportGroup = Literal["department", "topic", "agent"]


class Series(BaseModel):
    event: str = Field(description="osTicket's event name, translated (Created, Closed, ...)")
    counts: list[int] = Field(description="One count per entry of `days`")


class ReportPlot(BaseModel):
    days: list[date]
    series: list[Series]


class ReportTable(BaseModel):
    columns: list[str] = Field(description="The group's name column, then Opened, Assigned, Overdue, Closed, Reopened, Deleted, Service Time, Response Time")
    rows: list[list[str]] = Field(description="Cells as osTicket shows them (counts and hours with one decimal)")


class DashboardReport(BaseModel):
    """osTicket's Dashboard activity report (``scp/dashboard.php``) for the signed-in agent: osTicket decides which
    departments, topics and agents the agent's rows may cover."""

    start: Optional[date] = Field(description="First day of the report (default: a month ago)")
    period: Period
    group: ReportGroup
    plot: ReportPlot
    table: ReportTable
