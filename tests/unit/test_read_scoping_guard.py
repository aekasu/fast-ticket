"""Every repository read is either scoped to the caller or on a written-down list of reasons it is not.

Scoped reads take a required ``access=`` argument (no default). Any *other* read must appear in
``UNSCOPED_BY_DESIGN`` with the reason — so adding a read function forces the question "who may see this?"
to be answered in review, and a stale entry fails too.
"""

import ast
import inspect
from pathlib import Path

from app.integration.osticket import read_repository as repo

ROOT = Path(__file__).resolve().parents[2]

UNSCOPED_BY_DESIGN = {
    # Admin Panel objects: the routes are require_admin(), and osTicket shows these pages to administrators only.
    "get_banlist_entry": "Admin Panel (banlist)", "list_banlist": "Admin Panel (banlist)",
    "get_form": "Admin Panel (dynamic forms)", "get_form_fields": "Admin Panel (dynamic forms)",
    "list_forms": "Admin Panel (dynamic forms)",
    "list_roles": "Admin Panel (roles)",
    "get_sla": "Admin Panel (SLA plans)", "list_slas": "Admin Panel (SLA plans)",
    "get_staff": "Admin Panel (agents)", "list_staff": "Admin Panel (agents)",
    "get_staff_teams": "Admin Panel (agents) — team ids on an agent's detail",
    "get_staff_dept_access": "Admin Panel (agents) — extra-department access on an agent's detail",
    "get_topic_form_ids": "Admin Panel (help topics) — forms on a topic's detail",
    "get_organization_primary_contacts": "directory (route needs user.dir) — primary contacts on an organization's detail",
    "get_user_account": "directory (route needs user.dir) — account on a user's detail",
    "get_user_custom_fields": "directory (route needs user.dir) — custom answers on a user's detail",
    "get_organization_custom_fields": "directory (route needs user.dir) — custom answers on an organization's detail",
    "get_department_members": "Admin Panel (departments) — extended-access agents on a department's detail",
    "get_team": "Admin Panel (teams)", "list_teams": "Admin Panel (teams)",
    "get_team_members": "Admin Panel (teams) — member ids on a team's detail",
    "get_department": "Admin Panel (department detail)",
    "get_priority": "Admin Panel (priority detail)",
    "get_topic": "Admin Panel (topic detail)",
    "list_topics": "the Admin Panel's full topic list: the service reaches it only for administrators",
    # Reference data osTicket offers every agent, or narrows in Python by osTicket's own procedure.
    "list_topic_rows": "raw rows; lookups/topic_visibility.py applies Staff::getTopicNames",
    "get_help_topic_sort_mode": "an instance setting (how help topics are ordered), not agent or ticket data",
    "get_agent_name_format": "an instance setting (how agent names are written), not agent or ticket data",
    "list_priorities": "osTicket offers every priority to every agent (Priority::getPriorities)",
    # Knowledge base: osTicket applies no permission to viewing FAQs.
    "get_category": "knowledge base (no read permission in osTicket)", "list_categories": "knowledge base",
    "get_faq": "knowledge base", "list_faqs": "knowledge base",
    # The directory: the routes need user.dir, and osTicket narrows it no further.
    "get_user": "directory (route needs user.dir)", "list_users": "directory (route needs user.dir)",
    "get_organization": "directory (route needs user.dir)", "list_organizations": "directory (route needs user.dir)",
    # It is what builds the AccessContext everything else is scoped by.
    "get_staff_row": "loads the caller's own access",
    "get_staff_grants": "loads the caller's own access",
}


def _reads():
    for name, function in inspect.getmembers(repo, inspect.iscoroutinefunction):
        if not name.startswith("_") and function.__module__ == repo.__name__:
            yield name, function


def test_the_scan_found_the_reads():
    assert len(list(_reads())) > 40


def test_a_scoped_read_requires_access_and_has_no_default():
    for name, function in _reads():
        parameter = inspect.signature(function).parameters.get("access")
        if parameter is None:
            continue
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, f"{name}: access must be keyword-only"
        assert parameter.default is inspect.Parameter.empty, f"{name}: access must have NO default"


def test_every_unscoped_read_has_a_written_reason_and_none_are_stale():
    unscoped = {name for name, function in _reads() if "access" not in inspect.signature(function).parameters}
    assert unscoped - set(UNSCOPED_BY_DESIGN) == set(), (
        "reads that are neither scoped nor on the list — decide who may see them: "
        f"{sorted(unscoped - set(UNSCOPED_BY_DESIGN))}"
    )
    assert set(UNSCOPED_BY_DESIGN) - unscoped == set(), "stale entries (the function is gone or is now scoped)"


def test_every_service_call_to_a_scoped_read_passes_access():
    """Mocks accept any keyword, so a service that forgot access= would not fail its tests. This does."""
    scoped = {name for name, function in _reads() if "access" in inspect.signature(function).parameters}
    offenders = []
    for service_file in sorted((ROOT / "app").rglob("*.py")):
        if service_file.name == "read_repository.py":
            continue
        for node in ast.walk(ast.parse(service_file.read_text())):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "repo"
                and node.func.attr in scoped
                and "access" not in {keyword.arg for keyword in node.keywords}
            ):
                offenders.append(f"{service_file.relative_to(ROOT)}:{node.lineno} repo.{node.func.attr}")
    assert offenders == [], f"scoped reads called without access=: {offenders}"


def test_no_route_handler_queries_the_repository_directly():
    """Routers are thin: reads go through a service, which is where scoping is applied and tested."""
    offenders = []
    for router in sorted((ROOT / "app" / "modules").glob("*/router.py")):
        for node in ast.walk(ast.parse(router.read_text())):
            imported = (
                [node.module or ""] + [alias.name for alias in node.names] if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            )
            if any("read_repository" in name for name in imported):
                offenders.append(str(router.relative_to(ROOT)))
    assert offenders == [], offenders
