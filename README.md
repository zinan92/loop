<div align="center">

# loop

**把一次性的 coding-agent prompt，变成跨本地 Git 项目的「按价值排序、可审计、可暂停」的持续改进闭环**

**Turn a one-off coding-agent prompt into a value-ranked, auditable, pausable improvement loop for your local Git projects.**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![CI](https://github.com/zinan92/loop/actions/workflows/ci.yml/badge.svg)](https://github.com/zinan92/loop/actions/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#compatibility--兼容性)
[![Coding agent](https://img.shields.io/badge/coding%20agent-Codex%20%2B%20Claude-black.svg)](#compatibility--兼容性)

[📊 Visual flow / 可视化流程图](https://zinan92.github.io/loop/flow.html) · [🤖 For AI agents / 给 AI agent](AGENTS.md)

</div>

---

```
in   clean local Git repo + GitHub origin + .loop/contract.yaml + daily PM focus
out  GitHub issues/PRs + run logs + human digest + evening scorecard + bounded memory + (optional) Linear milestones/notifications

fail  not initialized / no gh auth / no GitHub remote  → LOOP_BLOCKED, no mutation
fail  no candidate clears the value line               → no-op cycle, no worker runs
fail  unsafe task or untrusted verification            → waiting_for_human, gated
fail  a higher-value item needs approval               → pause before doing lower-value work
```

> **中文一句话：** `loop` 是一个**控制平面**——它驱动一个 coding agent（Codex 或 Claude Code，可按角色配）在你的项目上**自己找活、按价值排序、跑通验证、开 PR、合并、写复盘**，每一步都有闸门和审计，随时可暂停。它的设计偏保守：**宁可这一轮什么都不做，也不硬找低价值的活**。
>
> **In one line:** `loop` is a **control plane** that drives a coding agent (Codex or Claude Code, configurable per role) to find work on your repo, rank it by value, pass verification, open and merge a PR, and write a recap — every step gated and auditable, pausable at any time. It is deliberately conservative: a cycle may correctly do **nothing** rather than invent low-value busywork.

---

## ⚠️ Before you run it — Safety & Permissions / 运行前必读

> **这一节是给"要把 loop 跑起来的人"和"替人操作 loop 的 agent"看的。先懂这 5 条，再 `loop start`。**
> Read these 5 facts before `loop start`. An agent operating `loop` on a human's behalf **must relay them first**.

1. **引擎进程以你的完整用户权限运行 / The engine runs as your full OS user.**
   `loopctl.py` reads your real secret files (default `~/.config/loop/secrets`, plus `~/.ssh`, `~/.aws`, …) — by design, so it can scan the worker's diff and **block any change that leaks a known secret value**. It needs to know your secrets in order to catch leaks.

2. **每个 agent 都被关在 worktree 里;验证命令再额外走 sandbox-exec / Each agent is confined to the worktree; verification adds sandbox-exec.**
   Verification commands run under `sandbox-exec` with **network denied** and secret dirs denied. The planner/worker/reviewer get a **scrubbed environment** (no secret env vars). File access is confined per provider: **Codex** limits *writes* to the worktree via `--sandbox workspace-write` (its reads stay broad); **Claude Code** limits reads, writes, **and** Bash to the worktree + run dir via its own headless sandbox — and the engine **rejects** unsafe permission modes for Claude (`bypassPermissions` raises an error) so config can't disable it. Either way, the post-worker allowlist + secret-leak scan (provider-agnostic) catch anything out of scope before merge.

3. **reviewer 通过 = 自动合并，中间没有人工复核 / Reviewer pass = auto-merge, no human gate.**
   When the reviewer returns `REVIEW_STATUS: pass`, the engine **autonomously** opens a PR and runs `gh pr merge --merge --delete-branch` into the pilot branch. (The GitHub issue itself is created earlier, at the start of the worker phase.) There is no separate human approval step between "reviewer pass" and "merged".

4. **`loop start` 是每小时一轮、跑到你喊停 / `loop start` runs hourly, forever.**
   The cadence is fixed at one cycle per hour until `loop stop`. Each passing cycle can create **and merge** a PR. Use `loop pause` / `loop stop` to halt.

5. **scheduler 是用户态守护进程，不是 launchd/cron / The scheduler is a user-space daemon.**
   It does **not** install launchd, cron, or login autostart, and does **not** survive a reboot. `loop stop` (or `scheduler uninstall`) turns it off.

---

## Compatibility / 兼容性

> **诚实地说清楚现在能跑在哪、不能跑在哪。架构是 agent-无关的，但 v1 的实现是单 adapter。**
> Honest scope. The architecture is agent-agnostic; the v1 implementation ships a single adapter.

| 轴 / Axis | v1 现状 / Status | 说明 / Notes |
|---|---|---|
| **Coding agent** | **Codex CLI + Claude Code** | Set per role via a `provider` field (`codex` default, or `claude`); all roles route through one `agent_exec()` seam, and the candidate/issue/report I/O is file-based and provider-agnostic. **Codex** confines writes with its own `--sandbox workspace-write`; **Claude Code** is confined by its own headless sandbox (reads/writes/Bash limited to the worktree + `--add-dir`). The engine **rejects** unsafe permission modes (`bypassPermissions` raises an error, asserted by unit tests) so config can't open that cage — but the runtime sandbox enforcement is Claude Code's own, not the engine's. |
| **OS** | **macOS only** | Verification commands require `sandbox-exec` and **fail closed with a `RuntimeError`** without it — so a cycle cannot complete off-macOS. (Planner/worker/reviewer themselves don't use it, but no change can pass verification.) Linux/Windows would need an equivalent sandbox; not in v1. |
| **Python** | **3.11+, standard library only** | No `pip install` for the engine itself. `pytest` is only needed to run the test suite. |
| **Linear** | **Optional** | With no API key, `loop init` still succeeds and sets `linear_sync.enabled = false`. GitHub issues/PRs, run logs, digest, and approval queues all work without Linear. |

> 一句话：**agent = Codex 或 Claude Code(按角色配),OS = 仅 macOS**。别的 OS 接缝留着但还没接上。以这张表为准,别信"任何 agent / 任何系统都能用"的笼统说法。
> TL;DR: **agents = Codex or Claude Code (per role); OS = macOS only.** Other OSes aren't wired yet. Trust this table, not aspirational claims.

---

## Prerequisites / 前置依赖

| 需要 / Requirement | 检查命令 / Verify |
|---|---|
| macOS with `sandbox-exec` | `which sandbox-exec` |
| Python 3.11+ | `python3 --version` |
| Git | `git --version` |
| GitHub CLI, **authenticated** | `gh auth status` |
| A coding-agent CLI — Codex (`codex exec`) and/or Claude Code (`claude --print`) | `codex --version` / `claude --version` |
| (optional) Linear API key | set `LINEAR_API_KEY` or `LOOP_LINEAR_API_KEY_FILE` |

If a prerequisite is missing, `loop init` **fails closed** with a `LOOP_BLOCKED` reason (`missing_github_auth`, `missing_github_remote`, `not_git_repo`, `github_repo_not_found`, `unsupported_platform`) **before any mutation**. Tip: `loop doctor` checks everything at once — including the agent CLI your registry actually requires.

---

## Quick start / 快速开始

```bash
# 1) Clone + run the test suite (expect: all tests pass)
git clone https://github.com/zinan92/loop.git
cd loop
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest loop-engine/tests -q

# 2) Put the wrapper on your PATH
mkdir -p ~/.local/bin
ln -sf "$(pwd)/loop-engine/bin/loop" ~/.local/bin/loop
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.zshrc to persist

# 3) First-run setup + pre-flight
loop setup --yes                  # writes ~/.config/loop/config.json and prints missing actions
loop doctor                       # checks gh / sandbox-exec / the agent CLI your registry needs

# 4) Initialize a target product repo (run from INSIDE it)
cd /path/to/your-product-repo        # must be a clean git repo with a GitHub origin
loop init --provider codex           # or: --provider claude
                                     # creates .loop/contract.yaml, a baseline tag,
                                     # a loop/<project>-pilot branch, and registry/state
loop status

# 5) Morning PM review across registered projects
loop morning                         # PM-agent review; writes pm-reviews/YYYY-MM-DD.{md,json}
loop approve <project>               # approve low-risk automatic work only
# To approve every medium-risk item today inside the PM-recommended envelope:
loop approve <project> --approve-medium
# Or override the envelope manually:
loop approve <project> --medium-envelope primary-surface \
  --allowed-file 'src/**' --allowed-file 'tests/**' \
  --verification-command 'git diff --check'

# 6) Start the approved day loop
loop start-day                       # first cycle now, then hourly until stop/pause/budget

# 7) Evening recap
loop evening                         # pauses all active registered loops, writes scorecards + daily report
```

To run continuously: `loop start` (hourly, immediate first cycle) → `loop status` / `loop digest` → `loop pause` / `loop stop`.

---

## How a cycle works / 一轮 cycle 做什么

> 完整可视化见 **[docs/flow.html](https://zinan92.github.io/loop/flow.html)**（给人看的图）。下面是文字版。
> Full visual in **[docs/flow.html](https://zinan92.github.io/loop/flow.html)**. Text version:

```text
trigger (run-now | hourly tick)
  └─ planner    reads daily-focus + PM review + scorecard + memory → candidates.json (+ issue files), value-scored
       └─ gates value line (≥ threshold) · do-nothing · blocked-category · risk envelope · higher-value-blocker
            └─ worker    opens the GitHub issue, then edits code in an isolated worktree (only Allowed Files)
                 └─ verify  trusted commands under sandbox-exec (network denied, secrets denied)
                      └─ allowlist + secret-leak scan (fail-closed)
                           └─ reviewer  must emit REVIEW_STATUS: pass | fail | needs_human
                                └─ on pass: open PR + merge --delete-branch  (auto)
                                     └─ Linear milestone (if enabled) → cycle-summary → digest → memory
```

By default **only the top-ranked auto-runnable task executes per cycle** (`max_tasks_per_cycle = 1`) to avoid same-cycle PR conflicts; the rest are written to `deferred-candidates.md`.

---

## Daily rhythm: morning & evening / 每日节奏

`loop` now owns the full daily routine: **morning review → approvals → day loop → evening recap**. The loop still keeps the human in charge of direction: morning review proposes and ranks work; only `loop approve` turns it into execution input.

**Morning — decide value, then approve:**
1. `loop morning [project...]` runs the built-in PM Review Agent over registered project snapshots, reads current loop state/digests/evening scorecards, ranks value-first work, assigns low/medium/high risk, and writes:
   - `loop-engine/pm-reviews/YYYY-MM-DD.md`
   - `loop-engine/pm-reviews/latest.md`
   - `loop-engine/pm-reviews/YYYY-MM-DD.json`
   - `loop-engine/pm-reviews/latest.json`
2. `loop approve <project>` writes that project's approved focus to:
   - `<product-repo>/.loop/daily-focus/YYYY-MM-DD.md`
   - `<product-repo>/.loop/daily-focus/latest.md`
   - `loop-engine/approvals/YYYY-MM-DD.{json,md}`
3. Medium-risk work is approved once in the morning, for the whole day, inside a bounded envelope:
   - recommended: `loop approve <project> --approve-medium`
   - `loop approve <project> --medium-envelope <name> --allowed-file ... --verification-command ...`

**Day — execute only approved work:**
1. `loop start-day [project...]` starts approved projects only.
2. Low-risk work may run unattended after value/verification gates.
3. Medium-risk work may run unattended only when it matches the morning-approved envelope. The first medium-risk execution runs with `supervised=true`, then continues hourly only if that first cycle does not leave `waiting_for_human`.
4. Budgets and stop rules come from daily focus: `recommended_cycles`, `stop_condition`, `value_threshold`, `max_noop_cycles`.

**Evening — stop, recap, score:**
1. `loop evening [project...]` pauses the named projects. With no project args, it pauses **all active registered loops**, even loops that were started manually outside the approval flow.
2. It refreshes project digests and writes:
   - `loop-engine/evening-scorecards/YYYY-MM-DD.md`
   - `loop-engine/evening-scorecards/latest.md`
   - `loop-engine/reports/daily/YYYY-MM-DD.{md,html}`
3. The next planner prompt reads the latest scorecard through the human-feedback context. If you use an external PM skill before `loop approve`, that PM layer should also read it.

### PM skill integration / PM skill 集成

No external PM skill is required: `loop morning` has a built-in PM Review Agent and is self-contained. However, installing a full PM skill package is **strongly recommended** for operators who want deeper discovery, user research, opportunity mapping, or roadmap synthesis before approval. External PM skills can enhance the same handoff artifacts that `loop` understands:

- `loop-engine/pm-reviews/latest.md`
- `loop-engine/pm-reviews/latest.json`
- `<product-repo>/.loop/daily-focus/latest.md`
- `loop-engine/approvals/latest.json`

In short: PM skills are strongly recommended power-ups, not runtime dependencies.

---

## Risk model / 风险模型

- **Low-risk** (tests, docs, CLI/digest wording, deterministic parsing, small observability/refactors) → may run unattended **after** value + verification gates pass.
- **Medium-risk** (visible local UI, small product-surface behavior, payload/schema changes with tests, single-module refactors) → can be approved once each morning with `loop approve <project> --approve-medium`; every task must stay inside that day's code-enforced envelope, and the first execution is supervised.
- **High-risk** (credentials/secrets/`.env`, external auth, launchd/cron, publishing/deploy, destructive ops, money/trading, broad rewrites, cross-project permission expansion) → **stays manual, never auto-executed.**

## Value line / 价值线

价值优先，不为了"能自动"就做没价值的活。Value-first: never spend agent time on low-value work just because it is easy to automate.

- Planner emits candidates with a `value_score` (1–5); the engine processes them by **descending value**.
- A candidate below `value_threshold` (default 3) → **no-op cycle**, the worker does not run.
- A high-value item that needs approval **blocks** lower-value busywork until you decide.

---

## For AI agents / 给 AI agent

> 大多数人会让**自己的 coding agent 读这个仓**，然后由 agent 来操作 loop、并向人解释它在干什么。
> Most adopters point their **own** coding agent at this repo and let it operate `loop` and explain it to the human.
> **The canonical agent-operation contract is [AGENTS.md](AGENTS.md)** — machine-readable commands, state fields, `waiting_for_human` reason codes + the human action each one needs, and the safety facts to relay. Read it first.

Quick orientation for an operating agent:

- **Read state, don't poll commands:** `loop status --json`, or read `loop-engine/state.json` directly. Key fields: `loop_job.state` (`active|paused|stopped`), `current_phase`, `waiting_for_human` (`{reason, issue_path}` or null), `runs[-1].status`.
- **Recap is a file:** read `loop-engine/reports/<project>/latest.md`; don't call `loop digest` in a loop.
- **Escalate, don't bypass:** anything in `waiting_for_human` needs a human decision (see the reason-code table in [AGENTS.md](AGENTS.md) and [Troubleshooting](#troubleshooting--排错)). Never edit prompts/reviewer output to force a pass.

---

## Configuration / 配置

- [examples/registry.example.json](examples/registry.example.json) — per-project config (auto-written by `loop init`)
- [examples/contract.example.yaml](examples/contract.example.yaml) — the `.loop/contract.yaml` schema
- [examples/daily-focus.example.md](examples/daily-focus.example.md) — daily focus + preapproved envelope

| Env var | Purpose |
|---|---|
| `LOOP_CONFIG_DIR` | First-run config directory; default `~/.config/loop` |
| `LINEAR_API_KEY` | Linear API key for milestone/status sync |
| `LOOP_LINEAR_API_KEY_FILE` | File fallback (default `~/.config/loop/linear-api-key`) |
| `LOOP_LINEAR_TEAM_KEY` | Linear team key, e.g. `ENG` |
| `LOOP_LINEAR_TEAM_NAME` | Human-readable Linear team name |
| `LOOP_NOTIFY_MODE` | `none`, `macos`, or `webhook` |
| `LOOP_NOTIFY_WEBHOOK_URL` / `LOOP_NOTIFY_WEBHOOK_URL_FILE` | Webhook notification target |

### Notifications / 通知

Notifications are opt-in. Configure them with:

```bash
loop notify setup --notify-mode macos
loop notify test
loop notify status
```

The engine notifies only high-signal events by default: `loop_started`, `needs_human`, merged work, and evening recap completion. Every notification attempt is also recorded in `loop-engine/logs/notifications.jsonl`.

### Output language / 输出语言

By default the loop generates artifacts in **English**. Set `output_language` on a project
(in `registry.json`, e.g. `"output_language": "Simplified Chinese"`) — or the
`LOOP_OUTPUT_LANGUAGE` env var — and the engine asks the agent to write generated **prose**
(issues, candidates, strategy brief, elicitation questions, worker/reviewer reports) in that
language.

**By design, structural Markdown headers (`## Risk`, `## Allowed Files`, …) and machine tokens
(`REVIEW_STATUS`, the `low`/`medium`/`high` risk values) stay in English** — the engine parses
them, and localizing them would break the safety gates. A non-English run therefore produces
localized prose inside an English structural skeleton. Prose localization is best-effort by the
coding agent; the engine guarantees the structure and parsing, not translation quality.

### Per-role agent provider / 按角色配 agent

Each role can run on a different provider via the `agents` block in `registry.json`:

```json
"agents": {
  "planner":  {"provider": "codex",  "model": "gpt-5.5"},
  "worker":   {"provider": "claude", "model": "sonnet"},
  "reviewer": {"provider": "codex",  "model": "gpt-5.5"}
}
```

`provider` defaults to `codex`, so existing registries keep working unchanged — in fact `loop init` writes the `agents` block **without** a `provider` key, and the `codex` default applies; add `provider` only to override a role to `claude`. For `claude`, the engine rejects unsafe permission modes (`bypassPermissions` raises an error); a missing Claude CLI raises `missing_claude_cli` at cycle time. A common safe split is **Claude worker + Codex reviewer** (let Claude write, keep an independent reviewer gating). Bootstrap an all-Claude project with `loop init --provider claude` (or `LOOP_DEFAULT_PROVIDER=claude`): it sets `provider: claude` for every role and **omits the model** so the Claude CLI uses its own current default. Codex roles keep `model: gpt-5.5` — set a model id your account can access if needed.

---

## Troubleshooting / 排错

When a cycle ends `needs_human`, look at two fields in `state.json`: **`waiting_for_human[].reason`** (per-item, what to act on) and **`runs[-1].control_gate.reason`** (why the loop paused).

Per-item `waiting_for_human[].reason`:

| reason | 含义 / Meaning | 你要做的 / Action |
|---|---|---|
| `no_candidate_over_value_line` | nothing cleared the value threshold (when do-nothing is disabled) | lower `value_threshold` in daily-focus, or accept the no-op |
| `blocked_category` | issue text matched a blocked keyword category | review the issue file under `runs/<id>/issues/`; reword or preapprove → `loop resume` |
| `untrusted_verification` | a verification command isn't in the contract's trusted set | add it to `.loop/contract.yaml`, or run it manually → `loop resume` |
| `medium_risk_requires_approval` / `medium_risk_requires_supervised_run` | a medium-risk item needs today's envelope + first supervised run | run `loop approve <project> --approve-medium` if morning recommended it, or approve a manual envelope; first run uses `--supervised` |
| `medium_envelope_violation` | the medium-risk task exceeded its envelope (files/commands) | tighten the issue or widen the envelope deliberately |
| `high_risk_requires_approval` | a high-risk candidate was surfaced | stays manual — never auto-run |
| `unsupported_risk` | the issue's risk field wasn't `low` or `medium` | fix the issue's `## Risk` |
| `task_gated` | worker/reviewer/merge raised an exception | inspect `task-error.txt` + logs before retrying |

Control-plane `control_gate.reason` (why the loop paused — the gated items, if any, are in `waiting_for_human`): `higher_value_item_requires_approval` (decide the high-value item → `loop resume`), `no_auto_executable_candidates` / `all_candidates_gated` (every candidate needs approval), `stop_condition_met` / `recommended_cycles_exhausted` (intended end of today's budget).

> A failed auto-merge is **not** a reason code: the cycle ends `needs_human` with the task marked `pass` but no merged PR — check the run log / `cycle-summary.json` and resolve the conflict on the pilot branch.

Day-start `LOOP_BLOCKED` reasons: `no_daily_approvals` means run `loop morning` then `loop approve <project>`; `not_approved_today` means the named project was not approved in today's approval artifact.

---

## Runtime artifacts / 运行产物

Git-ignored by design — they hold local paths, private strategy, and agent transcripts. **Keep them out of public repos:** `loop-engine/runs/`, `reports/`, `registry.json`, `state.json`, `pm-reviews/`, `approvals/`, `evening-scorecards/`, `human-feedback/`, `knowledge/`, `logs/`, `locks/`, `worktrees/`.

## Repository layout / 仓库结构

```text
loop/
├── README.md          ← you are here (bilingual, both audiences)
├── AGENTS.md          ← agent-operation contract (markdown, for agents)
├── docs/flow.html     ← visual flow (for humans)
├── examples/          ← registry / contract / daily-focus templates
├── loop-engine/
│   ├── bin/           ← loopctl.py (engine) + loop (wrapper)
│   ├── prompts/       ← planner / worker / reviewer prompts (agent-agnostic)
│   ├── scheduler/
│   └── tests/         ← test suite (CI-checked)
└── .gitignore
```

## License

[Apache-2.0](LICENSE). 自由使用、修改、商用，保留版权与许可声明；含明确专利授权。
Free to use, modify, and commercialize; keep the notices; includes an explicit patent grant.
