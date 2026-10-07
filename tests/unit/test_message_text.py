from app.core.message_text import keep_line_breaks


def test_plain_text_newlines_become_breaks():
    assert keep_line_breaks("a\nb\r\nc") == "a<br>b<br>c"


def test_markup_is_left_alone():
    assert keep_line_breaks("<p>a</p>\n<p>b</p>") == "<p>a</p>\n<p>b</p>"


def test_single_line_unchanged():
    assert keep_line_breaks("a < b") == "a < b"


def test_field_names_for_list_columns():
    import pytest

    from app.core.custom_fields import parse_field_names

    assert parse_field_names(None) is None and parse_field_names("") is None
    assert parse_field_names("a, b,a") == ["a", "b"]
    for bad in ("a;b", "x" * 65, ",".join(f"f{i}" for i in range(21)), "1abc", "a b"):
        with pytest.raises(ValueError):
            parse_field_names(bad)
