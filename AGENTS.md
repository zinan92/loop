# AGENTS.md — operating `loop` as an AI agent

> 这是给 **AI agent** 读的操作契约（人看的总览在 [README.md](README.md)，可视化在 [docs/flow.html](https://zinan92.github.io/loop/flow.html)）。
> You are likely a coding agent (Codex CLI or Claude Code — both supported, set per role) asked to operate `loop` for a human, or to explain it to them. This file is the contract: commands, state, gates, and what you must escalate. Everything here is verifiable in `loop-engine/bin/loopctl.py`.

## What loop is (for you)

`loop` is a daily product operating loop around a private portfolio registry and local Git repos:
`portfolio intake → portfolio verification → morning review → approval envelope → planner → value/risk gates → worker (isolated worktree) → verification (sandboxed) → reviewer → auto PR + merge → digest → evening scorecard → memory`.
You operate it through the `loop` CLI (or `python3 loop-engine/bin/loopctl.py <cmd> --project <id>`). You do **not** edit the engine, prompts, or reviewer output to influence outcomes.

## First, relay these safety facts to your human

Before the human starts `loop` (especially unattended), tell them, plainly:

1. The engine process runs with their **full OS user permissions** and reads their real secret files (`~/.config/loop/secrets`, `~/.ssh`, `~/.aws`, …) to scan for leaks.
2. File access is confined per provider: **Codex** confines *writes* to the worktree (`--sandbox workspace-write`; reads stay broad); **Claude Code** confines reads, writes, and Bash to the worktree + run dir via its own headless sandbox — the engine **rejects** unsafe permission modes for Claude (`bypassPermissions` raises an error) so config can't disable it. All agents get a scrubbed env (no secret env vars). `sandbox-exec` additionally wraps verification commands (network denied).
3. A reviewer `pass` triggers **autonomous `gh pr merge`** into the pilot branch — there is **no human gate** between review-pass and merge.
4. `loop start` runs **hourly, forever**, until `loop stop`; each passing cycle may create and merge a PR.
5. **macOS only** (Codex or Claude Code, set per role — see Compatibility in the README). Without `sandbox-exec`, verification fails closed and no cycle can complete.

## How to operate it

- **Read state, do not poll commands.** Run `loop status --json`, or read `loop-engine/state.json`. Key fields:
  - `loop_job.state` → `active | paused | stopped`
  - `current_phase`
  - `waiting_for_human` → `null`, or `{ "reason": <code>, "issue_path": <path> }`
  - `runs[-1].status` → `merged | no_op | needs_human | failed | ...`
- **Read the recap from a file**, not by re-running digest: `loop-engine/reports/<project>/latest.md`.
- **Start with portfolio onboarding once:** `loop portfolio init`, then `loop portfolio add ...` using any handle the human has (local path, GitHub repo/URL, Linear project, URL, or plain name). `loop init` also upserts the current repo into the portfolio.
- **Run CTO catch-up for the portfolio:** `loop portfolio intake [project...]` writes `loop-engine/portfolio/<project>/profile.{md,json}` with end goal, current stage/progress, primary artifacts, verification candidates, readiness blockers, next steps, and task-level risk boundaries.
- **Start each day with PM review:** `loop morning` first shows Portfolio Registry Verification and Portfolio Readiness boards, then runs the PM Review Agent and writes `pm-reviews/YYYY-MM-DD.{md,json}`; `loop approve <project>` writes the project's `.loop/daily-focus/latest.md`.
- **Do not ignore non-ready high-value projects:** if a project is valuable but lacks `.loop/contract.yaml`, clean baseline, or verification commands, surface readiness work. `loop approve <project> --init-loop` is the explicit mutation path for approved loop bootstrap; after it runs, rerun `loop morning` before execution.
- **Approve medium risk once per day:** when morning recommends a bounded envelope, `loop approve <project> --approve-medium` approves all same-day medium-risk items that stay inside that envelope. Do not approve medium risk item-by-item inside each cycle.
- **Start approved projects only:** `loop start-day` reads `approvals/latest.json`; it refuses projects not approved today.
- **Trigger one cycle:** `loop run-now` (add `--supervised` only when a preapproved medium-risk envelope exists for the queued work).
- **End the day:** `loop evening` pauses all active registered loops when no project args are given, refreshes digests, and writes `evening-scorecards/YYYY-MM-DD.md`.
- **Lifecycle:** `loop pause` / `loop resume` / `loop stop`. `loop init` registers a new project (fails closed if prerequisites are missing).
- **Escalate, never bypass.** Anything in `waiting_for_human` is a human decision. Surface it; do not reword issues or prompts to force a pass.

## Reason codes → required human action

**`waiting_for_human[].reason`** — one per gated item; this is what to act on:

| reason | what happened | human action |
|---|---|---|
| `no_candidate_over_value_line` | nothing cleared `value_threshold` (when do-nothing is off) | lower the threshold, or accept the no-op |
| `blocked_category` | issue matched a blocked keyword category | review `runs/<id>/issues/*.md`; reword or preapprove; `loop resume` |
| `untrusted_verification` | a verification command isn't in the contract's trusted set | add to `.loop/contract.yaml` or verify manually; `loop resume` |
| `medium_risk_requires_approval` / `medium_risk_requires_supervised_run` | medium-risk item needs today's envelope + first supervised run | use `loop approve <project> --approve-medium` if morning recommended it, or approve a manual envelope; first run is supervised |
| `medium_envelope_violation` | medium task exceeded its envelope | tighten the issue or widen the envelope deliberately |
| `high_risk_requires_approval` | a high-risk candidate was surfaced | stays manual — never auto-run |
| `unsupported_risk` | issue risk field wasn't `low` or `medium` | fix the issue's `## Risk` |
| `task_gated` | worker/reviewer/merge raised an exception | inspect `task-error.txt` + logs before retrying |

**`runs[-1].control_gate.reason`** — why the loop *paused* (the gated items themselves, if any, are in `waiting_for_human`): `higher_value_item_requires_approval` (decide the high-value item; `loop resume`), `no_auto_executable_candidates` / `all_candidates_gated` (every candidate needs approval), `stop_condition_met` / `recommended_cycles_exhausted` (intended end of the day's budget).

> A failed auto-merge is **not** a reason code: the cycle ends `needs_human` with the task marked `pass` but no merged PR — inspect the run log / `cycle-summary.json` and resolve the conflict on the pilot branch.

`LOOP_BLOCKED` reasons from daily setup: `portfolio_missing` means first daily PM review or intake needs `loop portfolio init` + `loop portfolio add ...`; `portfolio_no_review_projects` means every portfolio entry has `default_review=false`. `LOOP_BLOCKED` reasons from `loop init` / approved init-loop (no mutation occurs): `not_git_repo`, `missing_github_remote`, `missing_github_auth`, `github_repo_not_found`, `unsupported_platform` (no sandbox-exec / non-macOS), `missing_linear_team` (only when Linear is explicitly enabled). Run `loop doctor` to preflight init prerequisites.

> Not an init reason: a `provider: claude` role whose Claude CLI is absent raises a `missing_claude_cli` **RuntimeError during cycle execution** (planner/worker/reviewer), not during `loop init`.

## Machine-readable capability contract

```yaml
name: loop
version: 1
capability: Value-ranked, auditable, pausable coding-agent loop for local Git projects
platform: macOS            # verification requires sandbox-exec; fails closed without it
coding_agent: codex | claude   # per-role provider field (default codex); both route through one agent_exec() seam
runtime: python>=3.11 (stdlib only)
agents_config: 'registry.json -> agents.{planner|worker|reviewer}.{model: ..., [provider: codex|claude] optional, defaults to codex if absent}; claude permission_mode validated, unsafe modes rejected (bypassPermissions raises); missing claude CLI -> missing_claude_cli RuntimeError at cycle time'
output_language: 'registry.json project field (or LOOP_OUTPUT_LANGUAGE env); default English. Localizes generated PROSE only; structural headers + machine tokens (REVIEW_STATUS, risk low/medium/high) STAY English by design because the engine parses them. Prose localization is best-effort by the agent.'

commands:
  portfolio: { in: "operator machine", out: "~/.config/loop/portfolio.json via init|add|status plus portfolio/<project>/profile.{md,json} via intake; source of truth for daily PM review" }
  setup:    { in: "operator machine", out: "~/.config/loop/config.json + missing-action prompts" }
  init:     { in: "product repo cwd", out: ".loop/contract.yaml + registry + pilot branch", fail: "LOOP_BLOCKED <reason>" }
  morning:  { in: "portfolio registry + registered project snapshots", out: "portfolio verification board + pm-reviews/YYYY-MM-DD.{md,json} + latest.{md,json}, PM-agent value-ranked portfolio board" }
  approve:  { in: "project [--approve-medium | --medium-envelope ... | --init-loop]", out: ".loop/daily-focus/latest.md + approvals/latest.json, or approved loop bootstrap for readiness work" }
  reject:   { in: "project", out: "approvals/latest.json rejection record" }
  start-day: { in: "today's approved projects", out: "approved loops active; medium first run supervised" }
  evening:  { in: "projects or all active/approved projects", out: "evening-scorecards/YYYY-MM-DD.md + reports/daily/YYYY-MM-DD.md/html" }
  status:   { in: "project", out: "human text or JSON (--json)", reads: "state.json" }
  run-now:  { in: "project [--supervised]", out: "one full cycle or no-op", fail: "waiting_for_human <reason>" }
  start:    { in: "initialized project", out: "scheduler loaded, job active, first cycle immediate" }
  pause:    { in: "project", out: "job paused, scheduler stays loaded" }
  resume:   { in: "project", out: "job active, next tick fires" }
  stop:     { in: "project", out: "job stopped, scheduler unloaded for that project" }
  digest:   { in: "project [--json]", out: "reports/<project>/latest.md + .html" }
  doctor:   { in: "project (optional)", out: "PASS/FAIL preflight: git, gh+auth, sandbox-exec, the provider CLI in use" }
  notify:   { in: "setup|test|status", out: "macOS/webhook notification config + logs/notifications.jsonl" }
  init --provider codex|claude: bootstrap an all-codex or all-claude project (default codex)

gates:                      # in cycle order
  - value_line: value_score >= value_threshold (default 3), else no-op
  - do_nothing: after max_noop_cycles (default 2) consecutive no-ops, auto-pause
  - risk_envelope: non-low/medium risk gated (unsupported_risk); medium needs same-day preapproved envelope and first supervised run; high never auto-runs; risk is task-level, not project-level
  - blocked_category: keyword scan of issue intent over 7 categories -> gate to human
  - untrusted_verification: issue verification commands must match the contract's trusted set
  - higher_value_blocker: a waiting higher-value item blocks lower-value work
  - max_tasks_per_cycle: only the top N (default 1) auto-run; the rest -> deferred-candidates.md
  - worker_internal: verification under sandbox-exec (network denied) + Allowed-Files allowlist + secret-leak scan (all fail-closed)
  - reviewer: REVIEW_STATUS must be exactly pass | fail | needs_human

auto_execute: { low: true, medium: "same-day morning envelope + first supervised execution", high: false }
auto_merge: true            # reviewer pass → gh pr merge --merge --delete-branch (no human gate)

risk_model:
  task_level: true
  trading_low: "read-only gate review, backtest/report interpretation, data-quality checks with no broker/live path"
  trading_medium: "offline or paper/demo strategy changes bounded by files and verification"
  trading_high: "broker credentials, live orders, real-money movement, live trading config flips, unattended trade-capable scheduler"

sandbox_scope:
  verification_commands: "sandbox-exec; network denied; secret dirs denied; scrubbed env"
  codex_agent:  "codex --sandbox workspace-write confines writes to worktree; reads broad; scrubbed env"
  claude_agent: "Claude Code headless sandbox confines reads/writes/Bash to worktree + --add-dir; unsafe permission_mode rejected/raises (no bypassPermissions); scrubbed env"

runtime_artifacts:          # git-ignored; never commit to a public repo
  state: loop-engine/state.json
  runs: loop-engine/runs/<run_id>/
  pm_review: loop-engine/pm-reviews/latest.md
  pm_review_plan: loop-engine/pm-reviews/latest.json
  portfolio: ~/.config/loop/portfolio.json
  portfolio_profiles: loop-engine/portfolio/<project>/profile.{md,json}
  approvals: loop-engine/approvals/latest.json
  evening_scorecard: loop-engine/evening-scorecards/latest.md
  digest: loop-engine/reports/<project>/latest.md
  memory: loop-engine/knowledge/<project>/STATE.md
  registry: loop-engine/registry.json
  notifications: loop-engine/logs/notifications.jsonl
```
