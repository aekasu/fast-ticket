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
grep -rhoP 'command_type="\K[a-z_.]+' app/ | sort -u   # compare against the case list
```

## Handler method — `bridge/handlers/<domain>.php`

Pattern (see `bridge/handlers/teams.php`):

```php
require_once INCLUDE_DIR . 'class.team.php';

class FastApiTeamHandler {
    private static function requireTeam($team_id) {
        $team = Team::lookup((int) $team_id);
        if (!$team) throw new Exception("Team not found: {$team_id}");
        return $team;
    }

    public static function update($payload, $requester_staff_id) {
        $team = self::requireTeam($payload['team_id'] ?? 0);
        $errors = [];
        // build $vars from $payload using osTicket's expected field names
        if (!$team->update($vars, $errors))
            throw new Exception('Team::update failed: ' . implode(', ', $errors));
        return ['team_id' => $team->getId()];
    }
}
```

Rules:
- `public static function <verb>($payload, $requester_staff_id)`; return a small JSON-serialisable array (ids + key fields) — it becomes the outbox event payload / `result_payload`.
- **Throw** on any failure (missing object, `$errors` non-empty, false return). The plugin catches it and marks the row `failed` with the message. Never silently return.
- Cast every payload value (`(int)`, `trim((string) ...)`). Payload keys must match the Python Pydantic model field names — note the mapping in the file docstring when osTicket's names differ (`is_enabled` → `isenabled`).
- Use osTicket model methods (`::lookup`, `::create`, `->update`, `->postReply`, …) — never raw SQL against `ost_*` tables. Consult `notes/OSTICKET_CAPABILITIES.md` for the right method and its `$vars` shape; the osTicket source is in the osticket container (`docker compose exec osticket ls /var/www/html/include`).
- Where the action should be attributed to the requesting agent, look them up with `Staff::lookup($requester_staff_id)` and pass them through (thread posts, assignments, notes).
- Authorization is enforced in FastAPI; the handler assumes the caller is authorised, but must still validate existence/state.

## Test it

Unit-level PHP tests don't exist; verify against the running stack (see the `dev-stack` skill, and `notes/fast-ticket-bridge-bringup-and-testing.md` Part 3a):
1. Insert or `curl` a command, 2. trigger cron, 3. check the inbox row's `status` / `error_message` and the new outbox row.

Commit as `feat(bridge): ...` / `fix(bridge): ...`.
