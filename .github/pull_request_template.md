## Summary

<!-- What changed and why. Title format: <type>(<scope>): <summary> -->

## Checklist

- [ ] Relevant section of `notes/OSTICKET_CAPABILITIES.md` / `notes/INTEGRATION_CAPABILITIES.md` consulted
- [ ] Every new route has `require_perm(...)` / `require_admin()` (or a justified `# no permission required` comment)
- [ ] Request schemas fully typed and constrained (no `Any` / raw `dict`)
- [ ] Writes return 202 `CommandResponse` and honour `Idempotency-Key`
- [ ] New command types have a handler method and a `bridge/registry.php` entry
- [ ] No writes to `ost_*` tables outside the integration inbox/outbox
- [ ] Tests: happy path, 401, 403, 422, idempotency — `make test` passes
- [ ] New env vars documented in `.env.example`
- [ ] No `fitsm.zip` contents, `.env`, or caches committed
