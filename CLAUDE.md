@AGENTS.md

## Starting a session (read this first — the user clears the conversation often)

Do this at the start of every conversation, before asking the user what to do:

1. Read `notes/NEXT.md`. It is the handoff: where things stand, what is open, in what order. **If it lists a
   "Do first" item (e.g. open security findings), start there** unless the user asks for something else.
2. Read `notes/KNOWN_LIMITS.md` (what is knowingly incomplete) and skim `git log --oneline -15` and `git status`.
3. Tell the user in 3–5 lines: the branch, what was done last, and the first item you will take — then start
   it. Do not ask "what should I work on?" when `NEXT.md` already says. Ask only for a genuine decision
   (e.g. the upload design for attachments / CSV import, which `NEXT.md` marks as needing the user).

Standing instructions (the user has given these; do not re-ask):
- Stay on the current branch; do not create branches or worktrees. Never commit `.claude/settings.json`
  (it carries the user's own uncommitted change).
- Reading `.env` is allowed (the user permitted it): source it *inside* a command to export variables
  (`set -a; . ./.env; set +a; make test-e2e`); never print its values or write them to a file.
- Finish each piece the same way: `make test` → `make test-docker` → rebuild (`docker compose up -d --build`) →
  `make test-e2e`, then commit atomically (`feat(bridge): …` for `bridge/`, `feat(<domain>): …` for the API,
  with the `Co-Authored-By` trailer), then update `NEXT.md` / `KNOWN_LIMITS.md` in the same change.
- Depth over breadth: finish and verify what exists against osTicket's own pages before adding new operations.
- Before ending a session: make sure everything is committed, `NEXT.md` is current (delete finished items,
  add what you found), and say in the final message whether the user can safely clear the conversation.
- If `docker compose ps` shows the db container exited (WSL restarts do this), `docker compose up -d db`.

## Claude Code notes

Project skills live in `.claude/skills/` — use them for the matching task:

| Skill | Use when |
|---|---|
| `/add-endpoint` | Adding or extending an API endpoint (read or write), end to end |
| `/add-bridge-command` | Adding/fixing the PHP side of a command in `bridge/` |
| `/endpoint-tests` | Writing the mandatory test matrix for an endpoint |
| `/dev-stack` | Booting, resetting, or debugging the Docker stack / bridge round-trip |
| `/fitsm-reference` | Looking something up in the predecessor snapshot `fitsm.zip` |

- Run tests with `make test` (or a single file: `.venv/bin/python -m pytest tests/integration/test_tasks.py -v`) before reporting a change as done — and `make test-docker` (image's pinned deps). If you touched `bridge/` or `app/integration/osticket/read_repository.py`, also `make test-e2e` against a rebuilt stack (OSTICKET_ADMIN_USERNAME/PASSWORD exported); mocked tests cannot see those bugs.
- Use the built-in `/security-review` before opening a PR that touches auth, permissions, or the bridge.
- **Known limits** live in `notes/KNOWN_LIMITS.md` (rules in AGENTS.md → "Known limits"). When you leave a gap, add an entry; when you fix one, delete it in the same commit. Review the whole list at the end of every phase or security step, before opening a PR, and now and then when it has sat untouched — check each entry against the code, delete what is solved, and say what you changed in your summary.
- Scratch/extraction output goes in `notes/.scratch/` (gitignored; the session scratchpad can vanish mid-session) — never in tracked paths.
