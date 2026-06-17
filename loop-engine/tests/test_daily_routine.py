import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest


ENGINE = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("loopctl", ENGINE / "bin" / "loopctl.py")
loopctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loopctl)
_facade_spec = importlib.util.spec_from_file_location("loop_facade", ENGINE / "bin" / "loop_facade.py")
loop_facade = importlib.util.module_from_spec(_facade_spec)
_facade_spec.loader.exec_module(loop_facade)


def patch_engine(monkeypatch, tmp_path, repo):
    engine = tmp_path / "engine"
    engine.mkdir()
    registry = engine / "registry.json"
    registry.write_text(json.dumps({
        "projects": {
            "demo": {
                "name": "Demo",
                "repo_path": str(repo),
                "github_repo": "owner/demo",
                "pilot_branch": "loop/demo-pilot",
                "contract_path": str(repo / ".loop" / "contract.yaml"),
                "verification_commands": ["git diff --check"],
                "auto_approval": {"blocked_categories": [], "max_tasks_per_cycle": 1},
                "agents": {"planner": {"provider": "codex"}},
            }
        }
    }))
    monkeypatch.setattr(loopctl, "ENGINE_ROOT", engine)
    monkeypatch.setattr(loopctl, "REGISTRY_PATH", registry)
    monkeypatch.setattr(loopctl, "STATE_PATH", engine / "state.json")
    monkeypatch.setattr(loopctl, "LOCK_DIR", engine / "locks")
    monkeypatch.setattr(loopctl, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(loopctl, "CONFIG_PATH", tmp_path / "config" / "config.json")
    monkeypatch.setattr(loopctl, "scheduler_status_payload", lambda project: {
        "label": f"com.agent-loop.{project}",
        "plist": str(tmp_path / "noop.plist"),
        "installed": False,
        "loaded": False,
    })
    return engine


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".loop").mkdir()
    (repo / ".loop" / "contract.yaml").write_text("project_id: demo\nname: Demo\n")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "README.md").write_text("# Demo\n")
    return repo


def make_plain_git_repo(tmp_path, name="plain"):
    repo = tmp_path / name
    repo.mkdir()
    (repo / "README.md").write_text(f"# {name.title()}\n\nA useful product.\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/owner/{name}.git"], cwd=repo, check=True)
    return repo


def fake_pm_agent_plan(project="demo", envelope_name="primary-surface", envelope_commands=None):
    return {
        "date": loopctl.today_date(),
        "summary": "Demo has one clear user-visible improvement worth doing first.",
        "projects": [
            {
                "project": project,
                "name": "Demo",
                "decision": "loop",
                "today_focus": "Ship a visible calculator input flow.",
                "top_value_task": "Add real user input support.",
                "top_risk": "medium",
                "approval_needed": "Approve today's medium-risk envelope.",
                "user_benefit": "The user can run the product on their own values.",
                "success_criteria": "CLI accepts input and tests prove the behavior.",
                "reason": "This is the highest product value, even though it touches behavior.",
                "recommended_cycles": 2,
                "stop_condition": "Stop after the input flow ships.",
                "value_threshold": 4,
                "medium_risk_question": "Approve all medium-risk calculator-input work today within the primary surface envelope?",
                "medium_envelope": {
                    "name": envelope_name,
                    "scope": "CLI input behavior plus tests.",
                    "allowed_files": ["src/**", "tests/**"],
                    "verification_commands": envelope_commands or ["git diff --check"],
                    "forbidden_changes": ["credentials/secrets/.env", "deployment/publishing"],
                },
                "tasks": [
                    {
                        "rank": 1,
                        "task": "Add real user input support.",
                        "value_score": 5,
                        "risk": "medium",
                        "approval_path": "morning medium-risk envelope",
                        "benefit": "Users can run the product on their own inputs.",
                        "category": "new_feature",
                        "surface": "CLI",
                    },
                    {
                        "rank": 2,
                        "task": "Clarify README usage.",
                        "value_score": 3,
                        "risk": "low",
                        "approval_path": "auto after value and verification gates",
                        "benefit": "New users can find the command.",
                        "category": "activation",
                        "surface": "docs",
                    },
                ],
            }
        ],
        "questions_for_operator": [
            "Should Demo run before lower-value projects today?",
        ],
    }


def patch_pm_agent(monkeypatch, plan=None):
    calls = []

    def fake_agent_exec(prompt, cwd, output_path, extra_writable, agent_cfg=None):
        calls.append({
            "prompt": prompt,
            "cwd": cwd,
            "output_path": output_path,
            "extra_writable": extra_writable,
            "agent_cfg": agent_cfg or {},
        })
        extra_writable.mkdir(parents=True, exist_ok=True)
        (extra_writable / "pm-analysis.md").write_text("# PM Analysis\n")
        (extra_writable / "pm-plan.json").write_text(json.dumps(plan or fake_pm_agent_plan(), indent=2) + "\n")
        output_path.write_text("PM review complete.\n")

    monkeypatch.setattr(loopctl, "agent_exec", fake_agent_exec)
    return calls


def test_facade_supports_doctor_and_init_provider(monkeypatch):
    captured = {}

    def fake_run(cmd):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sys, "argv", ["loop", "init", "--provider", "claude"])
    monkeypatch.setattr(loop_facade.subprocess, "run", fake_run)

    assert loop_facade.main() == 0
    assert captured["cmd"][-3:] == ["init", "--provider", "claude"]

    monkeypatch.setattr(sys, "argv", ["loop", "doctor"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-1:] == ["doctor"]

    monkeypatch.setattr(sys, "argv", ["loop", "approve", "demo", "--approve-medium"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-4:] == ["approve", "--approve-medium", "--project", "demo"]

    monkeypatch.setattr(sys, "argv", ["loop", "portfolio", "add", "https://github.com/acme/app", "--mode", "plan-only"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-5:] == ["portfolio", "add", "https://github.com/acme/app", "--mode", "plan-only"]

    monkeypatch.setattr(sys, "argv", ["loop", "portfolio", "intake", "demo"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-3:] == ["portfolio", "intake", "demo"]

    monkeypatch.setattr(sys, "argv", ["loop", "approve", "newsletter", "--init-loop"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-4:] == ["approve", "--init-loop", "--project", "newsletter"]

    monkeypatch.setattr(sys, "argv", ["loop", "approve", "--all-init-loop"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-2:] == ["approve", "--all-init-loop"]

    monkeypatch.setattr(sys, "argv", ["loop", "portfolio", "init-loop", "--all-eligible"])
    assert loop_facade.main() == 0
    assert captured["cmd"][-3:] == ["portfolio", "init-loop", "--all-eligible"]


def test_portfolio_add_accepts_multiple_handle_types(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)

    loopctl.portfolio_add_command(str(repo), "Local Demo", None, None, None, None, "plan-only", True)
    loopctl.portfolio_add_command("https://github.com/acme/app", None, None, None, None, None, "read-only", True)
    loopctl.portfolio_add_command(None, None, None, None, "Content Pipeline", None, "hold", False)

    output = capsys.readouterr().out
    data = loopctl.load_portfolio()
    rows = {
        row["project"]: row
        for row in [loopctl.portfolio_entry_to_row(entry) for entry in data["projects"].values()]
    }
    assert "PORTFOLIO_ADDED" in output
    assert rows["local-demo"]["local_path"] == str(repo)
    assert rows["app"]["github_repo"] == "acme/app"
    assert rows["content-pipeline"]["linear_project"] == "Content Pipeline"
    assert rows["content-pipeline"]["default_review"] is False


def test_portfolio_intake_writes_cto_profile_for_local_repo(monkeypatch, tmp_path, capsys):
    repo = make_plain_git_repo(tmp_path, "newsletter")
    patch_engine(monkeypatch, tmp_path, make_repo(tmp_path))
    loopctl.portfolio_add_command(None, "Newsletter", str(repo), "owner/newsletter", None, None, "plan-only", True)

    loopctl.portfolio_intake_command(["newsletter"])

    output = capsys.readouterr().out
    assert "PORTFOLIO_INTAKE project=newsletter" in output
    profile = loopctl.load_portfolio_profile("newsletter")
    assert profile["project"] == "newsletter"
    assert profile["loop_readiness"] == "blocked_needs_loop_init"
    assert profile["verification_candidates"] == ["python3 -m pytest tests/"]
    assert "missing_loop_contract" in profile["blockers"]
    assert (tmp_path / "engine" / "portfolio" / "newsletter" / "profile.md").exists()


def test_morning_review_surfaces_readiness_work_for_uninitialized_repo(monkeypatch, tmp_path):
    registered = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, registered)
    newsletter_repo = make_plain_git_repo(tmp_path, "newsletter")
    loopctl.save_portfolio({
        "version": 1,
        "projects": {
            "newsletter": {
                "id": "newsletter",
                "name": "Newsletter",
                "mode": "plan-only",
                "default_review": True,
                "handles": {
                    "local_path": str(newsletter_repo),
                    "github_repo": "owner/newsletter",
                },
            },
        },
    })
    loopctl.portfolio_intake_command(["newsletter"])
    patch_pm_agent(monkeypatch, {
        "date": loopctl.today_date(),
        "summary": "Newsletter readiness is today's highest-value portfolio work.",
        "projects": [],
        "questions_for_operator": [],
    })

    result = loopctl.write_morning_review(None)

    text = result["markdown"]
    assert "Portfolio Readiness Board" in text
    assert "Prepare loop readiness" in text
    row = result["plan"]["projects"][0]
    assert row["project"] == "newsletter"
    assert row["tasks"][0]["value_score"] == 5
    assert row["tasks"][0]["risk"] == "medium"
    assert row["decision"] == "init-loop"
    assert "loop approve newsletter --init-loop" in text
    snapshot = json.loads(result["paths"]["snapshot"].read_text())
    assert snapshot["projects"][0]["portfolio_profile"]["loop_readiness"] == "blocked_needs_loop_init"
    assert engine.exists()


def test_morning_review_refreshes_missing_portfolio_profile(monkeypatch, tmp_path):
    registered = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, registered)
    newsletter_repo = make_plain_git_repo(tmp_path, "newsletter")
    loopctl.save_portfolio({
        "version": 1,
        "projects": {
            "newsletter": {
                "id": "newsletter",
                "name": "Newsletter",
                "mode": "plan-only",
                "default_review": True,
                "handles": {
                    "local_path": str(newsletter_repo),
                    "github_repo": "owner/newsletter",
                },
            },
        },
    })
    patch_pm_agent(monkeypatch, {
        "date": loopctl.today_date(),
        "summary": "Newsletter should be initialized before execution.",
        "projects": [],
        "questions_for_operator": [],
    })

    assert loopctl.load_portfolio_profile("newsletter") == {}
    result = loopctl.write_morning_review(None)

    profile = loopctl.load_portfolio_profile("newsletter")
    assert profile["loop_readiness"] == "blocked_needs_loop_init"
    assert result["plan"]["projects"][0]["decision"] == "init-loop"
    assert result["plan"]["projects"][0]["can_init_loop_today"] is True


def test_trading_read_only_profile_is_not_high_risk_by_default(monkeypatch, tmp_path):
    repo = make_plain_git_repo(tmp_path, "gold-trading")
    patch_engine(monkeypatch, tmp_path, make_repo(tmp_path))
    loopctl.portfolio_add_command(None, "Gold Trading", str(repo), "owner/gold-trading", None, None, "read-only", True)
    loopctl.portfolio_intake_command(["gold-trading"])

    entry = (loopctl.load_portfolio()["projects"])["gold-trading"]
    row = loopctl.portfolio_pm_row(entry)

    assert row["tasks"][0]["risk"] == "low"
    assert "read-only trading gate review" in row["tasks"][0]["task"]
    profile = loopctl.load_portfolio_profile("gold-trading")
    assert "live order submission" in profile["risk_boundaries"]["high"]
    assert "backtest/report analysis" in " ".join(profile["risk_boundaries"]["low"])


def test_approve_init_loop_bootstraps_portfolio_project(monkeypatch, tmp_path):
    repo = make_plain_git_repo(tmp_path, "newsletter")
    patch_engine(monkeypatch, tmp_path, make_repo(tmp_path))
    loopctl.portfolio_add_command(None, "Newsletter", str(repo), "owner/newsletter", None, None, "plan-only", True)
    called = {}

    def fake_bootstrap(path):
        called["path"] = path
        registry = loopctl.registry_data()
        registry.setdefault("projects", {})["newsletter"] = {
            "name": "Newsletter",
            "repo_path": str(repo),
            "github_repo": "owner/newsletter",
            "pilot_branch": "loop/newsletter-pilot",
            "contract_path": str(repo / ".loop" / "contract.yaml"),
            "verification_commands": ["python3 -m pytest tests/"],
            "auto_approval": {"blocked_categories": [], "max_tasks_per_cycle": 1},
        }
        loopctl.save_registry(registry)
        return "newsletter"

    monkeypatch.setattr(loopctl, "bootstrap_project", fake_bootstrap)

    loopctl.approve_command("newsletter", repo, None, [], [], init_loop=True)

    assert called["path"] == repo
    saved = loopctl.load_portfolio()["projects"]["newsletter"]
    assert saved["mode"] == "loop"
    assert saved["handles"]["loop_project_id"] == "newsletter"
    approvals = loopctl.load_latest_approvals()
    assert approvals["approved"]["newsletter"]["readiness_action"] == "init_loop"
    with pytest.raises(loopctl.LoopBlocked) as exc:
        loopctl.start_day_command(["newsletter"])
    assert exc.value.reason == "morning_required_after_init_loop"


def test_approve_all_init_loop_uses_morning_recommendations(monkeypatch, tmp_path, capsys):
    repo = make_plain_git_repo(tmp_path, "newsletter")
    video_repo = make_plain_git_repo(tmp_path, "video")
    patch_engine(monkeypatch, tmp_path, make_repo(tmp_path))
    loopctl.portfolio_add_command(None, "Newsletter", str(repo), "owner/newsletter", None, None, "plan-only", True)
    loopctl.portfolio_add_command(None, "Video", str(video_repo), "owner/video", None, None, "plan-only", True)
    calls = []

    def fake_bootstrap(path):
        path = pathlib.Path(path)
        project = path.name
        calls.append(project)
        registry = loopctl.registry_data()
        registry.setdefault("projects", {})[project] = {
            "name": project.title(),
            "repo_path": str(path),
            "github_repo": f"owner/{project}",
            "pilot_branch": f"loop/{project}-pilot",
            "contract_path": str(path / ".loop" / "contract.yaml"),
            "verification_commands": ["python3 -m pytest tests/"],
            "auto_approval": {"blocked_categories": [], "max_tasks_per_cycle": 1},
        }
        loopctl.save_registry(registry)
        return project

    monkeypatch.setattr(loopctl, "bootstrap_project", fake_bootstrap)
    plan_paths = loopctl.pm_review_paths()
    plan_paths["dir"].mkdir(parents=True, exist_ok=True)
    plan = {
        "date": loopctl.today_date(),
        "projects": [
            {"project": "newsletter", "decision": "init-loop"},
        ],
    }
    plan_paths["latest_json"].write_text(json.dumps(plan))

    loopctl.approve_command(None, repo, None, [], [], all_init_loop=True)

    output = capsys.readouterr().out
    assert "INIT_LOOP_APPROVED project=newsletter" in output
    assert calls == ["newsletter"]
    approvals = loopctl.load_latest_approvals()
    assert approvals["approved"]["newsletter"]["readiness_action"] == "init_loop"
    assert "video" not in approvals["approved"]


def test_portfolio_init_loop_all_eligible_bootstraps_all_ready_repos(monkeypatch, tmp_path, capsys):
    repo = make_plain_git_repo(tmp_path, "newsletter")
    video_repo = make_plain_git_repo(tmp_path, "video")
    patch_engine(monkeypatch, tmp_path, make_repo(tmp_path))
    loopctl.portfolio_add_command(None, "Newsletter", str(repo), "owner/newsletter", None, None, "plan-only", True)
    loopctl.portfolio_add_command(None, "Video", str(video_repo), "owner/video", None, None, "plan-only", True)
    loopctl.portfolio_add_command(None, "Idea", None, None, None, None, "hold", True)
    calls = []

    def fake_bootstrap(path):
        path = pathlib.Path(path)
        project = path.name
        calls.append(project)
        registry = loopctl.registry_data()
        registry.setdefault("projects", {})[project] = {
            "name": project.title(),
            "repo_path": str(path),
            "github_repo": f"owner/{project}",
            "pilot_branch": f"loop/{project}-pilot",
            "contract_path": str(path / ".loop" / "contract.yaml"),
            "verification_commands": ["python3 -m pytest tests/"],
            "auto_approval": {"blocked_categories": [], "max_tasks_per_cycle": 1},
        }
        loopctl.save_registry(registry)
        return project

    monkeypatch.setattr(loopctl, "bootstrap_project", fake_bootstrap)

    projects = loopctl.init_loop_eligible_projects()
    loopctl.bulk_approve_init_loop(projects)

    output = capsys.readouterr().out
    assert "INIT_LOOP_COMPLETE successes=2" in output
    assert sorted(calls) == ["newsletter", "video"]
    approvals = loopctl.load_latest_approvals()
    assert approvals["approved"]["newsletter"]["readiness_action"] == "init_loop"
    assert approvals["approved"]["video"]["readiness_action"] == "init_loop"


def test_morning_without_portfolio_requires_onboarding(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)

    try:
        loopctl.write_morning_review(None)
    except loopctl.LoopBlocked as exc:
        assert exc.reason == "portfolio_missing"
        assert "loop portfolio init" in exc.details["next_actions"]
    else:
        raise AssertionError("first daily PM review should require portfolio onboarding")


def test_morning_review_includes_portfolio_verification_board(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)
    loopctl.save_portfolio({
        "version": 1,
        "projects": {
            "demo": {
                "id": "demo",
                "name": "Demo",
                "mode": "loop",
                "default_review": True,
                "handles": {
                    "loop_project_id": "demo",
                    "local_path": str(repo),
                    "github_repo": "owner/demo",
                },
            },
            "newsletter": {
                "id": "newsletter",
                "name": "Newsletter",
                "mode": "plan-only",
                "default_review": True,
                "handles": {
                    "linear_project": "Newsletter",
                    "url": "https://example.com/newsletter",
                },
            },
        },
    })
    patch_pm_agent(monkeypatch)

    result = loopctl.write_morning_review(None)

    text = result["markdown"]
    assert "Portfolio Registry Verification" in text
    assert "Verify this is the full portfolio" in text
    assert "| demo | loop | True | executable |" in text
    assert "| newsletter | plan-only | True | pm_only_missing_local_path |" in text
    snapshot = json.loads(result["paths"]["snapshot"].read_text())
    assert [row["project"] for row in snapshot["portfolio_registry"]] == ["demo", "newsletter"]
    assert result["plan"]["projects"][0]["project"] == "demo"
    assert any(row["project"] == "newsletter" for row in result["plan"]["projects"])


def test_morning_review_writes_cross_project_board(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)
    calls = patch_pm_agent(monkeypatch)

    result = loopctl.write_morning_review(["demo"])

    assert result["paths"]["latest"].exists()
    text = result["paths"]["latest"].read_text()
    assert "Daily PM Review" in text
    assert "PM-skill-driven" in text
    assert "Ship a visible calculator input flow" in text
    assert "loop approve demo --approve-medium" in text
    assert "Ranked Development Tasks" in text
    assert "demo" in text
    assert calls and "PM Review Agent" in calls[0]["prompt"]
    plan = json.loads((engine / "pm-reviews" / "latest.json").read_text())
    assert plan["projects"][0]["medium_envelope"]["name"] == "primary-surface"
    assert (engine / "pm-reviews").exists()


def test_approve_writes_daily_focus_and_approval_artifacts(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)

    loopctl.approve_command(
        "demo",
        repo,
        "primary-surface",
        ["src/**", "tests/**"],
        ["git diff --check"],
    )

    focus = repo / ".loop" / "daily-focus" / "latest.md"
    assert focus.exists()
    text = focus.read_text()
    assert "preapproved_medium_risk: primary-surface" in text
    assert "preapproved_medium_risk_allowed_files:" in text
    approvals = json.loads((engine / "approvals" / "latest.json").read_text())
    assert approvals["approved"]["demo"]["medium_envelope"]["name"] == "primary-surface"


def test_approve_medium_uses_morning_recommended_envelope(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)
    patch_pm_agent(monkeypatch, fake_pm_agent_plan(envelope_name="calculator-input"))
    loopctl.write_morning_review(["demo"])

    loopctl.approve_command(
        "demo",
        repo,
        None,
        [],
        [],
        approve_medium=True,
    )

    focus = repo / ".loop" / "daily-focus" / "latest.md"
    text = focus.read_text()
    assert "preapproved_medium_risk: calculator-input" in text
    assert "preapproved_medium_risk_approval: all_medium_risk_items_today_within_envelope" in text
    approvals = json.loads((engine / "approvals" / "latest.json").read_text())
    assert approvals["approved"]["demo"]["medium_auto_approved_for_day"] is True
    assert approvals["approved"]["demo"]["medium_envelope"]["source"] == "morning_pm_review"


def test_pm_medium_envelope_verification_aligns_to_trusted_commands(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)
    registry = json.loads((engine / "registry.json").read_text())
    registry["projects"]["demo"]["verification_commands"] = ["python3 -m pytest tests/"]
    (engine / "registry.json").write_text(json.dumps(registry))
    patch_pm_agent(
        monkeypatch,
        fake_pm_agent_plan(
            envelope_name="calculator-input",
            envelope_commands=["git diff --check"],
        ),
    )

    result = loopctl.write_morning_review(["demo"])

    row = result["plan"]["projects"][0]
    envelope = row["medium_envelope"]
    assert envelope["verification_commands"] == ["python3 -m pytest tests/"]
    assert envelope["dropped_untrusted_verification_commands"] == ["git diff --check"]
    assert "dropped_untrusted_verification: git diff --check" in result["markdown"]

    loopctl.approve_command("demo", repo, None, [], [], approve_medium=True)
    focus_text = (repo / ".loop" / "daily-focus" / "latest.md").read_text()
    assert "- python3 -m pytest tests/" in focus_text
    assert "- git diff --check" not in focus_text


def test_approve_medium_requires_morning_envelope_or_explicit_envelope(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)

    try:
        loopctl.approve_command("demo", repo, None, [], [], approve_medium=True)
    except loopctl.LoopBlocked as exc:
        assert exc.reason == "no_medium_envelope_recommended"
    else:
        raise AssertionError("approve_medium should fail closed without a morning PM envelope")


def test_morning_review_requires_pm_agent_plan(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)

    def fake_agent_exec(prompt, cwd, output_path, extra_writable, agent_cfg=None):
        extra_writable.mkdir(parents=True, exist_ok=True)
        output_path.write_text("No machine plan.\n")

    monkeypatch.setattr(loopctl, "agent_exec", fake_agent_exec)

    try:
        loopctl.write_morning_review(["demo"])
    except loopctl.LoopBlocked as exc:
        assert exc.reason == "pm_review_missing_output"
    else:
        raise AssertionError("morning review should fail closed without pm-plan.json")


def test_start_day_runs_medium_first_cycle_supervised(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)
    loopctl.approve_command("demo", repo, "primary-surface", ["src/**"], ["git diff --check"])
    calls = []

    monkeypatch.setattr(loopctl, "cycle", lambda project, supervised=False: calls.append((project, supervised)) or {
        "run_id": "demo-1",
        "status": "merged",
        "waiting_for_human": [],
    })
    monkeypatch.setattr(loopctl, "load_scheduler", lambda project: calls.append(("load", project)))
    monkeypatch.setattr(loopctl, "send_notification", lambda *args, **kwargs: False)

    loopctl.start_day_command(["demo"])

    assert ("demo", True) in calls
    assert ("load", "demo") in calls
    state = loopctl.load_state()["projects"]["demo"]
    assert state["loop_job"]["state"] == "active"


def test_evening_writes_scorecard_and_daily_report(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)
    approvals = {
        "date": loopctl.today_date(),
        "approved": {"demo": {"approved_at": "now"}},
        "rejected": {},
    }
    loopctl.write_approvals(approvals)
    monkeypatch.setattr(loopctl, "send_notification", lambda *args, **kwargs: False)

    loopctl.evening_command(["demo"])

    assert (engine / "evening-scorecards" / "latest.md").exists()
    assert (engine / "reports" / "daily" / f"{loopctl.today_date()}.md").exists()


def test_evening_without_project_pauses_all_active_registered_loops(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)
    loopctl.start_loop("demo")
    assert loopctl.control_state("demo") == "active"
    monkeypatch.setattr(loopctl, "send_notification", lambda *args, **kwargs: False)

    loopctl.evening_command(None)

    assert loopctl.control_state("demo") == "paused"


def test_project_public_refs_do_not_expose_absolute_paths(tmp_path):
    repo = tmp_path / "repo"
    contract = repo / ".loop" / "contract.yaml"
    cfg = {
        "name": "Demo",
        "repo_path": str(repo),
        "github_repo": "owner/demo",
        "pilot_branch": "loop/demo-pilot",
        "contract_path": str(contract),
    }

    refs = loopctl.project_public_refs(cfg)

    assert refs == {
        "github_repo": "owner/demo",
        "pilot_branch": "loop/demo-pilot",
        "contract": ".loop/contract.yaml",
    }
    assert str(tmp_path) not in "\n".join(refs.values())


def test_setup_writes_config_and_prompts_gh_action(monkeypatch, tmp_path, capsys):
    repo = make_repo(tmp_path)
    patch_engine(monkeypatch, tmp_path, repo)
    monkeypatch.setattr(loopctl.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(loopctl, "gh_auth_ok", lambda: False)
    monkeypatch.setattr(loopctl.sys.stdin, "isatty", lambda: False)

    loopctl.setup_command(
        yes=True,
        provider="claude",
        linear_api_key_file=str(tmp_path / "linear-key"),
        notify_mode="none",
        webhook_url_file=None,
    )

    output = capsys.readouterr().out
    assert "SETUP_ACTION run: gh auth login" in output
    config = json.loads((tmp_path / "config" / "config.json").read_text())
    assert config["default_provider"] == "claude"
    assert config["linear"]["api_key_file"].endswith("linear-key")


def test_notify_none_logs_without_sending(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)
    loopctl.save_config({"notifications": {"mode": "none"}})

    assert loopctl.send_notification("test", "Title", "Body") is False
    assert (engine / "logs" / "notifications.jsonl").exists()
