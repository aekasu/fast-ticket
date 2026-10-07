from app.core.message_text import keep_line_breaks


def test_plain_text_newlines_become_breaks():
    assert keep_line_breaks("a\nb\r\nc") == "a<br>b<br>c"


def test_markup_is_left_alone():
    assert keep_line_breaks("<p>a</p>\n<p>b</p>") == "<p>a</p>\n<p>b</p>"


def test_single_line_unchanged():
    assert keep_line_breaks("a < b") == "a < b"
