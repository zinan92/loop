# loop

`loop` turns a one-off coding-agent prompt into a value-ranked, auditable,
pausable execution loop across local Git projects.

It is a control plane for:

- daily product focus
- planner candidate generation
- low-risk worker execution
- supervised medium-risk execution
- reviewer validation
- GitHub issue/PR bookkeeping
- Linear milestone/status sync
- human digest and approval queues

The project is intentionally conservative: it should create product value
without turning into autonomous busywork.

## What It Does

```text
input  clean local Git repo + GitHub origin + loop contract + optional daily focus
output Linear milestone + GitHub issues/PRs + run logs + digest + bounded memory

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
- Linear GraphQL API
- macOS `sandbox-exec` for verification isolation

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
be used, but Linear milestones/comments will be skipped or blocked depending on
project configuration.

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

No open-source license has been selected yet. Until a license is added, this
repository is source-available only.
