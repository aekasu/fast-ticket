"""A stored custom answer is returned typed, so a client does not decode osTicket's JSON."""

from app.core.custom_fields import answer_from_row


def row(type_, value, value_id=None, configuration=None, name="f"):
    return {"name": name, "label": "F", "type": type_, "value": value, "value_id": value_id, "configuration": configuration}


def test_text_stays_text():
    a = answer_from_row(row("text", "A-100"))
    assert (a.kind, a.value, a.bool_value, a.choices) == ("text", "A-100", None, None)
    assert answer_from_row(row("memo", None)).kind == "text"


def test_bool_is_a_boolean():
    assert answer_from_row(row("bool", "1")).bool_value is True
    assert answer_from_row(row("bool", "0")).bool_value is False
    assert answer_from_row(row("bool", None)).bool_value is False
    assert answer_from_row(row("bool", "1")).kind == "bool"


def test_a_single_choice_comes_from_the_label_map():
    a = answer_from_row(row("choices", '{"a":"Alpha"}', 0, '{"choices":"a:Alpha\\nb:Beta"}'))
    assert a.kind == "choices" and [(c.key, c.label) for c in a.choices] == [("a", "Alpha")]
    assert a.value == '{"a":"Alpha"}'                                     # the stored form is still there


def test_a_multiple_choice_as_a_key_list_takes_its_labels_from_the_configuration():
    a = answer_from_row(row("choices", '["a","z"]', None, '{"choices":"a:Alpha\\nb:Beta"}'))
    assert [(c.key, c.label) for c in a.choices] == [("a", "Alpha"), ("z", "z")]      # an unknown key keeps its key
    b = answer_from_row(row("choices", '{"a":"Alpha","b":"Beta"}', None, None))
    assert [c.key for c in b.choices] == ["a", "b"]


def test_nothing_selected_is_an_empty_list_and_a_custom_list_answer_is_choices_too():
    assert answer_from_row(row("choices", None)).choices == []
    assert answer_from_row(row("choices", "")).choices == []
    a = answer_from_row(row("list-3", '{"7":"Gold"}', 7))
    assert a.kind == "choices" and (a.choices[0].key, a.choices[0].label, a.value_id) == ("7", "Gold", 7)


def test_unreadable_values_stay_text():
    assert answer_from_row(row("choices", "not json")).kind == "text"
    assert answer_from_row(row("choices", "42")).kind == "text"
    assert answer_from_row(row("choices", '{"a":"Alpha"}', None, "{broken")).choices[0].label == "Alpha"
