"""Which permission does the API demand before it queues each command?

A static analysis, so the PHP registry's permission column can be generated from — and forever checked
against — what the routers actually declare:

    router function  --guard-->  require_perm("x") / require_admin() / require_staff()
    router function  --calls-->  service.<function>
    service function --queues--> dispatch_command(command_type="...")

Nothing here imports the app, so it is safe to run before settings exist.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_ROUTE_METHODS = {"get", "post", "patch", "put", "delete"}


def _guard(func: ast.AsyncFunctionDef) -> str | None:
    """``admin``, ``staff`` or ``<perm key>`` from the ``current_user`` default, else None."""
    for arg, default in zip(
        func.args.args[-len(func.args.defaults):] if func.args.defaults else [], func.args.defaults
    ):
        if arg.arg == "current_user" and isinstance(default, ast.Call) and isinstance(default.func, ast.Name):
            if default.func.id == "require_admin":
                return "admin"
            if default.func.id == "require_staff":
                return "staff"
            if default.func.id == "require_perm" and default.args and isinstance(default.args[0], ast.Constant):
                return default.args[0].value
    return None


def _service_calls(func: ast.AsyncFunctionDef) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "service"
    }


def _commands_by_service_function(service_file: Path) -> dict[str, set[str]]:
    """service function name -> the command types its body can queue."""
    source = service_file.read_text()
    out: dict[str, set[str]] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        body = ast.get_source_segment(source, node) or ""
        found = set(re.findall(r'command_type\s*=\s*"([a-z_.]+)"', body))
        for match in re.finditer(r'cmd_type\s*=\s*"([a-z_.]+)"(?:\s+if\s+\w+\s+else\s+"([a-z_.]+)")?', body):
            found |= {g for g in match.groups() if g}
        if found:
            out[node.name] = found
    return out


def command_guards() -> dict[str, set[str]]:
    """command type -> every guard the routes that can queue it declare (a healthy command has exactly one)."""
    guards: dict[str, set[str]] = {}
    for router_file in sorted((ROOT / "app" / "modules").glob("*/router.py")):
        service_file = router_file.with_name("service.py")
        if not service_file.exists():
            continue
        commands = _commands_by_service_function(service_file)
        for node in ast.walk(ast.parse(router_file.read_text())):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            is_route = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in _ROUTE_METHODS
                for d in node.decorator_list
            )
            guard = _guard(node)
            if not is_route or guard is None:
                continue
            for called in _service_calls(node):
                for command in commands.get(called, ()):
                    guards.setdefault(command, set()).add(guard)
    return guards
