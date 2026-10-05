"""Files attached to thread entries: how a stored file is presented and sent.

The bytes of a file kept in osTicket's database (storage backend ``D``, its default) are read from its file
table. A file kept by any other backend (filesystem, S3, ...) is read through the bridge (``file.stream``), which
asks osTicket's own backend; if that cannot open it either, the answer is 501.
"""

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.core.db import AsyncSessionLocal
from app.integration.osticket import read_repository as repo
from app.integration.osticket.bridge_read import open_bridge_stream

DATABASE_BACKEND = "D"
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$", re.I)


def group_by_entry(rows) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["entry_id"])].append(dict(row))
    return grouped


async def _chunks(file_id: int) -> AsyncIterator[bytes]:
    async with AsyncSessionLocal() as session:   # its own session: the request's closes before the body is sent
        async for chunk in repo.iter_file_chunks(session, file_id):
            yield chunk


async def _bridge_chunks(client, response) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


async def file_response(row: dict, *, staff_id: int, object_type: str, object_id: int) -> StreamingResponse:
    """Stream the file a scoped attachment read returned. Always an attachment download, never rendered inline.
    ``object_type`` is ``T`` (ticket) or ``A`` (task); the bridge checks the agent's access again."""
    name = (row["name"] or "file").replace("\r", "").replace("\n", "")
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name) or "file"
    media_type = row["content_type"] if _MEDIA_TYPE.fullmatch(row["content_type"] or "") else "application/octet-stream"
    if row["backend"] == DATABASE_BACKEND:
        body = _chunks(int(row["file_id"]))
    else:   # a storage plugin: osTicket's own backend reads it; a refusal becomes the response before it starts
        client, upstream = await open_bridge_stream(
            "file.stream", staff_id,
            {"object_type": object_type, "object_id": int(object_id), "attachment_id": int(row["id"])})
        body = _bridge_chunks(client, upstream)
    return StreamingResponse(
        body,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(int(row["size"])),
        },
    )
