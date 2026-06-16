# Contributing to loop

Thanks for your interest. `loop` is a small, stdlib-only Python engine; contributions
that keep it conservative and well-tested are very welcome.

## Prerequisites

- Python 3.11+ (the engine is standard-library only; `pytest` is needed for tests)
- git, GitHub CLI (`gh`)
- A coding-agent CLI: Codex (`codex exec`) and/or Claude Code (`claude --print`)
- macOS for actually *running* the loop (verification uses `sandbox-exec`). You can
  develop and run the **test suite** on Linux/macOS — the tests stub OS-specific bits.

Run `loop doctor` to check your environment.

## Running the tests

```bash
pip install pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest loop-engine/tests -q
```

CI (`.github/workflows/ci.yml`) runs the same suite on every push and pull request.
Please keep it green and add tests for new behavior.

## Design principles (please preserve)

- **Fail closed.** Safety gates reject on ambiguity; they never silently downgrade.
  (See the permission-mode clamp and the bootstrap preflights as examples.)
- **Structural markers and machine tokens stay English.** Issue section headers
  (`## Risk`, `## Allowed Files`, …) and tokens (`REVIEW_STATUS`, `low/medium/high`)
  are parsed by the engine. Only generated *prose* is localized via `output_language`.
- **Provider-agnostic seam.** All agents route through `agent_exec()`; adding a provider
  means a new `*_command()` builder plus a branch — keep personal/owner-specific values
  out of the engine (use config/env).
- **No runtime artifacts in git.** `registry.json`, `state.json`, `runs/`, `knowledge/`,
  etc. are git-ignored; never commit them.

## Pull requests

1. Branch from `main`, keep changes focused.
2. Add or update tests; ensure the suite is green locally.
3. Describe the behavior change and any safety implications.
4. For security-sensitive changes, see [SECURITY.md](SECURITY.md).
