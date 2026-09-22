# fast-ticket — Critical Fixes PR Plan

Scope: items #1–#4 from the audit (bridge command coverage, inbox claim race,
notes/ gitignore, capabilities-doc filename). Ordered so each PR is small,
testable, and doesn't block on a later one.

Suggested commit prefixes per rules.md: `fix(bridge): ...`, `feat(bridge): ...`,
`fix(config): ...`, `chore(docs): ...`.

---

## PR 1 — `chore(docs): fix notes/ gitignore and capabilities doc filename`

Smallest, zero-risk, unblocks everything else being reviewable in git history.

- [ ] `.gitignore`: replace the bare `notes/` line with something that only
      ignores actual scratch/extraction output, e.g.:
      ```
      fitsm.zip
      notes/.scratch/
      ```
      (keep `notes/INTEGRATION_CAPABILITIES.md` tracked)
- [ ] Decide the canonical filename: either
  - (a) `git mv notes/INTEGRATION_CAPABILITIES.md notes/OSTICKET_CAPABILITIES.md`, or
  - (b) keep `INTEGRATION_CAPABILITIES.md` and update `.agents/rules/rules.md`
        (lines ~41, ~120) to reference it.
- [ ] Fix the self-reference inside the doc itself (`notes/INTEGRATION_CAPABILITIES.md`
      currently points to `notes/OSTICKET_CAPABILITIES.md` — make it point to itself
      or whatever name you picked).
- [ ] `git add -f notes/<chosen-name>.md` if it was previously excluded, so it's
      actually tracked now — check `git status` shows it, not "ignored."

**Done when:** `notes/<file>.md` shows up in `git log --follow -- notes/`, and
rules.md + the doc's own header reference the same filename.

---

## PR 2 — `fix(bridge): make inbox claim atomic`

Do this before adding new command handlers, so the new handlers inherit a
correct claim mechanism instead of copying the racy one.

- [ ] `bridge/plugin.php` — `processInbox()`: replace the two-statement
      claim (`UPDATE ... WHERE status='pending'` then
      `SELECT ... WHERE status='processing'`) with a single atomic claim.
      Minimum viable fix (MySQL, no `SKIP LOCKED` before 8.0.1 — check your
      target version):
      ```php
      $run_id = bin2hex(random_bytes(8)); // or getmypid() . '-' . time()

      db_query("UPDATE ost_integration_inbox
                 SET status='processing', claimed_by='{$run_id}', processed_at=NULL
                 WHERE status='pending'
                 ORDER BY id ASC LIMIT {$batch_size}");

      $rows = db_query("SELECT * FROM ost_integration_inbox
                         WHERE status='processing' AND claimed_by='{$run_id}'
                         ORDER BY id ASC");
      ```
      (use `db_input()` / prepared params for `$run_id`, don't string-interpolate
      it raw even though it's server-generated — keep the pattern consistent
      with the rest of the file's `db_input()` usage)
- [ ] `docker/mysql/01_integration_tables.sql` — add a `claimed_by VARCHAR(32) NULL`
      column to `ost_integration_inbox` (and an index if you'll query by it).
- [ ] Add a stuck-row recovery path: on `processInbox()` entry, before claiming
      new `pending` rows, reset any `processing` row whose `processed_at IS NULL`
      and `created_at` is older than e.g. 5 minutes back to `pending` (handles
      a PHP fatal error mid-batch that never reaches the done/failed update).
      ```php
      db_query("UPDATE ost_integration_inbox
                 SET status='pending', claimed_by=NULL
                 WHERE status='processing'
                   AND processed_at IS NULL
                   AND created_at < DATE_SUB(NOW(), INTERVAL 5 MINUTE)");
      ```
- [ ] Test manually (or via `docker-compose.test.yml`): start two overlapping
      cron runs against the same seed data and confirm no command is executed
      twice (check `ost_integration_outbox` for duplicate `event_type` +
      `aggregate_id` pairs from a single logical write).

**Done when:** concurrent `processInbox()` calls never select the same row,
and a row abandoned mid-processing gets retried exactly once, not forever
and not zero times.

---

## PR 3 — `feat(bridge): add missing command handlers` (can split into sub-PRs per domain)

Add handler files + switch cases for every command type the Python side
already dispatches but the bridge doesn't recognize. Do this domain by domain
so each sub-PR is reviewable against one `app/modules/<domain>/service.py`.

### 3a — Departments & Teams
- [ ] Create `bridge/handlers/departments.php` — `FastApiDepartmentHandler` with
      `create()`, `update()`, `delete()`, modeled on `handlers/staff.php`'s
      structure (`requireX()` lookup helper + `Dept::create/lookup/update`).
- [ ] Create `bridge/handlers/teams.php` — `FastApiTeamHandler` with
      `create()`, `update()`, `delete()` (`Team::create/lookup/update`).
- [ ] `bridge/plugin.php` — add cases: `dept.create`, `dept.update`,
      `dept.delete`, `team.create`, `team.update`, `team.delete`.
- [ ] `bridge/plugin.php` — add both to `commandToEventType()` map:
      `dept.created/updated/deleted`, `team.created/updated/deleted`.
- [ ] Cross-check field names against `app/modules/staff/models.py`
      (`DepartmentCreate`, `TeamCreate`, etc.) so payload keys line up.

### 3b — Help Topics, SLAs, Priorities
- [ ] Create `bridge/handlers/lookups.php` — one file, three classes
      (`FastApiTopicHandler`, `FastApiSlaHandler`, `FastApiPriorityHandler`),
      each with `create()/update()/delete()`.
- [ ] `bridge/plugin.php` — add cases: `topic.create/update/delete`,
      `sla.create/update/delete`, `priority.create/update/delete`.
- [ ] Add to `commandToEventType()`.
- [ ] Cross-check against `app/modules/lookups/models.py`.

### 3c — Dynamic Forms
- [ ] Create `bridge/handlers/forms.php` — `FastApiFormHandler` with
      `create()/update()/delete()` against osTicket's `DynamicForm` model.
- [ ] `bridge/plugin.php` — add cases: `form.create/update/delete`.
- [ ] Add to `commandToEventType()`.
- [ ] Cross-check against `app/modules/forms/models.py`.

### 3d — Banlist
- [ ] Create `bridge/handlers/banlist.php` — `FastApiBanlistHandler` with
      `create()/update()/delete()` against osTicket's `Banlist` model.
- [ ] `bridge/plugin.php` — add cases: `banlist.create/update/delete`.
- [ ] Add to `commandToEventType()`.
- [ ] Cross-check against `app/modules/banlist/models.py`.

### 3e — Tasks (missing verbs) & Staff delete
- [ ] `bridge/handlers/tasks.php` — add `reopen()`, `addNote()`, `edit()`
      methods to `FastApiTaskHandler` (alongside existing `create/assign/close/delete`).
- [ ] `bridge/handlers/staff.php` — add `delete()` to `FastApiStaffHandler`.
- [ ] `bridge/plugin.php` — add cases: `task.reopen`, `task.note`, `task.edit`,
      `staff.delete`.
- [ ] Add to `commandToEventType()`: `task.reopened`, `task.note_added`,
      `task.edited`, `staff.deleted`.

### 3f — Cross-check for anything else missed
- [ ] Run this to confirm every `command_type=` string in the Python services
      has a matching `case` in `plugin.php` before merging:
      ```bash
      grep -rhoE 'command_type="[a-z._]+"' app/modules/*/service.py \
        | sed 's/command_type="\(.*\)"/\1/' | sort -u > /tmp/py_types.txt

      grep -oE "case '[a-z._]+':" bridge/plugin.php \
        | sed "s/case '\(.*\)':/\1/" | sort -u > /tmp/php_types.txt

      diff /tmp/py_types.txt /tmp/php_types.txt
      ```
      (the `ticket.answered`/`ticket.unanswered` pair sharing one `case` block
      is expected and fine — everything else in the `py_types`-only column
      needs a case)

**Done when:** the diff above is empty (or every remaining line is an
intentionally-shared case), and `docker-compose.test.yml`'s end-to-end suite
exercises each new command type at least once.

---

## PR 4 — `test(bridge): end-to-end coverage for newly-wired commands`

- [ ] For each domain touched in PR 3, add/extend an integration test in
      `tests/integration/` that: dispatches the command via the FastAPI route,
      asserts `202` + a `command_id`, then (against the real docker-compose
      test stack) asserts the row transitions to `status='done'` and a matching
      `ost_integration_outbox` event appears.
- [ ] Add a regression test that fails if a new `command_type` is introduced
      in `app/modules/**/service.py` without a matching `case` in
      `bridge/plugin.php` — e.g. a small Python test that shells out to the
      grep/diff from PR 3f and asserts empty output, so this can't silently
      regress again.

---

## Suggested merge order

1. PR 1 (docs/gitignore) — no code risk, unblocks reviewable history.
2. PR 2 (atomic claim) — foundational correctness fix, independent of new handlers.
3. PR 3a → 3e (one commit per domain, in any order) — each is additive and
   isolated to its own handler file + a few `case` lines.
4. PR 3f — verification diff, small final cleanup commit.
5. PR 4 — tests, once the commands they exercise actually exist.
