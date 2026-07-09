#!/usr/bin/env python3
"""Build cumulative Codex Project daily updates under Obsidian 008.

The report is generated from local Codex metadata:
- ~/.codex/state_5.sqlite for thread ids, cwd, source, and timestamps
- ~/.codex/session_index.jsonl for user-facing thread names
- rollout jsonl files for the last assistant outcome when available

Newest entries are inserted near the top of each pinned Project's daily-update.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


STATE_DB = Path("/Users/wendy/.codex/state_5.sqlite")
SESSION_INDEX = Path("/Users/wendy/.codex/session_index.jsonl")
DEFAULT_OUTPUT_ROOT = Path(
    "/Users/wendy/park-io/008_codex session insights and decision logs"
)
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

PROJECT_ALIASES = {
    "/Users/wendy/Documents/内容制作": "交易系统",
    "/Users/wendy/trading-orchestrator": "交易系统",
    "/Users/wendy/work/trading-co/park-intel": "ai newsletter",
    "/Users/wendy/Documents/agent自管理": "Agent OS",
    "/Users/wendy/Documents/知识库": "Park OS",
    "/Users/wendy/park-io": "Park OS",
    "/Users/wendy/work/input-to-park": "Daily Inbox",
    "/Users/wendy/Documents/内容生产": "Park 的内容生产",
}

PINNED_PROJECTS = [
    "交易系统",
    "Park 的内容生产",
    "Park OS",
    "Agent OS",
    "ai newsletter",
]

PROJECT_PURPOSES = {
    "交易系统": "帮助 Park 把黄金交易系统变成更安全、更清晰的个人交易操作系统。",
    "Park 的内容生产": "把 Park 的想法和 AI 工具体验转成可发布的内容资产。",
    "Park OS": "保存原始思考，并把它变成可检索、可复用的第二大脑资产。",
    "Agent OS": "让 Park 的 agent 工作流可审计、可重复、可安全自动化。",
    "ai newsletter": "稳定产出有来源健康和归档连续性的 AI/finance intelligence 产品。",
}


@dataclass
class ThreadActivity:
    id: str
    title: str
    project: str
    cwd: str
    rollout_path: str
    thread_source: str
    updated_at: dt.datetime
    request: str
    outcome: str
    status: str


def local_now() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def one_line(text: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    clean = clean.replace("|", "／")
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def first_meaningful_line(text: str, limit: int = 180) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip(" -\t")
        if not line:
            continue
        if line.startswith("<oai-mem-citation>"):
            break
        if line.startswith("::"):
            continue
        return one_line(line, limit)
    return ""


def project_for_cwd(cwd: str) -> str:
    for prefix, label in PROJECT_ALIASES.items():
        if cwd == prefix or cwd.startswith(prefix + "/"):
            return label
    if not cwd:
        return "Unmapped"
    return Path(cwd).name or cwd


def clean_segment(value: str) -> str:
    return (
        value.strip()
        .replace("/", "／")
        .replace(":", "：")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def load_session_titles(path: Path = SESSION_INDEX) -> dict[str, str]:
    titles: dict[str, str] = {}
    if not path.exists():
        return titles
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        thread_id = row.get("id")
        name = row.get("thread_name")
        if thread_id and name:
            titles[thread_id] = str(name).strip()
    return titles


def content_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, dict):
            text = item.get("text")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def event_timestamp(event: dict) -> float:
    raw = event.get("timestamp")
    if not raw:
        return 0.0
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def parse_rollout(path: str) -> tuple[str, str]:
    rollout = Path(path)
    if not rollout.exists():
        return "", ""

    user_lines: list[str] = []
    latest_final_after_user = ""
    latest_final_ts = 0.0
    latest_user_ts = 0.0
    assistant_messages: list[str] = []
    try:
        lines = rollout.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", ""

    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        payload = event.get("payload") or {}
        if event.get("type") == "response_item" and payload.get("type") == "message":
            role = payload.get("role")
            text = content_text(payload)
            if not text:
                continue
            if role == "user":
                user_lines.append(text)
                latest_user_ts = event_timestamp(event)
            elif role == "assistant":
                phase = payload.get("phase")
                if phase == "final_answer":
                    latest_final_after_user = text
                    latest_final_ts = event_timestamp(event)
                else:
                    assistant_messages.append(text)
        elif event.get("type") == "event_msg":
            if payload.get("type") == "agent_message":
                text = payload.get("message")
                if text and payload.get("phase") == "final_answer":
                    latest_final_after_user = str(text)
                    latest_final_ts = event_timestamp(event)

    request = first_meaningful_line(user_lines[-1] if user_lines else "", 200)
    if latest_user_ts and latest_final_ts and latest_final_ts < latest_user_ts:
        final_text = ""
    else:
        final_text = latest_final_after_user or (assistant_messages[-1] if assistant_messages else "")
    outcome = first_meaningful_line(final_text, 220)
    return request, outcome


def infer_status(outcome: str, thread_source: str) -> str:
    text = outcome.lower()
    if "暂无最终回复" in outcome:
        return "进行中"
    if any(word in outcome for word in ["成功", "已完成", "完成", "通过", "已更新", "已生成", "verified"]):
        return "完成"
    if any(word in outcome for word in ["失败", "未能", "阻塞", "failed", "blocked"]):
        return "阻塞/失败"
    if thread_source == "automation":
        return "已运行"
    return "有推进"


def fetch_threads(hours: int, titles: dict[str, str]) -> list[ThreadActivity]:
    now = local_now()
    cutoff = int((now - dt.timedelta(hours=hours)).timestamp())
    query = """
        SELECT id, rollout_path, cwd, COALESCE(thread_source, ''), COALESCE(title, ''),
               COALESCE(first_user_message, ''), updated_at
        FROM threads
        WHERE updated_at >= ?
        ORDER BY updated_at DESC
    """
    activities: list[ThreadActivity] = []
    with sqlite3.connect(STATE_DB) as conn:
        for row in conn.execute(query, (cutoff,)):
            thread_id, rollout_path, cwd, thread_source, db_title, first_user, updated = row
            title = titles.get(thread_id) or first_meaningful_line(db_title, 80) or thread_id
            request, outcome = parse_rollout(rollout_path)
            request = request or first_meaningful_line(first_user or db_title, 200)
            outcome = outcome or "暂无最终回复；记录到最近一次活动。"
            source_kind = thread_source or "user"
            if request.startswith("Automation:") or str(first_user).lstrip().startswith("Automation:"):
                source_kind = "automation"
            project = project_for_cwd(cwd)
            if project not in PINNED_PROJECTS:
                continue
            updated_at = dt.datetime.fromtimestamp(int(updated), LOCAL_TZ)
            activities.append(
                ThreadActivity(
                    id=thread_id,
                    title=one_line(title, 80),
                    project=project,
                    cwd=cwd,
                    rollout_path=rollout_path,
                    thread_source=source_kind,
                    updated_at=updated_at,
                    request=request,
                    outcome=outcome,
                    status=infer_status(outcome, source_kind),
                )
            )
    return activities


def project_summary_line(project: str, items: list[ThreadActivity]) -> str:
    user_count = sum(1 for item in items if item.thread_source != "automation")
    auto_count = len(items) - user_count
    titles = "、".join(item.title for item in items[:3])
    if len(items) > 3:
        titles += f" 等 {len(items)} 个 thread"
    return f"- **{project}**：{len(items)} 个 thread（人工 {user_count}，自动 {auto_count}）。重点：{titles}"


def value_summary(project: str, items: list[ThreadActivity]) -> str:
    if not items:
        return "今天没有发现 Codex thread 活动，因此没有新增可确认的用户价值。"

    joined = " ".join(f"{item.title} {item.request} {item.outcome}" for item in items).lower()
    if project == "交易系统":
        if any(word in joined for word in ["dashboard", "frontend", "前端", "chart", "图表", "console"]):
            return "交易工作台和可视化体验继续收口，Park 更容易从一个界面判断系统状态和交易上下文。"
        if any(word in joined for word in ["alert", "review", "feishu", "复盘", "告警"]):
            return "交易系统的日常运行、告警或复盘证据继续沉淀，Park 更容易知道系统是否值得信任。"
        return "交易系统继续推进安全、可观察、可复盘的个人交易操作系统。"
    if project == "Agent OS":
        if any(word in joined for word in ["daily closeout", "daily update", "automation", "know-how"]):
            return "Agent OS 的日闭环和知识沉淀能力增强，Park 更少依赖手动回忆昨天做了什么。"
        return "Agent OS 继续让跨项目 agent 工作更可审计、更可复用。"
    if project == "Park OS":
        return "Park OS 继续把原始想法变成可保存、可查找、可复用的知识资产。"
    if project == "Park 的内容生产":
        return "内容生产系统继续朝可发布内容资产推进；若没有发布证据，则今天主要是内部准备。"
    if project == "ai newsletter":
        return "Newsletter 产品继续维护生成、交付、归档和来源健康的连续性。"
    return "项目有推进，但 v1 需要人工复核具体用户价值。"


def render_project_entry(project: str, items: list[ThreadActivity], hours: int) -> str:
    now = local_now()
    start = now - dt.timedelta(hours=hours)

    anchor = f"<!-- codex-project-daily:{now.date()}:{hours}h:{project} -->"
    manual = [item for item in items if item.thread_source != "automation"]
    automated = [item for item in items if item.thread_source == "automation"]
    lines = [
        anchor,
        f"## {now.date()} Daily Update（过去 {hours} 小时）",
        "",
        f"- 窗口：{start.strftime('%Y-%m-%d %H:%M')} - {now.strftime('%Y-%m-%d %H:%M')} Asia/Shanghai",
        f"- Project：{project}",
        f"- 覆盖：{len(items)} 个 thread（人工 {len(manual)}，自动 {len(automated)}）",
        f"- 数据源：`{STATE_DB}` + rollout JSONL + `{SESSION_INDEX}`",
        "",
        "### CEO/PM 摘要",
        "",
        "<!-- llm-summary:start -->",
        f"- 项目目的：{PROJECT_PURPOSES.get(project, '未配置项目目的。')}",
        f"- 今日用户价值：{value_summary(project, items)}",
        f"- 状态：{len(items)} 个 thread（人工 {len(manual)}，自动 {len(automated)}）；细节见下方证据。",
        "<!-- llm-summary:end -->",
        "",
        "### 今日推进",
        "",
    ]
    if items:
        lines.append(project_summary_line(project, items))
    else:
        lines.append("- 过去窗口内没有找到这个 Project 的 Codex thread 活动。")
    lines.append("")

    if manual:
        lines.extend(["### 人工推进", ""])
        for item in manual:
            lines.extend(
                [
                    f"- `{item.title}`（{item.updated_at.strftime('%m-%d %H:%M')}，{item.status}）",
                    f"  - 目标/请求：{item.request}",
                    f"  - 结果/状态：{item.outcome}",
                ]
            )
        lines.append("")

    if automated:
        lines.extend(["### 自动运行", ""])
        for item in automated:
            lines.extend(
                [
                    f"- `{item.title}`（{item.updated_at.strftime('%m-%d %H:%M')}，{item.status}）",
                    f"  - 结果/状态：{item.outcome}",
                ]
            )
        lines.append("")

    if items:
        lines.extend(["### 证据", ""])
        for item in items[:10]:
            lines.append(f"- `{item.id}` · `{item.cwd}`")
        if len(items) > 10:
            lines.append(f"- 另有 {len(items) - 10} 个 thread 未展开列出。")
        lines.append("")

    lines.extend(["---", ""])
    return "\n".join(lines)


def upsert_entry(output: Path, entry: str, project: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# {project} Daily Updates\n\n"
        "> 累计日报；最新内容在最上面。记录这个 Codex Project 每天推进了什么。\n\n"
    )
    anchor = entry.splitlines()[0]
    if not output.exists():
        output.write_text(header + entry, encoding="utf-8")
        return

    existing = output.read_text(encoding="utf-8", errors="replace")
    if anchor in existing:
        pattern = re.compile(re.escape(anchor) + r".*?(?=\n<!-- codex-project-daily:|\Z)", re.S)
        updated = pattern.sub(entry.rstrip() + "\n", existing)
        output.write_text(updated, encoding="utf-8")
        return

    if existing.startswith("# "):
        marker = "\n<!-- codex-project-daily:"
        idx = existing.find(marker)
        if idx != -1:
            updated = existing[: idx + 1] + entry + existing[idx + 1 :]
        else:
            updated = existing.rstrip() + "\n\n" + entry
    else:
        updated = header + entry + "\n" + existing
    output.write_text(updated, encoding="utf-8")


def render_all_project_entries(hours: int) -> dict[str, str]:
    activities = fetch_threads(hours, load_session_titles())
    grouped: dict[str, list[ThreadActivity]] = defaultdict(list)
    for activity in activities:
        grouped[activity.project].append(activity)
    return {
        project: render_project_entry(project, grouped.get(project, []), hours)
        for project in PINNED_PROJECTS
    }


def write_project_reports(output_root: Path, hours: int) -> dict[str, str]:
    outputs: dict[str, str] = {}
    entries = render_all_project_entries(hours)
    for project, entry in entries.items():
        output = output_root / clean_segment(project) / "daily-update.md"
        upsert_entry(output, entry, project)
        outputs[project] = str(output)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    output_root = Path(args.output_root)
    entries = render_all_project_entries(args.hours)
    outputs: dict[str, str] = {
        project: str(output_root / clean_segment(project) / "daily-update.md")
        for project in PINNED_PROJECTS
    }
    if args.write:
        outputs = write_project_reports(output_root, args.hours)
    if args.json:
        print(
            json.dumps(
                {
                    "output_root": str(output_root),
                    "hours": args.hours,
                    "written": args.write,
                    "pinned_projects": PINNED_PROJECTS,
                    "outputs": outputs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("\n".join(entries.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
