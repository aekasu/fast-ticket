"""visible_topics — a literal port of Topic::getHelpTopics() and Staff::getTopicNames()."""

from app.modules.lookups.topic_visibility import FLAG_ACTIVE, topic_names, visible_topics

MINE, ELSEWHERE = 1, 2


def _t(tid, name, *, pid=0, public=True, dept=0, active=True):
    return {"topic_id": tid, "topic_pid": pid, "ispublic": int(public), "flags": FLAG_ACTIVE if active else 0,
            "topic": name, "dept_id": dept}


def _names(rows, **kwargs):
    return [name for _, name in visible_topics(rows, **kwargs)]


def test_a_topics_full_name_includes_its_parents():
    rows = [_t(1, "Billing"), _t(2, "Refunds", pid=1), _t(3, "Partial", pid=2)]
    _, names = topic_names(rows)
    assert names == {1: "Billing", 2: "Billing / Refunds", 3: "Billing / Refunds / Partial"}


def test_inactive_topics_are_dropped():
    rows = [_t(1, "General"), _t(2, "Retired", active=False)]
    assert _names(rows, staff_dept_ids={MINE}, filter_by_department=False) == ["General"]


def test_a_disabled_root_does_not_cascade_to_its_descendants():
    """osTicket's loop checks an ancestor's flag on the *next* pass, so it only sees a disabled ancestor that itself
    has a parent. A disabled ROOT therefore never cascades — reproduced as it is."""
    rows = [_t(1, "Root", active=False), _t(2, "Child", pid=1), _t(3, "Grandchild", pid=2)]
    topics, _ = topic_names(rows)
    assert topics[1]["disabled"] is True
    assert topics[2]["disabled"] is False
    assert topics[3]["disabled"] is False


def test_a_disabled_topic_below_the_root_cascades_to_its_descendants():
    rows = [_t(1, "Root"), _t(2, "Middle", pid=1, active=False), _t(3, "Leaf", pid=2), _t(4, "Deep", pid=3)]
    topics, _ = topic_names(rows)
    assert topics[2]["disabled"] is True
    assert topics[3]["disabled"] is True        # Middle is checked once the loop moves on to Root
    assert topics[4]["disabled"] is True        # and Leaf's cascaded flag is what Deep sees


def test_a_disabled_middle_topic_hides_its_children_from_the_list():
    rows = [_t(1, "Root"), _t(2, "Middle", pid=1, active=False), _t(3, "Leaf", pid=2)]
    assert _names(rows, staff_dept_ids=set(), filter_by_department=False) == ["Root"]


def test_without_department_filtering_every_active_topic_is_offered():
    rows = [_t(1, "Public"), _t(2, "Private elsewhere", public=False, dept=ELSEWHERE)]
    assert _names(rows, staff_dept_ids={MINE}, filter_by_department=False) == ["Public", "Private elsewhere"]


def test_public_topics_are_always_offered_private_ones_only_within_the_agents_departments():
    rows = [
        _t(1, "Public"),
        _t(2, "Private mine", public=False, dept=MINE),
        _t(3, "Private elsewhere", public=False, dept=ELSEWHERE),
        _t(4, "Private no dept", public=False, dept=0),
    ]
    assert _names(rows, staff_dept_ids={MINE}, filter_by_department=True) == ["Public", "Private mine", "Private no dept"]


def test_a_child_follows_its_own_visibility_and_its_parents():
    rows = [
        _t(1, "Sales", public=False, dept=ELSEWHERE),               # private to a department that is not mine
        _t(2, "Quotes", pid=1, public=True, dept=0),                # a public child of an inaccessible private parent
        _t(3, "Support", public=True),
        _t(4, "Tier 2", pid=3, public=False, dept=ELSEWHERE),       # a private child in another department
        _t(5, "Tier 1", pid=3, public=False, dept=MINE),            # a private child in mine
    ]
    # Sales: private elsewhere -> hidden.  Quotes: public, but its parent is private to another department -> hidden.
    # Support: public.  Tier 2: private elsewhere -> hidden.  Tier 1: mine -> shown.
    assert _names(rows, staff_dept_ids={MINE}, filter_by_department=True) == ["Support", "Support / Tier 1"]


def test_a_public_child_of_a_public_parent_is_offered():
    rows = [_t(1, "Support"), _t(2, "Login", pid=1)]
    assert _names(rows, staff_dept_ids={MINE}, filter_by_department=True) == ["Support", "Support / Login"]


def test_order_follows_the_rows_order():
    rows = [_t(3, "Zeta"), _t(1, "Alpha"), _t(2, "Mid")]
    assert _names(rows, staff_dept_ids=set(), filter_by_department=False) == ["Zeta", "Alpha", "Mid"]


def test_a_parent_loop_cannot_hang():
    rows = [_t(1, "A", pid=2), _t(2, "B", pid=1)]
    _, names = topic_names(rows)
    assert set(names) == {1, 2}     # terminates
