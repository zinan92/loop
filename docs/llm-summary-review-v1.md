# LLM Summary Review v2

This contract is the LLM layer after the deterministic Daily Closeout script runs.

## Purpose

Turn the deterministic evidence ledger into a human-readable CEO/PM progress report for Park without changing the underlying evidence.

The deterministic script answers: what happened and where is the proof?

The LLM review layer answers: what does this mean from a product and user-value perspective?

The LLM layer is allowed to make PM/CEO judgments, but those judgments must stay in explicitly marked LLM sections. It must not rewrite the deterministic evidence ledger.

## Inputs

- `/Users/wendy/park-io/008_codex session insights and decision logs/_daily-closeout.md`
- latest `daily-update.md` entry for each pinned Project
- `docs/north-star.md`
- `docs/project-gates-v1.md`
- evidence already written by the deterministic script

## Allowed Edits

The LLM may edit only the latest entry's marked LLM sections:

- `_daily-closeout.md`: latest entry `<!-- llm-summary:start -->` to `<!-- llm-summary:end -->`
- `_daily-closeout.md`: latest entry `<!-- llm-pm-review:start -->` to `<!-- llm-pm-review:end -->`
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
- deterministic sections 1-8 except through the marked LLM PM Review block

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

## PM Review Requirements

The global `LLM PM Review` block is the temporary fallback for pending evaluators. It may analyze:

- North Star alignment: `服务 / 部分服务 / 疑似偏离 / 证据不足`
- yesterday to-do: `有证据完成 / 部分 / 未找到证据 / 无基线`
- gate results: explain `未通过` and `状态未知`; do not relabel them
- tomorrow attack: one main recommendation and at most three supporting checks
- content candidate: one external-facing topic only if the evidence has standalone reader value

Every PM Review claim must be either:

- backed by a visible evidence section, path, gate row, commit, or thread record; or
- explicitly labeled `推断，待 Park 确认`

The LLM may fill blanks, but it may not invent facts.

## Completion Standard

- The latest global closeout entry starts with a useful CEO/PM summary.
- The latest global closeout entry includes a completed `LLM PM Review` block.
- Each latest pinned Project update has a useful CEO/PM summary before raw evidence.
- No evidence values were changed.
