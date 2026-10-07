from datetime import date

from app.integration.osticket.bridge_read import bridge_json
from app.modules.dashboard.models import DashboardReport, Period, ReportGroup

# osTicket's own keys (OverviewReport::$end_choices) and group names
_PERIODS = {"until_today": "now", "one_week": "+7 days", "two_weeks": "+14 days", "one_month": "+1 month", "one_quarter": "+3 months"}
_GROUPS = {"department": "dept", "topic": "topic", "agent": "staff"}


async def activity_report(staff_id: int, *, start: date | None, period: Period, group: ReportGroup) -> DashboardReport:
    result = await bridge_json("dashboard.report", staff_id, {
        "start": start.isoformat() if start else "", "period": _PERIODS[period], "group": _GROUPS[group]})
    return DashboardReport(start=start, period=period, group=group, plot=result["plot"], table=result["table"])
