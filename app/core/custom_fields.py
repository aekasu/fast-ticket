"""Custom form-field answers, shared by tickets, tasks, users and organizations.

A request carries ``custom_fields`` as ``{variable name: value}`` — the names are the ``name`` of fields
defined on the object's forms (see ``/forms/{id}/fields``). Values are what osTicket's field parsers accept:
text, a number, a boolean, a choice key, or a list of choice keys for multi-select fields. An empty string
clears a text answer. The bridge refuses names that are not editable fields of the object's forms.

A read returns the staff-visible answers as stored (``CustomFieldAnswer``): the text, and for choice-like
fields the key in ``value_id`` with the JSON label map in ``value``.
"""

from typing import Annotated, Optional, Union

from pydantic import BaseModel, Field, StringConstraints

CustomFieldName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_-][A-Za-z0-9_-]*$", max_length=64)]
CustomFieldText = Annotated[str, StringConstraints(max_length=100000)]
CustomFieldValue = Union[CustomFieldText, int, float, bool, list[Union[CustomFieldText, int]]]
CustomFields = Annotated[dict[CustomFieldName, CustomFieldValue], Field(max_length=64)]


class CustomFieldAnswer(BaseModel):
    name: str
    label: str
    type: str
    value: Optional[str] = None
    value_id: Optional[int] = None


# fields every object type already exposes as top-level properties; left out of ``custom_fields`` on reads
BUILT_IN_FIELDS = {
    "T": {"subject", "message", "priority"},
    "U": {"name", "email", "phone", "notes"},
    "O": {"name", "address", "phone", "website", "notes"},
    "A": {"title", "description"},
}
