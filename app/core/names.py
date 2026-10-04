"""An agent's display name and sort order, as osTicket's ``AgentsName`` does for the instance's
``agent_name_format`` setting. Agents have only a first and a last name (no salutation, middle name or suffix),
so the formats that need those parts reduce to what ``PersonsName`` yields with those parts empty."""

LAST_NAME_FIRST = frozenset({"last", "lastfirst", "legal"})   # Staff::getsortby()


def agent_name(first: str, last: str, fmt: str = "full") -> str:
    first, last = (first or "").strip(), (last or "").strip()
    if fmt == "first":
        return first
    if fmt in ("last", "formal"):
        return last
    if fmt == "lastfirst":
        return f"{last}, {first}".strip(", ")
    if fmt == "short":
        return f"{first} {last[:1]}"
    if fmt == "shortformal":
        return f"{first[:1]} {last}"
    return f"{first} {last}".strip()    # full, legal, complete, original (and any unknown format)


def sort_columns(fmt: str, prefix: str = "") -> list[str]:
    cols = ["lastname", "firstname"] if fmt in LAST_NAME_FIRST else ["firstname", "lastname"]
    return [prefix + col for col in cols]
