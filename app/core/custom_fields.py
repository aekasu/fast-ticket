"""Custom form-field answers, shared by tickets, tasks, users and organizations.

A request carries ``custom_fields`` as ``{variable name: value}`` — the names are the ``name`` of fields
defined on the object's forms (see ``/forms/{id}/fields``). Values are what osTicket's field parsers accept:
text, a number, a boolean, a choice key, or a list of choice keys for multi-select fields. An empty string
clears a text answer. The bridge refuses names that are not editable fields of the object's forms.

A read returns the staff-visible answers as stored (``CustomFieldAnswer``): the text, and for choice-like
fields the key in ``value_id`` with the JSON label map in ``value``.
"""

import json
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, StringConstraints

CustomFieldName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_-][A-Za-z0-9_-]*$", max_length=64)]
CustomFieldText = Annotated[str, StringConstraints(max_length=100000)]
CustomFieldValue = Union[CustomFieldText, int, float, bool, list[Union[CustomFieldText, int]]]
CustomFields = Annotated[dict[CustomFieldName, CustomFieldValue], Field(max_length=64)]


class AnswerChoice(BaseModel):
    key: str
    label: str


class CustomFieldAnswer(BaseModel):
    """One stored answer. ``value`` / ``value_id`` are osTicket's stored form; ``kind`` and the typed values beside
    them say what it means, so a client does not decode osTicket's JSON itself."""

    name: str
    label: str
    type: str
    value: Optional[str] = None
    value_id: Optional[int] = None
    kind: Literal["text", "bool", "choices"] = Field(
        default="text", description="text: use ``value``; bool: use ``bool_value``; choices: use ``choices``")
    bool_value: Optional[bool] = Field(default=None, description="A bool field's answer (kind=bool)")
    choices: Optional[list[AnswerChoice]] = Field(
        default=None, description="The selected choices of a choices or custom-list field (kind=choices), key and label; "
        "empty when nothing is selected. A custom-list field's key is the list item's id.")


def _choice_labels(configuration) -> dict[str, str]:
    """``key:Label`` lines of a choices field's configuration."""
    try:
        text = (json.loads(configuration) if isinstance(configuration, (str, bytes)) else configuration or {}).get("choices") or ""
    except (TypeError, ValueError, AttributeError):
        return {}
    labels = {}
    for line in str(text).splitlines():
        key, sep, label = line.partition(":")
        if sep and key.strip():
            labels[key.strip()] = label.strip()
    return labels


def answer_from_row(row: dict) -> CustomFieldAnswer:
    """An answer row (name, label, type, value, value_id, configuration) as a typed ``CustomFieldAnswer``.

    osTicket stores a choice as a JSON label map (``{"a":"Alpha"}``) — a multiple choice as such a map or as a list
    of keys — and a bool as ``"1"`` / ``"0"``; anything unreadable stays plain text.
    """
    base = dict(name=row["name"], label=row["label"], type=row["type"], value=row["value"], value_id=row["value_id"])
    kind_type, value = str(row["type"]), row["value"]
    if kind_type == "bool":
        flag = str(value or "").strip().lower() in ("1", "true", "yes")
        return CustomFieldAnswer(**base, kind="bool", bool_value=flag)
    if kind_type == "choices" or kind_type.startswith("list-"):
        try:
            parsed = json.loads(value) if value not in (None, "") else {}
        except (TypeError, ValueError):
            return CustomFieldAnswer(**base)
        labels = _choice_labels(row.get("configuration"))
        if isinstance(parsed, dict):
            picked = [AnswerChoice(key=str(k), label=str(v)) for k, v in parsed.items()]
        elif isinstance(parsed, list):
            picked = [AnswerChoice(key=str(k), label=labels.get(str(k), str(k))) for k in parsed]
        else:
            return CustomFieldAnswer(**base)
        return CustomFieldAnswer(**base, kind="choices", choices=picked)
    return CustomFieldAnswer(**base)


# fields every object type already exposes as top-level properties; left out of ``custom_fields`` on reads
BUILT_IN_FIELDS = {
    "T": {"subject", "message", "priority"},
    "U": {"name", "email", "phone", "notes"},
    "O": {"name", "address", "phone", "website", "notes"},
    "A": {"title", "description"},
}
