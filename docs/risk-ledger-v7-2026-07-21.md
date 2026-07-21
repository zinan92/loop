# Rolling Risk Ledger v7 - 2026-07-21

Scope: read-only triage of six known risks. No runtime files, credentials, launchd jobs, cookies, or publishing paths were changed.

Cross-reference: `zinan92/park-ai-intel#2` reports the finance daily newsletter stopped after the 2026-07-18 publish. Local evidence does not show a `park-ai-intel` build error: `scripts/refresh-finance.log` ran on 2026-07-19, 2026-07-20, and 2026-07-21 and found no new source content until a manual 2026-07-21 09:53 CST refresh published the 2026-07-21 page. The source directory has `2026-07-18-finance-daily-newsletter.md` and `2026-07-21-finance-daily-newsletter.md`, with no 2026-07-19 or 2026-07-20 source files. This makes the upstream finance-generation path the likely root, not the static site publish step.

## WeChat 40164

Status: Accepted operational risk, not an active code regression in this worktree. The failure mode is known and documented as WeChat rejecting API calls when the outbound IP is not in the official account allowlist.

Evidence:
- `/Users/wendy/Documents/生图测试/baoyu-skills/skills/baoyu-post-to-wechat/SKILL.md` documents `errcode 40164` as an invalid IP / allowlist issue.
- `/Users/wendy/work/content-ops/README.md`, `SOP.md`, and workflow diagrams document `40164` as an occasional WeChat API path failure with DOCX import as manual fallback.
- `rg "40164"` found no current `loop` engine runtime code dependency on WeChat publishing.

Recommendation: Accept as a manual-publishing fallback risk unless WeChat API publishing becomes a daily critical path. If it does, fix by moving the WeChat API caller behind a stable egress IP or server-side relay whose IP is explicitly allowlisted.

Effort: S for documenting the fallback in the active content ops runbook; M if a stable relay / allowlisted egress path must be implemented and verified.

## CLIProxy 502

Status: Real recurring infrastructure risk, but not proven as the direct cause of `park-ai-intel#2`. It affects LLM-backed evals and fallback generation paths; the finance publish logs point first to missing upstream source files for 2026-07-19 and 2026-07-20.

Evidence:
- `/Users/wendy/work/input-to-park/README.md` documents the intended LLM behavior: DeepSeek retries on SSL/429/5xx and then fails over to CLIProxy/Sonnet; config errors fail fast.
- `/Users/wendy/work/input-to-park/lib.py` still defaults `PARKIO_CLIPROXY_ENDPOINT` to `http://localhost:8317/v1/messages`.
- Archived eval summaries under `/Users/wendy/Archive/park-vault-presplit-20260719/001_agent-os/skills/_evaluations/` record `localhost:8317` returning `502 Bad Gateway`, causing live trigger tests to be skipped.
- `park-ai-intel#2` suspects CLIProxy 502, but local `park-ai-intel/scripts/refresh-finance.log` shows the static refresh ran and had no new content to publish on 2026-07-19 through the scheduled 2026-07-21 run.

Recommendation: Fix, but keep it separate from the static-site publish issue. Add a cheap health check for `localhost:8317/v1/messages` wherever CLIProxy is a configured fallback, and make upstream generation reports say `source generation failed` vs `site publish had no input`.

Effort: S for health-check/reporting; M if the CLIProxy service itself needs supervised repair or replacement.

## Douyin Cookie

Status: Active source-health risk. The current dashboard simultaneously shows recent Douyin fetches as successful/no-new and the dedicated `Douyin Cookie` check as failed because the cookie file is missing. That is an ambiguous health signal.

Evidence:
- `/Users/wendy/work/input-to-park/generate-status.py` marks `Douyin Cookie` failed when `_secrets/douyin-cookies.json` is absent, or when recent Douyin fetches report errors.
- `/Users/wendy/work/input-to-park/enrichment/media/run.py` raises when `douyin-cookies.json` is missing before media enrichment.
- `/Users/wendy/park-io/_inbox/status.html` shows `Douyin Cookie` as failed with `cookie 文件缺失`, while individual Douyin rows show successful/no-new.
- `/Users/wendy/park-io/_source management/source-health.md` still lists several Douyin sources with recent `TimeoutError` / no-success evidence.

Recommendation: Fix the health semantics before refreshing credentials. The dashboard should distinguish `cookie missing`, `cookie not needed for this fetch mode`, `cookie present but expired`, and `fetch succeeded without new items`.

Effort: S for dashboard/status semantics; S manual Park action if a fresh Douyin login cookie is actually required.

## Server Credential Rotation

Status: Open security cleanup item, not safe for an automatic worker. No secret values were read or printed. Local evidence shows the expected target state is environment/secret-manager based credentials plus proof that old exposed credentials are invalidated.

Evidence:
- `/Users/wendy/Documents/投研面板/docs/architecture/intel-source-migration-matrix.md` has a Phase 0 `Security cleanup` gate: rotate exposed credentials, prove old credentials invalid, move secrets to env/secret manager, scan current tree/history, and save an audit record.
- `park-ai-intel` code reads runtime credentials from environment variables for Upstash/Vercel-style APIs and keeps real values out of `.env.example`.
- `loop` itself treats credentials, `.env`, auth, launchd/cron, and publishing/deploy changes as high risk/manual-only.

Recommendation: Keep as manual P0. Use a dedicated credential-rotation issue per affected service, with the audit result containing only metadata: service name, rotation date, old credential invalidation proof type, and scan command/result. Do not include secret values in issues, PRs, logs, or reports.

Effort: M per service family; L if Git history cleanup or external provider admin work is required.

## Evidence Scripts Dangling References

Status: Fix. The current `agent自管理` checkout contains only `scripts/codex_daily_closeout.py`, `scripts/codex_project_daily_report.py`, and `scripts/codex_session_insights.py`; the two evidence scripts are absent as source files, but stale references remain in historical project evidence.

Evidence:
- `find /Users/wendy/Documents/agent自管理 ...` found only bytecode files for `codex_evidence_scanner` and `codex_evidence_contract_links` under `scripts/__pycache__/`, not source `.py` files.
- `/Users/wendy/park-io/008_codex session insights and decision logs/agent os/decision-log.md` still references `scripts/codex_evidence_scanner.py` and `scripts/codex_evidence_contract_links.py`, including old verification commands.
- The current worktree's `scripts/` directory also does not include these two source files.

Recommendation: Fix documentation pointers. Either restore the two scripts from the commit/archive where they were intentionally introduced, or mark those evidence workflows as retired and replace runnable commands with the current closeout/report scripts. Do not leave `.pyc` as the only artifact for a referenced command.

Effort: S if retiring/repointing docs; M if restoring scripts and adding current tests.

## Presplit Undo Archive

Status: Keep, but do not treat it as a one-click rollback. `/Users/wendy/Archive/park-vault-presplit-20260719` is a usable pre-split snapshot, but it contains secrets directories and a dirty working tree; restoring it wholesale would reintroduce old runtime state and potentially stale secrets.

Evidence:
- `/Users/wendy/Archive/park-vault-presplit-20260719` exists, is about 717 MB, and has a Git snapshot commit `bb8a735 pre-split snapshot 2026-07-19`.
- The current `/Users/wendy/park-io` is a symlink to `/Users/wendy/park-hands`, about 624 MB when dereferenced, and has active changes after the split.
- The pre-split archive includes `_secrets/`, `.system/`, `.git/`, Obsidian plugin state, and old automation/support scripts.
- The archive working tree is not clean: at least `_inbox/wewe-auth-alert.json` and `_source management/source-health.md` differ from the snapshot.

Recommendation: Accept the archive as a read-only restore source for selective recovery. If rollback is needed, perform path-level restore with an explicit exclude list for `_secrets/`, `.git/`, `.system/`, caches/logs, and active post-split content. Do not replace `/Users/wendy/park-io` wholesale.

Effort: S for selective recovery of one path; M for a documented dry-run restore plan; L for full vault rollback with diff review and post-restore validation.

## New Risk Note

`park-ai-intel#2` is currently blocked at the board-claimer contract layer: its issue body has a non-exact risk token, no `Allowed Files`, verification commands that do not match trusted commands, and a dirty local checkout. This is separate from the finance newsletter production issue. The contract needs to be rewritten before an automated worker can safely repair that repo.
