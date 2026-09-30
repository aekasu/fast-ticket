"""Who may hear about an event.

A broadcast is never "everyone connected". Each outbox event names an aggregate (``ticket``, ``user``, ...);
the rule for that aggregate decides, per connected client, whether the agent may be told:

  ticket, task      the ones in the agent's own list — osTicket's list visibility (department, assignment,
                    referral; assigned-only for limited agents), the same predicate the REST reads use
  user, org         agents who may use the directory (``user.dir``)
  kb.faq, kb.category   any agent (the knowledge base has no permission to read)
  kb.canned         agents whose role grants ``canned.manage``
  everything else   administrators — staff, departments, teams, help topics, SLAs, priorities, forms, banlist
                    are Admin Panel objects. An aggregate this table does not know is also admin-only: a new
                    kind of event fails closed instead of leaking.

Deletions cannot be checked (the row is gone, so nobody "can see" it): ``*.deleted`` events for tickets and
tasks go to administrators only.

Tickets and tasks cost one query per distinct agent per event; the rest need none.
"""

from app.core.ws_manager import Client
from app.integration.osticket import read_repository as repo


def aggregate_kind(aggregate_type: str) -> str:
    """``kb_canned`` (a command result) and ``kb.canned`` (an osTicket signal) are the same thing."""
    return (aggregate_type or "").replace("_", ".")


def resolve_aggregate_id(aggregate_type: str, aggregate_id, payload) -> str:
    """The id an event is about.

    A create command is queued before its subject exists, so its event has an empty ``aggregate_id`` and the new id
    is in the result payload (``ticket_id``, ``task_id``, ...). Model signals carry it as ``id`` (and set the
    aggregate id). Returns ``""`` if it cannot be told.
    """
    if aggregate_id not in (None, ""):
        return str(aggregate_id)
    if not isinstance(payload, dict):
        return ""
    base = aggregate_kind(aggregate_type).split(".")[-1]
    for key in (f"{base}_id", "id"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return str(value)
    ids = [v for k, v in payload.items() if k.endswith("_id") and isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return str(ids[0]) if len(ids) == 1 else ""


async def recipients(session, event: dict, clients: tuple[Client, ...] | list[Client]) -> list[Client]:
    """The subset of ``clients`` who may be told about ``event`` (a dict with type, aggregate_type, aggregate_id)."""
    kind = aggregate_kind(event.get("aggregate_type", ""))
    deleted = str(event.get("type", "")).endswith(".deleted")

    if kind in ("ticket", "task"):
        if deleted:
            return [c for c in clients if c.access.is_admin]
        try:
            aggregate_id = int(resolve_aggregate_id(event.get("aggregate_type"), event.get("aggregate_id"), event.get("payload")))
        except (TypeError, ValueError):
            return [c for c in clients if c.access.is_admin]      # cannot tell what it is about: admins only
        check = repo.ticket_is_visible if kind == "ticket" else repo.task_is_visible
        verdicts: dict[int, bool] = {}                            # one query per agent, however many sockets
        allowed = []
        for client in clients:
            if client.staff_id not in verdicts:
                verdicts[client.staff_id] = await check(session, aggregate_id, access=client.access)
            if verdicts[client.staff_id]:
                allowed.append(client)
        return allowed

    if kind in ("user", "org"):
        return [c for c in clients if c.access.has_global("user.dir")]
    if kind in ("kb.faq", "kb.category"):
        return list(clients)
    if kind == "kb.canned":
        return [c for c in clients if c.access.has_role_perm("canned.manage")]
    return [c for c in clients if c.access.is_admin]
