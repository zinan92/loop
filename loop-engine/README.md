# Agent Loop Engine

The engine runs one project through this loop:

```text
planner -> value/risk gates -> worker -> verification -> reviewer -> PR/digest
```

It is project-agnostic. Each product repo owns its `.loop/contract.yaml`; this
engine owns global state, locks, logs, prompts, scheduler glue, and reports.

## Cross-Project Command

From a product repo:

```bash
loop init
loop start
loop status
loop digest
loop pause
loop resume
loop stop
```

`loop init` resolves the current repo, creates `.loop/contract.yaml` if needed,
registers the project in `loop-engine/registry.json`, and creates or reuses
Linear records when Linear sync is enabled.

`loop start` only runs on an initialized project. It loads the user-space
scheduler daemon, marks the job active, and runs the first cycle immediately.

`loop stop` stops the job and unloads the daemon for that project. It does not
install launchd, cron, or login autostart assets.

## Engine CLI

```bash
python3 loop-engine/bin/loopctl.py init
python3 loop-engine/bin/loopctl.py start
python3 loop-engine/bin/loopctl.py status
python3 loop-engine/bin/loopctl.py digest
python3 loop-engine/bin/loopctl.py run-now
python3 loop-engine/bin/loopctl.py run-now --supervised
python3 loop-engine/bin/loopctl.py stop
```

Explicit project form:

```bash
python3 loop-engine/bin/loopctl.py status --project example-product
python3 loop-engine/bin/loopctl.py run-now --project example-product
python3 loop-engine/bin/loopctl.py scheduler status --project example-product
```

The product default is fixed: one cycle per hour, forever, until stopped.
Frequency and duration are intentionally not configurable in v1.

## Scheduler

Scheduler ticks are separate from job state:

```bash
python3 loop-engine/bin/loopctl.py scheduler load --project example-product
python3 loop-engine/bin/loopctl.py scheduler status --project example-product
python3 loop-engine/bin/loopctl.py scheduler uninstall --project example-product
```

`scheduler load` is the explicit ON switch. It starts a current-user daemon that
wakes periodically and only runs a cycle when the project job is active and due.

`scheduler uninstall` is the OFF switch. `scheduler install` is intentionally a
no-op compatibility command.

## Verification Isolation

Issue verification commands are not trusted by default. They must match the
project contract's trusted command set exactly.

Trusted verification commands are run with:

- `sandbox-exec`
- network denied
- secret directories denied
- scrubbed environment variables
- project worktree readable

Codex agent subprocesses receive a scrubbed environment as well. The engine
process itself can still read integration credentials needed for Linear/GitHub
operations.

## Human Digest

```bash
loop digest example-product
```

The digest prints recent runs, merged PRs, failures, and approval queues. It
also writes ignored runtime files under:

- `loop-engine/reports/<project>/latest.md`
- `loop-engine/reports/<project>/latest.html`

The digest is a review surface, not an approval mechanism.

## Feedback-Aware Memory

Each completed cycle writes ignored runtime files:

- `loop-engine/runs/<run_id>/cycle-summary.json`
- `loop-engine/runs/<run_id>/cycle-summary.md`
- `loop-engine/knowledge/<project>/events.jsonl`
- `loop-engine/knowledge/<project>/STATE.md`

`STATE.md` is generated from structured fields only and is fed back into
planner, worker, and reviewer prompts as bounded memory. It must not include
raw issue titles, raw errors, raw shell commands, reviewer prose, worker prose,
or candidate descriptions.

This is feedback-aware memory, not proof of autonomous self-improvement. An eval
layer is required before claiming later cycles are objectively better.

## Bootstrap Requirements

New projects require:

- clean Git worktree
- existing GitHub `origin` remote
- authenticated `gh`
- Linear API key when Linear sync is enabled
- configured Linear team key when Linear sync is enabled

The engine does not create GitHub repositories. Missing requirements fail closed
with `LOOP_BLOCKED` and no unsafe project mutation.

## Linear Configuration

Environment variables:

```bash
export LINEAR_API_KEY=...
export LOOP_LINEAR_TEAM_KEY=ENG
export LOOP_LINEAR_TEAM_NAME="Engineering"
```

Optional file fallback:

```bash
export LOOP_LINEAR_API_KEY_FILE="$HOME/.config/loop/linear-api-key"
```

Check the connection without printing the key:

```bash
python3 loop-engine/bin/loopctl.py linear-check --project example-product
```

## Rollback

Because product changes happen on Git branches and PRs, rollback should use the
target product repo's normal Git workflow: revert the PR, reset the loop pilot
branch, or stop the job with `loop stop`.
