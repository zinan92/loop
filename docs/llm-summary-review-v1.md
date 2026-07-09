# LLM Summary Review v1

This contract is the LLM layer after the deterministic Daily Closeout script runs.

## Purpose

Turn the deterministic evidence ledger into a human-readable CEO/PM progress report for Park without changing the underlying evidence.

The deterministic script answers: what happened and where is the proof?

The LLM review layer answers: what does this mean from a product and user-value perspective?

## Inputs

- `/Users/wendy/park-io/008_codex session insights and decision logs/_daily-closeout.md`
- latest `daily-update.md` entry for each pinned Project
- `docs/north-star.md`
- `docs/project-gates-v1.md`
- evidence already written by the deterministic script

## Allowed Edits

The LLM may edit only the latest entry's summary sections:

- `_daily-closeout.md`: `### 0. CEO/PM 摘要`
- each Project `daily-update.md`: `### CEO/PM 摘要`

The LLM must not edit:

- evidence tables
- commit hashes
- file paths
- thread ids
- timestamps
- blocker age
- automation memory
- historical entries from prior dates

## Summary Requirements

Use Park as the user name. Wendy is the agent assistant name and should not be used as the human user in summaries.

Each summary must be short and useful to a CEO/PM:

- lead with the new product/user value
- identify whether the work is user-visible, operator-facing, infrastructure, or maintenance
- mention the most important blocker or risk only if it changes what Park should do next
- avoid raw thread titles, command logs, and implementation detail unless they are the product value

## Output Style

Chinese, direct, concise.

Preferred shape:

- 今日新增价值：...
- 对用户/操作者意味着：...
- 当前风险：...
- 下一步产品动作：...

If there is no user-visible progress, say so directly and state the internal asset that improved.

## Completion Standard

- The latest global closeout entry starts with a useful CEO/PM summary.
- Each latest pinned Project update has a useful CEO/PM summary before raw evidence.
- No evidence values were changed.
