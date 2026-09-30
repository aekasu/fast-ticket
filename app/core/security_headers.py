"""Response headers every reply carries, so a browser or proxy in between does the safe thing.

* ``Cache-Control: no-store``          tokens, personal data and ticket contents must never be cached
* ``X-Content-Type-Options: nosniff``  a JSON body is never reinterpreted as something else
* ``X-Frame-Options: DENY``            nothing here is meant to be framed
* ``Referrer-Policy: no-referrer``     no URL (ids, search terms) leaks through Referer

A header the route already set is left alone. HSTS is deliberately not set here: it belongs on the TLS-terminating
proxy, which is the only place that knows the request really arrived over HTTPS.
"""

_DEFAULTS = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = {
                    **message,
                    "headers": [*message.get("headers", []), *((n, v) for n, v in _DEFAULTS if n not in present)],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)
