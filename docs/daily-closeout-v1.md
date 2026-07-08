# Daily Closeout v1

This contract owns the 04:10 Agent OS daily closeout.

## Purpose

Close the daily loop by connecting yesterday's draft to today's evidence and tomorrow's draft. The closeout is mechanical and evidence-first; it does not make Wendy's strategic choice.

## Schedule

- 03:00: `Know-How Sync v1`
- 04:10: `Daily Closeout v1`
- Weekly retrospective: manual for v1

## Outputs

- Project updates: `/Users/wendy/park-io/008_codex session insights and decision logs/<Project>/daily-update.md`
- Global cumulative closeout: `/Users/wendy/park-io/008_codex session insights and decision logs/_daily-closeout.md`
- Closeout state: `/Users/wendy/park-io/008_codex session insights and decision logs/_daily-closeout-state.json`

## Pinned Projects

Only these projects are in scope until Wendy explicitly adds more:

- `交易系统`
- `Park 的内容生产`
- `Park OS`
- `Agent OS`
- `ai newsletter`

## Required Sections

Every daily closeout entry must include these eight sections. Empty sections must say `SKIPPED（原因）`.

1. 北极星对照
2. 昨日 to-do 核对
3. 今日产出台账
4. 进度闸门
5. 异常与风险
6. 用户视角的今日成果
7. 明日 to-do（草案）
8. 内容候选

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
- label it as draft pending Wendy confirmation

The draft should be based on unfinished items, current blockers, and today's evidence. It is not the final strategic decision.

## Command

Run from `/Users/wendy/Documents/agent自管理`:

```bash
python3 scripts/codex_daily_closeout.py --hours 24 --write --json
```

## Completion Standard

- all eight sections exist in `_daily-closeout.md`
- pinned project daily updates were written or explicitly reported blocked
- code closeout status was recorded
- unknown files were reported without blocking the report
- tomorrow to-do draft exists
