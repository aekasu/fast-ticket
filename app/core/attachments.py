"""Files attached to thread entries: how a stored file is presented and sent.

The bytes are read from osTicket's own file table (storage backend ``D``, osTicket's default). A file kept by
a storage plugin (filesystem, S3) is not readable from here and is refused with 501, not guessed at.
"""

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.core.db import AsyncSessionLocal
from app.integration.osticket import read_repository as repo

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


def file_response(row: dict) -> StreamingResponse:
    """Stream the file a scoped attachment read returned. Always an attachment download, never rendered inline."""
    if row["backend"] != DATABASE_BACKEND:
        raise HTTPException(status_code=501, detail="This file is kept by a storage plugin the API cannot read.")
    name = (row["name"] or "file").replace("\r", "").replace("\n", "")
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name) or "file"
    media_type = row["content_type"] if _MEDIA_TYPE.match(row["content_type"] or "") else "application/octet-stream"
    return StreamingResponse(
        _chunks(int(row["file_id"])),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(int(row["size"])),
        },
    )
