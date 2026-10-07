"""The Dashboard's activity report. Any enabled agent may ask (``require_staff()``, as osTicket's dashboard page);
which departments, topics and agents the rows cover is osTicket's own rule, applied by its report class in the bridge
(``Staff::getDepts()``, the agents the agent manages, ``stats.agents``)."""

from datetime import date

from fastapi import APIRouter, Query

from app.core.security import require_staff
from app.modules.dashboard import service
from app.modules.dashboard.models import DashboardReport, Period, ReportGroup

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/report", response_model=DashboardReport, summary="Activity report")
async def activity_report(
    start: date | None = Query(default=None, description="First day (default: a month ago)"),
    period: Period = Query(default="until_today", description="How far past `start` the report runs"),
    group: ReportGroup = Query(default="department"),
    current_user: dict = require_staff(),
):
    return await service.activity_report(current_user["staff_id"], start=start, period=period, group=group)
