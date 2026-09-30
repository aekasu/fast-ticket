"""Every route the app really serves, and the gate each one carries.

FastAPI (0.14x) wraps ``include_router`` in a lazy ``_IncludedRouter``, so ``app.routes`` alone holds only a
handful of entries — a test that iterated it once passed while looking at nothing. ``all_routes`` flattens them
and refuses to return an implausibly small set, so that mistake cannot recur silently.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteInfo:
    method: str
    path: str
    dependant: object
    endpoint: object

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def _walk(routes):
    for route in routes:
        if hasattr(route, "effective_route_contexts"):          # an included router (lazy)
            yield from route.effective_route_contexts()
        elif hasattr(route, "routes") and not hasattr(route, "dependant"):
            yield from _walk(route.routes)                      # a Mount or a router holding routes
        else:
            yield route


def all_routes(app, *, minimum: int = 80) -> list[RouteInfo]:
    """Every HTTP route, one entry per method. ``minimum`` guards against enumerating a fraction of the app."""
    found = []
    for route in _walk(app.routes):
        methods = getattr(route, "methods", None)
        dependant = getattr(route, "dependant", None)
        if not methods or dependant is None:
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            found.append(RouteInfo(method, route.path, dependant, getattr(route, "endpoint", None)))
    assert len(found) >= minimum, f"only {len(found)} routes enumerated — the flattening is broken"
    return found


def guards(info: RouteInfo) -> list:
    """The ``require_perm`` / ``require_admin`` / ``require_staff`` dependencies on a route (their ``_check`` closures)."""
    out = []

    def walk(dependant):
        for sub in dependant.dependencies:
            call = getattr(sub, "call", None)
            if call is not None and getattr(call, "__name__", "") == "_check" and call.__module__ == "app.core.security":
                out.append(call)
            walk(sub)

    walk(info.dependant)
    return out


def uses_current_user(info: RouteInfo) -> bool:
    """True if the route authenticates through ``get_current_user`` (guard or direct)."""
    def walk(dependant):
        for sub in dependant.dependencies:
            if getattr(getattr(sub, "call", None), "__name__", "") == "get_current_user":
                return True
            if walk(sub):
                return True
        return False

    return walk(info.dependant)
