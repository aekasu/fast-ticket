from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.emails.models import (
    CommandResponse, EmailAccountInfo, EmailAddress, EmailAddressDetail, EmailAddressList, EmailCreate, EmailUpdate)


def _address(row: dict) -> dict:
    return dict(
        id=row["id"], email=row["email"], name=row["name"] or "", dept_id=row["dept_id"] or None, dept_name=row["dept_name"],
        priority_id=row["priority_id"] or None, priority_name=row["priority_name"], topic_id=row["topic_id"] or None,
        topic_name=row["topic_name"], no_autoresponse=bool(row["no_autoresponse"]), is_default=bool(row["is_default"]),
        has_mailbox=bool(row["has_mailbox"]), has_smtp=bool(row["has_smtp"]), created=row["created"], updated=row["updated"])


async def list_emails(session: AsyncSession, *, page: int, page_size: int) -> EmailAddressList:
    rows = await repo.list_emails(session, limit=page_size, offset=(page - 1) * page_size)
    return EmailAddressList(items=[EmailAddress(**_address(dict(r))) for r in rows], total=await repo.count_emails(session),
                            page=page, page_size=page_size)


async def get_email(session: AsyncSession, email_id: int) -> EmailAddressDetail | None:
    row = await repo.get_email(session, email_id)
    if row is None:
        return None
    accounts = [
        EmailAccountInfo(
            id=a["id"], type=a["type"], is_active=bool(a["active"]), host=a["host"] or "", port=a["port"], protocol=a["protocol"],
            encryption=a["encryption"], folder=a["folder"], fetch_frequency=a["fetchfreq"] if a["type"] == "mailbox" else None,
            fetch_max=a["fetchmax"] if a["type"] == "mailbox" else None, after_fetch=a["postfetch"] if a["type"] == "mailbox" else None,
            num_errors=a["num_errors"] or 0, last_error=a["last_error_msg"], last_error_at=a["last_error"], last_activity=a["last_activity"])
        for a in map(dict, await repo.list_email_accounts(session, email_id))
    ]
    return EmailAddressDetail(**_address(row), notes=row["notes"] or "", accounts=accounts)


def _response(cmd, key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=key)


async def create_email(session: AsyncSession, body: EmailCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="email.create", aggregate_type="email", aggregate_id="",
        payload=body.model_dump(), requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_email(session: AsyncSession, email_id: int, body: EmailUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="email.update", aggregate_type="email", aggregate_id=str(email_id),
        payload={"email_id": email_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_email(session: AsyncSession, email_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="email.delete", aggregate_type="email", aggregate_id=str(email_id),
        payload={"email_id": email_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)
