"""The CSV helpers: cells are safe to open in a spreadsheet, and an export is the list's pages, capped."""

from datetime import UTC, datetime

import pytest

from app.core import csv_export
from app.core.custom_fields import AnswerChoice, CustomFieldAnswer


@pytest.mark.parametrize("raw,expected", [
    ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"), ("+1", "'+1"), ("-2", "'-2"), ("@sum", "'@sum"),
    ("\tcmd", "'\tcmd"), ("plain", "plain"), (None, ""), (True, "Yes"), (False, "No"), (7, "7"),
])
def test_cells_cannot_run_as_formulas(raw, expected):
    assert csv_export.cell(raw) == expected


def test_a_datetime_is_iso_utc():
    assert csv_export.cell(datetime(2026, 10, 8, 6, 0, tzinfo=UTC)) == "2026-10-08T06:00:00Z"


def test_answers_read_as_osticket_shows_them():
    ans = lambda **kw: CustomFieldAnswer(name="n", label="L", type="text", **kw)   # noqa: E731
    assert csv_export.answer_cell(None) == ""
    assert csv_export.answer_cell(ans(value="=x")) == "'=x"
    assert csv_export.answer_cell(ans(kind="bool", bool_value=True)) == "Yes"
    assert csv_export.answer_cell(ans(kind="choices", choices=[AnswerChoice(key="a", label="Alpha"), AnswerChoice(key="b", label="Beta")])) == "Alpha, Beta"
    assert csv_export.answer_cell(ans(kind="choices", choices=[])) == ""


@pytest.mark.asyncio
async def test_rows_walk_every_page_and_stop_at_the_cap(monkeypatch):
    pages = {1: list(range(200)), 2: list(range(200, 450)[:200]), 3: [450, 451]}
    asked = []

    async def fetch(page, size):
        asked.append((page, size))
        return pages.get(page, [])

    assert [i async for i in csv_export.rows_of(fetch)] == list(range(200)) + list(range(200, 400)) + [450, 451]
    assert asked == [(1, 200), (2, 200), (3, 200)]

    monkeypatch.setattr(csv_export.settings, "EXPORT_MAX_ROWS", 250)
    asked.clear()
    assert len([i async for i in csv_export.rows_of(fetch)]) == 250 and asked == [(1, 200), (2, 200)]


@pytest.mark.asyncio
async def test_the_response_is_a_bom_headers_then_quoted_rows():
    async def rows():
        yield ["1", "a, b", 'say "hi"']

    response = csv_export.csv_response("tickets 2026.csv", ["A", "B", "C"], rows())
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b'\xef\xbb\xbfA,B,C\r\n1,"a, b","say ""hi"""\r\n'
    assert response.headers["content-disposition"] == 'attachment; filename="tickets_2026.csv"'
    assert response.media_type.startswith("text/csv")
