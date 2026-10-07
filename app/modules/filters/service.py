import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.bridge_read import bridge_json
from app.integration.osticket.commands_service import dispatch_command
from app.modules.filters.models import (
    ACTION_TYPES, ActionTypeInfo, CommandResponse, FilterActionItem, FilterCreate, FilterDetail, FilterItem, FilterList,
    FilterOptions, FilterRuleItem, FilterUpdate, MatchField, MatchGroup)

_MATCH_TYPES = [
    ("equal", "Equal"), ("not_equal", "Not Equal"), ("contains", "Contains"), ("dn_contain", "Does Not Contain"),
    ("starts", "Starts With"), ("ends", "Ends With"), ("match", "Matches Regex"), ("not_match", "Does Not Match Regex")]
# osTicket stores some configuration keys under other names
_STORED_AS_API = {"priority": "priority_id", "from": "from_email_id"}
_API_AS_STORED = {"priority_id": "priority", "from_email_id": "from"}
_CONFIG_FIELDS = ("canned_id", "dept_id", "priority_id", "sla_id", "team_id", "staff_id", "topic_id", "status_id",
                  "recipients", "subject", "message", "from_email_id")


def _item(row: dict) -> dict:
    return dict(id=row["id"], name=row["name"], execorder=row["execorder"], is_active=bool(row["is_active"]), target=row["target"],
                email_id=row["email_id"] or None, email_address=row["email_address"], match_all_rules=bool(row["match_all_rules"]),
                stop_onmatch=bool(row["stop_onmatch"]), rule_count=int(row["rule_count"] or 0), created=row["created"], updated=row["updated"])


def _action(row: dict) -> FilterActionItem:
    try:
        stored = json.loads(row["configuration"] or "{}")
    except ValueError:
        stored = {}
    config = {}
    for key, value in (stored if isinstance(stored, dict) else {}).items():
        name = _STORED_AS_API.get(key, key)
        if name in _CONFIG_FIELDS and value not in (None, ""):
            config[name] = int(value) if name.endswith("_id") and str(value).isdigit() else value
    return FilterActionItem.model_construct(id=row["id"], sort=row["sort"], type=row["type"], **{f: config.get(f) for f in _CONFIG_FIELDS})


async def list_filters(session: AsyncSession, *, page: int, page_size: int) -> FilterList:
    rows = await repo.list_filters(session, limit=page_size, offset=(page - 1) * page_size)
    return FilterList(items=[FilterItem(**_item(dict(r))) for r in rows], total=await repo.count_filters(session), page=page, page_size=page_size)


async def get_filter(session: AsyncSession, filter_id: int) -> FilterDetail | None:
    row = await repo.get_filter(session, filter_id)
    if row is None:
        return None
    rules = [FilterRuleItem(id=r["id"], what=r["what"], how=r["how"], val=r["val"]) for r in await repo.list_filter_rules(session, filter_id)]
    actions = [_action(dict(a)) for a in await repo.list_filter_actions(session, filter_id)]
    return FilterDetail(**_item(row), notes=row["notes"] or "", rules=rules, actions=actions)


async def options(staff_id: int) -> FilterOptions:
    result = await bridge_json("filter.options", staff_id, {})
    return FilterOptions(
        matches=[MatchGroup(group=g["group"], fields=[MatchField(**f) for f in g["fields"]]) for g in result["matches"]],
        match_types=[MatchField(key=k, label=v) for k, v in _MATCH_TYPES],
        targets=["Any", "Web", "API", "Email"],
        actions=[ActionTypeInfo(type=t, label=info[0], fields=list(info[1]), multi_use=info[2]) for t, info in ACTION_TYPES.items()])


def _payload_action(action) -> dict:
    out = {"type": action.type}
    for field in _CONFIG_FIELDS:
        value = getattr(action, field)
        if value is not None:
            out[_API_AS_STORED.get(field, field)] = value
    return out


def _response(cmd, key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=key)


def _payload(body) -> dict:
    data = body.model_dump(exclude_none=True, exclude={"rules", "actions"})
    if getattr(body, "rules", None) is not None:
        data["rules"] = [r.model_dump() for r in body.rules]
    if getattr(body, "actions", None) is not None:
        data["actions"] = [_payload_action(a) for a in body.actions]
    return data


async def create_filter(session: AsyncSession, body: FilterCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="filter.create", aggregate_type="filter", aggregate_id="",
        payload=_payload(body), requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_filter(session: AsyncSession, filter_id: int, body: FilterUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="filter.update", aggregate_type="filter", aggregate_id=str(filter_id),
        payload={"filter_id": filter_id, **_payload(body)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_filter(session: AsyncSession, filter_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="filter.delete", aggregate_type="filter", aggregate_id=str(filter_id),
        payload={"filter_id": filter_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)
