---
name: fitsm-reference
description: Safely consult fitsm.zip, the snapshot of fast-ticket's messy predecessor, to see how something was done before (bridge handlers, osTicket calls, payload shapes). Use when the user mentions fitsm, the predecessor, or "how did the old version do X".
---

# Consulting the predecessor (`fitsm.zip`)

`fitsm.zip` (~50 MB, repo root, gitignored) is **reference only**. Its contents must never land in this repository.

## Extract outside the repo

```bash
DEST="${TMPDIR:-/tmp}/fitsm-ref"          # prefer the session scratchpad if one is available
mkdir -p "$DEST"
unzip -l fitsm.zip | less                 # list first; extract only what you need
unzip -o -q fitsm.zip 'path/inside/zip/*' -d "$DEST"
grep -rn "Ticket::create" "$DEST" | head
```

Never extract into the repo (`.src_ref/`, `.tmp_extract/`, and `notes/.scratch/` are gitignored, but prefer a temp dir anyway). Delete the extraction when done.

## How to use what you find

- Treat it as **evidence of how osTicket behaves**, not as code to copy. The predecessor used a coarse "any authenticated staff can do anything" model and other patterns this project explicitly rejects.
- Re-implement findings in this repo's conventions (see `AGENTS.md`): typed Pydantic bodies, `require_perm`, `dispatch_command`, handler classes in `bridge/handlers/`.
- If it reveals an osTicket capability or quirk missing from `notes/OSTICKET_CAPABILITIES.md` / `notes/INTEGRATION_CAPABILITIES.md`, tell the user and offer to add it there.

Before committing, confirm nothing from the archive is staged:
```bash
git status --porcelain | grep -Ei 'fitsm|src_ref|tmp_extract' && echo "STOP: archive content staged"
```
