#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ENGINE_ROOT / "registry.json"
STATE_PATH = ENGINE_ROOT / "state.json"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
DEFAULT_CADENCE_SECONDS = 60 * 60
LOCK_DIR = ENGINE_ROOT / "locks"
LAUNCH_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCH_LABEL_PREFIX = "com.agent-loop"
# Where the engine reads the operator's real secret values to scan worker diffs for
# leaks. Env-configurable (LOOP_SECRETS_DIR) so adopters point it at their own secret
# store instead of a hardcoded personal path; default ~/.config/loop/secrets.
SECRETS_DIR = Path(
    os.environ.get("LOOP_SECRETS_DIR", str(Path.home() / ".config" / "loop" / "secrets"))
).expanduser()
SECRET_DENY_DIRS = [
    SECRETS_DIR,
    Path.home() / ".ssh",
    Path.home() / ".aws",
    Path.home() / ".config" / "gh",
    Path.home() / ".config" / "gcloud",
    Path.home() / ".gnupg",
]
SECRET_DENY_FILES = [Path.home() / ".netrc"]
SECRET_ENV_EXACT = {
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "LINEAR_API_KEY",
    "OPENAI_API_KEY",
    "SSH_AUTH_SOCK",
}
SECRET_ENV_KEYWORDS = (
    "ACCESS_KEY",
    "API_KEY",
    "CREDENTIAL",
    "PASSWORD",
    "PASSPHRASE",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)
DEFAULT_LINEAR_TEAM_KEY = os.environ.get("LOOP_LINEAR_TEAM_KEY", "ENG")
DEFAULT_LINEAR_TEAM_NAME = os.environ.get("LOOP_LINEAR_TEAM_NAME", DEFAULT_LINEAR_TEAM_KEY)
DEFAULT_LINEAR_API_KEY_FILE = Path(
    os.environ.get(
        "LOOP_LINEAR_API_KEY_FILE",
        str(Path.home() / ".config" / "loop" / "linear-api-key"),
    )
).expanduser()
LEARNING_EVENT_MAX_BYTES = 1024 * 1024
LEARNING_EVENT_ROTATIONS = 3
DEFAULT_VALUE_THRESHOLD = 3
DEFAULT_MAX_NOOP_CYCLES = 2


class LoopBlocked(RuntimeError):
    def __init__(self, reason: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def plus_seconds(seconds: int) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "projects": {}}
    return load_json(STATE_PATH)


def save_state(state: dict) -> None:
    write_json(STATE_PATH, state)


def knowledge_paths(project: str) -> dict[str, Path]:
    root = ENGINE_ROOT / "knowledge" / project
    return {
        "dir": root,
        "state": root / "STATE.md",
        "events": root / "events.jsonl",
    }


def ensure_memory_state(project: str) -> None:
    paths = knowledge_paths(project)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    if not paths["state"].exists():
        paths["state"].write_text(
            f"# Loop Memory: {project}\n\n"
            "## Operating Rules\n"
            "- Issue verification commands must exactly match project-level trusted commands.\n"
            "- Prefer low-risk tasks with tests, docs, CLI clarity, validation, or deterministic parsing.\n\n"
            "## Recent Lessons\n\n"
            "## Repeated Blockers\n\n"
            "## Recent Accepted Work\n"
        )


def rotate_learning_events(paths: dict[str, Path]) -> None:
    events = paths["events"]
    if not events.exists() or events.stat().st_size < LEARNING_EVENT_MAX_BYTES:
        return
    for index in range(LEARNING_EVENT_ROTATIONS - 1, 0, -1):
        src = paths["dir"] / f"events.{index}.jsonl"
        dst = paths["dir"] / f"events.{index + 1}.jsonl"
        if src.exists():
            src.replace(dst)
    events.replace(paths["dir"] / "events.1.jsonl")


def append_learning_event(project: str, event: dict) -> None:
    paths = knowledge_paths(project)
    ensure_memory_state(project)
    rotate_learning_events(paths)
    with paths["events"].open("a") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def loop_memory_snapshot(project: str, max_chars: int = 6000) -> str:
    ensure_memory_state(project)
    text = knowledge_paths(project)["state"].read_text()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 80].rstrip() + "\n\n[Loop memory truncated to fit prompt budget]\n"


def recent_events(project: str, limit: int = 20) -> list[dict]:
    paths = knowledge_paths(project)
    if not paths["events"].exists():
        return []
    rows: list[dict] = []
    with paths["events"].open() as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
                if len(rows) > limit:
                    rows = rows[-limit:]
    return rows


def registry_project(project: str) -> dict:
    registry = load_json(REGISTRY_PATH)
    if project not in registry.get("projects", {}):
        raise RuntimeError(f"unknown project: {project}")
    return registry["projects"][project]


def state_project(state: dict, project: str) -> dict:
    return state.setdefault("projects", {}).setdefault(project, {})


def loop_job(project_state: dict) -> dict:
    return project_state.setdefault("loop_job", {
        "state": "stopped",
        "cadence_seconds": DEFAULT_CADENCE_SECONDS,
        "mode": "hourly_forever",
    })


def scheduler_label(project: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", project).strip("-")
    return f"{LAUNCH_LABEL_PREFIX}.{safe}"


def scheduler_plist_path(project: str) -> Path:
    return LAUNCH_AGENT_DIR / f"{scheduler_label(project)}.plist"


class ProjectLock:
    def __init__(self, project: str):
        self.project = project
        self.path = LOCK_DIR / f"{project}.lock"
        self.handle = None

    def __enter__(self):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.seek(0)
            owner = self.handle.read().strip()
            raise RuntimeError(f"loop already running for {self.project}; lock owner: {owner or 'unknown'}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "project": self.project, "acquired_at": now_iso()}))
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            self.handle.seek(0)
            self.handle.truncate()
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def with_project_lock(project: str):
    return ProjectLock(project)


def run(cmd: list[str], cwd: Path, log_path: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if log_path is not None:
        log_path.write_text(
            "$ " + " ".join(cmd) + "\n\n"
            + "## STDOUT\n" + result.stdout
            + "\n## STDERR\n" + result.stderr
            + f"\n## EXIT_CODE\n{result.returncode}\n"
        )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def slugify_project_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "project"


def git(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git"] + cmd, cwd=cwd, check=check)


def git_root(cwd: Path) -> Path | None:
    result = git(["rev-parse", "--show-toplevel"], cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def find_contract_path(cwd: Path) -> Path | None:
    current = cwd.resolve()
    if current.is_file():
        current = current.parent
    for path in [current] + list(current.parents):
        candidate = path / ".loop" / "contract.yaml"
        if candidate.exists():
            return candidate.resolve()
    return None


def parse_contract_value(contract_path: Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.*?)\s*$")
    for line in contract_path.read_text().splitlines():
        match = pattern.match(line)
        if match:
            value = match.group(1).strip().strip("'\"")
            return value or None
    return None


def registry_data() -> dict:
    if not REGISTRY_PATH.exists():
        return {"projects": {}}
    return load_json(REGISTRY_PATH)


def save_registry(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(REGISTRY_PATH, data)


def linear_sync_cfg(cfg: dict) -> dict:
    sync = cfg.get("linear_sync") or {}
    if not sync and cfg.get("linear_control_issue"):
        sync = {"enabled": True, "control_issue": cfg["linear_control_issue"]}
    return sync


def linear_api_key(cfg: dict) -> str | None:
    sync = linear_sync_cfg(cfg)
    if not sync.get("enabled", False):
        return None
    env_key = os.environ.get("LINEAR_API_KEY")
    if env_key:
        return env_key.strip()
    key_file = sync.get("api_key_file")
    if not key_file:
        return None
    path = Path(key_file).expanduser()
    if not path.exists():
        return None
    key = path.read_text().strip()
    return key or None


def default_linear_cfg(control_issue: str | None = None) -> dict:
    sync = {
        "enabled": True,
        "api_key_file": str(DEFAULT_LINEAR_API_KEY_FILE),
        "comment_on_control_issue": True,
    }
    if control_issue:
        sync["control_issue"] = control_issue
    return {"linear_sync": sync, "linear_control_issue": control_issue}


def linear_graphql(cfg: dict, query: str, variables: dict | None = None) -> dict:
    key = linear_api_key(cfg)
    if not key:
        raise RuntimeError("Linear sync is enabled but no LINEAR_API_KEY or api_key_file is available")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        LINEAR_GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Linear API HTTP {exc.code}: {detail}") from exc
    if data.get("errors"):
        messages = "; ".join(error.get("message", "unknown Linear error") for error in data["errors"])
        raise RuntimeError(f"Linear API error: {messages}")
    return data["data"]


def linear_team_by_key(team_key: str = DEFAULT_LINEAR_TEAM_KEY) -> dict:
    data = linear_graphql(
        default_linear_cfg(),
        """
        query {
          teams {
            nodes { id key name }
          }
        }
        """,
    )
    for team in data.get("teams", {}).get("nodes", []):
        if team.get("key") == team_key:
            return team
    raise LoopBlocked("missing_linear_team", f"Linear team {team_key!r} was not found")


def linear_find_project_by_name(name: str) -> dict | None:
    data = linear_graphql(
        default_linear_cfg(),
        """
        query {
          projects(first: 100) {
            nodes {
              id
              name
            }
          }
        }
        """,
    )
    target = name.strip().lower()
    for project in data.get("projects", {}).get("nodes", []):
        if project.get("name", "").strip().lower() == target:
            return project
    return None


def linear_create_project(name: str, team_id: str) -> dict:
    data = linear_graphql(
        default_linear_cfg(),
        """
        mutation($input: ProjectCreateInput!) {
          projectCreate(input: $input) {
            success
            project { id name }
          }
        }
        """,
        {
            "input": {
                "name": name,
                "teamIds": [team_id],
                "description": "Agent Loop managed project.",
            }
        },
    )
    result = data["projectCreate"]
    if not result.get("success"):
        raise RuntimeError("projectCreate returned success=false")
    return result["project"]


def linear_find_control_issue(team_key: str, title: str) -> dict | None:
    data = linear_graphql(
        default_linear_cfg(),
        """
        query($teamKey: String!) {
          issues(first: 100, filter: { team: { key: { eq: $teamKey } } }) {
            nodes {
              id
              identifier
              title
              url
              project { id name }
            }
          }
        }
        """,
        {"teamKey": team_key},
    )
    for issue in data.get("issues", {}).get("nodes", []):
        if issue.get("title") == title:
            return issue
    return None


def linear_create_control_issue(team_id: str, project_id: str, title: str, description: str) -> dict:
    data = linear_graphql(
        default_linear_cfg(),
        """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier title url }
          }
        }
        """,
        {
            "input": {
                "teamId": team_id,
                "projectId": project_id,
                "title": title,
                "description": description,
            }
        },
    )
    result = data["issueCreate"]
    if not result.get("success"):
        raise RuntimeError("issueCreate returned success=false")
    return result["issue"]


def linear_bootstrap_project(project_id: str, project_name: str) -> dict:
    team = linear_team_by_key(DEFAULT_LINEAR_TEAM_KEY)
    project = linear_find_project_by_name(project_name)
    if project is None:
        project = linear_create_project(project_name, team["id"])
    title = f"[loop-control] {project_id}"
    issue = linear_find_control_issue(team["key"], title)
    if issue is None:
        issue = linear_create_control_issue(
            team["id"],
            project["id"],
            title,
            (
                f"Control issue for Agent Loop project `{project_id}`.\n\n"
                "Human-facing milestones live in Linear project milestones; "
                "task execution lives in GitHub issues and PRs."
            ),
        )
    return {
        "linear_project_id": project["id"],
        "linear_project": project["name"],
        "linear_control_issue": issue["identifier"],
        "linear_control_issue_id": issue["id"],
        "linear_control_issue_url": issue.get("url"),
    }


def linear_disabled_metadata() -> dict:
    return {
        "linear_project_id": None,
        "linear_project": None,
        "linear_control_issue": None,
        "linear_control_issue_id": None,
        "linear_control_issue_url": None,
        "linear_sync": {
            "enabled": False,
            "comment_on_control_issue": False,
        },
        "team": None,
    }


def bootstrap_linear_metadata(project_id: str, project_name: str) -> dict:
    cfg = default_linear_cfg()
    if not linear_api_key(cfg):
        return linear_disabled_metadata()
    linear = linear_bootstrap_project(project_id, project_name)
    return {
        **linear,
        "linear_sync": {
            "enabled": True,
            "api_key_file": str(DEFAULT_LINEAR_API_KEY_FILE),
            "control_issue": linear["linear_control_issue"],
            "comment_on_control_issue": True,
        },
        "team": DEFAULT_LINEAR_TEAM_NAME,
    }


def linear_issue(cfg: dict) -> dict:
    sync = linear_sync_cfg(cfg)
    issue_identifier = sync.get("control_issue") or cfg.get("linear_control_issue")
    if not issue_identifier:
        raise RuntimeError("Linear control issue is not configured")
    data = linear_graphql(
        cfg,
        """
        query($issueId: String!) {
          issue(id: $issueId) {
            id
            identifier
            title
            url
            state { name }
            team { id key name }
            project { id name }
          }
        }
        """,
        {"issueId": issue_identifier},
    )
    issue = data.get("issue")
    if not issue:
        raise RuntimeError(f"Linear issue not found: {issue_identifier}")
    return issue


def linear_create_comment(cfg: dict, issue_id: str, body: str) -> str:
    data = linear_graphql(
        cfg,
        """
        mutation($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment { id url }
          }
        }
        """,
        {"input": {"issueId": issue_id, "body": body}},
    )
    result = data["commentCreate"]
    if not result.get("success"):
        raise RuntimeError("Linear commentCreate returned success=false")
    return result["comment"]["url"] or result["comment"]["id"]


def linear_comment(cfg: dict, log_dir: Path, log_name: str, body: str) -> bool:
    sync = linear_sync_cfg(cfg)
    if not sync.get("enabled", False) or not sync.get("comment_on_control_issue", True):
        return False
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{log_name}.linear.json"
    try:
        issue = linear_issue(cfg)
        comment_ref = linear_create_comment(cfg, issue["id"], body)
        log_path.write_text(json.dumps({
            "ok": True,
            "issue": issue["identifier"],
            "comment": comment_ref,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }, indent=2) + "\n")
        return True
    except Exception as exc:
        log_path.write_text(json.dumps({
            "ok": False,
            "error": str(exc),
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }, indent=2) + "\n")
        return False


def linear_project_id(cfg: dict) -> str:
    if cfg.get("linear_project_id"):
        return cfg["linear_project_id"]
    issue = linear_issue(cfg)
    project = issue.get("project")
    if not project or not project.get("id"):
        raise RuntimeError("Linear control issue is not attached to a project")
    return project["id"]


def linear_create_milestone(cfg: dict, run_id: str, description: str, log_dir: Path) -> dict | None:
    sync = linear_sync_cfg(cfg)
    if not sync.get("enabled", False):
        return None
    log_path = log_dir / "linear-milestone-create.json"
    try:
        project_id = linear_project_id(cfg)
        data = linear_graphql(
            cfg,
            """
            mutation($input: ProjectMilestoneCreateInput!) {
              projectMilestoneCreate(input: $input) {
                success
                projectMilestone {
                  id
                  name
                  status
                  progress
                }
              }
            }
            """,
            {
                "input": {
                    "projectId": project_id,
                    "name": f"Loop cycle {run_id}",
                    "description": description,
                    "targetDate": dt.date.today().isoformat(),
                }
            },
        )
        result = data["projectMilestoneCreate"]
        if not result.get("success"):
            raise RuntimeError("projectMilestoneCreate returned success=false")
        milestone = result["projectMilestone"]
        log_path.write_text(json.dumps({"ok": True, "milestone": milestone, "updated_at": now_iso()}, indent=2) + "\n")
        return milestone
    except Exception as exc:
        log_path.write_text(json.dumps({"ok": False, "error": str(exc), "updated_at": now_iso()}, indent=2) + "\n")
        return None


def linear_update_milestone(cfg: dict, milestone_id: str | None, description: str, log_dir: Path, log_name: str) -> bool:
    if not milestone_id or not linear_sync_cfg(cfg).get("enabled", False):
        return False
    log_path = log_dir / f"{log_name}.linear-milestone.json"
    try:
        data = linear_graphql(
            cfg,
            """
            mutation($id: String!, $input: ProjectMilestoneUpdateInput!) {
              projectMilestoneUpdate(id: $id, input: $input) {
                success
                projectMilestone {
                  id
                  name
                  status
                  progress
                }
              }
            }
            """,
            {"id": milestone_id, "input": {"description": description}},
        )
        result = data["projectMilestoneUpdate"]
        if not result.get("success"):
            raise RuntimeError("projectMilestoneUpdate returned success=false")
        log_path.write_text(json.dumps({"ok": True, "milestone": result["projectMilestone"], "updated_at": now_iso()}, indent=2) + "\n")
        return True
    except Exception as exc:
        log_path.write_text(json.dumps({"ok": False, "error": str(exc), "updated_at": now_iso()}, indent=2) + "\n")
        return False


def milestone_description(run_id: str, phase: str, tasks: list[dict] | None = None, waiting_for_human: list[dict] | None = None) -> str:
    lines = [
        f"Loop cycle `{run_id}`",
        "",
        f"Phase: `{phase}`",
        f"Updated at: `{now_iso()}`",
        "",
    ]
    if tasks:
        lines.extend(["Tasks:", ""])
        for task in tasks:
            lines.extend([
                f"- `{task.get('task_id')}` `{task.get('status')}`",
                f"  - Issue: {task.get('github_issue') or 'none'}",
                f"  - PR: {task.get('github_pr') or 'none'}",
            ])
    if waiting_for_human:
        lines.extend(["", "Waiting for human:", ""])
        for item in waiting_for_human:
            lines.append(f"- `{item.get('reason')}` {item.get('issue_path') or ''} {item.get('error') or item.get('hits') or ''}")
    return "\n".join(lines)


def render_template(name: str, values: dict[str, str]) -> str:
    text = (ENGINE_ROOT / "prompts" / name).read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def safe_run_id(project: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{project}-{stamp}"


def first_heading(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "loop task"


def git_status_short(repo_path: Path) -> str:
    return git(["status", "--short"], cwd=repo_path).stdout.strip()


def ensure_clean_for_bootstrap(repo_path: Path) -> None:
    status = git_status_short(repo_path)
    if status:
        raise LoopBlocked(
            "dirty_worktree",
            f"Project repo must be clean before loop bootstrap:\n{status}",
            {"repo_path": str(repo_path), "status": status},
        )


def git_origin_url(repo_path: Path) -> str | None:
    result = git(["config", "--get", "remote.origin.url"], cwd=repo_path, check=False)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


def github_repo_from_origin(url: str | None) -> str | None:
    if not url:
        return None
    patterns = [
        r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$",
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url.strip())
        if match:
            return match.group(1)
    return None


def gh_auth_ok() -> bool:
    gh = shutil.which("gh")
    if not gh:
        return False
    return subprocess.run([gh, "auth", "status"], text=True, capture_output=True).returncode == 0


def detect_verification_commands(repo_path: Path) -> list[str]:
    if (repo_path / "tests").is_dir():
        return ["python3 -m pytest tests/"]
    if any((repo_path / name).exists() for name in ("pytest.ini", "pyproject.toml", "setup.cfg")):
        return ["python3 -m pytest"]
    if (repo_path / "package.json").exists():
        return ["npm test"]
    return ["git diff --check"]


def render_contract(
    project_id: str,
    project_name: str,
    repo_path: Path,
    verification_commands: list[str],
) -> str:
    commands = "\n".join(f"    - {command}" for command in verification_commands)
    return f"""project_id: {project_id}
name: {project_name}
repo_path: {repo_path}

purpose: >
  Improve the user-facing value of {project_name}. The loop should identify the
  highest-value small product change available in this repo, prefer observable
  behavior over internal cleanup, and stop when no candidate clears the value
  line.

artifact_contract:
  primary:
    - user-facing commands, screens, generated artifacts, or workflows
    - tests that prove the selected user-visible behavior
    - docs only when they clarify a real user or operator workflow
  not_primary:
    - launchd installation state
    - credential or external account state
    - production publishing state

risk_policy:
  auto_execute: low
  needs_human_approval:
    - credential or token handling
    - external auth probing changes
    - launchd, cron, or scheduler installation changes
    - destructive file operations
    - network publishing
    - money, trading, or payment behavior
    - broad architecture rewrites

allowed_low_risk_work:
  - small user-visible CLI, status, API, or artifact clarity improvements with tests
  - tests for the selected product behavior
  - README or docs that clarify the actual user or operator workflow
  - deterministic parsing, validation, or observability that unlocks user value
  - small internal refactors only when needed for the selected behavior and covered by tests

verification:
  commands:
{commands}

loop_rules:
  - Planner ranks candidates by product value, not ease.
  - Planner creates low-risk tasks with bounded Allowed Files.
  - No candidate over the value line means do nothing; do not create busywork.
  - Worker modifies only files listed in the issue.
  - Reviewer must verify Definition of Done and tests before pass.
  - Medium or high risk work is recorded for human approval.
  - When Linear sync is disabled, approval queues stay in local state, digest, and GitHub.
  - Runtime state and run logs live in the global loop engine, not here.
"""


def create_product_baseline_tag(repo_path: Path, project_id: str) -> str:
    tag = f"pre-loop-{project_id}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    git(["tag", tag], cwd=repo_path)
    return tag


def create_bootstrap_commit_and_branch(repo_path: Path, project_id: str) -> str:
    contract_rel = ".loop/contract.yaml"
    if git_status_short(repo_path):
        git(["add", contract_rel], cwd=repo_path)
        if git_status_short(repo_path):
            git(["commit", "-m", "chore(loop): initialize loop contract"], cwd=repo_path)
    pilot_branch = f"loop/{project_id}-pilot"
    branch_exists = git(["rev-parse", "--verify", f"refs/heads/{pilot_branch}"], cwd=repo_path, check=False).returncode == 0
    if not branch_exists:
        git(["branch", pilot_branch], cwd=repo_path)
    git(["push", "-u", "origin", pilot_branch], cwd=repo_path)
    return pilot_branch


def registry_project_by_repo(repo_path: Path) -> str | None:
    root = str(repo_path.resolve())
    for project_id, cfg in registry_data().get("projects", {}).items():
        cfg_repo = cfg.get("repo_path")
        if cfg_repo and str(Path(cfg_repo).resolve()) == root:
            return project_id
    return None


def registry_project_by_contract(contract_path: Path) -> str | None:
    target = str(contract_path.resolve())
    for project_id, cfg in registry_data().get("projects", {}).items():
        cfg_contract = cfg.get("contract_path")
        if cfg_contract and str(Path(cfg_contract).resolve()) == target:
            return project_id
    return None


def bootstrap_project(cwd: Path) -> str:
    repo_path = git_root(cwd)
    if repo_path is None:
        raise LoopBlocked("not_git_repo", "Current directory is not inside a git repository")
    existing = registry_project_by_repo(repo_path)
    if existing:
        return existing
    ensure_clean_for_bootstrap(repo_path)
    origin_url = git_origin_url(repo_path)
    github_repo = github_repo_from_origin(origin_url)
    if not github_repo:
        raise LoopBlocked(
            "missing_github_remote",
            "Loop bootstrap requires a GitHub origin remote; v1 does not create GitHub repos.",
            {"repo_path": str(repo_path), "origin": origin_url or ""},
        )
    if not gh_auth_ok():
        raise LoopBlocked("missing_github_auth", "GitHub CLI auth is required before loop bootstrap")

    contract_path = repo_path / ".loop" / "contract.yaml"
    contract_project_id = parse_contract_value(contract_path, "project_id") if contract_path.exists() else None
    project_id = contract_project_id or slugify_project_id(repo_path.name)
    project_name = parse_contract_value(contract_path, "name") if contract_path.exists() else repo_path.name
    project_name = project_name or repo_path.name
    linear = bootstrap_linear_metadata(project_id, project_name)
    verification_commands = detect_verification_commands(repo_path)
    baseline_tag = create_product_baseline_tag(repo_path, project_id)

    if not contract_path.exists():
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(render_contract(
            project_id,
            project_name,
            repo_path,
            verification_commands,
        ))

    pilot_branch = create_bootstrap_commit_and_branch(repo_path, project_id)
    registry = registry_data()
    registry.setdefault("projects", {})[project_id] = {
        "name": project_name,
        "repo_path": str(repo_path),
        "pilot_branch": pilot_branch,
        "contract_path": str(contract_path),
        "github_repo": github_repo,
        "linear_project": linear["linear_project"],
        "linear_project_id": linear["linear_project_id"],
        "linear_control_issue": linear["linear_control_issue"],
        "linear_control_issue_id": linear["linear_control_issue_id"],
        "linear_control_issue_url": linear.get("linear_control_issue_url"),
        "linear_sync": linear["linear_sync"],
        "team": linear["team"],
        "verification_commands": verification_commands,
        "auto_execute_risk": "low",
        "auto_approval": {
            "silent_approval_after_minutes": 30,
            "only_if_risk": "low",
            "max_tasks_per_cycle": 1,
            "blocked_categories": list(BLOCKED_SIGNALS.keys()),
        },
        "agents": {
            "planner": {"model": "gpt-5.5", "reasoning_effort": "high", "timeout_seconds": 600},
            "worker": {"model": "gpt-5.5", "reasoning_effort": "high", "timeout_seconds": 900},
            "reviewer": {"model": "gpt-5.5", "reasoning_effort": "high", "timeout_seconds": 600},
        },
    }
    save_registry(registry)
    state = load_state()
    project_state = state_project(state, project_id)
    project_state.setdefault("status", "stopped")
    project_state.setdefault("waiting_for_human", [])
    project_state.setdefault("rollback", {})["product_baseline_tag"] = baseline_tag
    save_state(state)
    return project_id


def resolve_project(cwd: Path, project: str | None = None, bootstrap: bool = False) -> str:
    if project:
        registry_project(project)
        return project
    contract_path = find_contract_path(cwd)
    if contract_path is not None:
        registered = registry_project_by_contract(contract_path)
        if registered:
            return registered
        project_id = parse_contract_value(contract_path, "project_id")
        if project_id and project_id in registry_data().get("projects", {}):
            return project_id
        if bootstrap:
            return bootstrap_project(contract_path.parent.parent)
        raise LoopBlocked(
            "contract_not_registered",
            f"Loop contract exists but project is not registered: {contract_path}",
            {"contract_path": str(contract_path)},
        )
    repo_path = git_root(cwd)
    if repo_path is None:
        raise LoopBlocked("not_git_repo", "Current directory is not inside a git repository")
    registered = registry_project_by_repo(repo_path)
    if registered:
        return registered
    if bootstrap:
        return bootstrap_project(repo_path)
    raise LoopBlocked(
        "not_initialized",
        "Project is not initialized for loop; run loop init first.",
        {"repo_path": str(repo_path)},
    )


# --- safety enforcement -------------------------------------------------------

# Keyword signals for each blocked category. The screen is a defense-in-depth
# heuristic on top of the planner's self-declared risk: if any signal appears in
# an issue, the task is gated to human review instead of auto-executed.
BLOCKED_SIGNALS: dict[str, list[str]] = {
    "credentials": ["secret", "credential", "api_key", "api-key", "apikey",
                    "private_key", "client_secret", "access_token", "access_key",
                    "bearer", "jwt", "password", "passphrase", ".env",
                    ".ssh", "id_rsa", "keychain", "keyring"],
    "external_auth": ["oauth", "auth probe", "login flow", "sign in", "sso",
                      "refresh token", "auth token"],
    "launchd_or_cron": ["launchd", "launchctl", ".plist", "crontab", "cron",
                        "scheduled job", "systemd"],
    "publishing": ["publish", "deploy", "release to", "npm publish", "vercel",
                   "push to production", "merge to main", "go live", "ship to"],
    "destructive_operations": ["rm -rf", "rm -r ", "drop table", "drop database",
                               "force push", "git push --force", "--force",
                               "delete all", "truncate", "reset --hard"],
    "money_or_trading": ["binance", "place order", "spot order", "futures",
                         "live trade", "withdraw", "wallet", "payment",
                         "real money", "send eth", "send btc", "transfer funds"],
    "broad_architecture_rewrite": ["rewrite", "rearchitect", "migrate the entire",
                                    "overhaul", "ground-up", "from scratch"],
}

# Patterns that, if they appear in an issue's Allowed Files, grant nothing
# (they would whitelist arbitrarily broad sets of files).
DANGEROUS_ALLOW_PATTERNS = {"", ".", "/", "*", "**", "*.*", "./*", "*/*"}

# Markers of credential material that must never be committed by a worker.
SECRET_MARKERS = ("lin_api", "sk-", "ghp_", "gho_", "ghs_", "xox", "AKIA",
                  "-----BEGIN", "PRIVATE KEY")


def _norm_path(p: str) -> str:
    p = p.strip().strip('"').strip()
    if p.startswith("./"):
        p = p[2:]
    # NB: only strip a single leading "./" and leading "/" — never collapse
    # "../" (must survive so the traversal check can reject it).
    return p.lstrip("/")


def parse_allowed_files(issue_path: Path) -> list[str]:
    """Extract file patterns listed under the '## Allowed Files' section."""
    patterns: list[str] = []
    in_section = False
    for line in issue_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower() == "## allowed files"
            continue
        if in_section and stripped:
            item = stripped.lstrip("-*").strip().strip("`").strip()
            if not item or item.startswith("#"):
                continue
            token = item.split()[0].strip("`").strip()
            if token:
                patterns.append(token)
    return patterns


def parse_verification_commands(issue_path: Path) -> list[str]:
    commands: list[str] = []
    in_section = False
    for line in issue_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower() == "## verification commands"
            continue
        if not in_section or not stripped:
            continue
        item = stripped.lstrip("-*").strip()
        if item and not item.startswith("#"):
            commands.append(item.strip("`").strip())
    return [command for command in commands if command]


def parse_issue_risk(issue_path: Path) -> str:
    sections = issue_sections(issue_path)
    risk_text = sections.get("risk", "")
    for line in risk_text.splitlines():
        item = line.strip().lstrip("-*").strip().lower()
        if item:
            return item.split()[0].strip("`:.")
    return ""


def require_issue_verification_commands(issue_path: Path) -> list[str]:
    commands = parse_verification_commands(issue_path)
    if not commands:
        raise RuntimeError(
            "auto-runnable issue has no parseable 'Verification Commands'; gating to human review"
        )
    return commands


def trusted_verification_commands(issue_path: Path, cfg: dict) -> list[str]:
    issue_commands = require_issue_verification_commands(issue_path)
    project_commands = [cmd for cmd in cfg.get("verification_commands", []) if cmd]
    if not project_commands:
        raise RuntimeError("project has no trusted verification_commands configured")
    untrusted = [cmd for cmd in issue_commands if cmd not in project_commands]
    if untrusted:
        raise RuntimeError(
            "issue contains untrusted verification command(s): " + "; ".join(untrusted)
        )
    commands: list[str] = []
    for command in issue_commands + project_commands:
        if command not in commands:
            commands.append(command)
    return commands


# Only these issue sections describe the work the task WILL do. Sections like
# "Out Of Scope" / "Reviewer Checklist" enumerate prohibitions ("no credentials,
# no launchd, no publishing") and must NOT be screened, or every legitimate
# issue trips the blocked-category scan.
SCREEN_SECTIONS = ("goal", "context", "verification commands")
NEGATIVE_SCREEN_MARKERS = (
    "do not ",
    "don't ",
    "must not ",
    "should not ",
    "no ",
    "never ",
    "without ",
    "avoid ",
)


def issue_sections(issue_path: Path) -> dict[str, str]:
    """Split an issue markdown file into {lowercased H2 header: body}."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in issue_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def section_excerpt(sections: dict[str, str], name: str, max_chars: int = 260) -> str:
    text = " ".join(line.strip().lstrip("-*").strip() for line in sections.get(name, "").splitlines())
    return display_text(text, max_chars=max_chars) or "-"


def parse_product_impact_section(sections: dict[str, str]) -> dict:
    impact: dict[str, str] = {}
    key_map = {
        "category": "category",
        "surface": "surface",
        "visibility": "visibility",
        "before": "before",
        "after": "after",
        "user benefit": "user_benefit",
        "user_benefit": "user_benefit",
    }
    for line in sections.get("product impact", "").splitlines():
        item = line.strip().lstrip("-*").strip()
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        normalized = key.strip().lower().replace("_", " ")
        mapped = key_map.get(normalized)
        if mapped and value.strip():
            impact[mapped] = display_text(value.strip(), max_chars=360)
    return impact


def files_are_tests_only(files: list[str]) -> bool:
    return bool(files) and all(
        path.startswith("tests/")
        or path.endswith("_test.py")
        or path.startswith("test_")
        or "/test_" in path
        for path in files
    )


def files_are_docs_only(files: list[str]) -> bool:
    return bool(files) and all(
        path.endswith(".md") or path.startswith("docs/") for path in files
    )


def task_changed_files_from_log(run_dir: Path, task_id: str) -> list[str]:
    status_path = run_dir / "tasks" / task_id / "worker-git-status.txt"
    if not status_path.exists():
        return []
    return [line.strip() for line in status_path.read_text().splitlines() if line.strip()]


def product_surface(issue_text: str, title: str) -> str:
    lowered = issue_text.lower()
    if "--sessions" in lowered or "session_index" in lowered or "_codex_recent" in lowered:
        return "recent-session suggestions"
    if "cli" in lowered:
        return "CLI output"
    if "widget" in lowered:
        return "widget output"
    if "readme" in lowered or "docs" in lowered:
        return "operator documentation"
    return title


def product_impact_for_task(run_dir: Path, task: dict) -> dict:
    raw_issue_path = task.get("issue_path")
    issue_path = Path(raw_issue_path) if raw_issue_path else None
    task_id = task.get("task_id") or "task"
    title = task.get("title") or task_id
    sections: dict[str, str] = {}
    issue_text = ""
    if issue_path and issue_path.exists() and issue_path.is_file():
        title = first_heading(issue_path)
        sections = issue_sections(issue_path)
        issue_text = issue_path.read_text()
    files = task_changed_files_from_log(run_dir, task_id)
    if not files and issue_path and issue_path.exists() and issue_path.is_file():
        files = parse_allowed_files(issue_path)
    declared_impact = parse_product_impact_section(sections)
    if declared_impact.get("before") and declared_impact.get("after") and declared_impact.get("user_benefit"):
        declared_impact.setdefault("visibility", "Potential user-visible product change")
        declared_impact.setdefault("evidence", f"Changed files: {display_text(', '.join(files) or 'not recorded')}")
        return declared_impact
    surface = display_text(product_surface(issue_text, title), max_chars=120)
    goal = section_excerpt(sections, "goal")
    definition = section_excerpt(sections, "definition of done")
    if files_are_tests_only(files):
        return {
            "visibility": "No direct user-visible behavior change",
            "before": f"No deterministic regression coverage protected {surface}.",
            "after": f"Synthetic tests now cover {surface} for the cases described in this task.",
            "user_benefit": (
                f"Users should see fewer silent regressions in {surface}; this cycle improved reliability, "
                "not visible product functionality."
            ),
            "evidence": f"Changed files: {display_text(', '.join(files) or 'tests only')}",
        }
    if files_are_docs_only(files):
        return {
            "visibility": "Operator-facing documentation change",
            "before": f"{surface} was less explicit for operators.",
            "after": definition if definition != "-" else goal,
            "user_benefit": "Users/operators get clearer expectations and fewer review ambiguities.",
            "evidence": f"Changed files: {display_text(', '.join(files) or 'docs')}",
        }
    return {
        "visibility": "Potential user-visible product change",
        "before": section_excerpt(sections, "context"),
        "after": definition if definition != "-" else goal,
        "user_benefit": f"The product should now better support: {goal}",
        "evidence": f"Changed files: {display_text(', '.join(files) or 'not recorded')}",
    }


def positive_intent_text(sections: dict[str, str]) -> str:
    lines: list[str] = []
    for name in SCREEN_SECTIONS:
        for line in sections.get(name, "").splitlines():
            normalized = line.strip().lower().lstrip("-*").strip()
            if not normalized:
                continue
            if any(marker in normalized for marker in NEGATIVE_SCREEN_MARKERS):
                continue
            lines.append(line)
    return "\n".join(lines).strip()


def screen_blocked(issue_path: Path, blocked_categories: list[str]) -> list[str]:
    """Return blocked-category hits in the issue's positive-intent sections only.

    Defense in depth on the planner's declared intent. Negative-constraint
    sections (Out Of Scope, Reviewer Checklist) are deliberately excluded so
    that an issue *forbidding* credentials/launchd/publishing is not mis-gated.
    """
    sections = issue_sections(issue_path)
    intent = positive_intent_text(sections)
    # If the issue lacks the expected structure, fail closed by scanning the
    # whole document (a malformed issue gets gated to human, which is safe).
    text = (intent or issue_path.read_text()).lower()
    hits: list[str] = []
    for category in blocked_categories:
        for signal in BLOCKED_SIGNALS.get(category, []):
            if signal in text:
                hits.append(f"{category}:{signal.strip()}")
                break
    return hits


def changed_files(worktree_path: Path) -> list[str]:
    """Files modified in the worktree per git porcelain status."""
    out = run(["git", "status", "--porcelain"], cwd=worktree_path).stdout
    files: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        part = line[3:].strip()
        if " -> " in part:  # rename
            part = part.split(" -> ")[-1].strip()
        files.append(part.strip().strip('"'))
    return [f for f in files if f]


def allowlist_violations(changed: list[str], patterns: list[str]) -> list[str]:
    """Changed files not covered by any Allowed-Files pattern.

    Hardened against over-broad globs: a bare `*` or `*.py` must not whitelist
    files at arbitrary directory depth, dangerous patterns grant nothing, and
    any `..` path escape is always a violation.
    """
    norm_patterns: list[str] = []
    for p in patterns:
        np = _norm_path(p)
        if not np or np in DANGEROUS_ALLOW_PATTERNS or ".." in np:
            continue  # over-broad / escaping patterns whitelist nothing
        norm_patterns.append(np)
    violations: list[str] = []
    for f in changed:
        nf = _norm_path(f)
        if not nf or ".." in nf:
            violations.append(f)
            continue
        ok = False
        for p in norm_patterns:
            if nf == p:
                ok = True
                break
            if p.endswith("/") and nf.startswith(p):
                ok = True
                break
            if nf.startswith(p.rstrip("/") + "/"):
                ok = True
                break
            # globs: `*` must not cross directory separators, so the pattern and
            # the path must live at the same depth.
            if "*" in p and p.count("/") == nf.count("/") and fnmatch.fnmatch(nf, p):
                ok = True
                break
        if not ok:
            violations.append(f)
    return violations


def scan_changed_for_secrets(worktree_path: Path, changed: list[str]) -> list[str]:
    """Block the read-and-launder vector: a changed file must not contain known
    secret values or credential markers. Fails closed."""
    secret_values: set[str] = set()
    secrets_dir = SECRETS_DIR
    if secrets_dir.exists():
        for sf in secrets_dir.iterdir():
            try:
                if sf.is_file() and sf.stat().st_size < 10_000:
                    val = sf.read_text(errors="ignore").strip()
                    if len(val) >= 8:
                        secret_values.add(val)
            except Exception:
                continue
    findings: list[str] = []
    for rel in changed:
        fp = worktree_path / rel
        try:
            if not fp.is_file():
                continue
            if fp.stat().st_size > 2_000_000:
                # too large to scan in a low-risk task -> fail closed
                findings.append(f"{rel}: file too large to scan for secrets; gating")
                continue
            content = fp.read_text(errors="ignore")
        except Exception:
            continue
        hit = next((v for v in secret_values if v and v in content), None)
        if hit:
            findings.append(f"{rel}: contains a known secret value")
            continue
        marker = next((m for m in SECRET_MARKERS if m in content), None)
        if marker:
            findings.append(f"{rel}: contains secret-like marker '{marker}'")
    return findings


# A planner-declared issue_path may ONLY be of this exact form. This blocks
# absolute paths and ../ traversal that would otherwise let the planner point
# the engine at an arbitrary file (e.g. a secret), which create_github_issue
# would then upload as an issue body — bypassing the worker-stage secret scan.
ISSUE_PATH_RE = re.compile(r"^issues/issue-\d+\.md$")
ISSUE_NAME_RE = re.compile(r"^issue-\d+\.md$")


def validate_issue_file(run_dir: Path, candidate: Path) -> Path:
    """Accept ONLY a real (non-symlink) regular file named issue-NNN.md that
    lives directly inside a real (non-symlink) run_dir/issues/ directory.

    This is the single chokepoint for every issue the engine will read and
    upload via `gh issue create --body-file`, so it must hold for BOTH the
    candidates.json path and the directory-glob fallback. A planner is untrusted
    output: an absolute path, `..` traversal, or a symlink to an external file
    (e.g. a secret) must never become an issue body.
    """
    issues_dir = run_dir / "issues"
    if issues_dir.is_symlink() or not issues_dir.is_dir():
        raise RuntimeError("run_dir/issues is missing or not a real directory")
    if not ISSUE_NAME_RE.match(candidate.name):
        raise RuntimeError(f"issue filename must match issue-NNN.md: {candidate.name!r}")
    if candidate.is_symlink():
        raise RuntimeError(f"issue file is a symlink (rejected): {candidate}")
    if not candidate.is_file():
        raise RuntimeError(f"issue file missing or not a regular file: {candidate}")
    if candidate.resolve().parent != issues_dir.resolve():
        raise RuntimeError(f"issue file escapes run_dir/issues/: {candidate}")
    return candidate.resolve()


def safe_issue_path(run_dir: Path, issue_path: str) -> Path:
    if not isinstance(issue_path, str) or not ISSUE_PATH_RE.match(issue_path):
        raise RuntimeError(
            f"unsafe issue_path (must match issues/issue-NNN.md): {issue_path!r}"
        )
    return validate_issue_file(run_dir, run_dir / issue_path)


def candidate_value_score(candidate: dict) -> int:
    raw = candidate.get("value_score", candidate.get("value"))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


def candidate_envelope_name(candidate: dict) -> str:
    return str(
        candidate.get("preapproved_envelope")
        or candidate.get("medium_risk_envelope")
        or candidate.get("envelope")
        or ""
    ).strip()


def candidate_requires_supervision(candidate: dict) -> bool:
    return (
        candidate.get("requires_supervised") is True
        or candidate.get("requires_supervision") is True
        or candidate.get("auto_execute") == "supervised"
    )


def candidate_issue_paths(
    run_dir: Path,
    min_value_score: int = 0,
    supervised: bool = False,
    medium_envelope: dict | None = None,
) -> list[Path]:
    candidates_json = run_dir / "candidates.json"
    if candidates_json.exists():
        paths: list[Path] = []
        for candidate in load_candidates(run_dir):
            risk = str(candidate.get("risk") or "").lower().strip()
            if min_value_score and candidate_value_score(candidate) < min_value_score:
                continue
            include = risk == "low" and candidate.get("auto_execute") is True
            if risk == "medium" and supervised and medium_envelope:
                include = (
                    candidate_requires_supervision(candidate)
                    and candidate_envelope_name(candidate) == medium_envelope.get("name")
                )
            if not include:
                continue
            issue_path = candidate.get("issue_path")
            if not issue_path:
                continue
            paths.append(safe_issue_path(run_dir, issue_path))
        return paths
    # Fallback (no candidates.json): the same chokepoint applies — only real
    # issue-NNN.md files inside a real issues/ dir. The legacy single issue.md
    # path is removed entirely.
    if min_value_score:
        return []
    issues_dir = run_dir / "issues"
    if not issues_dir.is_dir() or issues_dir.is_symlink():
        return []
    out: list[Path] = []
    for path in sorted(issues_dir.glob("*.md")):
        if ISSUE_NAME_RE.match(path.name):
            out.append(validate_issue_file(run_dir, path))
    return out


def max_tasks_per_cycle(auto: dict) -> int:
    raw = auto.get("max_tasks_per_cycle")
    if raw is None:
        return 1
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def write_deferred_issue_paths(run_dir: Path, issue_paths: list[Path], max_tasks: int) -> None:
    if not issue_paths:
        return
    lines = [
        "# Deferred Candidates",
        "",
        f"Only {max_tasks} auto-executable task(s) may run in one cycle.",
        "The remaining candidates were deferred to avoid same-cycle branch and PR conflicts.",
        "",
    ]
    for path in issue_paths:
        lines.append(f"- `{path.relative_to(run_dir)}`")
    (run_dir / "deferred-candidates.md").write_text("\n".join(lines) + "\n")


def load_candidates(run_dir: Path) -> list[dict]:
    candidates_json = run_dir / "candidates.json"
    if not candidates_json.exists():
        return []
    data = json.loads(candidates_json.read_text())
    if not isinstance(data, list):
        raise RuntimeError("candidates.json must contain an array")
    candidates = [candidate for candidate in data if isinstance(candidate, dict)]
    return sorted(candidates, key=candidate_value_score, reverse=True)


def candidate_scores_by_issue_path(run_dir: Path) -> dict[str, int]:
    scores: dict[str, int] = {}
    for candidate in load_candidates(run_dir):
        issue_path = candidate.get("issue_path")
        if isinstance(issue_path, str):
            scores[issue_path] = candidate_value_score(candidate)
    return scores


def issue_path_score(run_dir: Path, issue_path: Path) -> int:
    try:
        rel = issue_path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        rel = str(issue_path)
    return candidate_scores_by_issue_path(run_dir).get(rel, 0)


def candidate_gate_items(
    run_dir: Path,
    min_value_score: int,
    supervised: bool,
    medium_envelope: dict | None,
) -> list[dict]:
    items: list[dict] = []
    for candidate in load_candidates(run_dir):
        risk = str(candidate.get("risk") or "").lower().strip()
        score = candidate_value_score(candidate)
        if risk == "low" and candidate.get("auto_execute") is True and score < min_value_score:
            continue
        if risk == "medium":
            envelope_name = candidate_envelope_name(candidate)
            if not medium_envelope or envelope_name != medium_envelope.get("name"):
                items.append({
                    "reason": "medium_risk_requires_approval",
                    "title": display_text(candidate.get("title")),
                    "value_score": score,
                    "preapproved_envelope": envelope_name or None,
                })
            elif not supervised:
                items.append({
                    "reason": "medium_risk_requires_supervised_run",
                    "title": display_text(candidate.get("title")),
                    "value_score": score,
                    "preapproved_envelope": envelope_name,
                    "approval_hint": "Run `loop run-now --supervised` from the project after confirming the medium-risk envelope.",
                })
        elif risk == "high":
            items.append({
                "reason": "high_risk_requires_approval",
                "title": display_text(candidate.get("title")),
                "value_score": score,
            })
    return items


def candidate_value_summary(run_dir: Path, min_value_score: int) -> dict:
    candidates = load_candidates(run_dir)
    scores = [candidate_value_score(candidate) for candidate in candidates]
    return {
        "candidate_count": len(candidates),
        "max_value_score": max(scores) if scores else 0,
        "below_value_line": sum(1 for score in scores if score < min_value_score),
        "value_threshold": min_value_score,
    }


def higher_value_waiting_item(
    run_dir: Path,
    issue_paths: list[Path],
    waiting_for_human: list[dict],
) -> dict | None:
    if not issue_paths or not waiting_for_human:
        return None
    highest_executable = max(issue_path_score(run_dir, path) for path in issue_paths)
    higher_waiting = [
        item for item in waiting_for_human
        if int(item.get("value_score") or 0) > highest_executable
    ]
    if not higher_waiting:
        return None
    return sorted(higher_waiting, key=lambda item: int(item.get("value_score") or 0), reverse=True)[0]


def validate_medium_issue_against_envelope(issue_path: Path, cfg: dict, envelope: dict | None) -> None:
    if not envelope:
        raise RuntimeError("medium-risk issue has no preapproved envelope")
    if parse_issue_risk(issue_path) != "medium":
        raise RuntimeError("supervised envelope can only execute issues marked medium risk")
    declared_allowed = parse_allowed_files(issue_path)
    if not declared_allowed:
        raise RuntimeError("medium-risk issue has no parseable Allowed Files")
    envelope_allowed = envelope.get("allowed_files") or []
    if not envelope_allowed:
        raise RuntimeError("medium-risk envelope has no allowed_files")
    violations = allowlist_violations(declared_allowed, envelope_allowed)
    if violations:
        raise RuntimeError(
            "medium-risk issue Allowed Files exceed preapproved envelope: "
            + ", ".join(violations)
        )
    issue_commands = require_issue_verification_commands(issue_path)
    envelope_commands = envelope.get("verification_commands") or []
    if not envelope_commands:
        raise RuntimeError("medium-risk envelope has no verification_commands")
    outside_envelope = [command for command in issue_commands if command not in envelope_commands]
    if outside_envelope:
        raise RuntimeError(
            "medium-risk issue verification exceeds preapproved envelope: "
            + "; ".join(outside_envelope)
        )
    trusted_verification_commands(issue_path, cfg)


def task_id_from_issue(issue_path: Path, index: int) -> str:
    stem = issue_path.stem
    if stem.startswith("issue-"):
        return stem
    return f"task-{index:03d}"


def create_github_issue(cfg: dict, issue_path: Path, task_dir: Path) -> str | None:
    title = first_heading(issue_path)
    log_path = task_dir / "github-issue.log"
    result = run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            cfg["github_repo"],
            "--title",
            f"[loop] {title}",
            "--body-file",
            str(issue_path),
        ],
        cwd=Path(cfg["repo_path"]),
        log_path=log_path,
        check=False,
    )
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    (task_dir / "github-issue-url.txt").write_text(url + "\n")
    return url


def sandbox_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def sandbox_profile(extra_deny_paths: list[Path] | None = None, deny_network: bool = False) -> str:
    deny_rules: list[str] = []
    if deny_network:
        deny_rules.append("(deny network*)")
    for path in SECRET_DENY_DIRS:
        deny_rules.append(f'(deny file-read* file-write* (subpath "{sandbox_path(path)}"))')
    for path in SECRET_DENY_FILES:
        deny_rules.append(f'(deny file-read* file-write* (literal "{sandbox_path(path)}"))')
    for path in extra_deny_paths or []:
        quoted = sandbox_path(path)
        deny_rules.append(f'(deny file-read* file-write* (literal "{quoted}"))')
        deny_rules.append(f'(deny file-read* file-write* (subpath "{quoted}"))')
    return "(version 1) (allow default) " + " ".join(deny_rules)


def agent_sandbox_profile() -> str:
    return sandbox_profile()


def agent_env() -> dict:
    env = os.environ.copy()
    # The engine process may use credentials; child agents and verification
    # commands should not inherit them as ambient authority.
    for key in list(env):
        upper = key.upper()
        if key in SECRET_ENV_EXACT or any(keyword in upper for keyword in SECRET_ENV_KEYWORDS):
            env.pop(key, None)
    return env


def wrap_sandbox_command(cmd: list[str], profile: str) -> list[str]:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        raise RuntimeError("sandbox-exec is required for OS-level secret read isolation")
    return [sandbox_exec, "-p", profile] + cmd


def wrap_agent_command(cmd: list[str]) -> list[str]:
    # Agent CLIs apply their own workspace/permission model. Wrapping them in
    # sandbox-exec can break nested tool execution on macOS, while verification
    # commands still use the stricter OS sandbox below.
    return cmd


def verification_deny_paths(worktree_path: Path) -> list[Path]:
    names = [
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        ".envrc",
        ".secrets",
        "secrets",
    ]
    return [worktree_path / name for name in names]


def run_verification_command(command: str, cwd: Path, log_path: Path, timeout_seconds: int = 300) -> None:
    result = subprocess.run(
        wrap_sandbox_command(
            ["zsh", "-lc", command],
            sandbox_profile(verification_deny_paths(cwd), deny_network=True),
        ),
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        env=agent_env(),
    )
    log_path.write_text(
        "$ " + command + "\n\n"
        + "## STDOUT\n" + result.stdout
        + "\n## STDERR\n" + result.stderr
        + f"\n## EXIT_CODE\n{result.returncode}\n"
    )
    if result.returncode != 0:
        raise RuntimeError(f"verification command failed ({result.returncode}): {command}")


def agent_provider(agent_cfg: dict | None = None) -> str:
    agent_cfg = agent_cfg or {}
    provider = str(agent_cfg.get("provider") or agent_cfg.get("type") or "codex").strip().lower()
    aliases = {
        "codex-cli": "codex",
        "openai": "codex",
        "claude-code": "claude",
        "anthropic": "claude",
    }
    return aliases.get(provider, provider)


def resolve_agent_binary(provider: str, agent_cfg: dict | None = None) -> str:
    agent_cfg = agent_cfg or {}
    command = str(agent_cfg.get("command") or provider).strip()
    if provider == "codex":
        return command
    resolved = command if "/" in command else shutil.which(command)
    if provider == "claude" and not resolved:
        raise RuntimeError("missing_claude_cli: Claude CLI was not found on PATH")
    return resolved or command


def codex_command(cwd: Path, output_path: Path, extra_writable: Path, agent_cfg: dict | None = None) -> list[str]:
    agent_cfg = agent_cfg or {}
    cmd = [
        resolve_agent_binary("codex", agent_cfg),
        "exec",
        "--cd",
        str(cwd),
        "--add-dir",
        str(extra_writable),
    ]
    if agent_cfg.get("model"):
        cmd.extend(["--model", agent_cfg["model"]])
    if agent_cfg.get("reasoning_effort"):
        cmd.extend(["-c", f"model_reasoning_effort=\"{agent_cfg['reasoning_effort']}\""])
    sandbox = agent_cfg.get("sandbox", "workspace-write")
    if sandbox not in ("read-only", "workspace-write"):
        # never honor danger-full-access (or anything unexpected) from config
        sandbox = "workspace-write"
    cmd.extend([
        "-c",
        "approval_policy=\"never\"",
        "--sandbox",
        sandbox,
        "--output-last-message",
        str(output_path),
        "-",
    ])
    return cmd


def claude_command(cwd: Path, extra_writable: Path, agent_cfg: dict | None = None) -> list[str]:
    agent_cfg = agent_cfg or {}
    # Fail closed (parity with codex_command rejecting danger-full-access): an unsafe
    # permission mode would disable Claude Code's working-dir sandbox in an unattended
    # loop. Reject loudly rather than silently downgrade, so the operator's configured
    # mode and the actual behavior never diverge.
    safe_permission_modes = ("default", "acceptEdits", "plan")
    permission_mode = str(agent_cfg.get("permission_mode") or "acceptEdits")
    if permission_mode not in safe_permission_modes:
        raise RuntimeError(
            f"unsafe_permission_mode: {permission_mode!r} is not allowed for the claude provider; "
            f"use one of {safe_permission_modes} (bypassPermissions would disable the worktree sandbox)"
        )
    cmd = [
        resolve_agent_binary("claude", agent_cfg),
        "--print",
        "--input-format",
        "text",
        "--output-format",
        str(agent_cfg.get("output_format") or "text"),
        "--no-session-persistence",
        "--safe-mode",
        "--permission-mode",
        permission_mode,
        "--add-dir",
        str(extra_writable),
    ]
    if agent_cfg.get("model"):
        cmd.extend(["--model", agent_cfg["model"]])
    effort = agent_cfg.get("effort") or agent_cfg.get("reasoning_effort")
    if effort:
        cmd.extend(["--effort", str(effort)])
    if agent_cfg.get("max_budget_usd") is not None:
        cmd.extend(["--max-budget-usd", str(agent_cfg["max_budget_usd"])])
    if agent_cfg.get("allowed_tools"):
        allowed = agent_cfg["allowed_tools"]
        if isinstance(allowed, str):
            cmd.extend(["--allowedTools", allowed])
        else:
            cmd.extend(["--allowedTools", ",".join(str(item) for item in allowed)])
    if agent_cfg.get("disallowed_tools"):
        disallowed = agent_cfg["disallowed_tools"]
        if isinstance(disallowed, str):
            cmd.extend(["--disallowedTools", disallowed])
        else:
            cmd.extend(["--disallowedTools", ",".join(str(item) for item in disallowed)])
    if agent_cfg.get("settings"):
        cmd.extend(["--settings", str(agent_cfg["settings"])])
    if agent_cfg.get("append_system_prompt"):
        cmd.extend(["--append-system-prompt", str(agent_cfg["append_system_prompt"])])
    return cmd


def agent_exec(
    prompt: str,
    cwd: Path,
    output_path: Path,
    extra_writable: Path,
    agent_cfg: dict | None = None,
) -> None:
    prompt_path = output_path.with_suffix(".prompt.md")
    prompt_path.write_text(prompt)
    agent_cfg = agent_cfg or {}
    provider = agent_provider(agent_cfg)
    if provider == "codex":
        cmd = codex_command(cwd, output_path, extra_writable, agent_cfg)
    elif provider == "claude":
        cmd = claude_command(cwd, extra_writable, agent_cfg)
    else:
        raise RuntimeError(f"unsupported_agent_provider: {provider}")
    timeout_seconds = agent_cfg.get("timeout_seconds")
    try:
        result = subprocess.run(
            wrap_agent_command(cmd),
            input=prompt,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=agent_env(),
        )
    except subprocess.TimeoutExpired as exc:
        output_path.with_suffix(".stdout.log").write_text(exc.stdout or "")
        output_path.with_suffix(".stderr.log").write_text(
            (exc.stderr or "") + f"\nTIMEOUT after {timeout_seconds} seconds\n"
        )
        raise RuntimeError(f"{provider} exec timed out after {timeout_seconds} seconds")
    output_path.with_suffix(".stdout.log").write_text(result.stdout)
    output_path.with_suffix(".stderr.log").write_text(result.stderr)
    if provider == "claude":
        output_path.write_text(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"{provider} exec failed ({result.returncode}); see {output_path.with_suffix('.stderr.log')}")


def codex_exec(
    prompt: str,
    cwd: Path,
    output_path: Path,
    extra_writable: Path,
    agent_cfg: dict | None = None,
) -> None:
    cfg = dict(agent_cfg or {})
    cfg["provider"] = "codex"
    agent_exec(prompt, cwd, output_path, extra_writable, cfg)


def ensure_project_clean(repo_path: Path) -> None:
    status = run(["git", "status", "--short"], cwd=repo_path).stdout.strip()
    if status:
        raise RuntimeError(f"project repo is not clean before loop run:\n{status}")


def make_worktree(project: str, repo_path: Path, pilot_branch: str, branch_suffix: str) -> tuple[Path, str]:
    worktree_root = ENGINE_ROOT / "worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)
    worktree_path = worktree_root / branch_suffix
    branch = f"loop/{project}-{branch_suffix}"
    if worktree_path.exists():
        shutil.rmtree(worktree_path)
    run(["git", "fetch", "origin", pilot_branch], cwd=repo_path)
    run(["git", "worktree", "add", "-b", branch, str(worktree_path), pilot_branch], cwd=repo_path)
    return worktree_path, branch


def planner(project: str, cfg: dict, run_dir: Path, policy: dict | None = None, supervised: bool = False) -> list[Path]:
    policy = policy or daily_loop_policy(project, cfg)
    values = base_prompt_values(project, cfg, run_dir)
    values["LOOP_CONTROL_POLICY"] = policy_prompt_snapshot(policy, supervised)
    prompt = render_template("planner.md", values)
    agent_exec(
        prompt,
        Path(cfg["repo_path"]),
        run_dir / "planner-last-message.md",
        run_dir,
        cfg.get("agents", {}).get("planner"),
    )
    return candidate_issue_paths(
        run_dir,
        min_value_score=int(policy.get("value_threshold") or DEFAULT_VALUE_THRESHOLD),
        supervised=supervised,
        medium_envelope=policy.get("medium_envelope"),
    )


def worker(project: str, cfg: dict, task_dir: Path, issue_path: Path, branch_suffix: str) -> tuple[Path, str]:
    repo_path = Path(cfg["repo_path"])
    ensure_project_clean(repo_path)
    worktree_path, branch = make_worktree(project, repo_path, cfg["pilot_branch"], branch_suffix)
    values = base_prompt_values(project, cfg, task_dir)
    values.update({
        "WORKTREE_PATH": str(worktree_path),
        "ISSUE_PATH": str(issue_path),
    })
    prompt = render_template("worker.md", values)
    agent_exec(
        prompt,
        worktree_path,
        task_dir / "worker-last-message.md",
        task_dir,
        cfg.get("agents", {}).get("worker"),
    )
    for index, command in enumerate(trusted_verification_commands(issue_path, cfg), start=1):
        run_verification_command(command, worktree_path, task_dir / f"verification-{index}.log")
    # SAFETY GATE: the worker may only touch files declared in the issue's
    # "Allowed Files". Enforced in code here, before anything is committed/pushed.
    changed = changed_files(worktree_path)
    (task_dir / "worker-git-status.txt").write_text("\n".join(changed) + "\n")
    if changed:
        allowed = parse_allowed_files(issue_path)
        if not allowed:
            raise RuntimeError(
                "worker made changes but issue has no parseable 'Allowed Files'; "
                "gating to human review"
            )
        violations = allowlist_violations(changed, allowed)
        (task_dir / "allowlist-check.txt").write_text(
            "allowed:\n" + "\n".join(allowed)
            + "\n\nchanged:\n" + "\n".join(changed)
            + "\n\nviolations:\n" + "\n".join(violations) + "\n"
        )
        if violations:
            raise RuntimeError(
                "worker changed files outside Allowed Files: " + ", ".join(violations)
            )
        # SAFETY GATE: block read-and-launder exfiltration of secrets.
        leaks = scan_changed_for_secrets(worktree_path, changed)
        if leaks:
            (task_dir / "secret-scan.txt").write_text("\n".join(leaks) + "\n")
            raise RuntimeError("worker output contains possible secret material: " + "; ".join(leaks))
        run(["git", "add", "-A"], cwd=worktree_path)
        title = first_heading(issue_path)
        run(["git", "commit", "-m", f"loop: {title}"], cwd=worktree_path)
        run(["git", "push", "-u", "origin", branch], cwd=worktree_path)
    return worktree_path, branch


def reviewer(project: str, cfg: dict, task_dir: Path, issue_path: Path, worktree_path: Path, branch: str) -> str:
    values = base_prompt_values(project, cfg, task_dir)
    values.update({
        "WORKTREE_PATH": str(worktree_path),
        "ISSUE_PATH": str(issue_path),
    })
    prompt = render_template("reviewer.md", values)
    # Anti-plant: the worker shares task_dir, so remove any pre-existing report
    # before the reviewer runs — the verdict must come from the reviewer agent.
    report = task_dir / "reviewer-report.md"
    report.unlink(missing_ok=True)
    agent_exec(
        prompt,
        worktree_path,
        task_dir / "reviewer-last-message.md",
        task_dir,
        cfg.get("agents", {}).get("reviewer"),
    )
    if not report.exists():
        raise RuntimeError("reviewer did not create reviewer-report.md")
    text = report.read_text()
    match = re.search(r"^REVIEW_STATUS:\s*(pass|fail|needs_human)\s*$", text, re.M)
    status = match.group(1) if match else "needs_human"
    if status == "pass":
        title = first_heading(issue_path)
        issue_url_path = task_dir / "github-issue-url.txt"
        issue_url = issue_url_path.read_text().strip() if issue_url_path.exists() else ""
        issue_line = f"GitHub issue: {issue_url}\n\n" if issue_url else ""
        body = (
            f"Loop task: `{task_dir.name}`\n\n"
            f"Issue contract: `{issue_path}`\n\n"
            f"{issue_line}"
            f"Reviewer status: `{status}`\n"
        )
        pr_log = task_dir / "github-pr.log"
        result = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                cfg["github_repo"],
                "--base",
                cfg["pilot_branch"],
                "--head",
                branch,
                "--title",
                f"[loop] {title}",
                "--body",
                body,
            ],
            cwd=worktree_path,
            log_path=pr_log,
            check=False,
        )
        if result.returncode != 0:
            status = "needs_human"
    return status


def github_number(url: str) -> str | None:
    match = re.search(r"/(issues|pull)/(\d+)$", url.strip())
    return match.group(2) if match else None


def merge_passed_task(cfg: dict, task_dir: Path, branch: str) -> dict:
    pr_log = task_dir / "github-pr.log"
    pr_url = ""
    if pr_log.exists():
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", pr_log.read_text())
        pr_url = match.group(0) if match else ""
    issue_path = task_dir / "github-issue-url.txt"
    issue_url = issue_path.read_text().strip() if issue_path.exists() else ""
    pr_number = github_number(pr_url) if pr_url else None
    issue_number = github_number(issue_url) if issue_url else None
    if not pr_number:
        return {"merged": False, "pr": pr_url, "issue": issue_url, "reason": "missing_pr"}
    merge_log = task_dir / "github-pr-merge.log"
    merge = run(
        [
            "gh",
            "pr",
            "merge",
            pr_number,
            "--repo",
            cfg["github_repo"],
            "--merge",
            "--delete-branch",
        ],
        cwd=Path(cfg["repo_path"]),
        log_path=merge_log,
        check=False,
    )
    if merge.returncode != 0:
        return {"merged": False, "pr": pr_url, "issue": issue_url, "reason": "merge_failed"}
    if issue_number:
        run(
            [
                "gh",
                "issue",
                "close",
                issue_number,
                "--repo",
                cfg["github_repo"],
                "--comment",
                f"Closed after PR #{pr_number} was merged by the loop.",
            ],
            cwd=Path(cfg["repo_path"]),
            log_path=task_dir / "github-issue-close.log",
            check=False,
        )
    run(["git", "fetch", "origin", cfg["pilot_branch"]], cwd=Path(cfg["repo_path"]))
    run(["git", "pull", "--ff-only"], cwd=Path(cfg["repo_path"]), check=False)
    return {"merged": True, "pr": pr_url, "issue": issue_url}


def set_phase(project: str, phase: str, run_id: str | None = None) -> None:
    state = load_state()
    project_state = state_project(state, project)
    if run_id is not None:
        project_state["current_run_id"] = run_id
    project_state["current_phase"] = phase
    project_state["phase_updated_at"] = now_iso()
    save_state(state)


def clear_phase(project: str) -> None:
    state = load_state()
    project_state = state_project(state, project)
    project_state["current_phase"] = "idle"
    project_state.pop("current_run_id", None)
    project_state["phase_updated_at"] = now_iso()
    save_state(state)


def control_state(project: str) -> str:
    state = load_state()
    return loop_job(state_project(state, project)).get("state", "stopped")


def start_loop(project: str) -> None:
    registry_project(project)
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    job.update({
        "state": "active",
        "mode": "hourly_forever",
        "cadence_seconds": DEFAULT_CADENCE_SECONDS,
        "started_at": job.get("started_at") or now_iso(),
        "updated_at": now_iso(),
        "next_cycle_at": now_iso(),
    })
    project_state["status"] = "active"
    project_state.setdefault("waiting_for_human", [])
    save_state(state)
    print(f"LOOP_STARTED project={project} cadence=hourly forever=true next_cycle_at={job['next_cycle_at']}")


def pause_loop(project: str) -> None:
    registry_project(project)
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    job.update({"state": "paused", "paused_at": now_iso(), "updated_at": now_iso(), "next_cycle_at": None})
    project_state["status"] = "paused"
    save_state(state)
    print(f"LOOP_PAUSED project={project}")


def resume_loop(project: str) -> None:
    registry_project(project)
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    job.update({
        "state": "active",
        "resumed_at": now_iso(),
        "updated_at": now_iso(),
        "next_cycle_at": now_iso(),
        "cadence_seconds": DEFAULT_CADENCE_SECONDS,
        "mode": "hourly_forever",
    })
    project_state["status"] = "active"
    save_state(state)
    print(f"LOOP_RESUMED project={project} next_cycle_at={job['next_cycle_at']}")


def stop_loop(project: str) -> None:
    registry_project(project)
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    job.update({
        "state": "stopped",
        "stopped_at": now_iso(),
        "updated_at": now_iso(),
        "next_cycle_at": None,
    })
    project_state["status"] = "stopped"
    save_state(state)
    print(f"LOOP_STOPPED project={project}")


def status_payload(project: str) -> dict:
    cfg = registry_project(project)
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    policy = daily_loop_policy(project, cfg)
    return {
        "project": project,
        "name": cfg.get("name"),
        "repo_path": cfg.get("repo_path"),
        "github_repo": cfg.get("github_repo"),
        "linear_project": cfg.get("linear_project"),
        "linear_control_issue": cfg.get("linear_control_issue"),
        "linear_control_issue_url": cfg.get("linear_control_issue_url"),
        "status": project_state.get("status"),
        "loop_job": job,
        "current_run_id": project_state.get("current_run_id"),
        "current_phase": project_state.get("current_phase", "idle"),
        "last_run_id": project_state.get("last_run_id"),
        "waiting_for_human": project_state.get("waiting_for_human", []),
        "loop_policy": {
            "recommended_cycles": policy.get("recommended_cycles"),
            "value_threshold": policy.get("value_threshold"),
            "max_noop_cycles": policy.get("max_noop_cycles"),
            "stop_condition": policy.get("stop_condition"),
            "medium_envelope": (policy.get("medium_envelope") or {}).get("name"),
        },
        "scheduler": scheduler_status_payload(project),
        "digest": {key: str(value) for key, value in report_paths(project).items() if key != "dir"},
    }


def display_text(value: object, max_chars: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace("`", "'").replace("#", "")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def memory_atom(value: object, max_chars: int = 180) -> str:
    text = str(value or "")
    text = re.sub(r"[^a-zA-Z0-9_./:#?=&-]+", "-", text).strip("-")
    return text[:max_chars] or "-"


def reviewer_status(task_dir: Path) -> str | None:
    report = task_dir / "reviewer-report.md"
    if not report.exists():
        return None
    lines = report.read_text().splitlines()
    first = lines[0] if lines else ""
    if first == "REVIEW_STATUS: pass":
        return "pass"
    if first == "REVIEW_STATUS: fail":
        return "fail"
    if first == "REVIEW_STATUS: needs_human":
        return "needs_human"
    return "unknown"


def verification_logs(task_dir: Path) -> list[str]:
    return sorted(path.name for path in task_dir.glob("verification-*.log"))


def waiting_item(run_id: str, index: int, item: dict) -> dict:
    enriched = dict(item)
    enriched.setdefault("id", f"{run_id}-wait-{index:03d}")
    reason = enriched.get("reason", "unknown")
    hints = {
        "untrusted_verification": "Edit the project contract if this command should become trusted; otherwise reject the task.",
        "blocked_category": "Review the issue manually; the planner touched a category reserved for human approval.",
        "max_tasks_per_cycle": "Leave queued for a future cycle or explicitly approve manual execution.",
        "task_gated": "Inspect task-error.txt, worker logs, and reviewer report before retrying.",
        "medium_risk_requires_approval": "Approve a bounded medium-risk envelope in the Daily PM Review before execution.",
        "medium_risk_requires_supervised_run": "Run `loop run-now --supervised` only while watching the first execution.",
        "medium_envelope_violation": "Tighten the issue to the preapproved file and verification envelope, or reject it.",
        "high_risk_requires_approval": "High-risk work must stay manual; do not run it through unattended loop execution.",
        "no_candidate_over_value_line": "Update the daily focus or accept that this project should stop for today.",
        "unsupported_risk": "Only low risk and supervised preapproved medium risk are executable by the loop.",
    }
    enriched.setdefault("approval_hint", hints.get(reason, "Review manually before allowing execution."))
    return enriched


def run_strategy_payload(run_dir: Path) -> dict:
    brief_path = run_dir / "strategy-brief.md"
    questions_path = run_dir / "elicitation-questions.md"
    payload: dict[str, object] = {
        "strategy_brief_path": str(brief_path) if brief_path.exists() else None,
        "elicitation_questions_path": str(questions_path) if questions_path.exists() else None,
        "brief_excerpt": None,
        "questions": [],
    }
    if brief_path.exists():
        text = " ".join(line.strip() for line in brief_path.read_text().splitlines() if line.strip())
        payload["brief_excerpt"] = display_text(text, max_chars=420)
    if questions_path.exists():
        questions: list[str] = []
        for line in questions_path.read_text().splitlines():
            item = line.strip().lstrip("-*0123456789. ").strip()
            if item and not item.startswith("#"):
                questions.append(display_text(item, max_chars=240))
        payload["questions"] = questions[:5]
    return payload


def write_cycle_summary(
    project: str,
    run_id: str,
    run_dir: Path,
    status: str,
    started_at: str | None,
    tasks: list[dict],
    waiting_for_human: list[dict],
    control_gate: dict | None = None,
) -> dict:
    completed_at = now_iso()
    task_rows = []
    for task in tasks:
        task_dir = run_dir / "tasks" / task["task_id"]
        issue_path = Path(task["issue_path"])
        task_rows.append({
            "task_id": task["task_id"],
            "title": first_heading(issue_path) if issue_path.exists() else task["task_id"],
            "status": task.get("status"),
            "github_issue": task.get("github_issue"),
            "github_pr": task.get("github_pr"),
            "branch": task.get("branch"),
            "verification_logs": verification_logs(task_dir),
            "review_status": reviewer_status(task_dir),
            "product_impact": product_impact_for_task(run_dir, task),
        })
    waiting_rows = [
        waiting_item(run_id, index, item)
        for index, item in enumerate(waiting_for_human, start=1)
    ]
    completed_dt = parse_iso(completed_at)
    started_dt = parse_iso(started_at)
    elapsed_seconds = None
    if started_dt is not None and completed_dt is not None:
        elapsed_seconds = int((completed_dt - started_dt).total_seconds())
    metrics = {
        "tasks_total": len(task_rows),
        "tasks_merged": sum(1 for task in task_rows if task["status"] == "merged"),
        "tasks_blocked": len(waiting_rows),
        "verification_failures": sum(1 for item in waiting_rows if "verification" in item.get("reason", "")),
        "review_failures": sum(1 for task in task_rows if task.get("review_status") == "fail"),
        "elapsed_seconds": elapsed_seconds,
    }
    summary = {
        "schema_version": 1,
        "project": project,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "tasks": task_rows,
        "waiting_for_human": waiting_rows,
        "orchestrator_strategy": run_strategy_payload(run_dir),
        "control_gate": control_gate or {},
        "metrics": metrics,
    }
    (run_dir / "cycle-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    write_cycle_summary_markdown(run_dir / "cycle-summary.md", summary)
    return summary


def write_cycle_summary_markdown(path: Path, summary: dict) -> None:
    lines = [
        f"# Loop Cycle {display_text(summary['run_id'])}",
        "",
        f"- Project: `{display_text(summary['project'])}`",
        f"- Status: `{display_text(summary['status'])}`",
        f"- Completed: `{display_text(summary['completed_at'])}`",
        f"- Merged tasks: {summary['metrics']['tasks_merged']} / {summary['metrics']['tasks_total']}",
        f"- Waiting for human: {len(summary['waiting_for_human'])}",
        "",
    ]
    control_gate = summary.get("control_gate") or {}
    if control_gate:
        lines.extend([
            "## Control Gate",
            "",
            f"- Reason: `{display_text(control_gate.get('reason') or '-')}`",
            f"- Action: {display_text(control_gate.get('action') or '-')}",
            f"- Detail: {display_text(control_gate.get('detail') or '-')}",
            "",
        ])
    lines.extend([
        "## Orchestrator Strategy",
        "",
    ])
    strategy = summary.get("orchestrator_strategy") or {}
    if strategy.get("brief_excerpt"):
        lines.append(f"- Brief: {display_text(strategy.get('brief_excerpt'))}")
    else:
        lines.append("- Brief: -")
    questions = strategy.get("questions") or []
    if questions:
        lines.append("- Questions for the operator:")
        for question in questions:
            lines.append(f"  - {display_text(question)}")
    else:
        lines.append("- Questions for the operator: -")
    lines.extend([
        "",
        "## Completed Work",
        "",
    ])
    for task in summary["tasks"]:
        lines.append(
            f"- `{display_text(task['task_id'])}` `{display_text(task['status'])}` "
            f"{display_text(task['title'])} PR: {display_text(task.get('github_pr') or '-')}"
        )
    lines.extend(["", "## Product Impact", ""])
    if not summary["tasks"]:
        lines.append("- No product change was merged in this cycle.")
    for task in summary["tasks"]:
        impact = task.get("product_impact") or {}
        lines.append(f"- `{display_text(task['task_id'])}` {display_text(task.get('title'))}")
        if impact.get("category") or impact.get("surface"):
            lines.append(
                f"  - Category/surface: {display_text(impact.get('category') or '-')} / "
                f"{display_text(impact.get('surface') or '-')}"
            )
        lines.append(f"  - Visibility: {display_text(impact.get('visibility') or '-')}")
        lines.append(f"  - Before: {display_text(impact.get('before') or '-')}")
        lines.append(f"  - After: {display_text(impact.get('after') or '-')}")
        lines.append(f"  - User benefit: {display_text(impact.get('user_benefit') or '-')}")
        lines.append(f"  - Evidence: {display_text(impact.get('evidence') or '-')}")
    lines.extend(["", "## Waiting for human", ""])
    for item in summary["waiting_for_human"]:
        lines.append(
            f"- `{display_text(item['id'])}` `{display_text(item.get('reason'))}` "
            f"{display_text(item.get('approval_hint'))}"
        )
    path.write_text("\n".join(lines).rstrip() + "\n")


def insert_memory_lines(text: str, heading: str, new_lines: list[str], max_section_lines: int) -> str:
    if not new_lines:
        return text
    lines = text.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        lines.extend(["", heading])
        start = len(lines)
    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith("## ") and idx != start:
            end = idx
            break
    existing = [line for line in lines[start:end] if line.strip()]
    combined = (new_lines + existing)[:max_section_lines]
    return "\n".join(lines[:start] + combined + [""] + lines[end:]).rstrip() + "\n"


def distill_cycle_learning(project: str, summary: dict) -> None:
    append_learning_event(project, {
        "schema_version": 1,
        "type": "cycle_completed",
        "project": project,
        "run_id": summary["run_id"],
        "status": summary["status"],
        "metrics": summary.get("metrics", {}),
        "created_at": summary.get("completed_at") or now_iso(),
    })
    paths = knowledge_paths(project)
    text = paths["state"].read_text()
    lesson_lines: list[str] = []
    control_gate = summary.get("control_gate") or {}
    if summary.get("status") == "no_op" and control_gate.get("reason"):
        lesson_lines.append(
            f"- {memory_atom(summary['completed_at'])} {memory_atom(summary['run_id'])}: "
            f"Loop chose no-op because reason={memory_atom(control_gate.get('reason'))}. "
            "Future planner runs must not invent low-value work just to stay busy."
        )
    for item in summary.get("waiting_for_human", []):
        if item.get("reason") == "untrusted_verification":
            lesson_lines.append(
                f"- {memory_atom(summary['completed_at'])} {memory_atom(summary['run_id'])}: "
                "Planner proposed an untrusted verification command. Future planner runs must only use trusted contract commands."
            )
        elif item.get("reason") == "blocked_category":
            lesson_lines.append(
                f"- {memory_atom(summary['completed_at'])} {memory_atom(summary['run_id'])}: "
                "Planner proposed work in a blocked category. Future planner runs must choose safer tests, docs, validation, or parsing work."
            )
        elif item.get("reason") == "task_gated":
            lesson_lines.append(
                f"- {memory_atom(summary['completed_at'])} {memory_atom(summary['run_id'])}: "
                f"Worker task was gated with reason={memory_atom(item.get('reason'))}. "
                "Inspect cycle-summary.md for the human-readable error."
            )
    accepted_lines: list[str] = []
    for task in summary.get("tasks", []):
        if task.get("status") == "merged":
            accepted_lines.append(
                f"- {memory_atom(summary['run_id'])} {memory_atom(task.get('task_id'))} "
                f"merged PR: {memory_atom(task.get('github_pr') or '-')}"
            )
    text = insert_memory_lines(text, "## Recent Lessons", lesson_lines, max_section_lines=25)
    text = insert_memory_lines(text, "## Recent Accepted Work", accepted_lines, max_section_lines=25)
    paths["state"].write_text(text)


def base_prompt_values(project: str, cfg: dict, run_dir: Path) -> dict[str, str]:
    return {
        "PROJECT_NAME": cfg["name"],
        "REPO_PATH": cfg.get("repo_path", ""),
        "CONTRACT_PATH": cfg.get("contract_path", ""),
        "RUN_DIR": str(run_dir),
        "LOOP_MEMORY": loop_memory_snapshot(project),
        "DAILY_FOCUS": daily_focus_snapshot(cfg),
        "HUMAN_FEEDBACK": human_feedback_snapshot(project),
    }


def truncate_prompt_text(text: str, max_chars: int, label: str) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 80].rstrip() + f"\n\n[{label} truncated to fit prompt budget]\n"


def markdown_snapshot(path: Path, allowed_root: Path, label: str, missing: str, max_chars: int) -> str:
    if not path.exists():
        return missing
    try:
        root = allowed_root.resolve()
        if path.is_symlink() or path.parent.is_symlink():
            return f"{label} ignored: path is a symlink."
        resolved_path = path.resolve()
        if root != resolved_path and root not in resolved_path.parents:
            return f"{label} ignored: path escapes allowed root."
        if not resolved_path.is_file():
            return f"{label} ignored: path is not a regular file."
        text = resolved_path.read_text()
    except OSError as exc:
        return f"{label} unavailable: {exc}"
    return truncate_prompt_text(text, max_chars, label)


def daily_focus_snapshot(cfg: dict, max_chars: int = 6000) -> str:
    repo_path_raw = cfg.get("repo_path")
    if not repo_path_raw:
        return "No approved daily focus found for this project."
    repo_path = Path(repo_path_raw)
    focus_path = repo_path / ".loop" / "daily-focus" / "latest.md"
    return markdown_snapshot(
        focus_path,
        repo_path,
        "Daily focus",
        "No approved daily focus found for this project.",
        max_chars,
    )


def parse_markdown_control_fields(text: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    current_key: str | None = None
    in_code_block = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            current_key = None
            continue
        if in_code_block:
            continue
        if stripped.startswith("#"):
            current_key = None
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", raw)
        if match:
            key = match.group(1).strip().lower().replace("-", "_")
            value = match.group(2).strip()
            if value:
                fields[key] = value
                current_key = None
            else:
                fields[key] = []
                current_key = key
            continue
        if current_key:
            item = re.match(r"^\s*[-*]\s+(.+?)\s*$", raw)
            if item:
                values = fields.setdefault(current_key, [])
                if isinstance(values, list):
                    values.append(item.group(1).strip())
            elif stripped:
                current_key = None
    return fields


def field_as_list(fields: dict[str, object], key: str) -> list[str]:
    value = fields.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        if "," in value:
            return [item.strip() for item in value.split(",") if item.strip()]
        return [value.strip()] if value.strip() else []
    return []


def field_as_bool(fields: dict[str, object], key: str, default: bool) -> bool:
    value = fields.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def field_as_int(fields: dict[str, object], key: str, default: int) -> int:
    value = fields.get(key)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_recommended_cycles(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "none", "unlimited", "forever"}:
        return None
    range_match = re.search(r"(\d+)\s*(?:-|to|–|—)\s*(\d+)", text)
    if range_match:
        return int(range_match.group(2))
    number = re.search(r"\d+", text)
    return int(number.group(0)) if number else None


def daily_loop_policy(project: str, cfg: dict) -> dict:
    text = daily_focus_snapshot(cfg, max_chars=12000)
    fields = parse_markdown_control_fields(text)
    recommended_cycles = parse_recommended_cycles(fields.get("recommended_cycles"))
    value_threshold = max(1, min(5, field_as_int(fields, "value_threshold", DEFAULT_VALUE_THRESHOLD)))
    max_noop_cycles = max(1, field_as_int(fields, "max_noop_cycles", DEFAULT_MAX_NOOP_CYCLES))
    envelope_name = str(
        fields.get("preapproved_medium_risk")
        or fields.get("medium_risk_envelope")
        or ""
    ).strip()
    medium_envelope: dict | None = None
    if envelope_name:
        medium_envelope = {
            "name": envelope_name,
            "supervised_first_run": field_as_bool(
                fields, "preapproved_medium_risk_supervised_first_run", True
            ),
            "allowed_files": field_as_list(fields, "preapproved_medium_risk_allowed_files"),
            "verification_commands": field_as_list(
                fields, "preapproved_medium_risk_verification_commands"
            ),
        }
    return {
        "project": project,
        "recommended_cycles": recommended_cycles,
        "recommended_cycles_raw": fields.get("recommended_cycles"),
        "stop_condition": str(fields.get("stop_condition") or "").strip(),
        "stop_condition_file": str(fields.get("stop_condition_file") or "").strip(),
        "stop_condition_contains": field_as_list(fields, "stop_condition_contains"),
        "value_threshold": value_threshold,
        "allow_do_nothing": field_as_bool(fields, "allow_do_nothing", True),
        "max_noop_cycles": max_noop_cycles,
        "medium_envelope": medium_envelope,
        "raw_fields": fields,
    }


def policy_prompt_snapshot(policy: dict, supervised: bool) -> str:
    envelope = policy.get("medium_envelope") or {}
    lines = [
        "Loop control policy:",
        f"- supervised_run: {str(supervised).lower()}",
        f"- value_threshold: {policy.get('value_threshold')}",
        f"- recommended_cycles: {policy.get('recommended_cycles_raw') or policy.get('recommended_cycles') or 'not set'}",
        f"- stop_condition: {policy.get('stop_condition') or 'not set'}",
        f"- allow_do_nothing: {str(policy.get('allow_do_nothing')).lower()}",
    ]
    if envelope:
        lines.extend([
            f"- preapproved_medium_risk: {envelope.get('name')}",
            f"- medium_supervised_first_run: {str(envelope.get('supervised_first_run', True)).lower()}",
            "- medium_allowed_files:",
        ])
        lines.extend(f"  - {item}" for item in envelope.get("allowed_files") or ["-"])
        lines.append("- medium_verification_commands:")
        lines.extend(f"  - {item}" for item in envelope.get("verification_commands") or ["-"])
    else:
        lines.append("- preapproved_medium_risk: none")
    return "\n".join(lines)


def today_run_entries(project: str) -> list[dict]:
    state = load_state()
    prefix = f"{project}-{dt.datetime.now().strftime('%Y%m%d')}"
    runs = state_project(state, project).get("runs", [])
    return [run for run in runs if str(run.get("run_id", "")).startswith(prefix)]


def today_executed_cycle_count(project: str) -> int:
    return sum(1 for run in today_run_entries(project) if run.get("status") != "no_op")


def consecutive_noop_count(project: str) -> int:
    state = load_state()
    count = 0
    for run in reversed(state_project(state, project).get("runs", [])):
        if run.get("status") != "no_op":
            break
        count += 1
    return count


def stop_condition_met(cfg: dict, policy: dict) -> bool:
    rel = policy.get("stop_condition_file")
    needles = policy.get("stop_condition_contains") or []
    if not rel or not needles:
        return False
    repo_path = Path(cfg.get("repo_path", ""))
    if not repo_path:
        return False
    norm = _norm_path(str(rel))
    if not norm or ".." in norm:
        return False
    path = repo_path / norm
    try:
        repo_root = repo_path.resolve()
        if path.is_symlink() or path.parent.is_symlink():
            return False
        resolved = path.resolve()
        if repo_root != resolved and repo_root not in resolved.parents:
            return False
        if not resolved.is_file():
            return False
        text = resolved.read_text(errors="ignore")
    except OSError:
        return False
    return all(str(needle) in text for needle in needles)


def pause_loop_for_gate(project: str, reason: str) -> None:
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    job.update({
        "state": "paused",
        "paused_at": now_iso(),
        "updated_at": now_iso(),
        "next_cycle_at": None,
        "pause_reason": reason,
    })
    project_state["status"] = "paused"
    save_state(state)


def human_feedback_snapshot(project: str, max_chars: int = 9000) -> str:
    sections = [
        (
            "Latest Cross-Project Daily PM Review",
            markdown_snapshot(
                ENGINE_ROOT / "pm-reviews" / "latest.md",
                ENGINE_ROOT,
                "Daily PM review",
                "No cross-project Daily PM Review has been archived yet.",
                3000,
            ),
        ),
        (
            "Answered Orchestrator Questions",
            markdown_snapshot(
                ENGINE_ROOT / "human-feedback" / project / "elicitation-answers.md",
                ENGINE_ROOT,
                "Elicitation answers",
                "No answered Orchestrator questions have been archived for this project yet.",
                3000,
            ),
        ),
        (
            "Latest Evening Scorecard",
            markdown_snapshot(
                ENGINE_ROOT / "evening-scorecards" / "latest.md",
                ENGINE_ROOT,
                "Evening scorecard",
                "No evening scorecard has been archived yet.",
                3000,
            ),
        ),
    ]
    text = "\n\n".join(f"## {title}\n\n{body}" for title, body in sections)
    return truncate_prompt_text(text, max_chars, "Human feedback")


def report_paths(project: str) -> dict[str, Path]:
    root = ENGINE_ROOT / "reports" / project
    return {"dir": root, "markdown": root / "latest.md", "html": root / "latest.html"}


def hydrated_run_entry(run_entry: dict) -> dict:
    run_id = run_entry.get("run_id")
    run_dir = ENGINE_ROOT / "runs" / str(run_id)
    summary_path = run_dir / "cycle-summary.json"
    enriched = dict(run_entry)
    state_tasks_by_id = {
        task.get("task_id"): task for task in run_entry.get("tasks", []) if task.get("task_id")
    }
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
            enriched["status"] = summary.get("status", enriched.get("status"))
            summary_tasks = []
            for task in summary.get("tasks", enriched.get("tasks", [])):
                base = state_tasks_by_id.get(task.get("task_id"), {})
                summary_tasks.append({**base, **task})
            enriched["tasks"] = summary_tasks
            enriched["waiting_for_human"] = summary.get(
                "waiting_for_human", enriched.get("waiting_for_human", [])
            )
            enriched["orchestrator_strategy"] = summary.get(
                "orchestrator_strategy", enriched.get("orchestrator_strategy")
            )
            enriched["control_gate"] = summary.get("control_gate", enriched.get("control_gate"))
        except json.JSONDecodeError:
            pass
    if "orchestrator_strategy" not in enriched:
        enriched["orchestrator_strategy"] = run_strategy_payload(run_dir)
    tasks = []
    for task in enriched.get("tasks", []):
        task_row = dict(task)
        raw_issue_path = task_row.get("issue_path")
        issue_path = Path(raw_issue_path) if raw_issue_path else None
        if "title" not in task_row and issue_path and issue_path.exists() and issue_path.is_file():
            task_row["title"] = first_heading(issue_path)
        if "product_impact" not in task_row:
            task_row["product_impact"] = product_impact_for_task(run_dir, task_row)
        tasks.append(task_row)
    enriched["tasks"] = tasks
    return enriched


def digest_payload(project: str, limit: int = 5) -> dict:
    cfg = registry_project(project)
    state = load_state()
    project_state = state_project(state, project)
    runs = list(project_state.get("runs", []))[-limit:]
    runs = [hydrated_run_entry(run) for run in reversed(runs)]
    return {
        "project": project,
        "name": cfg.get("name"),
        "status": project_state.get("status"),
        "loop_job": loop_job(project_state),
        "github_repo": cfg.get("github_repo"),
        "linear_project": cfg.get("linear_project"),
        "linear_control_issue": cfg.get("linear_control_issue"),
        "linear_control_issue_url": cfg.get("linear_control_issue_url"),
        "last_run_id": project_state.get("last_run_id"),
        "current_phase": project_state.get("current_phase", "idle"),
        "recent_runs": runs,
        "approval_queue": project_state.get("waiting_for_human", []),
    }


def render_digest_markdown(payload: dict) -> str:
    lines = [
        f"# Loop Digest: {display_text(payload['project'])}",
        "",
        f"- Status: `{display_text(payload.get('status'))}`",
        f"- Phase: `{display_text(payload.get('current_phase') or 'idle')}`",
        f"- Last run: `{display_text(payload.get('last_run_id') or '-')}`",
        f"- GitHub: `{display_text(payload.get('github_repo') or '-')}`",
        f"- Linear: `{display_text(payload.get('linear_project') or '-')}`",
        "",
        "## Recent work",
        "",
    ]
    for run_entry in payload.get("recent_runs", []):
        lines.append(f"- `{display_text(run_entry.get('run_id'))}` `{display_text(run_entry.get('status'))}`")
        strategy = run_entry.get("orchestrator_strategy") or {}
        if strategy.get("brief_excerpt"):
            lines.append(f"  - Strategy: {display_text(strategy.get('brief_excerpt'))}")
        if strategy.get("questions"):
            lines.append("  - Questions for the operator:")
            for question in strategy.get("questions", [])[:5]:
                lines.append(f"    - {display_text(question)}")
        control_gate = run_entry.get("control_gate") or {}
        if control_gate:
            lines.append(
                f"  - Control gate: `{display_text(control_gate.get('reason') or '-')}` "
                f"{display_text(control_gate.get('action') or '-')}"
            )
        for task in run_entry.get("tasks", []):
            title = display_text(task.get("title") or task.get("task_id"))
            lines.append(
                f"  - `{display_text(task.get('task_id'))}` `{display_text(task.get('status'))}` "
                f"{title} PR: {display_text(task.get('github_pr') or '-')}"
            )
            impact = task.get("product_impact") or {}
            if impact:
                if impact.get("category") or impact.get("surface"):
                    lines.append(
                        f"    - Category/surface: {display_text(impact.get('category') or '-')} / "
                        f"{display_text(impact.get('surface') or '-')}"
                    )
                lines.append(f"    - Visibility: {display_text(impact.get('visibility') or '-')}")
                lines.append(f"    - Before: {display_text(impact.get('before') or '-')}")
                lines.append(f"    - After: {display_text(impact.get('after') or '-')}")
                lines.append(f"    - User benefit: {display_text(impact.get('user_benefit') or '-')}")
    lines.extend(["", "## Waiting for approval", ""])
    queue = payload.get("approval_queue", [])
    if not queue:
        lines.append("- None")
    for item in queue:
        detail = display_text(item.get("approval_hint") or item.get("error") or "")
        lines.append(f"- `{display_text(item.get('id') or '-')}` `{display_text(item.get('reason'))}` {detail}")
    return "\n".join(lines).rstrip() + "\n"


def render_digest_html(payload: dict, markdown_text: str) -> str:
    escaped = (
        markdown_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>Loop Digest: {display_text(payload['project'])}</title>"
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:980px;margin:32px auto;padding:0 20px;line-height:1.5;color:#1f2328}"
        "pre{white-space:pre-wrap;background:#f6f8fa;border:1px solid #d0d7de;padding:16px;border-radius:8px}"
        "</style>"
        "</head><body>"
        f"<pre>{escaped}</pre>"
        "</body></html>"
    )


def write_digest_report(project: str, payload: dict) -> dict[str, Path]:
    paths = report_paths(project)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    md = render_digest_markdown(payload)
    paths["markdown"].write_text(md)
    paths["html"].write_text(render_digest_html(payload, md))
    return paths


def print_status(project: str, as_json: bool) -> None:
    payload = status_payload(project)
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    job = payload["loop_job"]
    print(f"PROJECT {project} ({payload.get('name')})")
    print(f"STATE {job.get('state')} mode={job.get('mode')} cadence_seconds={job.get('cadence_seconds')}")
    print(f"PHASE {payload.get('current_phase')} current_run_id={payload.get('current_run_id') or '-'}")
    print(f"LAST_RUN {payload.get('last_run_id') or '-'}")
    print(f"NEXT_CYCLE {job.get('next_cycle_at') or '-'}")
    print(f"WAITING_FOR_HUMAN {len(payload.get('waiting_for_human') or [])}")
    policy = payload.get("loop_policy") or {}
    print(
        "POLICY "
        f"recommended_cycles={policy.get('recommended_cycles') or '-'} "
        f"value_threshold={policy.get('value_threshold') or '-'} "
        f"max_noop_cycles={policy.get('max_noop_cycles') or '-'} "
        f"medium_envelope={policy.get('medium_envelope') or '-'}"
    )
    print(f"GITHUB {payload.get('github_repo') or '-'}")
    print(f"LINEAR_PROJECT {payload.get('linear_project') or '-'}")
    print(f"LINEAR_CONTROL {payload.get('linear_control_issue') or '-'} {payload.get('linear_control_issue_url') or ''}".rstrip())
    digest = payload.get("digest") or {}
    print(f"DIGEST {digest.get('markdown') or '-'} {digest.get('html') or ''}".rstrip())
    scheduler = payload.get("scheduler") or {}
    print(f"SCHEDULER installed={scheduler.get('installed')} loaded={scheduler.get('loaded')} label={scheduler.get('label')}")


def init_command(project: str | None, cwd: Path) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=True)
    print(f"LOOP_INITIALIZED project={resolved}")
    print_status(resolved, False)


def status_command(project: str | None, cwd: Path, as_json: bool) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    print_status(resolved, as_json)


def digest_command(project: str | None, cwd: Path, as_json: bool = False) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    payload = digest_payload(resolved)
    paths = write_digest_report(resolved, payload)
    if as_json:
        print(json.dumps({
            "payload": payload,
            "reports": {key: str(value) for key, value in paths.items() if key != "dir"},
        }, indent=2, ensure_ascii=False))
        return
    print(render_digest_markdown(payload).rstrip())
    print(f"REPORT_MARKDOWN {paths['markdown']}")
    print(f"REPORT_HTML {paths['html']}")


def start_command(project: str | None, cwd: Path) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    payload = status_payload(resolved)
    was_active = payload.get("loop_job", {}).get("state") == "active"
    load_scheduler(resolved)
    if was_active:
        print(f"LOOP_ALREADY_ACTIVE project={resolved}")
        print_status(resolved, False)
        return
    start_loop(resolved)
    tick(resolved)
    print_status(resolved, False)


def pause_command(project: str | None, cwd: Path) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    pause_loop(resolved)
    print_status(resolved, False)


def resume_command(project: str | None, cwd: Path) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    resume_loop(resolved)
    tick(resolved)
    print_status(resolved, False)


def stop_command(project: str | None, cwd: Path) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    stop_loop(resolved)
    uninstall_scheduler(resolved)
    print_status(resolved, False)


def run_now_command(project: str | None, cwd: Path, supervised: bool = False) -> None:
    resolved = resolve_project(cwd, project=project, bootstrap=False)
    result = cycle(resolved, supervised=supervised)
    print(f"LOOP_RUN_NOW_COMPLETE project={resolved} run_id={result.get('run_id')} status={result.get('status')}")
    print_status(resolved, False)


def schedule_next_cycle(project: str) -> None:
    state = load_state()
    project_state = state_project(state, project)
    job = loop_job(project_state)
    if job.get("state") == "active":
        job["next_cycle_at"] = plus_seconds(int(job.get("cadence_seconds") or DEFAULT_CADENCE_SECONDS))
        job["updated_at"] = now_iso()
    save_state(state)


def due_for_cycle(project: str) -> bool:
    state = load_state()
    job = loop_job(state_project(state, project))
    if job.get("state") != "active":
        return False
    next_at = parse_iso(job.get("next_cycle_at"))
    return next_at is None or next_at <= dt.datetime.now()


def tick(project: str | None) -> None:
    registry = load_json(REGISTRY_PATH)
    projects = [project] if project else sorted(registry.get("projects", {}).keys())
    for name in projects:
        registry_project(name)
        if not due_for_cycle(name):
            print(f"LOOP_TICK_SKIP project={name} reason=not_due_or_inactive", flush=True)
            continue
        try:
            result = cycle(name)
            schedule_next_cycle(name)
            print(f"LOOP_TICK_RAN project={name} run_id={result.get('run_id')} status={result.get('status')}", flush=True)
        except Exception as exc:
            schedule_next_cycle(name)
            print(f"LOOP_TICK_FAILED project={name} error={exc}", flush=True)


def update_state(
    project: str,
    run_id: str,
    status: str,
    branch: str | None,
    tasks: list[dict] | None = None,
    waiting_for_human: list[dict] | None = None,
    metadata: dict | None = None,
) -> None:
    state = load_json(STATE_PATH)
    project_state = state.setdefault("projects", {}).setdefault(project, {})
    project_state["status"] = status
    project_state["last_run_id"] = run_id
    project_state["waiting_for_human"] = waiting_for_human or []
    entry = {
        "run_id": run_id,
        "status": status,
        "branch": branch,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if tasks is not None:
        entry["tasks"] = tasks
    if metadata:
        entry.update(metadata)
    if status == "merged" and tasks:
        merged_prs = [
            task.get("github_pr")
            for task in tasks
            if task.get("status") == "merged" and task.get("github_pr")
        ]
        if merged_prs:
            project_state["last_merged_pr"] = merged_prs[-1]
    if waiting_for_human:
        entry["waiting_for_human"] = waiting_for_human
    project_state.setdefault("runs", []).append(entry)
    write_json(STATE_PATH, state)


def finish_controlled_cycle(
    project: str,
    cfg: dict,
    run_id: str,
    run_dir: Path,
    started_at: str,
    status: str,
    task_results: list[dict],
    waiting_for_human: list[dict],
    milestone_id: str | None,
    control_gate: dict | None = None,
    pause_reason: str | None = None,
) -> dict:
    set_phase(project, "cycle_complete", run_id)
    summary = write_cycle_summary(
        project,
        run_id,
        run_dir,
        status,
        started_at,
        task_results,
        waiting_for_human,
        control_gate=control_gate,
    )
    distill_cycle_learning(project, summary)
    metadata = {"control_gate": control_gate} if control_gate else None
    update_state(project, run_id, status, None, task_results, waiting_for_human, metadata=metadata)
    if pause_reason:
        pause_loop_for_gate(project, pause_reason)
    linear_comment(
        cfg,
        run_dir,
        "cycle-complete",
        f"Loop cycle `{run_id}` completed with status `{status}`.\n\n"
        f"- Tasks: {len(task_results)}\n"
        f"- Merged: {sum(1 for task in task_results if task['status'] == 'merged')}\n"
        f"- Waiting for human: {len(waiting_for_human)}\n"
        f"- Control gate: `{(control_gate or {}).get('reason') or 'none'}`",
    )
    linear_update_milestone(
        cfg,
        milestone_id,
        milestone_description(run_id, status, task_results, waiting_for_human),
        run_dir,
        "cycle-complete",
    )
    clear_phase(project)
    print(
        f"RUN_COMPLETE {run_id} {status} "
        f"tasks={len(task_results)} waiting_for_human={len(waiting_for_human)}"
    )
    return {
        "run_id": run_id,
        "status": status,
        "tasks": task_results,
        "waiting_for_human": waiting_for_human,
        "control_gate": control_gate or {},
    }


def cycle(project: str, locked: bool = True, supervised: bool = False) -> dict:
    if locked:
        with with_project_lock(project):
            return cycle(project, locked=False, supervised=supervised)
    registry = load_json(REGISTRY_PATH)
    cfg = registry["projects"][project]
    policy = daily_loop_policy(project, cfg)
    run_id = safe_run_id(project)
    started_at = dt.datetime.now().isoformat(timespec="seconds")
    run_dir = ENGINE_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "project": project,
        "run_id": run_id,
        "started_at": started_at,
        "supervised": supervised,
        "loop_policy": {
            "recommended_cycles": policy.get("recommended_cycles"),
            "value_threshold": policy.get("value_threshold"),
            "stop_condition": policy.get("stop_condition"),
            "medium_envelope": (policy.get("medium_envelope") or {}).get("name"),
        },
    }, indent=2) + "\n")
    set_phase(project, "cycle_start", run_id)
    if stop_condition_met(cfg, policy):
        return finish_controlled_cycle(
            project,
            cfg,
            run_id,
            run_dir,
            started_at,
            "no_op",
            [],
            [],
            None,
            control_gate={
                "reason": "stop_condition_met",
                "action": "paused_loop",
                "detail": policy.get("stop_condition") or "Configured stop condition matched.",
            },
            pause_reason="stop_condition_met",
        )
    recommended_cycles = policy.get("recommended_cycles")
    if recommended_cycles is not None and today_executed_cycle_count(project) >= int(recommended_cycles):
        return finish_controlled_cycle(
            project,
            cfg,
            run_id,
            run_dir,
            started_at,
            "no_op",
            [],
            [],
            None,
            control_gate={
                "reason": "recommended_cycles_exhausted",
                "action": "paused_loop",
                "detail": f"Today already ran {today_executed_cycle_count(project)} cycle(s); budget is {recommended_cycles}.",
            },
            pause_reason="recommended_cycles_exhausted",
        )
    milestone = linear_create_milestone(
        cfg,
        run_id,
        milestone_description(run_id, "cycle_start"),
        run_dir,
    )
    milestone_id = milestone.get("id") if milestone else None
    if milestone:
        state = load_state()
        state_project(state, project)["current_milestone"] = milestone
        save_state(state)
    linear_comment(
        cfg,
        run_dir,
        "cycle-start",
        f"Loop cycle `{run_id}` started for `{cfg['name']}`.\n\n"
        f"- Project repo: `{cfg['repo_path']}`\n"
        f"- Pilot branch: `{cfg['pilot_branch']}`\n"
        f"- Contract: `{cfg['contract_path']}`",
    )
    repo_path = Path(cfg["repo_path"])
    auto = cfg.get("auto_approval", {})
    # Fail closed: if not configured, screen against ALL known categories and
    # cap tasks at a safe default, rather than running unscreened / unbounded.
    blocked_categories = auto.get("blocked_categories") or list(BLOCKED_SIGNALS.keys())
    max_tasks = max_tasks_per_cycle(auto)
    branch = None
    task_results: list[dict] = []
    waiting_for_human: list[dict] = []
    deferred_issue_paths: list[Path] = []
    try:
        set_phase(project, "planner", run_id)
        issue_paths = planner(project, cfg, run_dir, policy=policy, supervised=supervised)
        waiting_for_human.extend(candidate_gate_items(
            run_dir,
            int(policy.get("value_threshold") or DEFAULT_VALUE_THRESHOLD),
            supervised,
            policy.get("medium_envelope"),
        ))
        # SAFETY: catch a planner that polluted the product working tree (its
        # cwd) before any worker picks up stray changes via git.
        ensure_project_clean(repo_path)
        if not issue_paths:
            value_summary = candidate_value_summary(
                run_dir,
                int(policy.get("value_threshold") or DEFAULT_VALUE_THRESHOLD),
            )
            if waiting_for_human:
                return finish_controlled_cycle(
                    project,
                    cfg,
                    run_id,
                    run_dir,
                    started_at,
                    "needs_human",
                    [],
                    waiting_for_human,
                    milestone_id,
                    control_gate={
                        "reason": "no_auto_executable_candidates",
                        "action": "paused_loop",
                        "detail": "Planner found candidates, but they require human approval or supervised execution.",
                        **value_summary,
                    },
                    pause_reason="waiting_for_human_no_auto_tasks",
                )
            pause_after_noop = consecutive_noop_count(project) + 1 >= int(policy.get("max_noop_cycles") or DEFAULT_MAX_NOOP_CYCLES)
            if not policy.get("allow_do_nothing", True):
                waiting_for_human.append({
                    "reason": "no_candidate_over_value_line",
                    "approval_hint": "No candidate cleared the value line; update daily focus or lower the value threshold explicitly.",
                })
                return finish_controlled_cycle(
                    project,
                    cfg,
                    run_id,
                    run_dir,
                    started_at,
                    "needs_human",
                    [],
                    waiting_for_human,
                    milestone_id,
                    control_gate={
                        "reason": "no_candidate_over_value_line",
                        "action": "paused_loop",
                        "detail": "No candidate cleared the value line and do-nothing is disabled.",
                        **value_summary,
                    },
                    pause_reason="no_candidate_over_value_line",
                )
            return finish_controlled_cycle(
                project,
                cfg,
                run_id,
                run_dir,
                started_at,
                "no_op",
                [],
                [],
                milestone_id,
                control_gate={
                    "reason": "no_candidate_over_value_line",
                    "action": "paused_loop" if pause_after_noop else "wait_until_next_cycle",
                    "detail": "Planner produced no auto-executable candidate above today's value threshold.",
                    **value_summary,
                },
                pause_reason="no_candidate_over_value_line" if pause_after_noop else None,
            )
        # SAFETY SCREEN 1: gate any candidate whose issue text trips a blocked
        # category. Defense in depth on top of the planner's self-declared risk.
        screened: list[Path] = []
        for path in issue_paths:
            risk = parse_issue_risk(path)
            if risk not in {"low", "medium"}:
                waiting_for_human.append({
                    "issue_path": str(path), "reason": "unsupported_risk", "risk": risk or "missing",
                })
                continue
            if risk == "medium":
                if not supervised:
                    waiting_for_human.append({
                        "issue_path": str(path),
                        "reason": "medium_risk_requires_supervised_run",
                    })
                    continue
                try:
                    validate_medium_issue_against_envelope(path, cfg, policy.get("medium_envelope"))
                except Exception as exc:
                    waiting_for_human.append({
                        "issue_path": str(path), "reason": "medium_envelope_violation", "error": str(exc),
                    })
                    continue
            hits = screen_blocked(path, blocked_categories)
            if hits:
                waiting_for_human.append({
                    "issue_path": str(path), "reason": "blocked_category", "hits": hits,
                })
                continue
            if risk == "low":
                try:
                    trusted_verification_commands(path, cfg)
                except Exception as exc:
                    waiting_for_human.append({
                        "issue_path": str(path), "reason": "untrusted_verification", "error": str(exc),
                    })
                    continue
            screened.append(path)
        issue_paths = screened
        blocker = higher_value_waiting_item(run_dir, issue_paths, waiting_for_human)
        if blocker:
            return finish_controlled_cycle(
                project,
                cfg,
                run_id,
                run_dir,
                started_at,
                "needs_human",
                [],
                waiting_for_human,
                milestone_id,
                control_gate={
                    "reason": "higher_value_item_requires_approval",
                    "action": "paused_loop",
                    "detail": (
                        f"Higher-value candidate `{blocker.get('title') or '-'}` "
                        f"score={blocker.get('value_score')} requires approval before lower-value work runs."
                    ),
                },
                pause_reason="higher_value_item_requires_approval",
            )
        # SAFETY SCREEN 2: never auto-run more than max_tasks_per_cycle.
        # Extra auto-runnable tasks are deferred, not marked as human approval
        # blockers. They can be reconsidered on the next cycle after the repo
        # has absorbed the first PR, which avoids same-cycle branch conflicts.
        if len(issue_paths) > max_tasks:
            deferred_issue_paths = issue_paths[max_tasks:]
            write_deferred_issue_paths(run_dir, deferred_issue_paths, max_tasks)
            issue_paths = issue_paths[:max_tasks]
        if not issue_paths and waiting_for_human:
            return finish_controlled_cycle(
                project,
                cfg,
                run_id,
                run_dir,
                started_at,
                "needs_human",
                [],
                waiting_for_human,
                milestone_id,
                control_gate={
                    "reason": "all_candidates_gated",
                    "action": "paused_loop",
                    "detail": "Every candidate was gated before worker execution.",
                },
                pause_reason="waiting_for_human_no_auto_tasks",
            )
        candidate_lines = "\n".join(f"- `{task_id_from_issue(path, index)}`: {first_heading(path)}" for index, path in enumerate(issue_paths, start=1))
        linear_comment(
            cfg,
            run_dir,
            "planner-candidates",
            f"Planner: {len(issue_paths)} auto-executable, {len(waiting_for_human)} gated to human for `{run_id}`.\n\n"
            f"Deferred to future cycle: {len(deferred_issue_paths)}\n\n"
            f"{candidate_lines}",
        )
        linear_update_milestone(
            cfg,
            milestone_id,
            milestone_description(run_id, "planner_done", waiting_for_human=waiting_for_human),
            run_dir,
            "planner-candidates",
        )
        for index, issue_path in enumerate(issue_paths, start=1):
            task_id = task_id_from_issue(issue_path, index)
            task_dir = run_dir / "tasks" / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            github_issue = None
            worktree_path = None
            task_branch = None
            merge_result: dict = {"merged": False}
            result_status = "needs_human"
            try:
                set_phase(project, f"worker:{task_id}", run_id)
                github_issue = create_github_issue(cfg, issue_path, task_dir)
                branch_suffix = f"{run_id}-{task_id}"
                worktree_path, task_branch = worker(project, cfg, task_dir, issue_path, branch_suffix)
                branch = task_branch
                set_phase(project, f"reviewer:{task_id}", run_id)
                status = reviewer(project, cfg, task_dir, issue_path, worktree_path, task_branch)
                set_phase(project, f"merge:{task_id}", run_id)
                merge_result = merge_passed_task(cfg, task_dir, task_branch) if status == "pass" else {"merged": False}
                result_status = "merged" if merge_result.get("merged") else status
            except Exception as task_exc:
                (task_dir / "task-error.txt").write_text(str(task_exc) + "\n")
                result_status = "needs_human"
                waiting_for_human.append({
                    "issue_path": str(issue_path), "reason": "task_gated", "error": str(task_exc),
                })
            finally:
                # SAFETY: always clean up the task worktree so they don't accumulate.
                if worktree_path is not None:
                    run(["git", "worktree", "remove", "--force", str(worktree_path)],
                        cwd=repo_path, check=False)
            task_results.append({
                "task_id": task_id,
                "issue_path": str(issue_path),
                "status": result_status,
                "branch": task_branch,
                "github_issue": github_issue,
                "github_pr": merge_result.get("pr"),
            })
            linear_comment(
                cfg,
                task_dir,
                "task-result",
                f"Loop task `{task_id}` finished in `{run_id}`.\n\n"
                f"- Title: {first_heading(issue_path)}\n"
                f"- Status: `{result_status}`\n"
                f"- Branch: `{task_branch}`\n"
                f"- GitHub issue: {github_issue or 'not created'}\n"
                f"- GitHub PR: {merge_result.get('pr') or 'not created'}",
            )
            linear_update_milestone(
                cfg,
                milestone_id,
                milestone_description(run_id, f"task_done:{task_id}", task_results, waiting_for_human),
                task_dir,
                "task-result",
            )
        run(["git", "worktree", "prune"], cwd=repo_path, check=False)
        merged_ok = bool(task_results) and all(task["status"] == "merged" for task in task_results)
        final_status = "merged" if merged_ok and not waiting_for_human else "needs_human"
        return finish_controlled_cycle(
            project,
            cfg,
            run_id,
            run_dir,
            started_at,
            final_status,
            task_results,
            waiting_for_human,
            milestone_id,
        )
    except Exception as exc:
        (run_dir / "error.txt").write_text(str(exc) + "\n")
        summary = write_cycle_summary(project, run_id, run_dir, "failed", started_at, task_results, waiting_for_human)
        distill_cycle_learning(project, summary)
        update_state(project, run_id, "failed", branch, task_results, waiting_for_human)
        linear_comment(
            cfg,
            run_dir,
            "cycle-failed",
            f"Loop cycle `{run_id}` failed.\n\n"
            f"- Error: `{exc}`\n"
            f"- Completed tasks before failure: {len(task_results)}",
        )
        linear_update_milestone(
            cfg,
            milestone_id,
            milestone_description(run_id, "failed", task_results, waiting_for_human) + f"\n\nError: `{exc}`",
            run_dir,
            "cycle-failed",
        )
        clear_phase(project)
        raise


def git_has_changes(path: Path) -> bool:
    return bool(run(["git", "status", "--short"], cwd=path).stdout.strip())


def commit_control_plane(project: str, run_id: str, status: str) -> None:
    if not git_has_changes(ENGINE_ROOT.parent):
        return
    run(["git", "add", "loop-engine"], cwd=ENGINE_ROOT.parent)
    if not git_has_changes(ENGINE_ROOT.parent):
        return
    run(
        ["git", "commit", "-m", f"chore: record {project} unattended run {run_id} {status}"],
        cwd=ENGINE_ROOT.parent,
    )


def write_unattended_summary(project: str, results: list[dict]) -> Path:
    summary_dir = ENGINE_ROOT / "reports"
    summary_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = summary_dir / f"{project}-unattended-{stamp}.md"
    lines = [
        f"# {project} unattended loop summary",
        "",
        f"Generated at: {dt.datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend([
            f"## Cycle {index}: `{result.get('run_id')}`",
            "",
            f"- Status: `{result.get('status')}`",
            f"- Tasks: {len(result.get('tasks') or [])}",
            "",
        ])
        for task in result.get("tasks") or []:
            lines.extend([
                f"### {task.get('task_id')}",
                f"- Status: `{task.get('status')}`",
                f"- Issue: {task.get('github_issue')}",
                f"- PR: {task.get('github_pr')}",
                "",
            ])
    path.write_text("\n".join(lines))
    return path


def unattended(project: str, cycles: int, interval_minutes: float, commit: bool) -> None:
    registry = load_json(REGISTRY_PATH)
    cfg = registry["projects"][project]
    results: list[dict] = []
    for index in range(cycles):
        result = cycle(project)
        results.append(result)
        if commit:
            commit_control_plane(project, result["run_id"], result["status"])
        if index < cycles - 1:
            time.sleep(interval_minutes * 60)
    summary_path = write_unattended_summary(project, results)
    if commit:
        run(["git", "add", str(summary_path.relative_to(ENGINE_ROOT.parent))], cwd=ENGINE_ROOT.parent)
        commit_control_plane(project, "summary", "recorded")
    linear_comment(
        cfg,
        summary_path.parent,
        f"{summary_path.stem}-summary",
        f"Unattended loop block completed for `{cfg['name']}`.\n\n"
        f"- Cycles: {cycles}\n"
        f"- Summary: `{summary_path}`\n"
        f"- Statuses: {', '.join(result.get('status', 'unknown') for result in results)}",
    )
    print(f"UNATTENDED_COMPLETE project={project} cycles={cycles} summary={summary_path}")


def scheduler_status_payload(project: str) -> dict:
    label = scheduler_label(project)
    path = scheduler_plist_path(project)
    result = run(["launchctl", "list", label], cwd=ENGINE_ROOT.parent, check=False)
    state = load_state()
    runtime = state_project(state, project).get("scheduler_runtime", {})
    pid = runtime.get("pid")
    daemon_alive = bool(pid and process_alive(int(pid)))
    return {
        "label": label,
        "plist": str(path),
        "installed": False,
        "loaded": result.returncode == 0 or daemon_alive,
        "launchd_loaded": result.returncode == 0,
        "legacy_plist_installed": path.exists(),
        "daemon_alive": daemon_alive,
        "daemon_pid": pid,
    }


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def install_scheduler(project: str) -> None:
    registry_project(project)
    print(
        "SCHEDULER_INSTALL_NOOP "
        f"project={project} label={scheduler_label(project)} "
        "reason=manual-daemon-only use='scheduler load'"
    )


def cleanup_legacy_launch_agent(project: str) -> bool:
    label = scheduler_label(project)
    path = scheduler_plist_path(project)
    removed = False
    if path.exists():
        run(["launchctl", "unload", str(path)], cwd=ENGINE_ROOT.parent, check=False)
        path.unlink()
        removed = True
    if run(["launchctl", "list", label], cwd=ENGINE_ROOT.parent, check=False).returncode == 0:
        run(["launchctl", "remove", label], cwd=ENGINE_ROOT.parent, check=False)
        removed = True
    return removed


def load_scheduler(project: str) -> None:
    registry_project(project)
    cleanup_legacy_launch_agent(project)
    logs_dir = ENGINE_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    status = scheduler_status_payload(project)
    if status.get("daemon_alive"):
        print(f"SCHEDULER_LOADED project={project} daemon_pid={status.get('daemon_pid')} already=true")
        return
    stdout = (logs_dir / f"{project}-scheduler.out.log").open("ab")
    stderr = (logs_dir / f"{project}-scheduler.err.log").open("ab")
    proc = subprocess.Popen(
        [sys.executable, str(ENGINE_ROOT / "bin" / "loopctl.py"), "daemon", "--project", project],
        cwd=ENGINE_ROOT.parent,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    state = load_state()
    state_project(state, project)["scheduler_runtime"] = {
        "kind": "daemon",
        "pid": proc.pid,
        "started_at": now_iso(),
        "cadence_seconds": DEFAULT_CADENCE_SECONDS,
    }
    save_state(state)
    print(f"SCHEDULER_LOADED project={project} daemon_pid={proc.pid} kind=daemon")


def uninstall_scheduler(project: str) -> None:
    state = load_state()
    runtime = state_project(state, project).get("scheduler_runtime", {})
    pid = runtime.get("pid")
    if pid and process_alive(int(pid)):
        os.kill(int(pid), signal.SIGTERM)
    state_project(state, project).pop("scheduler_runtime", None)
    save_state(state)
    cleanup_legacy_launch_agent(project)
    print(f"SCHEDULER_UNINSTALLED project={project} label={scheduler_label(project)}")


def print_scheduler_status(project: str) -> None:
    print(json.dumps(scheduler_status_payload(project), indent=2))


def daemon(project: str) -> None:
    registry_project(project)
    print(f"SCHEDULER_DAEMON_STARTED project={project} pid={os.getpid()}", flush=True)
    while True:
        tick(project)
        time.sleep(60)


def linear_check(project: str, write_comment: bool) -> None:
    registry = load_json(REGISTRY_PATH)
    cfg = registry["projects"][project]
    issue = linear_issue(cfg)
    print(
        "LINEAR_CONNECTED "
        f"issue={issue['identifier']} "
        f"state={issue['state']['name']} "
        f"team={issue['team']['key']} "
        f"project={(issue.get('project') or {}).get('name') or 'none'}"
    )
    if write_comment:
        log_dir = ENGINE_ROOT / "reports"
        ok = linear_comment(
            cfg,
            log_dir,
            "linear-check",
            f"Linear API sync check passed at `{dt.datetime.now().isoformat(timespec='seconds')}`.",
        )
        print(f"LINEAR_COMMENT_WRITTEN {ok}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init_parser = sub.add_parser("init")
    init_parser.add_argument("--project")
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--project")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--project")
    status_parser.add_argument("--json", action="store_true")
    digest_parser = sub.add_parser("digest")
    digest_parser.add_argument("--project")
    digest_parser.add_argument("--json", action="store_true")
    pause_parser = sub.add_parser("pause")
    pause_parser.add_argument("--project")
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--project")
    stop_parser = sub.add_parser("stop")
    stop_parser.add_argument("--project")
    run_now_parser = sub.add_parser("run-now")
    run_now_parser.add_argument("--project")
    run_now_parser.add_argument("--supervised", action="store_true")
    tick_parser = sub.add_parser("tick")
    tick_parser.add_argument("--project")
    daemon_parser = sub.add_parser("daemon")
    daemon_parser.add_argument("--project", required=True)
    cycle_parser = sub.add_parser("cycle")
    cycle_parser.add_argument("--project")
    cycle_parser.add_argument("--supervised", action="store_true")
    unattended_parser = sub.add_parser("unattended")
    unattended_parser.add_argument("--project", required=True)
    unattended_parser.add_argument("--cycles", type=int, default=3)
    unattended_parser.add_argument("--interval-minutes", type=float, default=60)
    unattended_parser.add_argument("--no-commit", action="store_true")
    linear_parser = sub.add_parser("linear-check")
    linear_parser.add_argument("--project", required=True)
    linear_parser.add_argument("--write-comment", action="store_true")
    scheduler_parser = sub.add_parser("scheduler")
    scheduler_sub = scheduler_parser.add_subparsers(dest="scheduler_command", required=True)
    scheduler_install = scheduler_sub.add_parser("install")
    scheduler_install.add_argument("--project")
    scheduler_load = scheduler_sub.add_parser("load")
    scheduler_load.add_argument("--project")
    scheduler_uninstall = scheduler_sub.add_parser("uninstall")
    scheduler_uninstall.add_argument("--project")
    scheduler_status = scheduler_sub.add_parser("status")
    scheduler_status.add_argument("--project")
    args = parser.parse_args()
    try:
        cwd = Path.cwd()
        if args.command == "init":
            init_command(args.project, cwd)
        elif args.command == "start":
            start_command(args.project, cwd)
        elif args.command == "status":
            status_command(args.project, cwd, args.json)
        elif args.command == "digest":
            digest_command(args.project, cwd, args.json)
        elif args.command == "pause":
            pause_command(args.project, cwd)
        elif args.command == "resume":
            resume_command(args.project, cwd)
        elif args.command == "stop":
            stop_command(args.project, cwd)
        elif args.command == "run-now":
            run_now_command(args.project, cwd, supervised=args.supervised)
        elif args.command == "tick":
            tick(args.project)
        elif args.command == "daemon":
            daemon(args.project)
        elif args.command == "cycle":
            cycle(resolve_project(cwd, project=args.project, bootstrap=False), supervised=args.supervised)
        elif args.command == "unattended":
            unattended(args.project, args.cycles, args.interval_minutes, commit=not args.no_commit)
        elif args.command == "linear-check":
            linear_check(args.project, args.write_comment)
        elif args.command == "scheduler":
            project = resolve_project(cwd, project=args.project, bootstrap=False)
            if args.scheduler_command == "install":
                install_scheduler(project)
            elif args.scheduler_command == "load":
                load_scheduler(project)
            elif args.scheduler_command == "uninstall":
                uninstall_scheduler(project)
            elif args.scheduler_command == "status":
                print_scheduler_status(project)
        return 0
    except LoopBlocked as exc:
        print(f"LOOP_BLOCKED reason={exc.reason} message={exc}", file=sys.stderr)
        if exc.details:
            print(json.dumps({"reason": exc.reason, "details": exc.details}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    code = main()
    if os.environ.get("LOOPCTL_FORCE_EXIT") == "1":
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    raise SystemExit(code)
