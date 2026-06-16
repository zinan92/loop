# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project is pre-1.0.

## [0.1.0] - 2026-06-16

First public release of the `loop` engine.

### Added
- Value-ranked, auditable, pausable coding-agent loop for local Git projects
  (planner → value/risk gates → worker → sandboxed verification → reviewer →
  auto PR + merge → digest → bounded memory).
- **Per-role coding-agent providers**: Codex (default) or Claude Code, via a
  `provider` field, `loop init --provider`, or `LOOP_DEFAULT_PROVIDER`. Claude
  defaults omit the model so the Claude CLI picks its own current default.
- **Configurable output language** (`output_language` / `LOOP_OUTPUT_LANGUAGE`):
  generated prose is localized while structural headers and machine tokens stay
  English so the safety parsing keeps working.
- **Fail-closed permission clamp**: an unsafe Claude `permission_mode`
  (e.g. `bypassPermissions`) is rejected, not silently downgraded.
- **`loop doctor`**: one-shot prerequisite check (git, gh+auth, sandbox-exec, and
  the agent CLI the registry actually requires).
- **Configurable secret-scan dir** (`LOOP_SECRETS_DIR`).
- Graceful failure off-macOS: `launchctl` is guarded and bootstrap fails closed with
  `unsupported_platform` / `github_repo_not_found` **before any mutation**.
- Named `missing_<provider>_cli` errors for both Codex and Claude.
- Bilingual README, agent-facing `AGENTS.md`, and a visual `docs/flow.html`.
- CI (GitHub Actions) running the test suite; Apache-2.0 license.

### Security
- The engine reads real secret values to detect worker-diff leaks; verification runs
  under `sandbox-exec` with network and secret dirs denied. See [SECURITY.md](SECURITY.md).

[0.1.0]: https://github.com/zinan92/loop/releases/tag/v0.1.0
