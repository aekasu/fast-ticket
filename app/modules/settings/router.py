"""Instance settings a client needs.

Permissions — ``/settings/attachments`` is open to any agent (``require_staff()``): it is a limit the client must
know to build a form. The settings pages (``/settings/<section>``, osTicket's ``scp/settings.php``) are Admin Panel
pages (``isAdmin()``), so they are ``require_admin()``.
"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin, require_staff
from app.modules.settings import service
from app.modules.settings.models import SECTION_MODELS, AttachmentLimits, CommandResponse

router = APIRouter(tags=["settings"])


@router.get("/settings/attachments", response_model=AttachmentLimits, summary="How big an attachment may be")
async def attachment_limits(
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    return await service.attachment_limits(session)


# One pair of routes per settings page. They are written out (not registered in a loop) so the route analysis in
# tests/unit/route_guards.py can see each guard and the command each queues.

SystemSettings, SystemSettingsUpdate = SECTION_MODELS["system"]


@router.get("/settings/system", response_model=SystemSettings, summary="Admin Panel settings: system")
async def get_system_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "system")


@router.patch("/settings/system", response_model=CommandResponse, status_code=202,
              summary="Update the system settings (partial; osTicket validates)")
async def update_system_settings(
    body: SystemSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "system", body, current_user["staff_id"], idempotency_key)


TicketsSettings, TicketsSettingsUpdate = SECTION_MODELS["tickets"]


@router.get("/settings/tickets", response_model=TicketsSettings, summary="Admin Panel settings: tickets")
async def get_tickets_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "tickets")


@router.patch("/settings/tickets", response_model=CommandResponse, status_code=202,
              summary="Update the tickets settings (partial; osTicket validates)")
async def update_tickets_settings(
    body: TicketsSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "tickets", body, current_user["staff_id"], idempotency_key)


TasksSettings, TasksSettingsUpdate = SECTION_MODELS["tasks"]


@router.get("/settings/tasks", response_model=TasksSettings, summary="Admin Panel settings: tasks")
async def get_tasks_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "tasks")


@router.patch("/settings/tasks", response_model=CommandResponse, status_code=202,
              summary="Update the tasks settings (partial; osTicket validates)")
async def update_tasks_settings(
    body: TasksSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "tasks", body, current_user["staff_id"], idempotency_key)


AgentsSettings, AgentsSettingsUpdate = SECTION_MODELS["agents"]


@router.get("/settings/agents", response_model=AgentsSettings, summary="Admin Panel settings: agents")
async def get_agents_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "agents")


@router.patch("/settings/agents", response_model=CommandResponse, status_code=202,
              summary="Update the agents settings (partial; osTicket validates)")
async def update_agents_settings(
    body: AgentsSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "agents", body, current_user["staff_id"], idempotency_key)


UsersSettings, UsersSettingsUpdate = SECTION_MODELS["users"]


@router.get("/settings/users", response_model=UsersSettings, summary="Admin Panel settings: users")
async def get_users_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "users")


@router.patch("/settings/users", response_model=CommandResponse, status_code=202,
              summary="Update the users settings (partial; osTicket validates)")
async def update_users_settings(
    body: UsersSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "users", body, current_user["staff_id"], idempotency_key)


KbSettings, KbSettingsUpdate = SECTION_MODELS["kb"]


@router.get("/settings/kb", response_model=KbSettings, summary="Admin Panel settings: kb")
async def get_kb_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "kb")


@router.patch("/settings/kb", response_model=CommandResponse, status_code=202,
              summary="Update the kb settings (partial; osTicket validates)")
async def update_kb_settings(
    body: KbSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "kb", body, current_user["staff_id"], idempotency_key)


EmailsSettings, EmailsSettingsUpdate = SECTION_MODELS["emails"]


@router.get("/settings/emails", response_model=EmailsSettings, summary="Admin Panel settings: emails")
async def get_emails_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "emails")


@router.patch("/settings/emails", response_model=CommandResponse, status_code=202,
              summary="Update the emails settings (partial; osTicket validates)")
async def update_emails_settings(
    body: EmailsSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "emails", body, current_user["staff_id"], idempotency_key)


PagesSettings, PagesSettingsUpdate = SECTION_MODELS["pages"]


@router.get("/settings/pages", response_model=PagesSettings, summary="Admin Panel settings: pages")
async def get_pages_settings(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.get_section(session, "pages")


@router.patch("/settings/pages", response_model=CommandResponse, status_code=202,
              summary="Update the pages settings (partial; osTicket validates)")
async def update_pages_settings(
    body: PagesSettingsUpdate,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_section(session, "pages", body, current_user["staff_id"], idempotency_key)

