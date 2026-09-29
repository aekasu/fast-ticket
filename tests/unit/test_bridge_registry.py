"""Static consistency checks between the Python API and the PHP bridge.

There is no PHP test runner here, so these tests read ``bridge/plugin.php`` as text
and enforce the rule from AGENTS.md: a command type exists only when

  1. a Python service calls ``dispatch_command(command_type="<x>", ...)``,
  2. ``dispatchCommand()`` has a ``case '<x>':``, and
  3. ``commandToEventType()`` maps ``<x>`` to its past-tense event.

A missing case leaves the command ``failed`` ("Unknown command type"); a missing map
entry silently emits ``<x>.executed`` instead of the event consumers listen for.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = (ROOT / "bridge" / "plugin.php").read_text()


def _between(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a : text.index(end, a)]


DISPATCH_BODY = _between(PLUGIN, "function dispatchCommand", "function emitOutboxEvent")
EVENT_MAP_BODY = _between(PLUGIN, "function commandToEventType", "];")

BRIDGE_CASES = set(re.findall(r"case '([a-z_.]+)':", DISPATCH_BODY))
EVENT_MAP = dict(re.findall(r"'([a-z_.]+)'\s*=>\s*'([a-z_.]+)'", EVENT_MAP_BODY))


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
    """Guard against the regexes silently matching nothing after a refactor."""
    assert len(BRIDGE_CASES) > 40
    assert len(EVENT_MAP) > 40
    assert len(_python_command_types()) > 40


def test_every_python_command_has_a_bridge_case():
    missing = _python_command_types() - BRIDGE_CASES
    assert not missing, f"dispatched by Python but no case in dispatchCommand(): {sorted(missing)}"


def test_no_bridge_command_is_unreachable_from_the_api():
    """A bridge case nothing dispatches is dead code (user.set_org was one for a long time)."""
    dead = BRIDGE_CASES - _python_command_types()
    assert not dead, f"cases in dispatchCommand() that no Python service dispatches: {sorted(dead)}"


def test_every_bridge_case_has_an_event_mapping():
    missing = BRIDGE_CASES - EVENT_MAP.keys()
    assert not missing, (
        "no entry in commandToEventType() — events would be emitted as "
        f"'<command>.executed': {sorted(missing)}"
    )


def test_event_map_has_no_orphans():
    orphans = EVENT_MAP.keys() - BRIDGE_CASES
    assert not orphans, f"mapped in commandToEventType() but not dispatched: {sorted(orphans)}"


def test_event_names_stay_in_their_commands_domain():
    """``org.create`` -> ``org.*`` — a copy/paste slip must not cross domains.

    Only the domain (first segment) is stable: ``ticket.collaborator.add`` legitimately
    maps to ``ticket.collaborator_added``.
    """
    for command, event in EVENT_MAP.items():
        assert event.split(".")[0] == command.split(".")[0], (command, event)


def test_every_dispatched_handler_method_exists():
    """A misspelled ``FastApiXHandler::method`` is a PHP fatal at cron time, not at import."""
    defined: dict[str, set[str]] = {}
    for path in (ROOT / "bridge" / "handlers").glob("*.php"):
        text = path.read_text()
        for cls in re.findall(r"class\s+(FastApi\w+Handler)\b", text):
            defined[cls] = set(re.findall(r"public\s+static\s+function\s+(\w+)\s*\(", text))

    called = set(re.findall(r"(FastApi\w+Handler)::(\w+)\(", DISPATCH_BODY))
    assert called, "no handler calls found in dispatchCommand()"

    problems = sorted(
        f"{cls}::{method}"
        for cls, method in called
        if cls not in defined or method not in defined[cls]
    )
    assert not problems, f"dispatchCommand() calls handler methods that do not exist: {problems}"
