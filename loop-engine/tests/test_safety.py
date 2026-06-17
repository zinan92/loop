"""Reproducible safety tests for the loop engine.

Run: python3 -m pytest loop-engine/tests/test_safety.py -v
(from the repo root)

Covers the two pre-release blockers plus the round-1/2/3 hardening:
  B1  blocked-keyword scan must ignore the "Out Of Scope" section
  B2  candidates.json issue_path must be confined to run_dir/issues/issue-NNN.md
  + Allowed-Files allowlist hardening, secret-laundering scan, sandbox clamp.
"""
import importlib.util
import json
import pathlib

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("loopctl", ENGINE / "bin" / "loopctl.py")
loopctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loopctl)

ALL_CATS = list(loopctl.BLOCKED_SIGNALS.keys())

# A realistic issue that uses the project's template, where Out Of Scope lists
# the very prohibitions ("no credentials / launchd / publishing") that the old
# scanner mistook for risk hits.
TEMPLATE_ISSUE = """# Add deterministic CLI output test

## Risk
low

## Goal
Add a unit test asserting `python3 cli.py` prints a stable header line.

## Context
The CLI output is currently untested. A regression test pins the format.

## Allowed Files
- tests/test_cli.py

## Out Of Scope
- No credentials or token handling
- No launchd/cron changes
- No external auth or publishing

## Definition Of Done
- New test passes under `python3 -m pytest tests/`

## Verification Commands
- python3 -m pytest tests/

## Reviewer Checklist
- Confirm no credential, publishing, or launchd changes were made
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


# --- Blocker 1: Out Of Scope must not trip the scanner ----------------------

def test_screen_ignores_out_of_scope_and_reviewer_checklist(tmp_path):
    issue = _write(tmp_path, "issue-001.md", TEMPLATE_ISSUE)
    hits = loopctl.screen_blocked(issue, ALL_CATS)
    assert hits == [], f"benign issue wrongly gated by Out-Of-Scope text: {hits}"


def test_screen_catches_risk_in_goal(tmp_path):
    bad = TEMPLATE_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Wire Telegram credential send and install a launchd plist.",
    )
    issue = _write(tmp_path, "issue-002.md", bad)
    hits = loopctl.screen_blocked(issue, ALL_CATS)
    assert any(h.startswith("credentials") for h in hits), hits
    assert any(h.startswith("launchd_or_cron") for h in hits), hits


def test_screen_ignores_negative_constraints_inside_context(tmp_path):
    issue = _write(tmp_path, "issue-003.md", TEMPLATE_ISSUE.replace(
        "The CLI output is currently untested. A regression test pins the format.",
        "Use synthetic data only; do not read live credentials or user state.",
    ))

    assert loopctl.screen_blocked(issue, ALL_CATS) == []


def test_screen_catches_positive_risk_inside_context(tmp_path):
    issue = _write(tmp_path, "issue-004.md", TEMPLATE_ISSUE.replace(
        "The CLI output is currently untested. A regression test pins the format.",
        "Implement credential handling for Telegram login state.",
    ))

    hits = loopctl.screen_blocked(issue, ALL_CATS)
    assert any(h.startswith("credentials") for h in hits), hits


def test_screen_catches_live_trading_config_flip_even_if_low_risk(tmp_path):
    issue = _write(tmp_path, "issue-live.md", TEMPLATE_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Flip the live trading config, set dry_run to false, and enable execution mode for broker order submission.",
    ).replace(
        "The CLI output is currently untested. A regression test pins the format.",
        "This is described as a small config cleanup but it touches live trading behavior.",
    ))

    hits = loopctl.screen_blocked(issue, ALL_CATS)

    assert any(h.startswith("money_or_trading") for h in hits), hits


def test_screen_allows_read_only_trading_gate_review(tmp_path):
    issue = _write(tmp_path, "issue-readonly.md", TEMPLATE_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Write a read-only trading gate review from existing reports and backtest output.",
    ).replace(
        "The CLI output is currently untested. A regression test pins the format.",
        "Do not touch broker credentials, live config, order submission, or real-money paths.",
    ))

    assert loopctl.screen_blocked(issue, ALL_CATS) == []


def test_trading_project_gates_non_read_only_work_even_without_keywords(tmp_path):
    issue = _write(tmp_path, "issue-trading-default-deny.md", TEMPLATE_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Tune the strategy configuration threshold for the gold workflow.",
    ))
    cfg = {
        "name": "Gold Trading",
        "repo_path": str(tmp_path),
        "github_repo": "owner/gold-trading",
    }

    assert loopctl.trading_issue_gate(issue, cfg) == "trading_requires_manual_approval"


def test_trading_project_allows_read_only_gate_review(tmp_path):
    issue = _write(tmp_path, "issue-trading-readonly.md", TEMPLATE_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Write a read-only trading gate review from existing reports and backtest output.",
    ))
    cfg = {
        "name": "Gold Trading",
        "repo_path": str(tmp_path),
        "github_repo": "owner/gold-trading",
    }

    assert loopctl.trading_issue_gate(issue, cfg) is None


def test_screen_catches_positive_risk_hidden_on_negative_line(tmp_path):
    issue = _write(tmp_path, "issue-negation-mix.md", TEMPLATE_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Never use live trade directly; instead enable order submission through the broker config.",
    ))

    hits = loopctl.screen_blocked(issue, ALL_CATS)

    assert any(h.startswith("money_or_trading") for h in hits), hits


# --- Blocker 2: issue_path confinement --------------------------------------

def _run_dir_with_candidates(tmp_path, issue_path_value, make_real=True):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    if make_real:
        (run_dir / "issues" / "issue-001.md").write_text(TEMPLATE_ISSUE)
    (run_dir / "candidates.json").write_text(json.dumps([
        {"id": "c1", "risk": "low", "auto_execute": True, "issue_path": issue_path_value}
    ]))
    return run_dir


def test_candidate_path_accepts_valid(tmp_path):
    run_dir = _run_dir_with_candidates(tmp_path, "issues/issue-001.md")
    paths = loopctl.candidate_issue_paths(run_dir)
    assert len(paths) == 1 and paths[0].name == "issue-001.md"


def test_candidate_path_rejects_absolute(tmp_path):
    # simulate planner pointing at a secret file via absolute path
    secret = tmp_path / "secret.txt"
    secret.write_text("lin_api_TOPSECRETVALUE")
    run_dir = _run_dir_with_candidates(tmp_path, str(secret), make_real=False)
    with pytest.raises(RuntimeError):
        loopctl.candidate_issue_paths(run_dir)


def test_candidate_path_rejects_traversal(tmp_path):
    run_dir = _run_dir_with_candidates(tmp_path, "../../../etc/hosts", make_real=False)
    with pytest.raises(RuntimeError):
        loopctl.candidate_issue_paths(run_dir)


def test_candidate_path_rejects_wrong_name(tmp_path):
    run_dir = _run_dir_with_candidates(tmp_path, "issues/evil.md", make_real=False)
    with pytest.raises(RuntimeError):
        loopctl.candidate_issue_paths(run_dir)


def test_candidate_path_rejects_symlink_with_candidates_json(tmp_path):
    # planner declares issues/issue-001.md but it is a symlink to an external secret
    secret = tmp_path / "secret.txt"
    secret.write_text("lin_api_TOPSECRETVALUE")
    run_dir = _run_dir_with_candidates(tmp_path, "issues/issue-001.md", make_real=False)
    (run_dir / "issues" / "issue-001.md").symlink_to(secret)
    with pytest.raises(RuntimeError):
        loopctl.candidate_issue_paths(run_dir)


def test_fallback_rejects_symlink_without_candidates_json(tmp_path):
    # no candidates.json -> directory-glob fallback must also reject the symlink
    secret = tmp_path / "secret.txt"
    secret.write_text("lin_api_TOPSECRETVALUE")
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").symlink_to(secret)
    with pytest.raises(RuntimeError):
        loopctl.candidate_issue_paths(run_dir)


def test_fallback_accepts_real_issue_without_candidates_json(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").write_text(TEMPLATE_ISSUE)
    paths = loopctl.candidate_issue_paths(run_dir)
    assert len(paths) == 1 and paths[0].name == "issue-001.md"


def test_candidate_value_score_filters_low_value_work(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").write_text(TEMPLATE_ISSUE)
    (run_dir / "issues" / "issue-002.md").write_text(TEMPLATE_ISSUE)
    (run_dir / "candidates.json").write_text(json.dumps([
        {
            "id": "c1",
            "risk": "low",
            "auto_execute": True,
            "value_score": 2,
            "issue_path": "issues/issue-001.md",
        },
        {
            "id": "c2",
            "risk": "low",
            "auto_execute": True,
            "value_score": 4,
            "issue_path": "issues/issue-002.md",
        },
    ]))

    paths = loopctl.candidate_issue_paths(run_dir, min_value_score=3)

    assert [path.name for path in paths] == ["issue-002.md"]


def test_candidate_paths_are_sorted_by_value_score(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").write_text(TEMPLATE_ISSUE)
    (run_dir / "issues" / "issue-002.md").write_text(TEMPLATE_ISSUE)
    (run_dir / "candidates.json").write_text(json.dumps([
        {
            "id": "c1",
            "risk": "low",
            "auto_execute": True,
            "value_score": 3,
            "issue_path": "issues/issue-001.md",
        },
        {
            "id": "c2",
            "risk": "low",
            "auto_execute": True,
            "value_score": 5,
            "issue_path": "issues/issue-002.md",
        },
    ]))

    paths = loopctl.candidate_issue_paths(run_dir, min_value_score=3)

    assert [path.name for path in paths] == ["issue-002.md", "issue-001.md"]


def test_default_max_tasks_per_cycle_is_one():
    assert loopctl.max_tasks_per_cycle({}) == 1
    assert loopctl.max_tasks_per_cycle({"max_tasks_per_cycle": None}) == 1
    assert loopctl.max_tasks_per_cycle({"max_tasks_per_cycle": "bad"}) == 1
    assert loopctl.max_tasks_per_cycle({"max_tasks_per_cycle": 0}) == 1
    assert loopctl.max_tasks_per_cycle({"max_tasks_per_cycle": "2"}) == 2


def test_deferred_issue_paths_are_not_human_approval_items(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    issues_dir = run_dir / "issues"
    issues_dir.mkdir(parents=True)
    issue_1 = issues_dir / "issue-001.md"
    issue_2 = issues_dir / "issue-002.md"
    issue_1.write_text(TEMPLATE_ISSUE)
    issue_2.write_text(TEMPLATE_ISSUE)

    selected = [issue_1, issue_2]
    max_tasks = loopctl.max_tasks_per_cycle({})
    deferred = selected[max_tasks:]
    loopctl.write_deferred_issue_paths(run_dir, deferred, max_tasks)

    assert [path.name for path in selected[:max_tasks]] == ["issue-001.md"]
    deferred_text = (run_dir / "deferred-candidates.md").read_text()
    assert "Deferred Candidates" in deferred_text
    assert "`issues/issue-002.md`" in deferred_text
    assert "human" not in deferred_text.lower()


def test_higher_value_waiting_item_blocks_lower_value_work(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").write_text(TEMPLATE_ISSUE)
    (run_dir / "candidates.json").write_text(json.dumps([
        {
            "id": "c1",
            "risk": "medium",
            "auto_execute": False,
            "value_score": 5,
            "title": "Build the visible product surface",
            "issue_path": None,
        },
        {
            "id": "c2",
            "risk": "low",
            "auto_execute": True,
            "value_score": 3,
            "issue_path": "issues/issue-001.md",
        },
    ]))
    issue_paths = loopctl.candidate_issue_paths(run_dir, min_value_score=3)
    waiting = loopctl.candidate_gate_items(run_dir, 3, supervised=False, medium_envelope=None)

    blocker = loopctl.higher_value_waiting_item(run_dir, issue_paths, waiting)

    assert blocker["title"] == "Build the visible product surface"
    assert blocker["value_score"] == 5


def test_fallback_issues_do_not_bypass_value_line(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").write_text(TEMPLATE_ISSUE)

    assert loopctl.candidate_issue_paths(run_dir, min_value_score=3) == []


MEDIUM_ISSUE = TEMPLATE_ISSUE.replace("## Risk\nlow", "## Risk\nmedium").replace(
    "## Allowed Files\n- tests/test_cli.py",
    "## Allowed Files\n- web/widget.html\n- tests/test_widget.py",
)


def test_supervised_medium_candidate_requires_matching_envelope(tmp_path):
    run_dir = tmp_path / "runs" / "proj-run"
    (run_dir / "issues").mkdir(parents=True)
    (run_dir / "issues" / "issue-001.md").write_text(MEDIUM_ISSUE)
    (run_dir / "candidates.json").write_text(json.dumps([
        {
            "id": "c1",
            "risk": "medium",
            "auto_execute": "supervised",
            "requires_supervised": True,
            "preapproved_envelope": "desktop-widget",
            "value_score": 4,
            "issue_path": "issues/issue-001.md",
        }
    ]))

    assert loopctl.candidate_issue_paths(run_dir, min_value_score=3) == []
    paths = loopctl.candidate_issue_paths(
        run_dir,
        min_value_score=3,
        supervised=True,
        medium_envelope={"name": "desktop-widget"},
    )

    assert [path.name for path in paths] == ["issue-001.md"]


def test_medium_issue_must_stay_inside_preapproved_envelope(tmp_path):
    issue = tmp_path / "issue-001.md"
    issue.write_text(MEDIUM_ISSUE.replace("web/widget.html", "README.md"))
    cfg = {"verification_commands": ["python3 -m pytest tests/"]}
    envelope = {
        "name": "desktop-widget",
        "allowed_files": ["web/widget.html", "tests/"],
        "verification_commands": ["python3 -m pytest tests/"],
    }

    with pytest.raises(RuntimeError, match="Allowed Files exceed"):
        loopctl.validate_medium_issue_against_envelope(issue, cfg, envelope)


def test_medium_issue_forbidden_changes_are_enforced(tmp_path):
    issue = tmp_path / "issue-001.md"
    issue.write_text(MEDIUM_ISSUE.replace(
        "Add a unit test asserting `python3 cli.py` prints a stable header line.",
        "Update the widget and touch broker credentials as part of the same task.",
    ))
    cfg = {"verification_commands": ["python3 -m pytest tests/"]}
    envelope = {
        "name": "desktop-widget",
        "allowed_files": ["web/widget.html", "tests/"],
        "verification_commands": ["python3 -m pytest tests/"],
        "forbidden_changes": ["broker credentials", "live trading config"],
    }

    with pytest.raises(RuntimeError, match="forbidden"):
        loopctl.validate_medium_issue_against_envelope(issue, cfg, envelope)


def test_medium_issue_accepts_narrow_preapproved_envelope(tmp_path):
    issue = tmp_path / "issue-001.md"
    issue.write_text(MEDIUM_ISSUE)
    cfg = {"verification_commands": ["python3 -m pytest tests/"]}
    envelope = {
        "name": "desktop-widget",
        "allowed_files": ["web/widget.html", "tests/"],
        "verification_commands": ["python3 -m pytest tests/"],
    }

    loopctl.validate_medium_issue_against_envelope(issue, cfg, envelope)


# --- Allowed-Files allowlist hardening --------------------------------------

def test_allowlist_bare_star_grants_nothing():
    assert loopctl.allowlist_violations(["cli.py"], ["*"]) == ["cli.py"]


def test_allowlist_glob_no_dir_crossing():
    assert loopctl.allowlist_violations(["a/b/c.py"], ["*.py"]) == ["a/b/c.py"]
    assert loopctl.allowlist_violations(["c.py"], ["*.py"]) == []


def test_allowlist_path_escape_violates():
    assert loopctl.allowlist_violations(["../secrets/key"], ["*"]) == ["../secrets/key"]
    assert loopctl.allowlist_violations(["x.py"], ["../secrets/"]) == ["x.py"]


def test_allowlist_allows_legit():
    assert loopctl.allowlist_violations(["tests/test_cost.py"], ["tests/test_cost.py"]) == []
    assert loopctl.allowlist_violations(["tests/fixtures/a.json"], ["tests/fixtures/"]) == []
    assert loopctl.allowlist_violations(["tests/test_a.py"], ["tests/*.py"]) == []


def test_changed_files_preserves_porcelain_status_columns(monkeypatch, tmp_path):
    class Result:
        stdout = " M cli.py\nA  tests/test_cli.py\nR  old.py -> renamed.py\n?? scratch.md\n"

    monkeypatch.setattr(loopctl, "run", lambda *args, **kwargs: Result())

    assert loopctl.changed_files(tmp_path) == [
        "cli.py",
        "tests/test_cli.py",
        "renamed.py",
        "scratch.md",
    ]


def test_changed_files_ignores_python_verification_cache(monkeypatch, tmp_path):
    class Result:
        stdout = (
            " M cli.py\n"
            "?? __pycache__/cli.cpython-311.pyc\n"
            "?? pkg/__pycache__/mod.cpython-311.pyc\n"
            "?? .pytest_cache/CACHEDIR.TAG\n"
            "?? tests/test_cli.py\n"
        )

    monkeypatch.setattr(loopctl, "run", lambda *args, **kwargs: Result())

    assert loopctl.changed_files(tmp_path) == [
        "cli.py",
        "tests/test_cli.py",
    ]


# --- secret laundering scan -------------------------------------------------

def test_secret_marker_blocked(tmp_path):
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "t.py").write_text("KEY = 'ghp_ABCDEFG1234567890'\n")
    leaks = loopctl.scan_changed_for_secrets(wt, ["tests/t.py"])
    assert leaks and "marker" in leaks[0]


def test_secret_scan_clean_passes(tmp_path):
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    assert loopctl.scan_changed_for_secrets(wt, ["tests/ok.py"]) == []


# --- sandbox clamp ----------------------------------------------------------

def test_norm_path_keeps_traversal():
    assert loopctl._norm_path("../etc") == "../etc"
    assert loopctl._norm_path("./tests/a.py") == "tests/a.py"
    assert loopctl._norm_path("/abs/x") == "abs/x"
