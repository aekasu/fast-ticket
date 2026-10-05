"""Request size limit as a pure ASGI middleware (no dependency on the body being read)."""

import json
import re


class _TooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject request bodies over a limit with 413, before a route or Pydantic sees them.

    The limit is ``max_bytes``, or ``upload_max_bytes`` for the paths matching ``upload_paths`` (routes that take
    files; their own caps are checked by the request models). Checks ``Content-Length`` up front, and counts
    streamed bytes for chunked uploads that don't declare one.
    """

    def __init__(self, app, max_bytes: int, *, upload_max_bytes: int | None = None, upload_paths: str | None = None):
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes
        self.upload_paths = re.compile(upload_paths) if upload_paths else None

    def _limit_for(self, path: str) -> int:
        if self.upload_paths and self.upload_max_bytes and self.upload_paths.match(path):
            return max(self.max_bytes, self.upload_max_bytes)
        return self.max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = self._limit_for(scope["path"])   # a local: one middleware instance serves every request
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = limit + 1
                if declared > limit:
                    return await self._reject(send, limit)

        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send, limit)

    async def _reject(self, send, limit):
        body = json.dumps({"detail": f"Request body exceeds {limit} bytes."}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
