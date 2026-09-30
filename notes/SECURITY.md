# Security model — threat model and checks

The rules an engineer follows live in `AGENTS.md` → "Security model". This page is the *why* and the *how to
verify*. Open gaps are in `KNOWN_LIMITS.md`; keep the two in step.

## Trust boundaries

| Boundary | Who is on the other side | What protects it |
|---|---|---|
| Internet → API | Anyone | TLS proxy (deployment), rate limit, body-size cap, strict JWT, security headers, CORS locked by default |
| API → osTicket database (reads) | Trusted code | `SELECT` only; every ticket/task/thread read is scoped to the agent (`access=`) |
| API → inbox (writes) | Trusted code | Route guard (`require_perm`/`require_admin`), typed bodies, per-agent idempotency keys |
| Inbox → PHP bridge | Whoever can write the inbox table | The bridge re-checks the command's permission for the requesting agent (`registry.php` column 5) and runs it as that agent |
| Outbox → WebSocket | Connected agents | Per-agent audience (`app/modules/events/visibility.py`), live access, periodic revalidation |
| Login | Anyone | osTicket's own backends verify the password; API-side lockout; 2FA accounts refused |

Not defended: an attacker with write access to the database (the inbox is unsigned — see limits), or with the
integration key or JWT secret.

## What each layer guarantees

1. **Identity** — a token proves who; access is read from osTicket on every request, so a demotion or a disabled
   account applies immediately.
2. **Authorization** — each route has exactly one gate, listed in the matrix test.
3. **Scoping** — reads return only what osTicket would show that agent; a foreign id is a 404, not a 403.
4. **Bridge** — commands are permission-checked again in PHP and run as the requesting agent.
5. **Events** — a client hears only events about things it may see.

## How to run the checks

```bash
make test           # includes the authorization matrix, scoping guard, hardening tests, registry checks
make test-docker    # same on the image's pinned dependencies
make doctor         # live: versions, privileges, stuck commands
set -a; . ./.env; set +a; make test-e2e   # live stack; includes the oracle differential test
```

The oracle test is the strongest check: it compares the API's visibility with osTicket's own PHP for a scenario
with sub-departments, managed departments, referrals and team assignment. When you change a guard, prove it can
fail: break it on purpose and confirm a test goes red, then restore it.

## Before a PR that touches auth, permissions or the bridge

Run `/security-review`, then `make test`, `make test-docker`, and `make test-e2e` on a rebuilt stack.
