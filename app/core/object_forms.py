"""The extra forms an object carries (osTicket's "Manage Forms" dialog on a ticket, a user or an organization).

An object has its own built-in form (the ticket form, the user form, the organization form) and any number of extra
forms attached to it. Reading lists them in order with the extra forms that could be added; writing sends the whole
ordered list of extra-form ids (the dialog's behaviour): one left out is removed together with its answers.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ObjectForm(BaseModel):
    entry_id: int
    form_id: int
    title: str
    kind: Literal["built_in", "extra"] = Field(description="The object's own form cannot be removed or reordered away; extra forms can")
    sort: int


class FormChoice(BaseModel):
    form_id: int
    title: str


class ObjectForms(BaseModel):
    forms: list[ObjectForm]
    available: list[FormChoice] = Field(description="Extra forms that can be added (those not already on the object)")


class ObjectFormsUpdate(BaseModel):
    form_ids: list[int] = Field(
        max_length=50, description="Every form the object should carry, in order: its built-in form(s) as listed, "
        "then the extra ones. A form left out is removed together with its answers.")

    def model_post_init(self, _ctx) -> None:
        if any(i <= 0 for i in self.form_ids) or len(set(self.form_ids)) != len(self.form_ids):
            raise ValueError("form_ids are distinct positive ids")


def present(entries, attachable) -> ObjectForms:
    """The forms of an object and the extra forms still free to add."""
    have = {int(e["form_id"]) for e in entries}
    return ObjectForms(
        forms=[ObjectForm(entry_id=e["entry_id"], form_id=e["form_id"], title=e["title"] or "", sort=int(e["sort"] or 0),
                          kind="extra" if e["type"] == "G" else "built_in") for e in entries],
        available=[FormChoice(form_id=f["form_id"], title=f["title"] or "") for f in attachable if int(f["form_id"]) not in have])
