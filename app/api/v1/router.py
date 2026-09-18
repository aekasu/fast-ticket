from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import authenticate_websocket
from app.core.ws_manager import manager
from app.modules.auth.router import router as auth_router
from app.modules.kb.router import router as kb_router
from app.modules.lookups.router import router as lookups_router
from app.modules.staff.router import router as staff_router
from app.modules.tasks.router import router as tasks_router
from app.modules.tickets.router import router as tickets_router
from app.modules.users.router import router as users_router

v1_router = APIRouter()

# ── Auth (no token required) ──────────────────────────────────────────────────
v1_router.include_router(auth_router, prefix="/auth")

# ── Tickets ───────────────────────────────────────────────────────────────────
v1_router.include_router(tickets_router, prefix="/tickets")

# ── Tasks ─────────────────────────────────────────────────────────────────────
v1_router.include_router(tasks_router, prefix="/tasks")

# ── Users & Organizations ────────────────────────────────────────────────────
v1_router.include_router(users_router)

# ── Staff, Teams & Departments ───────────────────────────────────────────────
v1_router.include_router(staff_router)

# ── Lookups (Topics, SLAs, Priorities) ───────────────────────────────────────
v1_router.include_router(lookups_router)

# ── Knowledgebase & Canned Responses ─────────────────────────────────────────
v1_router.include_router(kb_router)

# ── Domain routers (added per phase) ─────────────────────────────────────────
# Phase 8: banlist
# Phase 9: search
# Phase 10: forms
# Phase 11: commands, events inspection


# ── WebSocket ─────────────────────────────────────────────────────────────────

@v1_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Authenticated WebSocket for real-time event push.

    Clients must include the token via the Sec-WebSocket-Protocol header:
        protocols: ["bearer", "<access_token>"]

    The token is kept out of the URL so it does not appear in proxy/access logs.
    """
    protocols = [
        p.strip()
        for p in ws.headers.get("sec-websocket-protocol", "").split(",")
        if p.strip()
    ]
    token = protocols[1] if len(protocols) >= 2 and protocols[0].lower() == "bearer" else None
    try:
        authenticate_websocket(token)
    except Exception:
        await ws.close(code=1008)
        return

    await manager.connect(ws, subprotocol="bearer")
    try:
        while True:
            await ws.receive_text()  # keep connection alive; server pushes only
    except WebSocketDisconnect:
        manager.disconnect(ws)
