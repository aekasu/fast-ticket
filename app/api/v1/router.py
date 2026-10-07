from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import authenticate_websocket
from app.core.ws_manager import manager
from app.modules.agents.router import router as agents_router
from app.modules.auth.router import router as auth_router
from app.modules.banlist.router import router as banlist_router
from app.modules.commands.router import router as commands_router
from app.modules.forms.router import router as forms_router
from app.modules.kb.router import router as kb_router
from app.modules.lookups.router import router as lookups_router
from app.modules.search.router import router as search_router
from app.modules.staff.router import router as staff_router
from app.modules.tasks.router import router as tasks_router
from app.modules.profile.router import router as profile_router
from app.modules.system.router import router as system_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.apikeys.router import router as apikeys_router
from app.modules.pages.router import router as pages_router
from app.modules.lists.router import router as lists_router
from app.modules.emailtemplates.router import router as emailtemplates_router
from app.modules.emails.router import router as emails_router
from app.modules.filters.router import router as filters_router
from app.modules.roles.router import router as roles_router
from app.modules.settings.router import router as settings_router
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

# ── Agent directory ──────────────────────────────────────────────────────────
v1_router.include_router(agents_router)

# ── Lookups (Topics, SLAs, Priorities) ───────────────────────────────────────
v1_router.include_router(lookups_router)

# ── Knowledgebase & Canned Responses ─────────────────────────────────────────
v1_router.include_router(kb_router)

# ── Banlist ──────────────────────────────────────────────────────────────────
v1_router.include_router(banlist_router)

# ── Search ───────────────────────────────────────────────────────────────────
v1_router.include_router(search_router)

# ── Forms ────────────────────────────────────────────────────────────────────
v1_router.include_router(forms_router)

# ── Commands & Events Inspection ───────────────────────────────────────────
v1_router.include_router(commands_router)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@v1_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Authenticated WebSocket for real-time event push.

    Clients must include the token via the Sec-WebSocket-Protocol header:
        protocols: ["bearer", "<access_token>"]

    The token is kept out of the URL so it does not appear in proxy/access logs.

    What a client is sent is decided per agent: only events about things osTicket lets them see (their own
    tickets and tasks, the directory if they hold ``user.dir``, ...); see ``app.modules.events.visibility``.
    The connection ends when the token expires or the account is disabled (checked every ``WS_RECHECK_SECONDS``),
    and an agent may hold ``WS_MAX_CONNECTIONS_PER_STAFF`` at once. The server only pushes; anything a client
    sends is ignored.
    """
    protocols = [
        p.strip()
        for p in ws.headers.get("sec-websocket-protocol", "").split(",")
        if p.strip()
    ]
    token = protocols[1] if len(protocols) >= 2 and protocols[0].lower() == "bearer" else None
    try:
        claims, access = await authenticate_websocket(token)
    except Exception:
        await ws.close(code=1008)
        return

    client = await manager.connect(ws, access, expires_at=float(claims["exp"]), subprotocol="bearer",
                                  issued_at=float(claims.get("iat", 0)))
    if client is None:      # this agent already holds the maximum number of connections
        return
    try:
        while True:
            await ws.receive_text()  # keep connection alive; server pushes only
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(client)

# ── Roles and permission keys ────────────────────────────────────────────────
v1_router.include_router(roles_router)
v1_router.include_router(filters_router)
v1_router.include_router(emails_router)
v1_router.include_router(emailtemplates_router)
v1_router.include_router(lists_router)
v1_router.include_router(pages_router)
v1_router.include_router(apikeys_router)
v1_router.include_router(dashboard_router)
v1_router.include_router(system_router)
v1_router.include_router(profile_router)

# ── Instance settings a client needs (attachment limits) ─────────────────────
v1_router.include_router(settings_router)
