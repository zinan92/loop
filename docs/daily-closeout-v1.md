# Daily Closeout v1

This contract owns the 04:10 Agent OS daily closeout.

## Purpose

Close the daily loop by connecting yesterday's draft to today's evidence and tomorrow's draft. The deterministic closeout is mechanical and evidence-first; it does not make Park's strategic choice.

## Schedule

- 03:00: `Know-How Sync v1`
- 04:10: `Daily Closeout v1`
- Weekly retrospective: manual for v1
- LLM summary review: after the deterministic script writes evidence

## Outputs

- Project updates: `/Users/wendy/park-io/008_codex session insights and decision logs/<Project>/daily-update.md`
- Global cumulative closeout: `/Users/wendy/park-io/008_codex session insights and decision logs/_daily-closeout.md`
- Closeout state: `/Users/wendy/park-io/008_codex session insights and decision logs/_daily-closeout-state.json`

## Pinned Projects

Only these projects are in scope until Park explicitly adds more:

- `交易系统`
- `Park 的内容生产`
- `Park OS`
- `Agent OS`
- `ai newsletter`

## Required Sections

Every daily closeout entry must include the CEO/PM summary first, followed by the eight evidence sections. Empty sections must say `SKIPPED（原因）`.

0. CEO/PM 摘要
   - one paragraph or 3-5 bullets
   - answer: from a product perspective, what new user value exists today?
   - avoid raw logs, command lists, and thread-title dumps
   - mention only the evidence that matters to a CEO or PM

1. 北极星对照
2. 昨日 to-do 核对
3. 今日产出台账
4. 进度闸门
5. 异常与风险
6. 用户视角的今日成果
7. 明日 to-do（草案）
8. 内容候选

## CEO/PM Summary Standard

The closeout is not complete if the first reader-facing section is only a log table.

The summary must answer these questions:

- What did this project or portfolio become able to do today that it could not do yesterday?
- Which user, operator, or buyer benefits from that change?
- Is the work product-facing, internal infrastructure, or only maintenance?
- What is the single most important next product move?

If the answer is "no user-visible change", say that directly and explain what internal asset was improved.

## Code Closeout

Daily Closeout v1 must inspect git state and record evidence. The deterministic script does not commit or push by itself.

A human or runtime agent may commit and push only when it can verify all of these:

- the repo is on an intended branch
- all staged files are known safe text/source/docs/config files
- no blacklisted file is included
- secret scan is clean
- the logical commit unit is clear

If any condition is not true, do not commit. Record `BLOCKED` or `SKIPPED` with reason and age, then continue the report.

Blacklisted examples:

- secret files, tokens, private keys, `.env`, live broker config, Tiger properties
- runtime logs and receipts
- screenshots, archives, zip/tgz files, large generated artifacts
- unknown untracked directories

## README / Docs Closeout

Update README only when today's work changes an external behavior:

- new command
- new API
- install or run instructions
- user-facing workflow
- automation contract

Otherwise report `SKIPPED（今日无对外行为变化）`.

## Website Closeout

Website updates are event-driven, not daily quotas.

Update or prepare website material only when there is a release event, usable product surface, or clear public milestone. Otherwise report `SKIPPED（今日无可展示发布事件）`.

## Know-How Closeout

Reference the 03:00 know-how sync result. Do not run a seven-day retrospective in v1. If there is no stable reusable decision today, report `SKIPPED（今日无稳定可复用决策）`.

## Blocked / SKIPPED Age

Track recurring actionable `BLOCKED` and actionable `SKIPPED` items by stable key.

- day 1: record only
- day 2: keep visible
- day 3+: escalate in the closeout summary

Benign event-driven skips such as "no website release today" do not need escalation.

## Tomorrow To-Do Draft

Always produce a draft:

- one main attack
- at most three auxiliary items
- label it as draft pending Park confirmation

The draft should be based on unfinished items, current blockers, and today's evidence. It is not the final strategic decision.

## Gate Source

Use `/Users/wendy/Documents/agent自管理/docs/project-gates-v1.md` as the v1 gate source. A gate can be:

- `通过`: evidence shows the gate is satisfied in the closeout window
- `未通过`: evidence shows the gate is broken or stale
- `状态未知`: the gate exists, but no evaluator or fresh artifact was found

Do not report `未配置` unless the project is missing from `project-gates-v1.md`.

The v1 evaluator is intentionally conservative. It may use:

- latest Project `daily-update.md` entry text
- repo closeout status
- automation memory freshness
- configured output file existence

It must not infer business success from implementation activity alone.

## Command

Run from `/Users/wendy/Documents/agent自管理`:

```bash
python3 scripts/codex_daily_closeout.py --hours 24 --write --json
```

After the command completes, apply `/Users/wendy/Documents/agent自管理/docs/llm-summary-review-v1.md`.

## Completion Standard

- all eight sections exist in `_daily-closeout.md`
- the latest `_daily-closeout.md` entry has a CEO/PM summary reviewed by the automation LLM
- each latest Project `daily-update.md` entry has a CEO/PM summary reviewed by the automation LLM
- pinned project daily updates were written or explicitly reported blocked
- code closeout status was recorded
- unknown files were reported without blocking the report
- tomorrow to-do draft exists
