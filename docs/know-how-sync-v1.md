# Know-How Sync v1

This contract owns the 03:00 Agent OS knowledge sync.

## Purpose

Keep `/Users/wendy/park-io/008_codex session insights and decision logs` aligned with real source `decision-log.md` files and refresh thread-level `know-how.md`.

## Non-Negotiables

- Source truth is the real source `decision-log.md`; do not reconstruct decisions from historical transcripts.
- Obsidian `decision-log.md` copies must stay byte-identical to their source files.
- `know-how.md` is an abstraction from the synced decision log only.
- If a source decision log is missing, report it as skipped; do not fabricate a replacement.
- Preserve project-root `daily-update.md` files. They are owned by the Daily Closeout pipeline.
- Weekly or seven-day retrospective judgment is out of v1. Park runs that manually for now.

## Command

Run from `/Users/wendy/Documents/agent自管理`:

```bash
python3 scripts/codex_session_insights.py --write --json
```

## Verification

- Confirm every retained thread folder has exactly `decision-log.md` and `know-how.md`.
- Confirm each Obsidian `decision-log.md` is byte-identical to the source with `cmp -s`.
- Confirm project-root `daily-update.md` files still exist after sync.

## Output

Report:

- retained thread count
- retained project/thread names
- skipped missing sources
- output root
- verification result
