# Decision Log

> Current correction: the initial cwd-based grouping below was superseded on 2026-07-06. Codex `project` means the user-facing Codex App project label from the sidebar, not the transcript `cwd` basename.

## 2026-07-06 Codex Session Insights Backfill

### Objective

Bring Wendy's historical Codex project sessions into `/Users/wendy/park-io/008_codex session insights and decision logs` so every session has a `decision-log.md` and `know-how.md`, then define a daily 03:00 process that turns decision logs into higher-level project know-how.

### Decisions

- Treat `session_meta.payload.cwd` in Codex JSONL as the project key because this machine has no separate `~/.codex/projects` directory and every parsed session has a valid `cwd`.
- Count `/Users/wendy` as a project because Codex recorded it as the session cwd; flag it as broad in generated gotchas so future agents narrow ownership before editing.
- Use `/Users/wendy/.codex/sessions` and `/Users/wendy/.codex/archived_sessions` as the source of truth for historical Codex sessions.
- Preserve the user's requested archive shape: `008/<project>/<session>/decision-log.md` plus `know-how.md`.
- Generate a reusable system prompt under `_system/know-how-system-prompt.md` so future daily runs can turn event/decision records into higher-level project harness knowledge.
- Count sessions by unique Codex `thread_id`, not by JSONL file. The machine has `552` JSONL fragments, which merge into `540` real sessions across `49` projects.
- Create the daily Codex app automation `codex-session-know-how-refresh` for 03:00 local time. It runs the generator with `--skip-existing` so it can add new sessions without overwriting existing curated `know-how.md` files.

### Gotchas

- Historical backfill is transcript-derived; exact facts should still be verified against the raw JSONL when precision matters.
- Existing decision logs are sparse and project-level, so they can enrich related sessions but cannot replace session-level transcript evidence.
- Broad home-directory sessions are real Codex sessions, but they are weaker project boundaries than repo-specific cwd sessions.
- Some Codex threads are split across multiple JSONL files; using raw file count would overstate the session count.

## 2026-07-06 Correction: Codex App Project Labels

### Objective

Correct the historical Codex session archive so Wendy can use it as a project-level harness that matches the Codex sidebar mental model: one project label, many sessions, each session with `decision-log.md` and `know-how.md`.

### Decisions

- Supersede the earlier cwd-based grouping. A Codex project is the user-facing Codex App project label, such as `交易系统`, not `/Users/wendy/Documents/内容制作` or any other cwd basename.
- Use Codex App project catalog data from `state_5.sqlite` and `codex-dev.db`, then use `/Users/wendy/.codex/session_index.jsonl` as the preferred source for human-readable thread titles.
- Count sessions by unique Codex thread id under the 9 Codex App projects. Corrected count: 9 projects and 87 sessions.
- Rebuild `/Users/wendy/park-io/008_codex session insights and decision logs` from scratch after removing the cwd-grouped archive generated earlier in the day.
- Preserve the required archive shape: `008/<Codex App project label>/<session>/decision-log.md` plus `know-how.md`.
- Update the daily 03:00 automation `codex-session-know-how-refresh` so future runs add sessions using the Codex App project label boundary and do not overwrite curated existing files.

### Correct Counts

- `交易系统`: 20 sessions
- `ai newsletter`: 19 sessions
- `课程`: 18 sessions
- `park os`: 11 sessions
- `agent os`: 10 sessions
- `park的内容生产`: 6 sessions
- `finance newsletter`: 1 session
- `关系管理`: 1 session
- `自动回复系统`: 1 session

### Gotchas

- `cwd` is implementation metadata and evidence, not the archive grouping key.
- Some archived rollout files contain multiple `session_meta` entries; use the first session metadata in the file for the rollout thread id.
- `local_thread_catalog` and `state_5.sqlite` can disagree on title quality; prefer `session_index.jsonl` when it has a later human-facing title.
- Source JSONL file count is not the same as session count because some threads have multiple fragments.

## 2026-07-06 Final Policy: Active Thread Index + Project Know-How

### Objective

Make `/Users/wendy/park-io/008_codex session insights and decision logs` usable as a current Codex project knowledge index without fabricating historical decision logs from old transcripts.

### Decisions

- Stop creating historical thread-level `decision-log.md` and `know-how.md` files from transcript backfill.
  - Rationale: transcript summaries are not real decision logs; they dilute the user's actual project records.
  - Evidence: user explicitly rejected retrospective decision-log generation on 2026-07-06.

- Keep only active Codex App threads in the Obsidian session folder tree.
  - Rationale: the visible Obsidian structure should match the current Codex sidebar and avoid archived-session clutter.
  - Evidence: corrected counts are 53 active threads and 34 archived threads excluded.

- Name session folders from the current Codex thread title.
  - Rationale: Wendy frequently renames Codex threads; folder names must stay readable and searchable by current session name.
  - Evidence: `scripts/codex_session_insights.py` now uses `session_index.jsonl`, `local_thread_catalog`, and `state_5.sqlite` title metadata.

- Maintain `know-how.md` at project level only.
  - Rationale: know-how is a project harness; per-thread know-how creates too many small, repetitive, low-authority files.
  - Evidence: `008/<project>/know-how.md` is generated for each of the 9 Codex App projects.

- Treat thread folders as active-session indexes unless a future thread creates a real decision log.
  - Rationale: current historical threads should stay traceable, but not pretend to have hand-maintained decision logs.
  - Evidence: each active thread folder now contains `session-note.md`; no `sessions/*/decision-log.md` or `sessions/*/know-how.md` exists after rebuild.

- Sync existing real project-level logs into the project folder, but do not infer new decisions from historical transcripts.
  - Rationale: real source logs and implementation notes are evidence; generated transcript recaps are not.
  - Evidence: source logs include `/Users/wendy/Documents/内容制作/decision-log.md`, `/Users/wendy/trading-orchestrator/decision-log.md`, `/Users/wendy/trading-orchestrator/implementation-notes.md`, and this `decision-log.md`.

- Update the daily 03:00 automation to sync active thread names, remove generated archived session folders, and refresh project-level know-how.
  - Rationale: renamed Codex sessions should stay findable in Obsidian without manual folder maintenance.
  - Evidence: automation `codex-session-know-how-refresh` was updated to run `python3 scripts/codex_session_insights.py --write --json`.

### Gotchas

- Do not run the old 87-session historical backfill policy again.
- Project-level `decision-log.md` can be long when it syncs a real source log; that is acceptable because the source log is canonical evidence, not transcript fabrication.
- Future thread-level `decision-log.md` files are allowed only when the thread actually starts maintaining one going forward.
- Daily sync must not overwrite a future real thread-level `decision-log.md`; thread `session-note.md` is just an index file.

## 2026-07-06 Final Simplification: Existing Decision Logs Only

### Objective

Keep `/Users/wendy/park-io/008_codex session insights and decision logs` minimal and evidence-based: only projects that already have real `decision-log.md` sources should appear, and each retained project should contain only `decision-log.md` and `know-how.md`.

### Decisions

- Delete all generated session folders and session notes from 008.
  - Rationale: Wendy did not ask to preserve session notes; the current near-term structure should be project-only.
  - Evidence: user explicitly requested deleting all generated session notes and removing empty `sessions` folders.

- Delete every generated project folder that has no real decision-log source.
  - Rationale: no decision log means there is no evidence base for know-how yet.
  - Evidence: only `交易系统`, `agent os`, and `park os` currently have discoverable real decision-log sources.

- Keep exactly two markdown files per retained project folder: `decision-log.md` and `know-how.md`.
  - Rationale: this is the simplest usable structure while the decision-log habit is still being established.
  - Evidence: `scripts/codex_session_insights.py` now writes only those two files for retained projects.

- Generate `know-how.md` only from existing real decision logs.
  - Rationale: know-how should be an abstraction from decisions, not a reconstruction from old chat transcripts.
  - Evidence: the script now ignores Codex session JSONL for content generation.

### Gotchas

- Do not recreate `_system`, `sessions`, `session-note.md`, thread-level `decision-log.md`, thread-level `know-how.md`, or `implementation-notes.md` inside 008 under the current policy.
- `implementation-notes.md` may still be useful evidence in the source project, but 008 should not copy it as a third markdown file while the requested structure is two files only.
- If a future project gets its first real `decision-log.md`, it can be added to the retained project list and then receive a project-level `know-how.md`.

## 2026-07-06 Final Policy: Thread-Level Decision Logs and Know-How

### Objective

Make `/Users/wendy/park-io/008_codex session insights and decision logs` a thread-level knowledge harness: every retained Codex thread has its own final `decision-log.md` and concise `know-how.md`, while avoiding fabricated historical backfill.

### Decisions

- Move both `decision-log.md` and `know-how.md` to thread level.
  - Rationale: Wendy wants traceability from know-how back to the exact session/thread where the decision happened.
  - Evidence: latest user instruction on 2026-07-06 changed the structure from project-level know-how back to thread-level for both files.

- Treat source `decision-log.md` files generated during Codex work as final records.
  - Rationale: the Obsidian site should mirror the real working record, not rewrite or summarize it into a different decision-log format.
  - Evidence: `scripts/codex_session_insights.py` now copies source decision logs verbatim into OB thread folders.

- Generate `know-how.md` per thread from that thread's current decision log.
  - Rationale: know-how is a reusable abstraction from decisions, but should remain tied to the decision evidence that created it.
  - Evidence: each retained thread folder now gets one concise generated `know-how.md`.

- Keep the current retained set limited to existing real decision-log sources.
  - Rationale: there is still no historical decision-log backfill; projects/threads without a real source log stay out of 008.
  - Evidence: retained mappings are `交易系统/Tradingview api研究`, `交易系统/接入老虎证券`, `agent os/撰写decision-log转know-how提示词`, and `park os/整理项目为Park原始输出`.

### Gotchas

- Do not reintroduce project-level merged logs unless Wendy explicitly changes the policy again.
- Do not add `session-note.md` or transcript-derived files; a thread enters 008 only through a real source `decision-log.md`.
- `/Users/wendy/trading-orchestrator/decision-log.md` is currently mapped to `交易系统/接入老虎证券`, but it is a broad repo-level trading log; if future precision matters, split the source log first instead of splitting only the OB copy.
- "实时同步" currently means this sync script can copy the latest source log immediately when run, and the scheduled task runs daily; true filesystem watch mode would require a separate watcher.

## 2026-07-07 Codex Project Daily Report Automation

### Objective

Give Wendy one cumulative Markdown ledger that shows, by Codex Project, what changed in the last day and what has been pushed forward across the week.

### Decisions

- Create a separate cumulative report at `/Users/wendy/park-io/009_codex project daily reports/codex-project-daily.md`.
  - Rationale: daily project progress is different from 008's decision-log/know-how archive.
  - Evidence: `scripts/codex_project_daily_report.py --hours 48 --write` generated the first 48-hour report.

- Generate reports from local Codex evidence only: `state_5.sqlite`, `session_index.jsonl`, and rollout JSONL files.
  - Rationale: the report should reflect what actually happened inside Codex App, not a hand-written memory summary.
  - Evidence: the script reads thread ids, cwd, rollout paths, titles, timestamps, user requests, and final assistant outcomes.

- Group by Codex Project using a maintained cwd-to-project mapping.
  - Rationale: Codex's local DB exposes cwd reliably; the visible project label must be recovered through a mapping layer.
  - Evidence: current mappings include `交易系统`, `AI newsletter`, `agent os`, `park os`, `Daily Inbox`, and `内容生产`.

- Keep one Markdown cumulative, newest entry first.
  - Rationale: Wendy wants to track today's progress and the past week from one file.
  - Evidence: the script upserts entries by date/window anchor and inserts new sections near the top.

- Create a daily 04:00 Codex automation named `Codex Project Daily Report`.
  - Rationale: the report should be maintained without manual prompting.
  - Evidence: automation id `codex-project-daily-report` runs `python3 scripts/codex_project_daily_report.py --hours 24 --write --json`.

### Gotchas

- Some scheduled runs have an empty `thread_source`; infer automation from `Automation:` in the request/title instead of trusting the column alone.
- A still-running thread can contain a newer user request after an older final answer; do not treat the old final answer as the outcome for the new request.
- `cwd` is not the same as Codex Project label; update `PROJECT_ALIASES` when Wendy creates or renames major Codex Projects.
- The report is factual and evidence-based, not a deep strategic synthesis; if Wendy later wants narrative strategy analysis, that should be a separate layer.

## 2026-07-07 Pinned Project Daily Updates Under 008

### Objective

Make 008 the single Obsidian home for pinned Codex Project continuity: each pinned Project has its own cumulative daily progress file in its project folder.

### Decisions

- Move the daily update output from the single 009 report into 008 project folders.
  - Rationale: Wendy wants project-by-project continuity next to each Project's decision logs, know-how, and thread folders.
  - Evidence: `scripts/codex_project_daily_report.py --hours 48 --write` now writes `008/<Project>/daily-update.md`.

- Track only currently pinned Projects.
  - Rationale: the report should not become another noisy index of every local Codex/background thread.
  - Evidence: pinned list is `交易系统`, `Park 的内容生产`, `Park OS`, `Agent OS`, and `ai newsletter`.

- Keep empty pinned Projects visible with a "no activity" daily entry.
  - Rationale: if a pinned Project had no progress, that is still useful status.
  - Evidence: `Park 的内容生产/daily-update.md` was created with 0 thread activity for the current 48-hour bootstrap window.

- Update the daily 04:00 automation to write per-project 008 updates.
  - Rationale: the scheduled report should match the current Obsidian structure.
  - Evidence: automation `codex-project-daily-report` now runs the same script and expects outputs under `008/<Project>/daily-update.md`.

### Gotchas

- Codex local SQLite does not currently expose a reliable pinned-project table; pinned status is maintained in `PINNED_PROJECTS` and cwd mapping in `PROJECT_ALIASES`.
- Future pinned projects can be auto-created in 008 only after the new project label and cwd mapping are added to the script.
- On case-insensitive macOS, `Park OS` may resolve to an existing `park os` folder and `Agent OS` to `agent os`; the script still uses the configured display label inside `daily-update.md`.
- Do not resurrect the 009 single-report file as the primary daily artifact; it is now historical/legacy unless Wendy asks to keep a global rollup too.

## 2026-07-08 Preserve 008 Daily Updates During Know-How Sync

### Objective

Keep each pinned Codex Project's cumulative `daily-update.md` durable inside 008 while still allowing the daily decision-log/know-how sync to refresh thread-level folders.

### Decisions

- Change `scripts/codex_session_insights.py` so its cleanup preserves any project-level `daily-update.md`.
  - Rationale: the 03:00 know-how sync and the 04:00 Project Daily Report both operate under `/Users/wendy/park-io/008_codex session insights and decision logs`; the earlier full-root reset erased the cumulative daily updates before the next report run.
  - Evidence: `reset_output_root()` now removes stale project child folders/files except `daily-update.md`, instead of deleting each project directory wholesale.

- Keep thread-level sync authoritative for `decision-log.md` and `know-how.md`, but no longer authoritative for project-level daily reports.
  - Rationale: `daily-update.md` is maintained by `scripts/codex_project_daily_report.py`, so the know-how sync should not manage or delete it.
  - Evidence: a temp-output verification preserved seeded `daily-update.md` files, removed stale thread output, and regenerated retained thread folders.

### Gotchas

- If future project-level artifacts are added under 008, they need explicit preservation rules or a clearer ownership split.
- The current preservation rule is filename-based: only project-root `daily-update.md` is preserved during know-how sync cleanup.
- Stale project directories without a preserved project-level file are removed during cleanup; retained thread folders are recreated from `SOURCE_THREADS`.
- Case-insensitive paths still matter: `Park OS` and `park os`, or `Agent OS` and `agent os`, can resolve to the same folder on this machine.
