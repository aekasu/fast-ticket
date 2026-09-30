"""The one way to say "no visibility scoping".

Every scoped read in ``read_repository`` takes a required ``access=`` argument: either the caller's
``AccessContext`` (the query is narrowed to what osTicket lets that agent see) or ``UNRESTRICTED``.
There is no default, so a new read cannot forget to scope — it has to choose, and choosing
``UNRESTRICTED`` is a visible, greppable decision (osTicket's ``search.all`` permission is the one
legitimate reason today).

This lives in its own module so ``read_repository`` and ``access`` need not import each other.
"""


class _Unrestricted:
    unrestricted = True

    def __repr__(self) -> str:
        return "UNRESTRICTED"


UNRESTRICTED = _Unrestricted()
