"""Synchronous, read-only questions for osTicket's own PHP (``bridge/read.php``, ``bridge/reads.php``).

Most reads are SQL (``read_repository``). A few need osTicket's code: a canned response with the ticket's variables
filled in, the bytes of a file kept by a storage backend other than the database. Those go over HTTP to the plugin's
``POST /api/fast-ticket/read`` with the integration key, as the staff member who asked, and never write. The caller
checks the agent's access first (a resource they may not open is a 404 before the bridge is asked); the PHP checks
again with osTicket's own rules.
"""

import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


def read_url(auth_url: str | None = None) -> str:
    """``…/api/auth/integration`` -> ``…/api/fast-ticket/read`` (same host and key)."""
    return re.sub(r"/auth/integration/?$", "/fast-ticket/read", auth_url or settings.OSTICKET_AUTH_URL)


def _request(op: str, staff_id: int, params: dict) -> dict:
    return {
        "url": read_url(),
        "json": {"op": op, "staff_id": int(staff_id), "params": params},
        "headers": {"X-Integration-Key": settings.FASTAPI_INTEGRATION_KEY},
    }


def refusal(status: int, body: dict) -> HTTPException:
    """How the bridge's refusal reaches the client: a resource the agent may not open is a 404, never a 403."""
    code, message = body.get("code"), body.get("error") or "The read was refused."
    if status in (403, 404) and code in ("not_found", "forbidden"):
        return HTTPException(status_code=404, detail=message)
    if status == 501:
        return HTTPException(status_code=501, detail=message)
    logger.error("osTicket read refused: %s %s %s", status, code, message)   # a bad key, a missing plugin file, a PHP error
    return HTTPException(status_code=502, detail="The osTicket bridge could not answer the read.")


def _unreachable(exc: Exception) -> HTTPException:
    logger.error("osTicket read bridge unreachable: %s", exc)
    return HTTPException(status_code=503, detail="osTicket is temporarily unavailable.")


def _json_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


async def bridge_json(op: str, staff_id: int, params: dict) -> dict[str, Any]:
    """Run a read that answers JSON; returns its ``result``."""
    try:
        async with httpx.AsyncClient(timeout=settings.OSTICKET_AUTH_TIMEOUT) as client:
            response = await client.post(**_request(op, staff_id, params))
    except httpx.TransportError as exc:
        raise _unreachable(exc)
    if response.status_code != 200:
        raise refusal(response.status_code, _json_body(response))
    result = _json_body(response).get("result")
    if not isinstance(result, dict):
        raise refusal(502, {"error": "no result"})
    return result


async def open_bridge_stream(op: str, staff_id: int, params: dict) -> tuple[httpx.AsyncClient, httpx.Response]:
    """Start a read that streams bytes. The status is checked before anything is returned, so a refusal can still
    become an error response; the caller must close both objects (``await response.aclose(); await client.aclose()``)."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(settings.OSTICKET_AUTH_TIMEOUT, read=60.0))
    try:
        response = await client.send(client.build_request("POST", **_request(op, staff_id, params)), stream=True)
    except httpx.TransportError as exc:
        await client.aclose()
        raise _unreachable(exc)
    if response.status_code != 200:
        try:
            body = _json_body(httpx.Response(response.status_code, content=await response.aread()))
        finally:
            await response.aclose()
            await client.aclose()
        raise refusal(response.status_code, body)
    return client, response
