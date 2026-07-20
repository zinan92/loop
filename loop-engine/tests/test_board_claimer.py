import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest


ENGINE = pathlib.Path(__file__).resolve().parents[1]
BIN = ENGINE / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

_spec = importlib.util.spec_from_file_location("board_claimer", BIN / "board_claimer.py")
board_claimer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = board_claimer
_spec.loader.exec_module(board_claimer)


def _item(number, status="Todo"):
    return board_claimer.BoardItem(
        item_id=f"item-{number}",
        content_id=f"issue-{number}",
        repo="zinan92/tokenpulse",
        number=number,
        title=f"Smoke {number}",
        url=f"https://github.com/zinan92/tokenpulse/issues/{number}",
        status=status,
        assignees=(),
    )


def _snapshot(*items):
    return board_claimer.BoardSnapshot(
        project_id="project-3",
        title="Dev Queue",
        url="https://github.com/users/zinan92/projects/3",
        status_field_id="status-field",
        status_options={"Todo": "todo", "In Progress": "progress"},
        items=tuple(items),
    )


class RecordingClaimer(board_claimer.BoardClaimer):
    def __init__(self, tmp_path, board, fail_issue=None):
        super().__init__(
            owner="zinan92",
            project_number=3,
            actor="park-ai-bot",
            wip_limit=3,
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path,
        )
        self.board = board
        self.fail_issue = fail_issue
        self.events = []

    def verify_actor(self):
        self.events.append(("actor", self.actor))

    def snapshot(self):
        return self.board

    def existing_open_pr(self, item):
        del item
        return None

    def claim(self, board, item, position, run_id):
        del board, run_id
        self.events.append(("claim", item.number, position))

    def execute(self, item, run_id):
        del run_id
        self.events.append(("execute", item.number))
        if item.number == self.fail_issue:
            raise RuntimeError("synthetic failure")
        return f"https://github.com/zinan92/tokenpulse/pull/{item.number}"

    def release(self, board, item, reason):
        del board
        self.events.append(("release", item.number, reason))


def test_parse_board_preserves_todo_order_and_status():
    payload = {
        "data": {"user": {"projectV2": {
            "id": "project-3",
            "title": "Dev Queue",
            "url": "https://github.com/users/zinan92/projects/3",
            "fields": {"nodes": [{
                "id": "status-field",
                "name": "Status",
                "options": [
                    {"id": "todo", "name": "Todo"},
                    {"id": "progress", "name": "In Progress"},
                ],
            }]},
            "items": {"nodes": [
                {
                    "id": "item-31",
                    "content": {"id": "issue-31", "number": 31, "title": "First", "url": "u31", "repository": {"nameWithOwner": "zinan92/tokenpulse"}, "assignees": {"nodes": []}},
                    "fieldValues": {"nodes": [{"name": "Todo", "optionId": "todo", "field": {"id": "status-field", "name": "Status"}}]},
                },
                {
                    "id": "item-32",
                    "content": {"id": "issue-32", "number": 32, "title": "Second", "url": "u32", "repository": {"nameWithOwner": "zinan92/tokenpulse"}, "assignees": {"nodes": [{"login": "park-ai-bot"}]}},
                    "fieldValues": {"nodes": [{"name": "In Progress", "optionId": "progress", "field": {"id": "status-field", "name": "Status"}}]},
                },
            ]},
        }}}}

    board = board_claimer.parse_board(payload)

    assert [item.number for item in board.items] == [31, 32]
    assert [item.status for item in board.items] == ["Todo", "In Progress"]
    assert board.items[1].assignees == ("park-ai-bot",)


def test_run_once_claims_todo_top_to_bottom_with_wip_limit(tmp_path):
    claimer = RecordingClaimer(
        tmp_path,
        _snapshot(_item(20, "In Progress"), _item(31), _item(32), _item(33)),
    )

    payload = claimer.run_once()

    assert [(event[1], event[2]) for event in claimer.events if event[0] == "claim"] == [(31, 2), (32, 3)]
    assert [result["status"] for result in payload["results"]] == ["awaiting_human_merge", "awaiting_human_merge"]
    assert payload["in_progress_before"] == 1
    assert payload["errors"] == []


def test_failure_releases_claim_and_does_not_skip_ahead(tmp_path):
    claimer = RecordingClaimer(tmp_path, _snapshot(_item(31), _item(32), _item(33)), fail_issue=31)

    payload = claimer.run_once()

    assert ("claim", 31, 1) in claimer.events
    assert ("execute", 31) in claimer.events
    assert any(event[0:2] == ("release", 31) for event in claimer.events)
    assert not any(event[0] == "claim" and event[1] in {32, 33} for event in claimer.events)
    assert payload["state"] == "needs_human"


def test_pr_body_has_handoff_sections_and_closes(tmp_path):
    issue = tmp_path / "issue.md"
    issue.write_text("# Tiny doc\n\n## Outcome\nDocument the smoke marker.\n")

    body = board_claimer.pr_body(issue, ["docs/smoke.md"], ["python3 -m pytest tests/"], 31)

    assert "## What" in body
    assert "## Why" in body
    assert "## Validation" in body
    assert "Closes #31" in body


def test_make_worktree_can_start_from_fetched_remote_default(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(board_claimer.loopctl, "ENGINE_ROOT", tmp_path / "engine")

    def fake_run(command, cwd=None, **kwargs):
        del kwargs
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(board_claimer.loopctl, "run", fake_run)

    _, branch = board_claimer.loopctl.make_worktree(
        "tokenpulse", tmp_path / "repo", "main", "board-issue-31", base_ref="origin/main"
    )

    assert branch == "loop/tokenpulse-board-issue-31"
    assert calls[-1][0][-1] == "origin/main"


def test_changed_files_expands_untracked_directories(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, cwd=None, **kwargs):
        del kwargs
        captured["command"] = command
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(command, 0, "?? docs/board-claimer-smoke-b.md\n", "")

    monkeypatch.setattr(board_claimer.loopctl, "run", fake_run)

    assert board_claimer.loopctl.changed_files(tmp_path) == ["docs/board-claimer-smoke-b.md"]
    assert captured["command"] == ["git", "status", "--porcelain", "--untracked-files=all"]


def test_board_claimer_source_has_no_merge_command():
    source = (BIN / "board_claimer.py").read_text()

    assert '"pr", "merge"' not in source
    assert "autoMergeRequest" in source


def test_execute_cleans_worktree_when_worker_raises(monkeypatch, tmp_path):
    item = _item(31)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    claimer = board_claimer.BoardClaimer(
        owner="zinan92",
        project_number=3,
        actor="park-ai-bot",
        wip_limit=3,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    cleanup = {}
    monkeypatch.setattr(claimer, "existing_open_pr", lambda value: None)
    monkeypatch.setattr(claimer, "fetch_issue", lambda value: {
        "title": value.title,
        "body": "## Outcome\nTiny.\n\n## Risk\nlow\n",
    })
    monkeypatch.setattr(claimer, "default_branch", lambda repo: "main")
    monkeypatch.setattr(board_claimer, "read_registry", lambda path: {})
    monkeypatch.setattr(board_claimer, "project_config_for_repo", lambda registry, repo: (
        "tokenpulse",
        {"repo_path": str(repo_path), "pilot_branch": "main"},
    ))
    monkeypatch.setattr(board_claimer.loopctl, "parse_issue_risk", lambda path: "low")
    monkeypatch.setattr(board_claimer.loopctl, "screen_blocked", lambda path, categories: [])
    monkeypatch.setattr(board_claimer.loopctl, "trusted_verification_commands", lambda path, cfg: [])

    def failing_worker(project, cfg, task_dir, issue_path, branch_suffix, base_ref=None):
        del project, cfg, task_dir, issue_path, base_ref
        path = board_claimer.loopctl.ENGINE_ROOT / "worktrees" / branch_suffix
        path.mkdir(parents=True, exist_ok=True)
        raise RuntimeError("worker failed after worktree creation")

    monkeypatch.setattr(board_claimer.loopctl, "worker", failing_worker)
    monkeypatch.setattr(
        claimer,
        "cleanup_worktree",
        lambda repo, worktree: cleanup.update({"repo": repo, "worktree": worktree}),
    )
    monkeypatch.setattr(
        claimer,
        "cleanup_failed_branch",
        lambda repo, branch: cleanup.update({"branch": branch}),
    )

    with pytest.raises(RuntimeError, match="worker failed"):
        claimer.execute(item, "run-1")

    assert cleanup["repo"] == repo_path
    assert cleanup["worktree"].name == "board-tokenpulse-issue-31"
    assert cleanup["branch"] == "loop/tokenpulse-board-tokenpulse-issue-31"


def test_existing_open_pr_requires_exact_close_actor_and_head(tmp_path):
    class FakeGitHub:
        def json(self, args, cwd=None):
            del args, cwd
            return [
                {"url": "wrong-number", "state": "OPEN", "body": "Closes #310", "headRefName": "branch-a", "baseRefName": "main", "author": {"login": "park-ai-bot"}},
                {"url": "wrong-actor", "state": "OPEN", "body": "Closes #31", "headRefName": "branch-b", "baseRefName": "main", "author": {"login": "someone-else"}},
                {"url": "https://github.com/zinan92/tokenpulse/pull/41", "state": "OPEN", "body": "## Validation\n\nCloses #31\n", "headRefName": "branch-c", "baseRefName": "main", "author": {"login": "park-ai-bot"}},
            ]

    claimer = board_claimer.BoardClaimer(
        owner="zinan92",
        project_number=3,
        actor="park-ai-bot",
        wip_limit=3,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        github=FakeGitHub(),
    )

    assert claimer.existing_open_pr(_item(31))["headRefName"] == "branch-c"


def test_seed_stack_base_resets_stale_state_and_finds_chain_tip(monkeypatch, tmp_path):
    claimer = board_claimer.BoardClaimer(
        owner="zinan92",
        project_number=3,
        actor="park-ai-bot",
        wip_limit=3,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
    )
    issue_28 = _item(28, "In Progress")
    issue_29 = _item(29, "In Progress")
    issue_28 = board_claimer.BoardItem(**{**issue_28.__dict__, "assignees": ("park-ai-bot",)})
    issue_29 = board_claimer.BoardItem(**{**issue_29.__dict__, "assignees": ("park-ai-bot",)})
    details = {
        28: {"url": "pr-31", "headRefName": "issue-28", "baseRefName": "main"},
        29: {"url": "pr-32", "headRefName": "issue-29", "baseRefName": "issue-28"},
    }
    monkeypatch.setattr(claimer, "existing_open_pr", lambda item: details[item.number])
    claimer.stack_base_by_repo = {"zinan92/tokenpulse": "deleted-old-tip"}

    claimer.seed_stack_bases([issue_29, issue_28])

    assert claimer.stack_base_by_repo == {"zinan92/tokenpulse": "issue-29"}
    claimer.seed_stack_bases([])
    assert claimer.stack_base_by_repo == {}


def test_seed_stack_base_rejects_disconnected_bot_prs(monkeypatch, tmp_path):
    claimer = board_claimer.BoardClaimer(
        owner="zinan92",
        project_number=3,
        actor="park-ai-bot",
        wip_limit=3,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
    )
    items = []
    for number in (28, 29):
        item = _item(number, "In Progress")
        items.append(board_claimer.BoardItem(**{**item.__dict__, "assignees": ("park-ai-bot",)}))
    monkeypatch.setattr(claimer, "existing_open_pr", lambda item: {
        "url": f"pr-{item.number}",
        "headRefName": f"issue-{item.number}",
        "baseRefName": "main",
    })

    with pytest.raises(RuntimeError, match="unique open PR stack tip"):
        claimer.seed_stack_bases(items)


def test_facade_dispatches_board_claimer(monkeypatch):
    facade_spec = importlib.util.spec_from_file_location("loop_facade_board", BIN / "loop_facade.py")
    facade = importlib.util.module_from_spec(facade_spec)
    facade_spec.loader.exec_module(facade)
    captured = {}

    def fake_run(command):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(facade.subprocess, "run", fake_run)
    monkeypatch.setattr(facade.sys, "argv", ["loop", "board-claimer", "status", "--project-number", "3"])

    assert facade.main() == 0
    assert captured["command"][-3:] == ["status", "--project-number", "3"]
