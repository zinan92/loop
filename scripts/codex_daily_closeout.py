#!/usr/bin/env python3
"""Build the Agent OS Daily Closeout ledger.

This script is intentionally mechanical. It writes the global closeout ledger and
delegates pinned-project daily updates to codex_project_daily_report.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import codex_project_daily_report as project_daily


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = project_daily.DEFAULT_OUTPUT_ROOT
DEFAULT_CLOSEOUT_FILE = DEFAULT_OUTPUT_ROOT / "_daily-closeout.md"
DEFAULT_STATE_FILE = DEFAULT_OUTPUT_ROOT / "_daily-closeout-state.json"
NORTH_STAR = REPO_ROOT / "docs" / "north-star.md"
GATE_SOURCE = REPO_ROOT / "docs" / "project-gates-v1.md"

REPO_CHECKS = {
    "Agent OS": REPO_ROOT,
    "交易系统": Path("/Users/wendy/trading-orchestrator"),
    "ai newsletter": Path("/Users/wendy/work/trading-co/park-intel"),
    "Park OS": Path("/Users/wendy/Documents/知识库"),
    "Park 的内容生产": Path("/Users/wendy/Documents/内容生产"),
    "内容制作": Path("/Users/wendy/Documents/内容制作"),
}

TEXTLIKE_EXTENSIONS = {
    ".astro",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

BLACKLIST_RE = re.compile(
    r"(^|/)(\.env|_secrets?|secrets?|logs?|outputs?|node_modules|\.vendor)(/|$)"
    r"|live\.env|tiger.*properties|private[_-]?key|token|cookie|\.pem$|\.key$",
    re.I,
)

GENERATED_EXTENSIONS = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".log",
    ".mov",
    ".mp4",
    ".pdf",
    ".png",
    ".tgz",
    ".webp",
    ".zip",
}


@dataclass
class RepoStatus:
    label: str
    path: str
    root: str
    branch: str
    clean: bool
    status: str
    summary: str
    safe: list[str]
    unknown: list[str]
    blacklisted: list[str]
    recent_commits: list[str]


@dataclass
class GateResult:
    project: str
    gate: str
    status: str
    evidence: str


def local_now() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        text=True,
        capture_output=True,
    )


def clean_line(text: str, limit: int = 220) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def classify_path(path: str) -> str:
    p = path.strip()
    suffix = Path(p).suffix.lower()
    if BLACKLIST_RE.search(p) or suffix in GENERATED_EXTENSIONS:
        return "blacklisted"
    if suffix in TEXTLIKE_EXTENSIONS:
        return "safe"
    return "unknown"


def parse_status_paths(short_status: str) -> list[str]:
    paths: list[str] = []
    for raw in short_status.splitlines():
        if not raw or raw.startswith("## "):
            continue
        path = raw[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        paths.append(path)
    return paths


def git_repo_status(label: str, path: Path, since: dt.datetime) -> RepoStatus:
    if not path.exists():
        return RepoStatus(
            label=label,
            path=str(path),
            root="",
            branch="",
            clean=False,
            status="SKIPPED",
            summary="路径不存在",
            safe=[],
            unknown=[],
            blacklisted=[],
            recent_commits=[],
        )

    root_result = run(["git", "rev-parse", "--show-toplevel"], path)
    if root_result.returncode != 0:
        return RepoStatus(
            label=label,
            path=str(path),
            root="",
            branch="",
            clean=False,
            status="SKIPPED",
            summary="不是 Git repo",
            safe=[],
            unknown=[],
            blacklisted=[],
            recent_commits=[],
        )

    root = Path(root_result.stdout.strip())
    status_result = run(["git", "status", "--short", "--branch"], root)
    status_text = status_result.stdout.strip()
    branch = status_text.splitlines()[0].removeprefix("## ") if status_text else ""
    changed_paths = parse_status_paths(status_text)

    safe: list[str] = []
    unknown: list[str] = []
    blacklisted: list[str] = []
    for item in changed_paths:
        kind = classify_path(item)
        if kind == "safe":
            safe.append(item)
        elif kind == "blacklisted":
            blacklisted.append(item)
        else:
            unknown.append(item)

    since_utc = since.astimezone(dt.timezone.utc).isoformat()
    log_result = run(
        [
            "git",
            "log",
            f"--since={since_utc}",
            "--pretty=format:%h %s",
            "--max-count=20",
        ],
        root,
    )
    commits = [line for line in log_result.stdout.splitlines() if line.strip()]

    if not changed_paths and "ahead" in branch:
        status = "READY_TO_PUSH"
        summary = f"clean，但分支未推送：{branch}"
    elif not changed_paths:
        status = "SKIPPED"
        summary = "clean，无需 commit"
    elif blacklisted or unknown:
        status = "BLOCKED"
        summary = f"{len(blacklisted)} 个黑名单项，{len(unknown)} 个未知项；本轮不自动提交"
    else:
        status = "READY_FOR_REVIEW"
        summary = f"{len(safe)} 个 safe-looking 改动；需确认逻辑单元后再 commit/push"

    return RepoStatus(
        label=label,
        path=str(path),
        root=str(root),
        branch=branch,
        clean=not changed_paths,
        status=status,
        summary=summary,
        safe=safe[:20],
        unknown=unknown[:20],
        blacklisted=blacklisted[:20],
        recent_commits=commits,
    )


def current_issue_keys(repo_statuses: list[RepoStatus]) -> dict[str, str]:
    issues: dict[str, str] = {}
    for repo in repo_statuses:
        if repo.status == "BLOCKED":
            issues[f"repo:{repo.root or repo.path}"] = f"{repo.label}: {repo.summary}"
    if not NORTH_STAR.exists():
        issues["config:north-star"] = "Agent OS 缺少 docs/north-star.md"
    return issues


def update_issue_state(state_path: Path, issues: dict[str, str], today: dt.date, write: bool) -> dict:
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    else:
        state = {}

    active: dict[str, dict] = state.get("active_issues") or {}
    updated: dict[str, dict] = {}
    today_s = str(today)
    for key, description in issues.items():
        previous = active.get(key) or {}
        first_seen = previous.get("first_seen") or today_s
        last_seen = previous.get("last_seen")
        age_days = int(previous.get("age_days") or 0)
        if last_seen == today_s:
            age_days = max(age_days, 1)
        else:
            age_days = age_days + 1 if previous else 1
        updated[key] = {
            "description": description,
            "first_seen": first_seen,
            "last_seen": today_s,
            "age_days": age_days,
        }

    new_state = {
        "updated_at": local_now().isoformat(),
        "active_issues": updated,
    }
    if write:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_state


def read_first_line(path: Path) -> str:
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.lstrip().startswith("#"):
            continue
        line = raw.strip(" \t")
        if line:
            return line
    return ""


def extract_previous_todos(closeout_file: Path, today: dt.date) -> list[str]:
    if not closeout_file.exists():
        return []
    text = closeout_file.read_text(encoding="utf-8", errors="replace")
    anchors = list(re.finditer(r"<!-- codex-daily-closeout:(\d{4}-\d{2}-\d{2}):\d+h -->", text))
    for index, match in enumerate(anchors):
        date_s = match.group(1)
        if date_s == str(today):
            continue
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
        entry = text[match.start() : end]
        section = re.search(
            r"### 7\. 明日 to-do（草案）\n(?P<body>.*?)(?=\n### 8\.|\n---|\Z)",
            entry,
            re.S,
        )
        if not section:
            return []
        todos = []
        for raw in section.group("body").splitlines():
            line = raw.strip()
            if line.startswith("- "):
                todos.append(line[2:].strip())
        return todos
    return []


def decision_entries_for_today(today: dt.date) -> list[str]:
    entries: list[str] = []
    for source in [
        REPO_ROOT / "decision-log.md",
        Path("/Users/wendy/Documents/内容制作/decision-log.md"),
        Path("/Users/wendy/trading-orchestrator/decision-log.md"),
        Path("/Users/wendy/Documents/知识库/chatgpt-project-personal-growth-to-park-raw/decision-log.md"),
    ]:
        if not source.exists():
            continue
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"## {today}"):
                entries.append(f"{source}: {line.removeprefix('## ').strip()}")
    return entries


def know_how_files(output_root: Path) -> list[str]:
    if not output_root.exists():
        return []
    return [str(path) for path in sorted(output_root.glob("*/*/know-how.md"))]


def automation_memory_summary(automation_id: str) -> str:
    path = Path("/Users/wendy/.codex/automations") / automation_id / "memory.md"
    if not path.exists():
        return f"SKIPPED（无 memory：{path}）"
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ)
    age_hours = (local_now() - mtime).total_seconds() / 3600
    status = "fresh" if age_hours <= 36 else "STALE"
    return f"{status}，mtime={mtime.strftime('%Y-%m-%d %H:%M')}，path={path}"


def latest_project_entry(path: str) -> str:
    output = Path(path)
    if not output.exists():
        return ""
    text = output.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"<!-- codex-project-daily:.*? -->.*?(?=\n<!-- codex-project-daily:|\Z)",
        text,
        re.S,
    )
    return match.group(0) if match else text


def contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def coverage_count(entry: str) -> int:
    match = re.search(r"覆盖：(\d+) 个 thread", entry)
    if not match:
        return 0
    return int(match.group(1))


def gate_evidence_text(entry: str) -> str:
    return re.sub(
        r"### CEO/PM 摘要\n.*?(?=\n### 今日推进|\Z)",
        "",
        entry,
        flags=re.S,
    )


def repo_by_label(repo_statuses: list[RepoStatus], label: str) -> RepoStatus | None:
    for repo in repo_statuses:
        if repo.label == label:
            return repo
    return None


def gate(
    project: str,
    name: str,
    status: str,
    evidence: str,
) -> GateResult:
    return GateResult(project=project, gate=name, status=status, evidence=evidence)


def status_from_keywords(
    project: str,
    gate_name: str,
    entry: str,
    path: str,
    pass_keywords: list[str],
    no_activity_is_failure: bool = False,
) -> GateResult:
    if no_activity_is_failure and coverage_count(entry) == 0:
        return gate(project, gate_name, "未通过", f"`{path}`；窗口内 0 个 thread")
    evidence_text = gate_evidence_text(entry)
    if contains_any(evidence_text, pass_keywords):
        matched = [word for word in pass_keywords if word.lower() in evidence_text.lower()]
        return gate(project, gate_name, "通过", f"`{path}`；命中：{', '.join(matched[:4])}")
    return gate(project, gate_name, "状态未知", f"`{path}`；未发现可判定关键词")


def evaluate_project_gates(
    project_outputs: dict[str, str],
    repo_statuses: list[RepoStatus],
) -> list[GateResult]:
    if not GATE_SOURCE.exists():
        return [
            gate(project, "Gate source", "未配置", f"缺少 `{GATE_SOURCE}`")
            for project in project_outputs
        ]

    entries = {
        project: latest_project_entry(path)
        for project, path in project_outputs.items()
    }
    results: list[GateResult] = []

    trading_entry = entries.get("交易系统", "")
    trading_path = project_outputs.get("交易系统", "")
    trading_repo = repo_by_label(repo_statuses, "交易系统")
    results.append(
        status_from_keywords(
            "交易系统",
            "System visibility",
            trading_entry,
            trading_path,
            ["dashboard", "frontend", "前端", "chart", "图表", "console", "工作台", "告警", "复盘"],
        )
    )
    if trading_repo and trading_repo.status == "BLOCKED":
        results.append(gate("交易系统", "Risk safety", "未通过", f"`{trading_repo.root or trading_repo.path}`；{trading_repo.summary}"))
    elif trading_repo:
        results.append(gate("交易系统", "Risk safety", "通过", f"`{trading_repo.root or trading_repo.path}`；{trading_repo.summary}"))
    else:
        results.append(gate("交易系统", "Risk safety", "状态未知", "未找到交易系统 repo status"))
    results.append(
        status_from_keywords(
            "交易系统",
            "Review loop",
            trading_entry,
            trading_path,
            ["morning review", "evening review", "system alert", "pm_reports", "feishu", "告警", "复盘"],
        )
    )
    results.append(
        status_from_keywords(
            "交易系统",
            "Execution readiness",
            trading_entry,
            trading_path,
            ["broker", "tiger", "data-feed", "readiness", "can_submit", "订单", "券商", "老虎"],
        )
    )

    content_entry = entries.get("Park 的内容生产", "")
    content_path = project_outputs.get("Park 的内容生产", "")
    results.append(
        status_from_keywords(
            "Park 的内容生产",
            "Content asset produced",
            content_entry,
            content_path,
            ["draft", "script", "article", "thread", "发布", "草稿", "脚本", "文章", "内容资产"],
            no_activity_is_failure=True,
        )
    )
    results.append(
        status_from_keywords(
            "Park 的内容生产",
            "Packaging complete",
            content_entry,
            content_path,
            ["channel", "platform", "小红书", "公众号", "网站", "publishable", "格式", "平台"],
            no_activity_is_failure=True,
        )
    )
    results.append(
        status_from_keywords(
            "Park 的内容生产",
            "Feedback loop",
            content_entry,
            content_path,
            ["feedback", "review", "published", "上线", "复盘", "反馈"],
            no_activity_is_failure=True,
        )
    )

    park_os_entry = entries.get("Park OS", "")
    park_os_path = project_outputs.get("Park OS", "")
    if coverage_count(park_os_entry) > 0:
        results.append(gate("Park OS", "Raw capture", "通过", f"`{park_os_path}`；窗口内 {coverage_count(park_os_entry)} 个 thread"))
    else:
        results.append(gate("Park OS", "Raw capture", "未通过", f"`{park_os_path}`；窗口内 0 个 thread"))
    results.append(
        status_from_keywords(
            "Park OS",
            "Processed layer",
            park_os_entry,
            park_os_path,
            ["定位", "整理", "结构", "sop", "diagram", "上线", "维护", "改成", "总结"],
        )
    )
    if Path(park_os_path).exists():
        results.append(gate("Park OS", "Retrieval path", "通过", f"`{park_os_path}` 已存在"))
    else:
        results.append(gate("Park OS", "Retrieval path", "未通过", f"`{park_os_path}` 不存在"))

    agent_entry = entries.get("Agent OS", "")
    agent_path = project_outputs.get("Agent OS", "")
    know_how_health = automation_memory_summary("codex-session-know-how-refresh")
    closeout_health = automation_memory_summary("codex-project-daily-report")
    if know_how_health.startswith("fresh") and closeout_health.startswith("fresh"):
        results.append(gate("Agent OS", "Automation health", "通过", f"know-how: {know_how_health}；closeout: {closeout_health}"))
    else:
        results.append(gate("Agent OS", "Automation health", "未通过", f"know-how: {know_how_health}；closeout: {closeout_health}"))
    results.append(
        status_from_keywords(
            "Agent OS",
            "Evidence discipline",
            agent_entry,
            agent_path,
            ["decision-log", "know-how", "daily closeout", "daily update", "证据", "commit"],
        )
    )
    if Path(agent_path).exists() and all(Path(path).exists() for path in project_outputs.values()):
        results.append(gate("Agent OS", "Daily closeout", "通过", f"`{DEFAULT_CLOSEOUT_FILE}`；{len(project_outputs)} 个 Project daily updates"))
    else:
        results.append(gate("Agent OS", "Daily closeout", "未通过", f"`{DEFAULT_CLOSEOUT_FILE}` 或 Project daily updates 不完整"))
    agent_repo = repo_by_label(repo_statuses, "Agent OS")
    if agent_repo and agent_repo.status == "BLOCKED":
        results.append(gate("Agent OS", "Repo hygiene", "未通过", f"`{agent_repo.root or agent_repo.path}`；{agent_repo.summary}"))
    elif agent_repo:
        results.append(gate("Agent OS", "Repo hygiene", "通过", f"`{agent_repo.root or agent_repo.path}`；{agent_repo.summary}"))
    else:
        results.append(gate("Agent OS", "Repo hygiene", "状态未知", "未找到 Agent OS repo status"))

    newsletter_entry = entries.get("ai newsletter", "")
    newsletter_path = project_outputs.get("ai newsletter", "")
    results.append(
        status_from_keywords(
            "ai newsletter",
            "Brief generated",
            newsletter_entry,
            newsletter_path,
            ["newsletter", "daily", "日报", "brief", "财经日报"],
        )
    )
    results.append(
        status_from_keywords(
            "ai newsletter",
            "Delivery verified",
            newsletter_entry,
            newsletter_path,
            ["推送成功", "delivery", "delivered", "receipt", "feishu", "送达"],
        )
    )
    results.append(
        status_from_keywords(
            "ai newsletter",
            "Archive updated",
            newsletter_entry,
            newsletter_path,
            ["archive", "obsidian", "归档", "daily-update", "008"],
        )
    )
    results.append(
        status_from_keywords(
            "ai newsletter",
            "Source health visible",
            newsletter_entry,
            newsletter_path,
            ["source health", "来源健康", "stale", "disabled", "failing", "health"],
        )
    )

    return results


def render_repo_table(repo_statuses: list[RepoStatus]) -> list[str]:
    lines = ["| Repo | 状态 | 证据 |", "|---|---|---|"]
    for repo in repo_statuses:
        evidence = repo.root or repo.path
        lines.append(f"| {repo.label} | {repo.status}: {repo.summary} | `{evidence}` |")
    return lines


def portfolio_value_summary(
    project_outputs: dict[str, str],
    repo_statuses: list[RepoStatus],
    gate_results: list[GateResult],
) -> list[str]:
    blocked = [repo for repo in repo_statuses if repo.status == "BLOCKED"]
    ready = [repo for repo in repo_statuses if repo.status == "READY_FOR_REVIEW"]
    unknown_gates = [result for result in gate_results if result.status == "状态未知"]
    lines = [
        "- 今天的核心产出是 Agent OS 日闭环能力上线：Park 可以在 008 里同时看到 Project daily update、全局 daily closeout、blocker age 和明日草案。",
        f"- 用户价值：CEO/PM 不需要翻 thread 细节，也能知道 {len(project_outputs)} 个 pinned Projects 今天有没有推进、哪里卡住、下一步该核对什么。",
    ]
    if blocked:
        lines.append(
            f"- 当前最大风险：{len(blocked)} 个 repo 有 unknown/blacklisted 项，系统选择不自动提交，避免把 logs、截图、secrets 或本地 artifacts 推上去。"
        )
    if ready:
        labels = "、".join(repo.label for repo in ready[:3])
        lines.append(f"- 可收口项：{labels} 有 safe-looking 改动，需要确认逻辑单元后再决定 commit/push。")
    if unknown_gates:
        lines.append(f"- Gate evaluator 已接入：仍有 {len(unknown_gates)} 个 gate 缺少可判定证据，下一步补 artifact-specific evaluator。")
    else:
        lines.append("- Gate evaluator 已接入：本轮所有 gate 都有明确判断。")
    return lines


def render_closeout(
    *,
    hours: int,
    output_root: Path,
    closeout_file: Path,
    state: dict,
    project_outputs: dict[str, str],
    repo_statuses: list[RepoStatus],
    gate_results: list[GateResult],
) -> str:
    now = local_now()
    start = now - dt.timedelta(hours=hours)
    today = now.date()
    previous_todos = extract_previous_todos(closeout_file, today)
    north_star = read_first_line(NORTH_STAR)
    decisions = decision_entries_for_today(today)
    know_hows = know_how_files(output_root)
    active_issues = state.get("active_issues") or {}
    escalations = [item for item in active_issues.values() if int(item.get("age_days") or 0) >= 3]
    recent_commits = [
        f"{repo.label}: {commit}"
        for repo in repo_statuses
        for commit in repo.recent_commits
    ]
    readme_touched = [
        repo.label
        for repo in repo_statuses
        if any("README" in path for path in repo.safe + repo.unknown + repo.blacklisted)
        or any("README" in commit for commit in repo.recent_commits)
    ]

    anchor = f"<!-- codex-daily-closeout:{today}:{hours}h -->"
    lines = [
        anchor,
        f"## {today} Daily Closeout（过去 {hours} 小时）",
        "",
        f"- 窗口：{start.strftime('%Y-%m-%d %H:%M')} - {now.strftime('%Y-%m-%d %H:%M')} Asia/Shanghai",
        f"- 输出根目录：`{output_root}`",
        f"- Project daily updates：{len(project_outputs)} 个",
        f"- Pipeline status：{'BLOCKED items present' if active_issues else 'complete with skips'}",
        "",
        "### 0. CEO/PM 摘要",
        "",
        "<!-- llm-summary:start -->",
        *portfolio_value_summary(project_outputs, repo_statuses, gate_results),
        "<!-- llm-summary:end -->",
        "",
        "### 1. 北极星对照",
        "",
    ]
    if north_star:
        lines.extend(
            [
                f"- 北极星：{north_star}",
                f"- 今日对照：状态未知（v1 只机械汇总，不替 Park 判断战略漂移）。证据：`{NORTH_STAR}`",
            ]
        )
    else:
        lines.append(f"- SKIPPED（未定义 `docs/north-star.md`）。证据：`{NORTH_STAR}`")

    lines.extend(["", "### 2. 昨日 to-do 核对", ""])
    if previous_todos:
        for todo in previous_todos:
            lines.append(
                f"- ⚠️ 待人工核对：{todo}。证据：`{closeout_file}`；原因：v1 未配置逐条 to-do matcher。"
            )
    else:
        lines.append("- 无基线（首日或断档）。")

    lines.extend(["", "### 3. 今日产出台账", ""])
    lines.extend(
        [
            "| 项 | 结果 | 证据 |",
            "|---|---|---|",
            f"| Commits | {len(recent_commits)} 条 | {', '.join(f'`{item}`' for item in recent_commits[:8]) or 'SKIPPED（窗口内无 git commit）'} |",
            f"| Decision log 新增 | {len(decisions)} 条 | {'<br>'.join(f'`{clean_line(item)}`' for item in decisions[:8]) or 'SKIPPED（今日未发现 `## YYYY-MM-DD` 决策条目）'} |",
            f"| Know-how | {len(know_hows)} 个现有文件 | `scripts/codex_session_insights.py`；{automation_memory_summary('codex-session-know-how-refresh')} |",
            f"| README | {'更新/有改动' if readme_touched else 'SKIPPED（今日未检测到 README 改动）'} | {', '.join(readme_touched) or '`git log/status`'} |",
            "| 网站 | SKIPPED（今日无可展示发布事件；v1 未配置 website publish contract） | `docs/daily-closeout-v1.md` |",
        ]
    )

    lines.extend(["", "### 4. 进度闸门", ""])
    lines.extend(["| Project | Gate | 状态 | 证据 |", "|---|---|---|---|"])
    for result in gate_results:
        lines.append(f"| {result.project} | {result.gate} | {result.status} | {result.evidence} |")

    lines.extend(["", "### 5. 异常与风险", ""])
    lines.append("- Code closeout:")
    lines.extend(render_repo_table(repo_statuses))
    lines.append("")
    lines.append(f"- Know-how automation：{automation_memory_summary('codex-session-know-how-refresh')}")
    lines.append(f"- Daily closeout automation：{automation_memory_summary('codex-project-daily-report')}")
    if active_issues:
        lines.append("- Blocked / actionable skipped age:")
        for key, item in active_issues.items():
            age = item.get("age_days")
            marker = "升级" if int(age or 0) >= 3 else "记录"
            lines.append(f"  - {marker} day {age}: {item.get('description')} (`{key}`)")
    else:
        lines.append("- Blocked / actionable skipped age：SKIPPED（无 active issue）")
    if escalations:
        lines.append(f"- Escalation：{len(escalations)} 个事项达到 day 3+，需要 Park 决策。")
    else:
        lines.append("- Escalation：SKIPPED（无 day 3+ active issue）")

    lines.extend(["", "### 6. 用户视角的今日成果", ""])
    lines.append(
        "- Park 现在可以在 008 中查看 pinned Projects 的最新 daily update，并在 `_daily-closeout.md` 中看到昨日核对、代码状态、know-how 状态和明日草案。"
    )
    lines.append("- 如果某个 Project 今天没有用户可见变化，其 Project daily update 会显式记录无活动。")

    lines.extend(["", "### 7. 明日 to-do（草案）", ""])
    if active_issues:
        first_issue = next(iter(active_issues.values()))
        lines.append(f"- 主攻：处理 `{first_issue.get('description')}`（草案，待 Park 确认）。")
    else:
        lines.append("- 主攻：Park 确认明日唯一主线（草案，待确认）。")
    lines.append("- 辅助 1：核对今日 `_daily-closeout.md` 中的状态未知 gate。")
    lines.append("- 辅助 2：如有可发布成果，再决定是否触发 website/content adapter。")
    lines.append("- 辅助 3：保持 03:00 know-how sync 与 04:10 closeout 分工清晰。")

    lines.extend(["", "### 8. 内容候选", ""])
    lines.append("- 无（v1 未发现 release-level external artifact；多数日子应该是无）。")

    lines.extend(["", "### Project Daily Update Outputs", ""])
    for project, path in project_outputs.items():
        lines.append(f"- {project}: `{path}`")
    lines.extend(["", "---", ""])
    return "\n".join(lines)


def upsert_closeout(output: Path, entry: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Daily Closeout\n\n"
        "> 累计日闭环；最新内容在最上面。连接昨日承诺、今日证据、明日草案。\n\n"
    )
    anchor = entry.splitlines()[0]
    if not output.exists():
        output.write_text(header + entry, encoding="utf-8")
        return

    existing = output.read_text(encoding="utf-8", errors="replace")
    if anchor in existing:
        pattern = re.compile(re.escape(anchor) + r".*?(?=\n<!-- codex-daily-closeout:|\Z)", re.S)
        updated = pattern.sub(entry.rstrip() + "\n", existing)
        output.write_text(updated, encoding="utf-8")
        return

    marker = "\n<!-- codex-daily-closeout:"
    idx = existing.find(marker)
    if idx != -1:
        updated = existing[: idx + 1] + entry + existing[idx + 1 :]
    else:
        updated = existing.rstrip() + "\n\n" + entry
    output.write_text(updated, encoding="utf-8")


def generate(hours: int, output_root: Path, closeout_file: Path, state_file: Path, write: bool) -> dict:
    now = local_now()
    start = now - dt.timedelta(hours=hours)
    project_outputs = {
        project: str(output_root / project_daily.clean_segment(project) / "daily-update.md")
        for project in project_daily.PINNED_PROJECTS
    }
    if write:
        project_outputs = project_daily.write_project_reports(output_root, hours)

    repo_statuses = [
        git_repo_status(label, path, start)
        for label, path in REPO_CHECKS.items()
    ]
    gate_results = evaluate_project_gates(project_outputs, repo_statuses)
    issues = current_issue_keys(repo_statuses)
    state = update_issue_state(state_file, issues, now.date(), write)
    entry = render_closeout(
        hours=hours,
        output_root=output_root,
        closeout_file=closeout_file,
        state=state,
        project_outputs=project_outputs,
        repo_statuses=repo_statuses,
        gate_results=gate_results,
    )
    if write:
        upsert_closeout(closeout_file, entry)

    return {
        "output_root": str(output_root),
        "closeout_file": str(closeout_file),
        "state_file": str(state_file),
        "hours": hours,
        "written": write,
        "project_outputs": project_outputs,
        "repo_statuses": [
            {
                "label": repo.label,
                "path": repo.path,
                "root": repo.root,
                "branch": repo.branch,
                "status": repo.status,
                "summary": repo.summary,
                "safe_count": len(repo.safe),
                "unknown_count": len(repo.unknown),
                "blacklisted_count": len(repo.blacklisted),
                "recent_commit_count": len(repo.recent_commits),
            }
            for repo in repo_statuses
        ],
        "gate_results": [
            {
                "project": result.project,
                "gate": result.gate,
                "status": result.status,
                "evidence": result.evidence,
            }
            for result in gate_results
        ],
        "active_issue_count": len(state.get("active_issues") or {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--closeout-file", default=str(DEFAULT_CLOSEOUT_FILE))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = generate(
        hours=args.hours,
        output_root=Path(args.output_root),
        closeout_file=Path(args.closeout_file),
        state_file=Path(args.state_file),
        write=args.write,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"closeout_file: {result['closeout_file']}")
        print(f"active_issues: {result['active_issue_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
