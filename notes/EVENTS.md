# Real-time events (WebSocket)

The API pushes changes to connected agents so a client need not poll. This is the contract for that stream; it is not
in the OpenAPI schema. How a client uses it alongside REST is in `CLIENTS.md`. Implementation: `app/api/v1/router.py`
(`/ws`), `app/workers/events.py` (outbox → clients), `app/modules/events/visibility.py` (who hears what).

## Connecting

`GET /api/v1/ws`, upgraded to a WebSocket, with the access token in the subprotocol header — never the URL:

```js
new WebSocket("wss://host/api/v1/ws", ["bearer", accessToken]);
```

- A missing, forged, expired or disabled-account token is closed with code `1008`.
- An agent may hold `WS_MAX_CONNECTIONS_PER_STAFF` sockets (default 10); further ones are refused.
- The server only pushes. Anything the client sends is ignored (frames over 64 KiB close the socket). There is no
  subscribe/unsubscribe: the server decides what each agent hears.
- A connection ends when its token expires or the account is disabled, checked every `WS_RECHECK_SECONDS` (15 s), so
  reconnect after logging in again. A client that cannot take a message within `WS_SEND_TIMEOUT_SECONDS` is dropped.

## Message shape

Every message is one JSON text frame:

```json
{ "type": "ticket.replied", "aggregate_type": "ticket", "aggregate_id": "42", "payload": { "...": "..." } }
```

| Field | Meaning |
|---|---|
| `type` | `<kind>.<what happened>`, e.g. `ticket.created`, `ticket.closed`, `staff.updated` |
| `aggregate_type` | the kind of thing it is about: `ticket`, `task`, `user`, `org`, `staff`, `dept`, `team`, `form`, `banlist`, `kb.category`, `kb.faq`, `kb.canned`, … |
| `aggregate_id` | the osTicket id, **as a string**; `""` if it could not be told |
| `payload` | event-specific object (see below) — treat it as a hint, not a record |

**Treat an event as "this changed, refetch it".** Do not rebuild state from `payload`: its shape depends on how the
change was made, and it is deliberately minimal and free of credentials. The REST read is the source of truth.

Two sources produce events, and the same `type` can come from both:

- **A command finished** (a write made through this API). `type` is the past-tense event named for that command in
  `bridge/registry.php` (`ticket.reply` → `ticket.replied`); `payload` is the command's result (a create carries the
  new id, e.g. `ticket_id`). Only **successful** commands emit an event; a failed command emits nothing, so a client
  learns of failure through `GET /commands/{id}`, not the socket.
- **A change made in osTicket's own UI** (or by email, cron, …). `type` is `<aggregate_type>.created|updated|deleted`;
  `payload` is the row's plain columns with credential-like ones removed. Every agent login also produces a
  `staff.updated` for administrators (osTicket's last-login write); ignore it unless it matters.

The list of command events: `grep -oP "^\s*'\K[a-z_.]+(?=' +=>)" bridge/registry.php` for commands, and the fourth
element of each line for the event name.

## Who receives what

Decided per agent, on the server (`visibility.py`); a client never needs to filter for security and cannot widen it.

| Aggregate | Sent to |
|---|---|
| `ticket`, `task` | agents who have it **in their list** (osTicket's list visibility: department, assignment, referral; assigned-only for limited agents) |
| `user`, `org` | agents with `user.dir` |
| `kb.faq`, `kb.category` | every agent |
| `kb.canned` | agents whose role grants `canned.manage` |
| everything else (`staff`, `dept`, `team`, `form`, `banlist`, topics, SLAs, priorities, and the Admin Panel's `role`, `apikey`, `page`, `list`, `settings`, `emailtemplate`, `email`, `filter`, `schedule`, `ticket_status`, `log`, any unknown kind) | administrators only. A ticket's `ticket.linked` / `.unlinked` / `.unmerged` follow the ticket rule. `profile.*` events (an agent's own profile and password) are about `staff` and reach administrators only, not the agent |
| `*.deleted` of a ticket or task | administrators only (the row is gone, so no one can be checked against it) |

Consequences: an agent who can open a ticket only through a referral is not told about it; an agent is not told when a
ticket they could see is deleted — treat a `404` on refetch as a deletion.

## Delivery guarantees — there are none beyond best-effort

- **No replay, no event ids.** A process starts at the end of the log. An event raised while you were disconnected
  (or while the API restarted) is never delivered.
- **So: on every (re)connect, refetch whatever the screen shows.** Then apply events as they arrive.
- Events can arrive out of order across rows that committed concurrently, and a change may be reported more than once
  (osTicket fires both `model.*` and `object.*` signals; the bridge de-duplicates within one process only). Make
  handling idempotent: "refetch ticket 42" twice is harmless.
- An agent's own write returns its event too (after the command finishes) — use it, or the `?wait=` result, not both,
  to refresh.

## Adding a new kind of event

Add its aggregate to the table in `app/modules/events/visibility.py` **and** to the table above. An aggregate the
code does not know is delivered to administrators only (fails closed), which is safe but probably not what you want.
