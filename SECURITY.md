# Security Policy

## Reporting a vulnerability

Please report security issues **privately** via GitHub Security Advisories
([Report a vulnerability](https://github.com/zinan92/loop/security/advisories/new)),
not in a public issue. We aim to acknowledge within a few days.

## What this tool does (read before running)

`loop` is a high-privilege automation tool. By design it:

- Runs as your **full OS user**. The engine reads your real secret files
  (`LOOP_SECRETS_DIR`, default `~/.config/loop/secrets`, plus `~/.ssh`, `~/.aws`, …)
  in order to scan worker diffs and **block** changes that would leak a known secret.
- **Auto-merges** a pull request into the pilot branch when the reviewer returns
  `REVIEW_STATUS: pass` — there is no separate human gate between review-pass and merge.
- Drives a coding agent (Codex or Claude Code) that edits code in an isolated git
  worktree. Codex confines writes via `--sandbox workspace-write`; Claude Code is
  confined by its own headless sandbox, and the engine **rejects** unsafe permission
  modes (`bypassPermissions` raises) so configuration cannot disable that sandbox.
- Runs verification commands under macOS `sandbox-exec` with network denied and
  secret directories denied.

## In scope

- Sandbox/permission escapes (an agent or verification command writing or reading
  outside its worktree, or reaching the network when it should be denied).
- A path that lets registry/contract/daily-focus configuration disable a safety gate
  (value line, risk envelope, allowlist, secret scan, blocked categories, the
  permission-mode clamp).
- Secret leakage into committed/merged content that the post-worker scan fails to catch.

## Out of scope (by design)

- The engine reading your secret files to power leak detection (intended).
- Auto-merge on reviewer pass (intended; disclosed above — supervise accordingly).
- Running on an untrusted repository or with an untrusted contract/daily-focus.

## Supported versions

Pre-1.0: only the latest `main` is supported. Pin a commit if you need stability.
