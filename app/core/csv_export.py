"""CSV exports of the lists an agent sees (osTicket's "Export" on a queue or a directory).

An export is the list itself, page by page, so it carries exactly the rows (and only the rows) the agent's list shows;
it adds no access of its own. Cells are written for a spreadsheet: ticket subjects and names are typed by customers, so a
cell that starts with ``=``, ``+``, ``-`` or ``@`` is prefixed with a quote and cannot run as a formula (osTicket's own
exporter has that escape switched off).
"""

import csv
import io
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.custom_fields import CustomFieldAnswer

PAGE = 200
_FORMULA = re.compile(r"^[=+\-@\t\r]")


def cell(value: Any) -> str:
    """One value as the text of a CSV cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        text = value.isoformat().replace("+00:00", "Z")
    else:
        text = str(value)
    return "'" + text if _FORMULA.match(text) else text


def answer_cell(answer: CustomFieldAnswer | None) -> str:
    """A custom-field answer as osTicket shows it: the choices' labels, Yes/No, or the text."""
    if answer is None:
        return ""
    if answer.kind == "bool":
        return cell(answer.bool_value) if answer.bool_value is not None else ""
    if answer.kind == "choices":
        return cell(", ".join(c.label for c in answer.choices or []))
    return cell(answer.value)


def _line(values: Sequence[str]) -> bytes:
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow(values)
    return out.getvalue().encode("utf-8")


async def rows_of(fetch_page: Callable[[int, int], Awaitable[list]]) -> AsyncIterator[Any]:
    """Every item of a list, ``PAGE`` at a time, up to ``EXPORT_MAX_ROWS``. ``fetch_page(page, page_size)`` returns the
    page's items (an empty page ends it)."""
    sent, page = 0, 1
    while sent < settings.EXPORT_MAX_ROWS:
        items = await fetch_page(page, PAGE)
        if not items:
            return
        for item in items[: settings.EXPORT_MAX_ROWS - sent]:
            yield item
            sent += 1
        if len(items) < PAGE:
            return
        page += 1


def csv_response(filename: str, headers: Sequence[str], rows: AsyncIterator[Sequence[str]]) -> StreamingResponse:
    """The rows as a download. Starts with a UTF-8 byte-order mark, as osTicket's exports do, so Excel reads the text right."""

    async def body() -> AsyncIterator[bytes]:
        yield b"\xef\xbb\xbf" + _line(headers)
        async for row in rows:
            yield _line(row)

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "export.csv"
    return StreamingResponse(body(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{safe}"'})
