import importlib.util
import json
import pathlib
import subprocess
import sys


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


def test_morning_review_writes_cross_project_board(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    engine = patch_engine(monkeypatch, tmp_path, repo)

    result = loopctl.write_morning_review(["demo"])

    assert result["paths"]["latest"].exists()
    text = result["paths"]["latest"].read_text()
    assert "Daily PM Review" in text
    assert "Ranked Development Tasks" in text
    assert "demo" in text
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
