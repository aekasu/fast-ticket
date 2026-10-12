"""The Manage Forms models: what an object carries, what can still be added, and a list that makes sense."""

import pytest
from pydantic import ValidationError

from app.core.object_forms import ObjectFormsUpdate, present


def test_present_marks_the_built_in_form_and_offers_only_what_is_not_there():
    entries = [{"entry_id": 5, "form_id": 2, "sort": 1, "title": "Ticket Details", "type": "T"},
               {"entry_id": 9, "form_id": 7, "sort": 2, "title": "Hardware", "type": "G"}]
    attachable = [{"form_id": 7, "title": "Hardware"}, {"form_id": 8, "title": "Software"}]
    got = present(entries, attachable)
    assert [(f.form_id, f.kind) for f in got.forms] == [(2, "built_in"), (7, "extra")]
    assert [f.form_id for f in got.available] == [8]


@pytest.mark.parametrize("ids", [[0], [-1], [3, 3]])
def test_a_form_list_is_distinct_positive_ids(ids):
    with pytest.raises(ValidationError):
        ObjectFormsUpdate(form_ids=ids)


def test_an_ordered_list_is_accepted_as_sent():
    assert ObjectFormsUpdate(form_ids=[2, 9, 7]).form_ids == [2, 9, 7]
