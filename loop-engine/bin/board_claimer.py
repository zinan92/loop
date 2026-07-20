#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import loopctl


HERE = Path(__file__).resolve().parent
ENGINE_ROOT = HERE.parent
DEFAULT_REGISTRY = ENGINE_ROOT / "registry.json"
DEFAULT_RUNTIME_ROOT = ENGINE_ROOT
DEFAULT_OWNER = "zinan92"
DEFAULT_PROJECT_NUMBER = 3
DEFAULT_ACTOR = "park-ai-bot"
DEFAULT_WIP_LIMIT = 3
DEFAULT_POLL_SECONDS = 60
_STOP_REQUESTED = False


PROJECT_QUERY = """
query($owner:String!,$number:Int!){
  user(login:$owner){
    projectV2(number:$number){
      id number title url
      fields(first:50){nodes{
        ... on ProjectV2SingleSelectField{id name options{id name}}
      }}
      items(first:100){nodes{
        id type
        content{
          ... on Issue{id number title url state repository{nameWithOwner} assignees(first:20){nodes{login}}}
        }
        fieldValues(first:20){nodes{
          ... on ProjectV2ItemFieldSingleSelectValue{
            name optionId field{... on ProjectV2SingleSelectField{id name}}
          }
        }}
      }}
    }
  }
}
"""

UPDATE_STATUS_MUTATION = """
mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){
  updateProjectV2ItemFieldValue(input:{
    projectId:$project,itemId:$item,fieldId:$field,
    value:{singleSelectOptionId:$option}
  }){projectV2Item{id}}
}
"""


@dataclass(frozen=True)
class BoardItem:
    item_id: str
    content_id: str
    repo: str
    number: int
    title: str
    url: str
    status: str | None
    assignees: tuple[str, ...]


@dataclass(frozen=True)
class BoardSnapshot:
    project_id: str
    title: str
    url: str
    status_field_id: str
    status_options: dict[str, str]
    items: tuple[BoardItem, ...]


class CommandFailed(RuntimeError):
    pass


class GitHub:
    def run(self, args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["gh", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown gh failure"
            raise CommandFailed(f"gh {' '.join(args[:3])} failed: {message}")
        return result

    def json(self, args: list[str], cwd: Path | None = None) -> dict | list:
        result = self.run(args, cwd=cwd)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CommandFailed(f"gh returned non-JSON output for {' '.join(args[:3])}") from exc

    def graphql(self, query: str, variables: dict[str, object]) -> dict:
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            args.extend(["-F", f"{key}={value}"])
        payload = self.json(args)
        if not isinstance(payload, dict):
            raise CommandFailed("GraphQL response was not an object")
        errors = payload.get("errors") or []
        if errors:
            raise CommandFailed("GraphQL error: " + "; ".join(str(item.get("message")) for item in errors))
        return payload


def parse_board(payload: dict) -> BoardSnapshot:
    project = (((payload.get("data") or {}).get("user") or {}).get("projectV2"))
    if not project:
        raise RuntimeError("Dev Queue project is not visible to the current GitHub actor")
    status_field = None
    for field in ((project.get("fields") or {}).get("nodes") or []):
        if field and field.get("name") == "Status" and field.get("id"):
            status_field = field
            break
    if not status_field:
        raise RuntimeError("Dev Queue has no single-select Status field")
    options = {
        str(option["name"]): str(option["id"])
        for option in status_field.get("options") or []
        if option.get("name") and option.get("id")
    }
    for required in ("Todo", "In Progress"):
        if required not in options:
            raise RuntimeError(f"Dev Queue Status is missing required option: {required}")
    items: list[BoardItem] = []
    for node in ((project.get("items") or {}).get("nodes") or []):
        content = node.get("content") or {}
        repo = ((content.get("repository") or {}).get("nameWithOwner"))
        if not repo or not content.get("number") or not content.get("id"):
            continue
        status = None
        for value in ((node.get("fieldValues") or {}).get("nodes") or []):
            field = (value or {}).get("field") or {}
            if field.get("name") == "Status":
                status = value.get("name")
                break
        items.append(BoardItem(
            item_id=str(node["id"]),
            content_id=str(content["id"]),
            repo=str(repo),
            number=int(content["number"]),
            title=str(content.get("title") or f"Issue {content['number']}"),
            url=str(content.get("url") or ""),
            status=str(status) if status else None,
            assignees=tuple(
                str(person.get("login"))
                for person in ((content.get("assignees") or {}).get("nodes") or [])
                if person.get("login")
            ),
        ))
    return BoardSnapshot(
        project_id=str(project["id"]),
        title=str(project.get("title") or "Dev Queue"),
        url=str(project.get("url") or ""),
        status_field_id=str(status_field["id"]),
        status_options=options,
        items=tuple(items),
    )


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "item"


def read_registry(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"loop registry not found: {path}")
    return json.loads(path.read_text())


def project_config_for_repo(registry: dict, repo: str) -> tuple[str, dict]:
    for project, cfg in (registry.get("projects") or {}).items():
        if str(cfg.get("github_repo") or "").lower() == repo.lower():
            return str(project), dict(cfg)
    raise RuntimeError(f"no loop registry entry maps to GitHub repo {repo}")


def issue_markdown(issue: dict) -> str:
    return f"# {issue['title']}\n\n{str(issue.get('body') or '').strip()}\n"


def pr_body(issue_path: Path, changed: list[str], commands: list[str], issue_number: int) -> str:
    sections = loopctl.issue_sections(issue_path)
    outcome = loopctl.section_excerpt(sections, "outcome", max_chars=600)
    if outcome == "-":
        outcome = loopctl.section_excerpt(sections, "goal", max_chars=600)
    what = "\n".join(f"- `{path}`" for path in changed) or "- Complete the issue contract."
    validation = "\n".join(f"- `{command}` — passed" for command in commands)
    return (
        "## What\n\n"
        f"{what}\n\n"
        "## Why\n\n"
        f"{outcome}\n\n"
        "## Validation\n\n"
        f"{validation}\n\n"
        f"Closes #{issue_number}\n"
    )


class BoardClaimer:
    def __init__(
        self,
        owner: str,
        project_number: int,
        actor: str,
        wip_limit: int,
        registry_path: Path,
        runtime_root: Path,
        worker_reasoning_effort: str | None = None,
        github: GitHub | None = None,
    ):
        if wip_limit < 1:
            raise ValueError("wip_limit must be positive")
        self.owner = owner
        self.project_number = project_number
        self.actor = actor
        self.wip_limit = wip_limit
        self.registry_path = registry_path
        self.runtime_root = runtime_root
        self.worker_reasoning_effort = worker_reasoning_effort
        self.github = github or GitHub()
        self.stack_base_by_repo: dict[str, str] = {}
        self.log_dir = runtime_root / "logs" / "board-claimer"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def state_path(self) -> Path:
        return self.runtime_root / "logs" / "board-claimer-state.json"

    @property
    def pid_path(self) -> Path:
        return self.runtime_root / "logs" / "board-claimer.pid"

    def write_state(self, **updates: object) -> None:
        state: dict = {}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                state = {}
        state.update({
            "owner": self.owner,
            "project_number": self.project_number,
            "actor": self.actor,
            "wip_limit": self.wip_limit,
            "worker_reasoning_effort": self.worker_reasoning_effort,
            "updated_at": now_iso(),
            **updates,
        })
        loopctl.write_json(self.state_path, state)

    def snapshot(self) -> BoardSnapshot:
        return parse_board(self.github.graphql(PROJECT_QUERY, {
            "owner": self.owner,
            "number": self.project_number,
        }))

    def verify_actor(self) -> None:
        user = self.github.json(["api", "user"])
        login = str((user or {}).get("login") or "") if isinstance(user, dict) else ""
        if login != self.actor:
            raise RuntimeError(f"GitHub actor mismatch: expected {self.actor}, got {login or 'unknown'}")

    def set_status(self, board: BoardSnapshot, item: BoardItem, status: str) -> None:
        option = board.status_options.get(status)
        if not option:
            raise RuntimeError(f"Status option is unavailable: {status}")
        self.github.graphql(UPDATE_STATUS_MUTATION, {
            "project": board.project_id,
            "item": item.item_id,
            "field": board.status_field_id,
            "option": option,
        })

    def issue_comment(self, item: BoardItem, body: str, check: bool = True) -> None:
        self.github.run([
            "issue", "comment", str(item.number), "--repo", item.repo, "--body", body,
        ], check=check)

    def assign(self, item: BoardItem, add: bool, check: bool = True) -> None:
        flag = "--add-assignee" if add else "--remove-assignee"
        self.github.run([
            "issue", "edit", str(item.number), "--repo", item.repo, flag, self.actor,
        ], check=check)

    def claim(self, board: BoardSnapshot, item: BoardItem, position: int, run_id: str) -> None:
        assigned = False
        try:
            if self.actor not in item.assignees:
                self.assign(item, True)
                assigned = True
            self.issue_comment(
                item,
                f"Claimed by **{self.actor}** via loop board-claimer.\n\n"
                f"- Queue: `Todo`\n- Position: `{position}`\n- Run: `{run_id}`\n"
                f"- Signature: `{self.actor} / Mini execution`",
            )
            self.set_status(board, item, "In Progress")
        except Exception:
            try:
                self.set_status(board, item, "Todo")
            except Exception:
                pass
            if assigned:
                self.assign(item, False, check=False)
            raise

    def release(self, board: BoardSnapshot, item: BoardItem, reason: str) -> None:
        self.set_status(board, item, "Todo")
        self.assign(item, False, check=False)
        self.issue_comment(
            item,
            f"Claim released by **{self.actor}** because execution did not reach an open PR.\n\n"
            f"- Reason: `{reason[:400]}`\n- Status restored to: `Todo`",
            check=False,
        )

    def fetch_issue(self, item: BoardItem) -> dict:
        payload = self.github.json([
            "issue", "view", str(item.number), "--repo", item.repo,
            "--json", "id,number,title,body,url,state,assignees",
        ])
        if not isinstance(payload, dict) or payload.get("state") != "OPEN":
            raise RuntimeError(f"board item is not an open issue: {item.url}")
        return payload

    def existing_open_pr(self, item: BoardItem) -> dict | None:
        payload = self.github.json([
            "pr", "list", "--repo", item.repo, "--state", "open",
            "--search", f"Closes #{item.number} in:body",
            "--json", "url,number,state,author,headRefName,baseRefName,body",
        ])
        if not isinstance(payload, list):
            return None
        for pr in payload:
            author = ((pr.get("author") or {}).get("login")) if isinstance(pr, dict) else None
            closes_exactly = re.search(
                rf"(?mi)^\s*Closes\s+#{item.number}\s*$",
                str(pr.get("body") or ""),
            )
            if (
                pr.get("state") == "OPEN"
                and pr.get("url")
                and pr.get("headRefName")
                and author == self.actor
                and closes_exactly
            ):
                return dict(pr)
        return None

    def seed_stack_bases(self, in_progress: list[BoardItem]) -> None:
        self.stack_base_by_repo = {}
        open_by_repo: dict[str, list[dict]] = {}
        for item in in_progress:
            if self.actor not in item.assignees:
                continue
            existing = self.existing_open_pr(item)
            if existing:
                open_by_repo.setdefault(item.repo, []).append(existing)
        for repo, pull_requests in open_by_repo.items():
            by_head = {str(pr["headRefName"]): pr for pr in pull_requests}
            referenced_bases = {
                str(pr.get("baseRefName") or "")
                for pr in pull_requests
                if str(pr.get("baseRefName") or "") in by_head
            }
            tips = set(by_head) - referenced_bases
            if len(tips) != 1:
                raise RuntimeError(f"cannot resolve a unique open PR stack tip for {repo}")
            tip = next(iter(tips))
            visited: set[str] = set()
            cursor = tip
            while cursor in by_head and cursor not in visited:
                visited.add(cursor)
                cursor = str(by_head[cursor].get("baseRefName") or "")
            if len(visited) != len(by_head):
                raise RuntimeError(f"open bot PRs do not form one stack for {repo}")
            self.stack_base_by_repo[repo] = tip

    def review_only(
        self,
        project: str,
        cfg: dict,
        task_dir: Path,
        issue_path: Path,
        worktree_path: Path,
    ) -> str:
        memory_path = self.runtime_root / "knowledge" / project / "STATE.md"
        memory = memory_path.read_text() if memory_path.exists() else "No prior loop memory."
        values = {
            "PROJECT_NAME": str(cfg.get("name") or project),
            "WORKTREE_PATH": str(worktree_path),
            "ISSUE_PATH": str(issue_path),
            "RUN_DIR": str(task_dir),
            "LOOP_MEMORY": memory[:6000],
            "OUTPUT_LANGUAGE": str(cfg.get("output_language") or "English"),
        }
        report = task_dir / "reviewer-report.md"
        report.unlink(missing_ok=True)
        loopctl.agent_exec(
            loopctl.render_template("reviewer.md", values),
            worktree_path,
            task_dir / "reviewer-last-message.md",
            task_dir,
            cfg.get("agents", {}).get("reviewer"),
        )
        if not report.exists():
            raise RuntimeError("reviewer did not create reviewer-report.md")
        match = re.search(r"^REVIEW_STATUS:\s*(pass|fail|needs_human)\s*$", report.read_text(), re.M)
        return match.group(1) if match else "needs_human"

    def default_branch(self, repo: str) -> str:
        payload = self.github.json(["repo", "view", repo, "--json", "defaultBranchRef"])
        branch = (((payload or {}).get("defaultBranchRef") or {}).get("name")) if isinstance(payload, dict) else None
        if not branch:
            raise RuntimeError(f"could not resolve default branch for {repo}")
        return str(branch)

    def create_pr(
        self,
        item: BoardItem,
        base: str,
        branch: str,
        task_dir: Path,
        issue_path: Path,
        changed: list[str],
        commands: list[str],
    ) -> str:
        body_path = task_dir / "pull-request.md"
        body_path.write_text(pr_body(issue_path, changed, commands, item.number))
        result = self.github.run([
            "pr", "create", "--repo", item.repo, "--base", base, "--head", branch,
            "--title", f"[board] {item.title}", "--body-file", str(body_path),
        ], cwd=Path.cwd())
        pr_url = result.stdout.strip()
        if not re.match(r"^https://github\.com/[^/]+/[^/]+/pull/\d+$", pr_url):
            raise RuntimeError("gh pr create did not return a PR URL")
        (task_dir / "github-pr-url.txt").write_text(pr_url + "\n")
        details = self.github.json([
            "pr", "view", pr_url,
            "--json", "url,state,isDraft,mergedAt,autoMergeRequest,author,headRefName,baseRefName",
        ])
        author = ((details.get("author") or {}).get("login")) if isinstance(details, dict) else None
        if (
            not isinstance(details, dict)
            or details.get("state") != "OPEN"
            or details.get("mergedAt") is not None
            or details.get("autoMergeRequest") is not None
            or author != self.actor
        ):
            raise RuntimeError("opened PR failed the actor/open/no-auto-merge contract")
        return pr_url

    def cleanup_worktree(self, repo_path: Path, worktree_path: Path | None) -> None:
        if worktree_path and worktree_path.exists():
            loopctl.run(["git", "worktree", "remove", "--force", str(worktree_path)], cwd=repo_path, check=False)
        loopctl.run(["git", "worktree", "prune"], cwd=repo_path, check=False)

    def cleanup_failed_branch(self, repo_path: Path, branch: str | None) -> None:
        if not branch:
            return
        remote = loopctl.run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=repo_path, check=False)
        if remote.returncode == 0:
            loopctl.run(["git", "push", "origin", "--delete", branch], cwd=repo_path, check=False)
        loopctl.run(["git", "branch", "-D", branch], cwd=repo_path, check=False)

    def execute(self, item: BoardItem, run_id: str) -> str:
        existing = self.existing_open_pr(item)
        if existing:
            self.stack_base_by_repo[item.repo] = str(existing["headRefName"])
            return str(existing["url"])
        issue = self.fetch_issue(item)
        registry = read_registry(self.registry_path)
        project, cfg = project_config_for_repo(registry, item.repo)
        if self.worker_reasoning_effort:
            agents = dict(cfg.get("agents") or {})
            worker_agent = dict(agents.get("worker") or {})
            worker_agent["reasoning_effort"] = self.worker_reasoning_effort
            agents["worker"] = worker_agent
            cfg["agents"] = agents
        repo_path = Path(str(cfg.get("repo_path") or "")).expanduser().resolve()
        if not repo_path.is_dir():
            raise RuntimeError(f"registered repo path is unavailable: {repo_path}")
        base = self.stack_base_by_repo.get(item.repo) or self.default_branch(item.repo)
        cfg["pilot_branch"] = base
        task_dir = self.log_dir / run_id / f"{safe_slug(item.repo)}-issue-{item.number}"
        task_dir.mkdir(parents=True, exist_ok=True)
        issue_path = task_dir / f"issue-{item.number}.md"
        issue_path.write_text(issue_markdown(issue))
        (task_dir / "github-issue-url.txt").write_text(item.url + "\n")

        if loopctl.parse_issue_risk(issue_path) != "low":
            raise RuntimeError("board-claimer auto-executes only issues marked Risk: low")
        hits = loopctl.screen_blocked(issue_path, cfg.get("blocked_categories") or [])
        if hits:
            raise RuntimeError("issue matched blocked categories: " + ", ".join(hits))
        commands = loopctl.trusted_verification_commands(issue_path, cfg)
        branch_suffix = f"board-{safe_slug(item.repo.split('/')[-1])}-issue-{item.number}"
        # Resolve cleanup coordinates before worker() starts. worker() may raise
        # after creating the worktree but before it can return the tuple.
        worktree_path: Path | None = loopctl.ENGINE_ROOT / "worktrees" / branch_suffix
        branch: str | None = f"loop/{project}-{branch_suffix}"
        succeeded = False
        git_identity = {
            "GIT_AUTHOR_NAME": self.actor,
            "GIT_AUTHOR_EMAIL": f"{self.actor}@users.noreply.github.com",
            "GIT_COMMITTER_NAME": self.actor,
            "GIT_COMMITTER_EMAIL": f"{self.actor}@users.noreply.github.com",
        }
        previous_identity = {key: os.environ.get(key) for key in git_identity}
        os.environ.update(git_identity)
        try:
            worktree_path, branch = loopctl.worker(
                project,
                cfg,
                task_dir,
                issue_path,
                branch_suffix,
                base_ref=f"origin/{base}",
            )
            if self.review_only(project, cfg, task_dir, issue_path, worktree_path) != "pass":
                raise RuntimeError("independent reviewer did not pass the task")
            changed_path = task_dir / "worker-git-status.txt"
            changed = [line for line in changed_path.read_text().splitlines() if line.strip()]
            pr_url = self.create_pr(item, base, branch, task_dir, issue_path, changed, commands)
            self.stack_base_by_repo[item.repo] = branch
            succeeded = True
            return pr_url
        finally:
            for key, value in previous_identity.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            self.cleanup_worktree(repo_path, worktree_path)
            if not succeeded and not (task_dir / "github-pr-url.txt").exists():
                self.cleanup_failed_branch(repo_path, branch)

    def run_once(self) -> dict:
        self.verify_actor()
        board = self.snapshot()
        in_progress = [item for item in board.items if item.status == "In Progress"]
        capacity = max(0, self.wip_limit - len(in_progress))
        todos = [item for item in board.items if item.status == "Todo"]
        self.seed_stack_bases(in_progress)
        run_id = "board-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        results: list[dict] = []
        errors: list[dict] = []
        self.write_state(state="running", run_id=run_id, capacity=capacity)
        ordered_todos = [
            (position, item)
            for position, item in enumerate(board.items, start=1)
            if item.status == "Todo"
        ]
        for position, item in ordered_todos[:capacity]:
            if _STOP_REQUESTED:
                break
            claimed = False
            try:
                self.claim(board, item, position, run_id)
                claimed = True
                pr_url = self.execute(item, run_id)
                results.append({
                    "repo": item.repo,
                    "issue": item.url,
                    "position": position,
                    "status": "awaiting_human_merge",
                    "pr": pr_url,
                })
            except Exception as exc:
                if claimed:
                    self.release(board, item, str(exc))
                errors.append({"issue": item.url, "position": position, "error": str(exc)})
                break
        state = "stopped" if _STOP_REQUESTED else ("needs_human" if errors else "idle")
        payload = {
            "run_id": run_id,
            "state": state,
            "project": board.url,
            "wip_limit": self.wip_limit,
            "in_progress_before": len(in_progress),
            "todo_before": len(todos),
            "results": results,
            "errors": errors,
            "stopped": _STOP_REQUESTED,
        }
        self.write_state(**payload)
        return payload


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop_requested(signum: int, frame: object) -> None:
    del signum, frame
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def build_claimer(args: argparse.Namespace) -> BoardClaimer:
    return BoardClaimer(
        owner=args.owner,
        project_number=args.project_number,
        actor=args.actor,
        wip_limit=args.wip_limit,
        registry_path=Path(args.registry).expanduser().resolve(),
        runtime_root=Path(args.runtime_root).expanduser().resolve(),
        worker_reasoning_effort=args.worker_reasoning_effort,
    )


def start_daemon(args: argparse.Namespace) -> int:
    claimer = build_claimer(args)
    if claimer.pid_path.exists():
        try:
            pid = int(claimer.pid_path.read_text().strip())
        except ValueError:
            pid = 0
        if pid and process_alive(pid):
            print(f"BOARD_CLAIMER_ALREADY_RUNNING pid={pid}")
            return 0
    log_path = claimer.runtime_root / "logs" / "board-claimer-daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(Path(__file__).resolve()), "daemon",
        "--owner", args.owner,
        "--project-number", str(args.project_number),
        "--actor", args.actor,
        "--wip-limit", str(args.wip_limit),
        "--registry", str(Path(args.registry).expanduser().resolve()),
        "--runtime-root", str(Path(args.runtime_root).expanduser().resolve()),
        "--poll-seconds", str(args.poll_seconds),
    ]
    if args.worker_reasoning_effort:
        command.extend(["--worker-reasoning-effort", args.worker_reasoning_effort])
    with log_path.open("ab") as handle:
        proc = subprocess.Popen(
            command,
            cwd=ENGINE_ROOT.parent,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
            env=os.environ.copy(),
        )
    claimer.pid_path.write_text(str(proc.pid) + "\n")
    claimer.write_state(state="active", daemon_pid=proc.pid)
    print(f"BOARD_CLAIMER_STARTED pid={proc.pid}")
    return 0


def stop_daemon(args: argparse.Namespace) -> int:
    claimer = build_claimer(args)
    if not claimer.pid_path.exists():
        claimer.write_state(state="stopped", daemon_pid=None)
        print("BOARD_CLAIMER_STOPPED already=true")
        return 0
    try:
        pid = int(claimer.pid_path.read_text().strip())
    except ValueError:
        pid = 0
    if pid and process_alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + 30
        while process_alive(pid) and time.time() < deadline:
            time.sleep(0.2)
        if process_alive(pid):
            raise RuntimeError(f"board-claimer did not stop cleanly within 30 seconds: pid={pid}")
    claimer.pid_path.unlink(missing_ok=True)
    claimer.write_state(state="stopped", daemon_pid=None)
    print(f"BOARD_CLAIMER_STOPPED pid={pid or '-'}")
    return 0


def daemon(args: argparse.Namespace) -> int:
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    claimer = build_claimer(args)
    claimer.pid_path.write_text(str(os.getpid()) + "\n")
    claimer.write_state(state="active", daemon_pid=os.getpid())
    try:
        while not _STOP_REQUESTED:
            payload = claimer.run_once()
            if payload.get("errors"):
                break
            claimer.write_state(state="active", daemon_pid=os.getpid())
            for _ in range(max(1, args.poll_seconds)):
                if _STOP_REQUESTED:
                    break
                time.sleep(1)
    finally:
        claimer.pid_path.unlink(missing_ok=True)
        claimer.write_state(state="stopped", daemon_pid=None)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Claim GitHub Projects Todo issues into the existing loop execution chain")
    root.add_argument("command", choices=["once", "start", "stop", "status", "daemon"])
    root.add_argument("--owner", default=DEFAULT_OWNER)
    root.add_argument("--project-number", type=int, default=DEFAULT_PROJECT_NUMBER)
    root.add_argument("--actor", default=DEFAULT_ACTOR)
    root.add_argument("--wip-limit", type=int, default=DEFAULT_WIP_LIMIT)
    root.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    root.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    root.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    root.add_argument("--worker-reasoning-effort", choices=["low", "medium", "high", "xhigh"])
    return root


def main() -> int:
    args = parser().parse_args()
    secrets_dir = os.environ.get("LOOP_SECRETS_DIR")
    if secrets_dir:
        secret_path = Path(secrets_dir).expanduser().resolve()
        loopctl.SECRETS_DIR = secret_path
        if secret_path not in loopctl.SECRET_DENY_DIRS:
            loopctl.SECRET_DENY_DIRS.append(secret_path)
    try:
        if args.command == "once":
            signal.signal(signal.SIGTERM, stop_requested)
            payload = build_claimer(args).run_once()
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 1 if payload.get("errors") else 0
        if args.command == "start":
            return start_daemon(args)
        if args.command == "stop":
            return stop_daemon(args)
        if args.command == "daemon":
            return daemon(args)
        claimer = build_claimer(args)
        state = json.loads(claimer.state_path.read_text()) if claimer.state_path.exists() else {"state": "stopped"}
        state["daemon_alive"] = bool(state.get("daemon_pid") and process_alive(int(state["daemon_pid"])))
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"BOARD_CLAIMER_BLOCKED {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
