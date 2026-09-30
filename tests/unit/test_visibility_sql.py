"""The visibility predicates, executed as SQL against an in-memory database.

Each scenario is one ticket or task in a known state; each assertion states whether osTicket lets an agent
see it — in a list (``Staff::getTicketsVisibility`` / the task-list visibility block) and on its page
(``Ticket::checkStaffPerm`` / ``Task::checkStaffPerm``). The two differ on purpose in osTicket and are
reproduced as they are; the cases where they differ are called out.
"""

import sqlite3

import pytest

from app.core.access import AccessContext
from app.core.scope import UNRESTRICTED
from app.integration.osticket.read_repository import task_visibility, ticket_visibility

ME, OTHER = 5, 9
DEPT_MINE, DEPT_OTHER, DEPT_THIRD = 1, 2, 3
TEAM_MINE, TEAM_OTHER = 7, 8


@pytest.fixture(scope="module")
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ost_ticket_status (id INTEGER PRIMARY KEY, state TEXT);
        INSERT INTO ost_ticket_status VALUES (1, 'open'), (2, 'closed');
        CREATE TABLE ost_ticket (ticket_id INTEGER PRIMARY KEY, dept_id INT, staff_id INT, team_id INT, status_id INT);
        CREATE TABLE ost_thread (id INTEGER PRIMARY KEY, object_id INT, object_type TEXT);
        CREATE TABLE ost_thread_referral (id INTEGER PRIMARY KEY, thread_id INT, object_id INT, object_type TEXT);
        CREATE TABLE ost_task (id INTEGER PRIMARY KEY, dept_id INT, staff_id INT, team_id INT, flags INT,
                               object_type TEXT, object_id INT);
        """
    )
    OPEN, CLOSED = 1, 2
    tickets = {   # id: (dept, staff, team, status)
        1: (DEPT_MINE, 0, 0, OPEN),        # in my department, open
        2: (DEPT_MINE, 0, 0, CLOSED),      # in my department, closed
        3: (DEPT_OTHER, 0, 0, OPEN),       # elsewhere, unassigned
        4: (DEPT_OTHER, ME, 0, OPEN),      # elsewhere, open, assigned to me
        5: (DEPT_OTHER, ME, 0, CLOSED),    # elsewhere, CLOSED, assigned to me
        6: (DEPT_OTHER, 0, TEAM_MINE, OPEN),
        7: (DEPT_OTHER, 0, TEAM_MINE, CLOSED),
        8: (DEPT_OTHER, 0, 0, OPEN),       # referred to me, open
        9: (DEPT_OTHER, 0, 0, CLOSED),     # referred to me, closed
        10: (DEPT_OTHER, 0, 0, OPEN),      # referred to my department, open
        11: (DEPT_OTHER, 0, 0, CLOSED),    # referred to my department, closed
        12: (DEPT_OTHER, 0, 0, OPEN),      # referred to my team, open
        13: (DEPT_OTHER, 0, 0, CLOSED),    # referred to my team, closed
        14: (DEPT_OTHER, 0, 0, OPEN),      # a merged child (thread type C) referred to me
        15: (DEPT_THIRD, OTHER, TEAM_OTHER, OPEN),   # someone else's entirely
        16: (DEPT_THIRD, 0, 0, OPEN),      # referred to someone else
    }
    for tid, (dept, staff, team, status) in tickets.items():
        conn.execute("INSERT INTO ost_ticket VALUES (?,?,?,?,?)", (tid, dept, staff, team, status))
        conn.execute("INSERT INTO ost_thread VALUES (?,?,?)", (tid, tid, "C" if tid == 14 else "T"))
    referrals = [(8, ME, "S"), (9, ME, "S"), (10, DEPT_MINE, "D"), (11, DEPT_MINE, "D"),
                 (12, TEAM_MINE, "E"), (13, TEAM_MINE, "E"), (14, ME, "S"), (16, OTHER, "S")]
    for i, (thread, obj, kind) in enumerate(referrals, 1):
        conn.execute("INSERT INTO ost_thread_referral VALUES (?,?,?,?)", (i, thread, obj, kind))

    tasks = {   # id: (dept, staff, team, open?, object_type, object_id)
        1: (DEPT_MINE, 0, 0, 1, None, 0),
        2: (DEPT_MINE, 0, 0, 0, None, 0),         # closed, my department
        3: (DEPT_OTHER, 0, 0, 1, None, 0),
        4: (DEPT_OTHER, ME, 0, 1, None, 0),
        5: (DEPT_OTHER, ME, 0, 0, None, 0),       # closed, assigned to me
        6: (DEPT_OTHER, 0, TEAM_MINE, 1, None, 0),
        7: (DEPT_OTHER, 0, TEAM_MINE, 0, None, 0),
        8: (DEPT_OTHER, 0, 0, 0, None, 0),        # closed, nobody's, elsewhere
        9: (DEPT_OTHER, 0, 0, 1, "T", 4),         # open task of ticket 4 (open, assigned to me)
        10: (DEPT_OTHER, 0, 0, 1, "T", 5),        # open task of ticket 5 (CLOSED, assigned to me)
        11: (DEPT_THIRD, OTHER, TEAM_OTHER, 1, None, 0),
    }
    for tid, (dept, staff, team, is_open, otype, oid) in tasks.items():
        conn.execute("INSERT INTO ost_task VALUES (?,?,?,?,?,?,?)", (tid, dept, staff, team, is_open, otype, oid))
    return conn


def _agent(*, depts=(DEPT_MINE,), teams=(TEAM_MINE,), assigned_only=False):
    return AccessContext(
        staff_id=ME, dept_ids=frozenset(depts), team_ids=frozenset(teams), assigned_only=assigned_only,
    )


def _tickets(db, access, *, detail):
    sql = (
        "SELECT t.ticket_id FROM ost_ticket t JOIN ost_ticket_status ts ON ts.id = t.status_id "
        f"WHERE {ticket_visibility(access, detail=detail)} ORDER BY 1"
    )
    return [row[0] for row in db.execute(sql)]


def _tasks(db, access, *, detail):
    sql = f"SELECT t.id FROM ost_task t WHERE {task_visibility(access, detail=detail)} ORDER BY 1"
    return [row[0] for row in db.execute(sql)]


# ── tickets ───────────────────────────────────────────────────────────────────


def test_ticket_list_shows_my_departments_and_open_work_assigned_or_referred_to_me_or_my_team(db):
    assert _tickets(db, _agent(), detail=False) == [
        1, 2,        # my department, open or closed
        4,           # open and assigned to me
        6,           # open and assigned to my team
        8,           # open and referred to me
        10, 11,      # referred to my department (any state)
        12,          # open and referred to my team
        14,          # a merged child (thread type C) referred to me
    ]


def test_ticket_page_also_opens_referrals_in_any_state(db):
    """Ticket::checkStaffPerm: department, OR open and assigned to me/my team, OR referred (agent, dept, team) in any state."""
    assert _tickets(db, _agent(), detail=True) == [1, 2, 4, 6, 8, 9, 10, 11, 12, 13, 14]


def test_the_list_and_the_page_differ_exactly_where_osticket_differs(db):
    """A closed ticket referred to me or my team is on no list but can be opened: osTicket's own asymmetry."""
    in_list = set(_tickets(db, _agent(), detail=False))
    on_page = set(_tickets(db, _agent(), detail=True))
    assert on_page - in_list == {9, 13}
    assert in_list <= on_page


def test_an_assignment_stops_counting_when_the_ticket_closes(db):
    for detail in (False, True):
        seen = _tickets(db, _agent(), detail=detail)
        assert 4 in seen and 5 not in seen        # open+mine yes; closed+mine no
        assert 6 in seen and 7 not in seen        # open+my team yes; closed+my team no


def test_other_peoples_tickets_stay_invisible(db):
    for detail in (False, True):
        seen = _tickets(db, _agent(), detail=detail)
        assert 3 not in seen and 15 not in seen and 16 not in seen


def test_a_limited_access_agent_loses_department_access_in_lists(db):
    """assigned_only: only what is assigned to me or my team (or referred to me) while open."""
    assert _tickets(db, _agent(assigned_only=True), detail=False) == [4, 6, 8, 12, 14]


def test_a_limited_access_agent_still_opens_department_referrals_on_the_page(db):
    """isReferred() for a department does not consult assigned-only — osTicket's own quirk, mirrored."""
    seen = _tickets(db, _agent(assigned_only=True), detail=True)
    assert 1 not in seen and 2 not in seen              # no department access at all
    assert {4, 6, 8, 9, 10, 11, 12, 13, 14} <= set(seen)


def test_an_agent_without_teams_sees_no_team_work(db):
    for detail in (False, True):
        seen = _tickets(db, _agent(teams=()), detail=detail)
        assert 6 not in seen and 12 not in seen and 13 not in seen


def test_an_agent_without_departments_sees_only_what_is_assigned_or_referred_to_them(db):
    assert _tickets(db, _agent(depts=()), detail=False) == [4, 6, 8, 12, 14]


def test_unrestricted_sees_every_ticket(db):
    assert len(_tickets(db, UNRESTRICTED, detail=False)) == 16
    assert len(_tickets(db, UNRESTRICTED, detail=True)) == 16


def test_a_department_grants_its_tickets_but_only_that_department(db):
    seen = _tickets(db, _agent(depts=(DEPT_OTHER,), teams=()), detail=False)
    assert {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14} <= set(seen)
    assert 1 not in seen and 15 not in seen


# ── tasks ─────────────────────────────────────────────────────────────────────


def test_task_list(db):
    assert _tasks(db, _agent(), detail=False) == [
        1, 2,        # my department, open or closed
        4,           # open and assigned to me
        6,           # open and assigned to my team
        9,           # an open task of a ticket that is open and assigned to me
    ]


def test_task_page_opens_any_closed_task_and_anything_assigned_to_me_or_my_team(db):
    """Task::checkStaffPerm denies only: outside my departments AND open AND not mine AND not my team's."""
    assert _tasks(db, _agent(), detail=True) == [1, 2, 4, 5, 6, 7, 8]


def test_a_closed_task_can_be_opened_by_any_agent_but_is_not_listed(db):
    """osTicket's quirk, mirrored: task 8 is closed, unassigned, in a department the agent cannot access."""
    assert 8 in _tasks(db, _agent(), detail=True)
    assert 8 not in _tasks(db, _agent(), detail=False)


def test_a_task_of_my_ticket_is_listed_only_while_that_ticket_is_open_and_mine(db):
    listed = _tasks(db, _agent(), detail=False)
    assert 9 in listed and 10 not in listed          # ticket 4 open, ticket 5 closed


def test_the_page_does_not_use_the_linked_ticket_rule(db):
    """Task 9 is listed through its ticket but its page follows checkStaffPerm, which has no such clause."""
    assert 9 not in _tasks(db, _agent(), detail=True)


def test_a_limited_access_agent_sees_only_assigned_tasks(db):
    assert _tasks(db, _agent(assigned_only=True), detail=False) == [4, 6, 9]
    assert _tasks(db, _agent(assigned_only=True), detail=True) == [2, 4, 5, 6, 7, 8]   # closed ones remain openable


def test_other_peoples_open_tasks_stay_invisible(db):
    for detail in (False, True):
        assert 3 not in _tasks(db, _agent(), detail=detail)
        assert 11 not in _tasks(db, _agent(), detail=detail)


def test_unrestricted_sees_every_task(db):
    assert len(_tasks(db, UNRESTRICTED, detail=False)) == 11


def test_ids_are_inlined_as_integers_only():
    """AccessContext ids are ints read from osTicket; the predicate must never carry anything else."""
    sql = ticket_visibility(_agent(depts=(1, 2), teams=(7,)), detail=False)
    assert "IN (1,2)" in sql and "IN (7)" in sql
    with pytest.raises((ValueError, TypeError)):
        ticket_visibility(AccessContext(staff_id=5, dept_ids=frozenset({"1); DROP TABLE ost_ticket;--"})), detail=False)
