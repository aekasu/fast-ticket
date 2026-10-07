"""Static consistency checks between the Python API and the PHP bridge.

There is no PHP test runner here, so these tests read ``bridge/registry.php`` as text
and enforce the rule from AGENTS.md: a command type exists only when

  1. a Python service calls ``dispatch_command(command_type="<x>", ...)``, and
  2. ``bridge/registry.php`` has one entry ``'<x>' => [file, class, method, event, permission]``, where
     ``permission`` is exactly what the API's own route demands before queueing it.

A missing entry leaves the command ``failed`` ("Unknown command type"); a wrong handler
method is a PHP fatal at run time, so the class/method are checked against handlers/*.php.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = (ROOT / "bridge" / "registry.php").read_text()

ENTRIES = {
    command: (file, cls, method, event, permission)
    for command, file, cls, method, event, permission in re.findall(
        r"'([a-z_.]+)'\s*=>\s*\['(\w+)',\s*'(FastApi\w+Handler)',\s*'(\w+)',\s*'([a-z_.]+)',\s*'([a-z_.:]+)'\]",
        REGISTRY,
    )
}
BRIDGE_CASES = set(ENTRIES)
EVENT_MAP = {command: entry[3] for command, entry in ENTRIES.items()}


def _python_command_types() -> set[str]:
    """Every command type a Python service can dispatch.

    Two spellings exist: ``command_type="x"`` in the call, and a variable chosen beforehand
    (``cmd_type = "a" if flag else "b"``) — matching only the first hid ticket.answered/unanswered.
    """
    found: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text()
        found |= set(re.findall(r'command_type\s*=\s*"([a-z_.]+)"', text))
        for match in re.finditer(r'cmd_type\s*=\s*"([a-z_.]+)"(?:\s+if\s+\w+\s+else\s+"([a-z_.]+)")?', text):
            found |= {g for g in match.groups() if g}
    return found


def test_registry_was_parsed():
    """Guard against the regex silently matching nothing after a refactor."""
    assert len(ENTRIES) > 40
    assert len(_python_command_types()) > 40
    # every entry line was understood: one '=>' per entry
    assert REGISTRY.count("=> ['") == len(ENTRIES)


def test_every_python_command_has_a_registry_entry():
    missing = _python_command_types() - BRIDGE_CASES
    assert not missing, f"dispatched by Python but not in bridge/registry.php: {sorted(missing)}"


def test_no_registry_entry_is_unreachable_from_the_api():
    """An entry nothing dispatches is dead code (user.set_org was one for a long time)."""
    dead = BRIDGE_CASES - _python_command_types()
    assert not dead, f"in bridge/registry.php but no Python service dispatches them: {sorted(dead)}"


def test_event_names_stay_in_their_commands_domain():
    """``org.create`` -> ``org.*`` — a copy/paste slip must not cross domains.

    Only the domain (first segment) is stable: ``ticket.collaborator.add`` legitimately
    maps to ``ticket.collaborator_added``.
    """
    for command, event in EVENT_MAP.items():
        assert event.split(".")[0] == command.split(".")[0], (command, event)


def test_every_registered_handler_method_exists():
    """A misspelled ``FastApiXHandler::method`` is a PHP fatal at run time, not at import."""
    defined: dict[str, set[str]] = {}
    files: dict[str, str] = {}
    for path in (ROOT / "bridge" / "handlers").glob("*.php"):
        text = path.read_text()
        for cls in re.findall(r"class\s+(FastApi\w+Handler)\b", text):
            defined[cls] = set(re.findall(r"public\s+static\s+function\s+(\w+)\s*\(", text))
            files[cls] = path.stem

    problems = sorted(
        f"{command}: {cls}::{method} in handlers/{file}.php"
        for command, (file, cls, method, _event, _permission) in ENTRIES.items()
        if cls not in defined or method not in defined[cls] or files[cls] != file
    )
    assert not problems, f"registry entries pointing at missing handlers: {problems}"


# ── the permission column ─────────────────────────────────────────────────────


def _spec_for(guard: str) -> str:
    from app.core.permissions import permission_kind

    return "admin" if guard == "admin" else "self" if guard == "staff" else f"{permission_kind(guard)}:{guard}"


# The only commands any enabled agent may queue: each acts on the requesting agent's own record and reads no id
# from the payload (bridge/handlers/profile.php). Registry spec: ``self``.
SELF_SERVICE = {"profile.update", "profile.change_password"}


def test_self_service_commands_are_exactly_the_profile_ones():
    assert {c for c, e in ENTRIES.items() if e[4] == "self"} == SELF_SERVICE


def test_every_command_has_exactly_one_api_guard_and_it_is_never_merely_staff():
    """A command reachable through routes with different guards would make the registry ambiguous; and a write
    that any agent may queue would need no permission at all."""
    from tests.unit.route_guards import command_guards

    guards = command_guards()
    assert set(guards) == set(ENTRIES), "route analysis and registry disagree on the command set"
    assert not {c: g for c, g in guards.items() if len(g) != 1}
    assert {c for c, g in guards.items() if g == {"staff"}} == SELF_SERVICE, "a write route guarded only by require_staff"


def test_the_bridge_demands_exactly_what_the_api_route_demands():
    """The PHP dispatcher refuses a staff member who lacks the registry's permission; it must be the same
    permission the API checked, or the two layers disagree about who may do what."""
    from tests.unit.route_guards import command_guards

    mismatches = {
        command: (entry[4], _spec_for(next(iter(command_guards()[command]))))
        for command, entry in ENTRIES.items()
        if entry[4] != _spec_for(next(iter(command_guards()[command])))
    }
    assert not mismatches, f"registry permission != API guard (registry, API): {mismatches}"


def test_permission_specs_are_well_formed_and_their_kind_is_right():
    from app.core.permissions import ALL_PERMISSIONS, permission_kind

    for command, entry in ENTRIES.items():
        spec = entry[4]
        if spec in ("admin", "self"):
            continue
        kind, _, key = spec.partition(":")
        assert kind in ("global", "role") and key in ALL_PERMISSIONS, (command, spec)
        assert permission_kind(key) == kind, (command, spec)


def test_admin_panel_commands_are_exactly_the_admin_only_domains():
    admin = {c for c, e in ENTRIES.items() if e[4] == "admin"}
    domains = {c.split(".")[0] for c in admin}
    assert domains == {"staff", "dept", "team", "topic", "sla", "priority", "form", "banlist", "role", "log", "apikey", "page", "list", "settings", "emailtemplate", "email", "filter", "schedule"}
    assert not [c for c in ENTRIES if c.split(".")[0] in domains and ENTRIES[c][4] != "admin"]
