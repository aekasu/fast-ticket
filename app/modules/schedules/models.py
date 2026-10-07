from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

ScheduleType = Literal["business_hours", "holidays"]
Repeats = Literal["never", "daily", "weekly", "weekdays", "weekends", "monthly", "yearly"]
HHMM = r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$"


class ScheduleEntry(BaseModel):
    """One line of a schedule, stored as osTicket stores it: ``repeats`` says how the others read.

    * never: the day ``starts_on``.   * daily / weekdays / weekends: every such day.
    * weekly: ``day`` 1-7 (Monday-Sunday).
    * monthly: the day of the month of ``starts_on`` (``week`` empty; ``day`` is then optional and must match), or ``week``
      1-5 / -1 (last) with ``day`` 1-7 (that weekday).
    * yearly: the date of ``starts_on`` (``week`` empty; ``day`` and ``month`` optional and must match), or ``week``
      1-5 / -1 with ``day`` 1-7 in ``month`` 1-12.
    """

    id: int
    name: str
    repeats: Repeats
    starts_on: date
    starts_at: str = Field(description="HH:MM:SS")
    ends_at: str = Field(description="HH:MM:SS")
    all_day: bool
    stops_on: Optional[date] = None
    day: Optional[int] = None
    week: Optional[int] = None
    month: Optional[int] = None
    description: str = Field(default="", description="osTicket's own sentence for the entry, e.g. 'Weekly on Monday (08:00 am - 05:00 pm)'")


class ScheduleItem(BaseModel):
    id: int
    name: str
    type: ScheduleType
    timezone: Optional[str] = Field(default=None, description="Empty: the schedule floats with the viewer's timezone")
    description: str = ""
    entry_count: int
    created: Instant
    updated: Instant


class ScheduleDetail(ScheduleItem):
    holiday_schedule_ids: list[int] = Field(default=[], description="Business hours only: the holiday schedules that apply")
    entries: list[ScheduleEntry]


class ScheduleList(BaseModel):
    items: list[ScheduleItem]
    total: int
    page: int
    page_size: int


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: ScheduleType
    timezone: Optional[str] = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=255)
    copy_from_id: Optional[int] = Field(default=None, gt=0, description="Start from a copy of this schedule's entries (same type)")
    holiday_schedule_ids: list[int] = Field(default=[], max_length=50)


class ScheduleUpdate(BaseModel):
    """The type cannot change."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    timezone: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=255)
    holiday_schedule_ids: Optional[list[int]] = Field(default=None, max_length=50)


class EntryFields(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    repeats: Repeats
    starts_on: date
    all_day: bool = False
    starts_at: Optional[str] = Field(default=None, pattern=HHMM, description="Needed unless all_day")
    ends_at: Optional[str] = Field(default=None, pattern=HHMM, description="Needed unless all_day")
    stops_on: Optional[date] = Field(default=None, description="Repeats until this day (empty: forever)")
    day: Optional[int] = Field(default=None, ge=1, le=31)
    week: Optional[int] = Field(default=None, ge=-1, le=5)
    month: Optional[int] = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _the_fields_the_repeat_needs(self):
        if not self.all_day and not (self.starts_at and self.ends_at):
            raise ValueError("starts_at and ends_at are needed unless the entry is all day")
        if self.week == 0:
            raise ValueError("week is 1-5, or -1 for the last")
        if self.repeats == "weekly" and not (self.day and self.day <= 7):
            raise ValueError("a weekly entry needs day 1-7 (Monday-Sunday)")
        if self.repeats == "monthly" and self.week and not (self.day and self.day <= 7):
            raise ValueError("a monthly entry on the n-th weekday needs day 1-7 (Monday-Sunday)")
        if self.repeats == "yearly" and self.week and not (self.month and self.day and self.day <= 7):
            raise ValueError("a yearly entry on the n-th weekday needs month and day 1-7 (Monday-Sunday)")
        return self


class EntryCreate(EntryFields):
    pass


class EntryUpdate(EntryFields):
    """An entry is replaced as a whole (osTicket's entry form posts every field)."""
