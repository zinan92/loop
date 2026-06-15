# Daily Focus: Example Product

Date: 2026-06-15

## Today Direction

Make the highest-value user-visible improvement first. Do not spend cycles on
internal cleanup unless it directly unlocks that improvement.

## Value-Ranked Work

1. Improve the primary user-facing artifact so the before/after benefit is
   obvious to a normal user.
   - value_score: 5
   - expected_risk: medium
   - approval: supervised envelope required
2. Improve the digest/status output so an operator can see what changed without
   reading logs.
   - value_score: 4
   - expected_risk: low
3. Add tests around the new value gate.
   - value_score: 3
   - expected_risk: low

## Recommended Budget

- recommended_cycles: 1-2
- stop_condition: Stop after the user-visible improvement lands or after one
  no-op cycle.

## Preapproved Medium-Risk Envelope

name: primary-surface-improvement

Allowed files:

- `src/**`
- `web/**`
- `tests/**`

Allowed changes:

- local UI or product-surface behavior needed for the ranked item above
- tests and deterministic validation for that behavior

Forbidden changes:

- credentials, secrets, `.env`, auth tokens
- launchd, cron, scheduler installation
- deployment, publishing, external posting
- destructive data/file operations
- cross-project permission expansion

First execution must be supervised.
