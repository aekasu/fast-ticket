"""Which help topics an agent may pick — a literal port of osTicket's own procedure.

Two osTicket functions decide it and both are procedural, so this follows them step by step rather than
re-deriving the intent (include/class.topic.php and include/class.staff.php, osTicket 1.18):

* ``Topic::getHelpTopics()``  builds each topic's full "Parent / Child" name and its *disabled* flag
  (a topic with the active flag off, plus a cascade from its ancestors), and drops disabled topics.
* ``Staff::getTopicNames()``  then, unless the agent holds ``visibility.departments`` (and has departments at
  all), keeps a topic only if it is public, or belongs to no department, or to one of the agent's — and drops
  a child whose parent is private to a department the agent is not in.

The cascade has an order-dependent quirk (a child is marked disabled only from its *second* ancestor onward,
and it reads the ancestor's flag as it stands at that moment); it is reproduced exactly.
"""

FLAG_ACTIVE = 0x0002   # Topic::FLAG_ACTIVE


def topic_names(rows) -> tuple[dict[int, dict], dict[int, str]]:
    """``Topic::getHelpTopics()`` before filtering: per-topic info (with the cascaded ``disabled``) and full names."""
    topics: dict[int, dict] = {}
    for row in rows:                      # in osTicket's own (sort) order
        topics[int(row["topic_id"])] = {
            "pid": int(row["topic_pid"] or 0),
            "public": bool(row["ispublic"]),
            "disabled": not (int(row["flags"] or 0) & FLAG_ACTIVE),
            "topic": row["topic"],
            "dept_id": int(row["dept_id"] or 0),
        }
    names: dict[int, str] = {}
    for topic_id in list(topics):
        info = topics[topic_id]
        name = info["topic"]
        loop = {topic_id}
        parent = None
        while info["pid"]:
            info = topics.get(info["pid"])
            if not info:
                break
            name = f"{info['topic']} / {name}"
            if parent and parent["disabled"]:
                topics[topic_id]["disabled"] = True     # cascade
            if info["pid"] in loop:
                break
            loop.add(info["pid"])
            parent = info
        names[topic_id] = name
    return topics, names


def visible_topics(rows, *, staff_dept_ids, filter_by_department: bool) -> list[tuple[int, str]]:
    """``(topic_id, full name)`` for the topics an agent sees, in osTicket's order.

    ``filter_by_department`` is ``not hasPerm(visibility.departments) and the agent has departments``.
    """
    topics, names = topic_names(rows)
    active = [tid for tid in names if not topics[tid]["disabled"]]
    if not filter_by_department:
        return [(tid, names[tid]) for tid in active]

    staff = set(staff_dept_ids)
    clean: dict[int, str] = {}
    for tid in active:
        info = topics[tid]
        if info["pid"]:
            # a child: public, or no department, or one of mine...
            if info["public"] or not info["dept_id"] or info["dept_id"] in staff:
                clean[tid] = names[tid]
            # ...but hidden if its parent is private to a department that is not mine
            parent = topics.get(info["pid"])
            if parent and not parent["public"] and parent["dept_id"] and parent["dept_id"] not in staff:
                clean.pop(tid, None)
        elif info["public"]:
            clean[tid] = names[tid]
        elif not info["dept_id"] or info["dept_id"] in staff:
            clean[tid] = names[tid]
    return list(clean.items())
