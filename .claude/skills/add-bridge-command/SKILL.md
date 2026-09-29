---
name: add-bridge-command
description: Implement or fix the PHP side of a fast-ticket command in the osTicket integration bridge (bridge/plugin.php dispatchCommand + bridge/handlers/*.php + commandToEventType). Use when adding a new command_type, when a queued inbox command fails or sits unhandled, or when editing any file in bridge/.
---

# Add a bridge command (PHP side)

The bridge runs **inside osTicket** (PHP, osTicket's include path). It turns `ost_integration_inbox` rows into calls on osTicket's own model classes, so osTicket's validation, filters, SLA, notifications, and audit trail all still run.

## Three places, always in sync

A command type is only real when all three exist, spelled identically:

1. **Python** — `dispatch_command(command_type="x.verb", ...)` in `app/modules/<domain>/service.py`
2. **`bridge/plugin.php` → `dispatchCommand()`** — a `case`:
   ```php
   case 'team.update':
       require_once dirname(__FILE__) . '/handlers/teams.php';
       $result = FastApiTeamHandler::update($payload, $row['requested_by_staff_id']);
       break;
   ```
   Put it under the matching `// ── <Domain> ──` section comment.
3. **`bridge/plugin.php` → `commandToEventType()`** — `'team.update' => 'team.updated',` (past tense; compound verbs use `_`, e.g. `ticket.note` → `ticket.note_added`). A missing entry doesn't error — it silently falls back to `<command>.executed`, which no Python handler listens for.

Check for drift:
```bash
diff <(grep -oP "case '\K[a-z_.]+" bridge/plugin.php | sort -u) \
     <(sed -n '/function commandToEventType/,/\];/p' bridge/plugin.php | grep -oP "^\s*'\K[a-z_.]+(?=' +=>)" | sort -u)
# `make test-unit` runs tests/unit/test_bridge_registry.py, which does this check for you
```

## Handler method — `bridge/handlers/<domain>.php`

Read the osTicket source **before** writing it — `docker compose exec osticket sed -n 1,80p /var/www/html/include/class.<x>.php`, and the admin page that does the same thing (`/var/www/html/scp/<x>.php`). osTicket's model API is written for its own web UI and is full of traps; every handler in this repo was once wrong because someone guessed the API. Do not guess method names or signatures, and do not trust `notes/OSTICKET_CAPABILITIES.md` for them (it claimed priorities have full CRUD; they have none).

```php
require_once INCLUDE_DIR . 'class.team.php';
require_once dirname(__FILE__) . '/common.php';     // FastApiModel helpers

class FastApiTeamHandler {
    public static function update($payload, $requester_staff_id) {
        $team = self::requireTeam($payload['team_id'] ?? 0);
        // start from what is stored, overlay only what the payload provides
        FastApiModel::saveViaUpdate($team, self::overlay(self::storedVars($team), $payload), 'Team');
        return ['team_id' => $team->getId()];
    }
}
```

Rules:
- `public static function <verb>($payload, $requester_staff_id)`; return a small array (ids + key fields) — it becomes `result_payload` and the outbox event payload. A `.create` handler **must** return a non-zero `*_id` (the dispatcher fails the command otherwise).
- **Throw** on any failure, with osTicket's own reason (`json_encode($errors)`). Never `return false`/silently succeed. Check *both* the return value and `$errors` — several osTicket `update()` methods return true while filling `$errors`.
- Payload keys are the Python Pydantic field names (`is_enabled`, `is_public`, `internal_notes`), not osTicket's (`isenabled`, `ispublic`, `notes`). Map them explicitly and note it in the file docstring. `body.model_dump(exclude_none=True)` means an absent key = "leave as is"; an explicit `false`/`0`/`""` is a real value.
- No raw SQL against `ost_*` tables; osTicket model methods only.

### The traps (each one broke a shipped handler)

1. **`Model::create()` only instantiates.** Nothing is saved until `->update($vars, $errors)` / `->save()`. Symptom: command `done` with a null id. Never report success on a return value alone.
2. **`->update()` overwrites every field** and web-form conventions apply: checkboxes are "key present = on" (`isset`), and it *deletes* members / extended access / permissions / topic links when their key is absent. So updates must start from the stored state (`storedVars`) and overlay the payload — a partial PATCH must never lose fields. (`departments.php`, `teams.php`, `staff.php` are the reference.)
3. **osTicket's ORM never writes a falsy value to a field that was never set** (`0` compares equal to null), so a new row created "private/disabled" silently gets the column default (usually enabled). `FastApiModel::saveViaUpdate()` does a second `update()` pass on creation for this; use it.
4. **Dynamic-form data** (organization/user phone & notes, task title, ticket subject) is stored as form-entry *answers*, not columns. osTicket's `update()` validates against the global `$_POST`, which doesn't exist under cron, and an existing answer ignores a data source. Use `FastApiModel::applyAnswers()` (parse → validate → `setAnswer` → save).
5. **Forms, not scalars.** `Ticket::assign/transfer`, `Task::assign/transfer` take an `AssignmentForm` / `TransferForm` (`AssignmentForm::instantiate(['assignee'=>'s1'|'t1', 'comments'=>…])`; the transfer field is `dept`, not `dept_id`). Validate the form first to get the real refusal reason.
6. **`$thisstaff`.** The dispatcher sets it to the requesting staff member; osTicket's permission checks and authorship read it. Don't set it yourself, and expect `checkStaffPerm()` to apply. Where osTicket's method doesn't check, call `checkStaffPerm($thisstaff, Ticket::PERM_X)`.
7. **The ORM caches entities per process** and one cron run drains up to 50 commands; the dispatcher flushes the cache per command — don't rely on object state across commands.
8. **Soft deletes / core data.** `DynamicForm::delete()` only flags the row; osTicket marks even its core forms deletable, so the API only deletes custom ones. Guard destructive operations against seeded data.
9. **Environment validation.** osTicket verifies email domains have MX records; test data must use a deliverable domain.
10. `Exception` isn't enough: a call to a method that doesn't exist raises a PHP `Error`. (The dispatcher catches `Throwable` — keep it that way.)

## Verify against the real stack (mandatory)

There is no PHP unit-test runner, and the Python tests mock the bridge away — so a handler is only verified once it has run against osTicket:

1. Fast dev loop (no rebuild): copy the changed files into the running containers — `docker cp bridge/. fast-ticket-osticket-cron-1:/var/www/html/include/plugins/fastapi_integration/` (and the `osticket` container), then `chown -R www-data` them. The image bakes `bridge/` at build time; the *final* check must be after `docker compose up -d --build osticket osticket-cron`.
2. Add or extend a test in `tests/e2e/test_bridge_domains.py` (create → read back → partial update proves stored fields survive → delete; plus the refusal cases). Run `make test-e2e   # needs OSTICKET_ADMIN_USERNAME/PASSWORD exported (a human sources .env; agents must not read it)`.
3. Failures show up in the inbox row's `error_message`; debug a handler in isolation by running PHP against osTicket's bootstrap (`api/api.inc.php`) inside the container with `$thisstaff = Staff::lookup(1)`.

`tests/unit/test_bridge_registry.py` statically enforces that every Python command has a `case`, every `case` has a `commandToEventType` entry, no `case` is unreachable, and every handler method the dispatcher calls exists.

Commit as `feat(bridge): ...` / `fix(bridge): ...`.
