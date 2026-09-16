from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import authenticate_websocket
from app.core.ws_manager import manager
from app.modules.auth.router import router as auth_router

v1_router = APIRouter()

# ── Auth (no token required) ──────────────────────────────────────────────────
v1_router.include_router(auth_router, prefix="/auth")

# ── Domain routers (added per phase) ─────────────────────────────────────────
# Phase 2: from app.modules.tickets.router import router as tickets_router
#          v1_router.include_router(tickets_router, prefix="/tickets", tags=["tickets"])
# Phase 3: tasks
# Phase 4: users, organizations
# Phase 5: staff, teams, departments
# Phase 6: topics, sla, priorities
# Phase 7: kb, canned
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
