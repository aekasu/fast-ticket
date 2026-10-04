from app.core.names import agent_name, sort_columns


def test_formats_follow_osticket_for_an_agent_with_only_first_and_last_name():
    assert agent_name("Ada", "Lovelace", "full") == "Ada Lovelace"
    assert agent_name("Ada", "Lovelace", "lastfirst") == "Lovelace, Ada"
    assert agent_name("Ada", "Lovelace", "short") == "Ada L"
    assert agent_name("Ada", "Lovelace", "shortformal") == "A Lovelace"
    assert agent_name("Ada", "Lovelace", "last") == "Lovelace"
    assert agent_name("Ada", "Lovelace", "first") == "Ada"
    assert agent_name("Ada", "Lovelace", "legal") == "Ada Lovelace"       # no middle initial to add
    assert agent_name("Ada", "", "lastfirst") == "Ada"                    # osTicket trims the dangling comma


def test_the_sort_order_puts_the_last_name_first_only_for_last_lastfirst_and_legal():
    assert sort_columns("full") == ["firstname", "lastname"]
    assert sort_columns("short", "s.") == ["s.firstname", "s.lastname"]
    for fmt in ("last", "lastfirst", "legal"):
        assert sort_columns(fmt) == ["lastname", "firstname"]
