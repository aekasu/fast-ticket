"""Staff, Teams, and Departments endpoints.

Permissions:
  staff.create
  staff.edit
  staff.delete
  team.create
  team.edit
  team.delete
  dept.create
  dept.edit
  dept.delete
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm
from app.modules.staff import service
from app.modules.staff.models import (
    CommandResponse,
    DepartmentCreate,
    DepartmentUpdate,
    DepartmentDetail,
    DepartmentListResponse,
    TeamCreate,
    TeamUpdate,
    TeamDetail,
    TeamListResponse,
    StaffCreate,
    StaffUpdate,
    StaffDetail,
    StaffListResponse,
)

router = APIRouter(tags=["staff"])

# ── Departments ───────────────────────────────────────────────────────────────

@router.get("/departments", response_model=DepartmentListResponse, summary="List departments")
async def list_departments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("dept.create"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_departments(session, page=page, page_size=page_size)


@router.get("/departments/{dept_id}", response_model=DepartmentDetail, summary="Get department")
async def get_department(
    dept_id: int,
    current_user: dict = require_perm("dept.create"),
    session: AsyncSession = Depends(get_session),
):
    dept = await service.get_department(session, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    return dept


@router.post("/departments", response_model=CommandResponse, status_code=202, summary="Create department")
async def create_department(
    body: DepartmentCreate,
    current_user: dict = require_perm("dept.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_department(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/departments/{dept_id}", response_model=CommandResponse, status_code=202, summary="Update department")
async def update_department(
    dept_id: int,
    body: DepartmentUpdate,
    current_user: dict = require_perm("dept.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_department(session, dept_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/departments/{dept_id}", response_model=CommandResponse, status_code=202, summary="Delete department")
async def delete_department(
    dept_id: int,
    current_user: dict = require_perm("dept.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_department(session, dept_id, current_user["staff_id"], idempotency_key)


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams", response_model=TeamListResponse, summary="List teams")
async def list_teams(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("team.create"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_teams(session, page=page, page_size=page_size)


@router.get("/teams/{team_id}", response_model=TeamDetail, summary="Get team")
async def get_team(
    team_id: int,
    current_user: dict = require_perm("team.create"),
    session: AsyncSession = Depends(get_session),
):
    team = await service.get_team(session, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.post("/teams", response_model=CommandResponse, status_code=202, summary="Create team")
async def create_team(
    body: TeamCreate,
    current_user: dict = require_perm("team.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_team(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/teams/{team_id}", response_model=CommandResponse, status_code=202, summary="Update team")
async def update_team(
    team_id: int,
    body: TeamUpdate,
    current_user: dict = require_perm("team.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_team(session, team_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/teams/{team_id}", response_model=CommandResponse, status_code=202, summary="Delete team")
async def delete_team(
    team_id: int,
    current_user: dict = require_perm("team.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_team(session, team_id, current_user["staff_id"], idempotency_key)


# ── Staff ─────────────────────────────────────────────────────────────────────

@router.get("/staff", response_model=StaffListResponse, summary="List staff")
async def list_staff(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("staff.create"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_staff(session, page=page, page_size=page_size)


@router.get("/staff/{staff_id}", response_model=StaffDetail, summary="Get staff")
async def get_staff(
    staff_id: int,
    current_user: dict = require_perm("staff.create"),
    session: AsyncSession = Depends(get_session),
):
    staff = await service.get_staff(session, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    return staff


@router.post("/staff", response_model=CommandResponse, status_code=202, summary="Create staff")
async def create_staff(
    body: StaffCreate,
    current_user: dict = require_perm("staff.create"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_staff(session, body, current_user["staff_id"], idempotency_key)


@router.patch("/staff/{staff_id}", response_model=CommandResponse, status_code=202, summary="Update staff")
async def update_staff(
    staff_id: int,
    body: StaffUpdate,
    current_user: dict = require_perm("staff.edit"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_staff(session, staff_id, body, current_user["staff_id"], idempotency_key)


@router.delete("/staff/{staff_id}", response_model=CommandResponse, status_code=202, summary="Delete staff")
async def delete_staff(
    staff_id: int,
    current_user: dict = require_perm("staff.delete"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_staff(session, staff_id, current_user["staff_id"], idempotency_key)
