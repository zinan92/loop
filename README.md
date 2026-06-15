<div align="center">

# loop

**把一次性的 coding-agent prompt，变成跨本地 Git 项目的「按价值排序、可审计、可暂停」的自进化执行闭环**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-70%20passing-brightgreen.svg)](loop-engine/tests)

</div>

---

`loop` turns a one-off coding-agent prompt into a value-ranked, auditable,
pausable execution loop across local Git projects.

It is a control plane for:

- daily product focus
- planner candidate generation
- low-risk worker execution
- supervised medium-risk execution
- reviewer validation
- GitHub issue/PR bookkeeping
- optional Linear milestone/status sync
- human digest and approval queues

The project is intentionally conservative: it should create product value
without turning into autonomous busywork.

## What It Does

```text
input  clean local Git repo + GitHub origin + loop contract + optional daily focus
output GitHub issues/PRs + run logs + digest + bounded memory + optional Linear milestone

gate   missing init/auth/remote        -> LOOP_BLOCKED
gate   no candidate above value line   -> no-op, no worker run
gate   unsafe task or verification     -> waiting_for_human
gate   higher-value approval item      -> pause before lower-value work
```

Core adapters:

- Python 3.11+ standard library
- Git
- GitHub CLI (`gh`)
- Codex CLI
- Linear GraphQL API, optional
- macOS `sandbox-exec` for verification isolation

## 示例输出

`loop status` —— 回来后一眼看清当前状态：

```text
STATE paused
LAST_RUN <project>-20260615-155853
NEXT_CYCLE -
WAITING_FOR_HUMAN 0
GITHUB <owner>/<project>
LINEAR_PROJECT <project>
DIGEST loop-engine/reports/<project>/latest.md
SCHEDULER installed=False loaded=False
```

一轮 cycle 跑完（价值门 + 验证门都通过 → 合并 PR）：

```text
RUN_COMPLETE <project>-20260615-105730 merged tasks=1 waiting_for_human=0
```

价值门拦截（没有候选过价值线 → 不硬找事做）：

```text
RUN_COMPLETE <project>-20260615-163000 no_op tasks=0 waiting_for_human=0
```

`loop digest` 写出 `loop-engine/reports/<project>/latest.md`，回答"我离开时 loop 做了什么"：
完成的工作 + PR + product before/after + 等待审批队列。

## PM Skills Are Optional

`loop` does not import or depend on a PM skill package.

A PM workflow can be useful upstream because it can write durable planning
artifacts such as:

- `<product>/.loop/daily-focus/latest.md`
- `loop-engine/pm-reviews/latest.md`
- `loop-engine/human-feedback/<project>/elicitation-answers.md`
- `loop-engine/evening-scorecards/latest.md`

Those files are plain Markdown inputs. You can generate them with a PM skill,
write them manually, or omit them for a simpler run.

## Quick Start

```bash
git clone https://github.com/zinan92/loop.git
cd loop

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest loop-engine/tests -v
```

Install the local wrapper:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/loop-engine/bin/loop" ~/.local/bin/loop
```

Initialize a product repo:

```bash
cd /path/to/product-repo
loop init
loop status
```

Run one immediate cycle:

```bash
loop run-now
```

Start the hourly loop:

```bash
loop start
loop status
loop digest
loop stop
```

The default cadence is fixed: one cycle per hour, forever, until `loop stop`.

## Project Requirements

For v1, a target project must be:

- a clean local Git repo
- connected to an existing GitHub `origin`
- usable with authenticated `gh`
- initialized with `.loop/contract.yaml`
- optionally connected to Linear through `LINEAR_API_KEY`

The engine does not create GitHub repos automatically.
If no Linear API key is configured, `loop init` still succeeds and disables
Linear sync for that project; GitHub issues/PRs, local run logs, digests, and
approval queues remain available.

## Configuration

See:

- [examples/registry.example.json](examples/registry.example.json)
- [examples/contract.example.yaml](examples/contract.example.yaml)
- [examples/daily-focus.example.md](examples/daily-focus.example.md)

Useful environment variables:

| Variable | Purpose |
|---|---|
| `LINEAR_API_KEY` | Linear API key for milestone/status sync |
| `LOOP_LINEAR_API_KEY_FILE` | File fallback for the Linear API key |
| `LOOP_LINEAR_TEAM_KEY` | Linear team key, for example `ENG` |
| `LOOP_LINEAR_TEAM_NAME` | Human-readable Linear team name |

If Linear is disabled or unavailable, GitHub and local digest behavior can still
be used, and Linear milestones/comments are skipped for that project.

## Risk Model

Low-risk work may run unattended after value and verification gates pass:

- tests
- docs
- CLI/status/digest wording
- deterministic parsing or validation
- small observability/reporting changes
- small internal refactors with unchanged behavior

Medium-risk work needs a daily preapproved envelope and should be supervised for
the first execution:

- visible local UI/widget changes
- small product-surface behavior changes
- payload/schema changes with tests
- single-module non-trivial refactors

High-risk work stays manual:

- credentials, secrets, `.env`, auth tokens
- external auth/login/OAuth probing
- launchd, cron, or scheduler installation
- publishing, deployment, or external posting
- destructive file/data operations
- trading, payment, broker, or money movement
- broad architecture rewrites
- cross-project permission expansion

## Value Line

The loop is value-first:

- daily focus ranks work by expected product value
- planner emits candidates with value scores
- engine processes candidates by descending value score
- by default, only the top auto-runnable task executes per cycle to avoid
  same-cycle PR conflicts
- a high-value approval item blocks lower-value busywork
- no valuable candidate means no-op, not forced work

This is the core rule: do not spend agent time on low-value tasks just because
they are easy to automate.

## Common Commands

```bash
loop init
loop start
loop status
loop digest
loop pause
loop resume
loop stop
loop run-now
loop run-now --supervised
```

Explicit project form:

```bash
python3 loop-engine/bin/loopctl.py status --project example-product
python3 loop-engine/bin/loopctl.py run-now --project example-product
python3 loop-engine/bin/loopctl.py scheduler status --project example-product
```

## Runtime Artifacts

Runtime artifacts are intentionally ignored by Git:

- `loop-engine/runs/`
- `loop-engine/reports/`
- `loop-engine/registry.json`
- `loop-engine/state.json`
- `loop-engine/pm-reviews/`
- `loop-engine/human-feedback/`
- `loop-engine/knowledge/`
- `loop-engine/logs/`
- `loop-engine/locks/`
- `loop-engine/worktrees/`

These files may contain local paths, private project strategy, Linear/GitHub
metadata, and agent transcripts. Keep them out of public repos.

## Repository Layout

```text
loop/
├── README.md
├── examples/
├── loop-engine/
│   ├── bin/
│   ├── prompts/
│   ├── scheduler/
│   └── tests/
└── .gitignore
```

## License

[Apache-2.0](LICENSE). 你可以自由使用、修改、商用本项目，需保留版权与许可声明；
许可证含明确的专利授权。
