@AGENTS.md

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
- Scratch/extraction output goes in `notes/.scratch/` (gitignored; the session scratchpad can vanish mid-session) — never in tracked paths.
