# Building a client on fast-ticket

fast-ticket is a backend. Any client — a web frontend, a mobile app, a CLI, a script — consumes it as an HTTP and
WebSocket contract. **No client code lives in this repository**; a frontend belongs in its own repo. This page is
what such a client needs to know that the OpenAPI schema alone does not say. Event shapes are in `EVENTS.md`.

## The contract

- **HTTP:** `GET /openapi.json` (also `/docs`, `/redoc`) is the schema; generate typed clients from it. It is served
  only while `API_DOCS_ENABLED=true` (the dev default) — a production deployment turns it off, so a client's build
  should read the schema from a dev/staging instance or a copy saved with a release, not from production.
- **Base path:** every route is under `/api/v1/…`; `GET /health` (no auth) is outside it.
- **WebSocket:** `GET /api/v1/ws` — not described by OpenAPI; see `EVENTS.md`.
- Identifiers are osTicket's integer primary keys (`ticket_id`, `task_id`, …). The public `number` is for display.

## Authentication

1. `POST /api/v1/auth/login` with an `application/x-www-form-urlencoded` body (`username`, `password`) — the OAuth2
   password flow, not JSON. Returns `{"access_token", "token_type": "bearer", "expires_in": <seconds>}`.
2. Send `Authorization: Bearer <token>` on every other request.
3. There are **no refresh tokens**: when the token expires (`expires_in`, default 30–60 min) log in again. A `401`
   on any request means the same thing — expired, forged, agent disabled, or password changed since it was issued.
4. `GET /api/v1/auth/me` returns what the agent may do *right now* (`is_admin`, `global_permissions`, `roles`,
   `department_ids`, `team_ids`, `assigned_only`). It is read from osTicket on every call, so use it to decide which
   buttons to show — but the server enforces the same rules whatever the UI shows. Re-read it when an event says an
   agent, role or department changed (access can lag up to `ACCESS_CACHE_TTL_SECONDS`, default 5 s).
5. Login refusals carry a reason in the `X-Login-Error` response header: `invalid_credentials` (401),
   `account_disabled` (401), `two_factor_required` (403), `password_change_required` (403). Accounts with 2FA cannot
   use the API at all yet. A locked-out account/address pair gets `429` with `Retry-After`.

Where to keep the token is the client's decision (the API sets no cookies). Memory is safest; anything in
`localStorage` is readable by any script on the page.

## Reads

- **Timestamps are instants.** Every timestamp the API returns (`created`, `updated`, `closed`, `due_date`,
  `est_due_date`, `last_activity`, `registered`, `created_at`, ...) is ISO 8601 in UTC with a `Z`:
  `"2026-10-03T18:18:02Z"`. Parse it as an instant and show it in the viewer's timezone (or osTicket's configured
  one). A datetime you *send* (`due_date`) may carry an offset (converted) or none (taken as UTC); prefer `Z`.

- A resource the agent may not open is a **404, never a 403**. A client must treat 404 as "gone or not yours".
- Lists are paginated and capped; the page-size limit is in the schema. List responses use a top-level key named for
  the resource (`{"tickets": [...]}`) — check the schema, since a few (commands) use `items`.
- Ticket and task lists take search, sort and a queue (`GET /tickets/queues` gives the queues with counts).

## Writes are asynchronous — the 202 pattern

Every write is queued, not executed inline. This is the part clients most often get wrong.

1. `POST`/`PATCH`/`DELETE` answers **`202`** with `{"command_id", "status": "pending", "idempotency_key"}`. That
   means "queued", **not** "done" — and `202` says nothing about whether osTicket will accept it.
2. Learn the outcome with `GET /api/v1/commands/{command_id}?wait=<seconds ≤ 30>`: the request is held until the
   command is `done` or `failed` (or the wait runs out, in which case ask again). `status` is one of `pending`,
   `processing`, `done`, `failed`.
3. `done` → `result_payload` is a **JSON string** (parse it); a create returns the new id there (`ticket_id`, …).
   `failed` → `error_message` is osTicket's own reason, including permission refusals — show it to the agent.
4. Then re-read the resource (or wait for its event, `EVENTS.md`). Validation the API can do up front is a `422`
   at step 1; validation only osTicket can do arrives as `failed` at step 2.

A client sees only its own commands (administrators see all).

**Idempotency.** Send a fresh `Idempotency-Key` header (≤ 200 characters) per user action and reuse it only to retry
that same action. A repeat from the same agent returns the original command instead of queuing a second one. The same
key with a different request body is a `422`. Generate it when the user clicks, not when the request is sent, so a
retry after a network error reuses it.

## Errors

`{"detail": "..."}` with the right status; `422` bodies carry FastAPI's per-field list in `detail`. `413` for a body
over `MAX_REQUEST_BYTES`, `429` for rate limits (`Retry-After`). Attachment downloads stream the file and answer
`Content-Disposition: attachment`; they are never rendered inline.

## Browsers: CORS and deployment

- Set `CORS_ORIGINS` (comma-separated) to the frontend's origin. Empty means no cross-origin access. Credentials are
  not allowed (the token travels in a header), so a client must not send cookies.
- Allowed methods are `GET POST PATCH DELETE`; allowed request headers `Authorization`, `Content-Type`,
  `Idempotency-Key`; readable response headers `X-Login-Error`, `Retry-After`, `Content-Disposition`.
- CORS does not apply to the WebSocket handshake, which is authenticated by its token instead.
- Simplest production shape: one reverse proxy serving the frontend's static files at `/` and forwarding `/api`
  (including the WebSocket upgrade) to this API. That is same-origin, so none of the CORS settings are needed.
  Behind a proxy, set `TRUSTED_PROXY_COUNT` so rate limits and the login lockout see the real client address.

## Known holes a client will meet

See `KNOWN_LIMITS.md`. The ones that shape a UI: no file upload, no ticket export, no custom queues, list search does
not read message bodies, team and SLA lists are admin-only, 2FA accounts cannot log in, no event replay.
