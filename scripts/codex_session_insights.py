#!/usr/bin/env python3
"""Sync real Codex decision logs into thread-level Obsidian folders.

Current policy:
- No historical transcript backfill.
- No project-level merged decision logs.
- Each retained Codex thread has its own decision-log.md and know-how.md.
- OB decision-log.md is a verbatim copy of the source decision-log.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
from pathlib import Path


DEFAULT_OUTPUT_ROOT = Path(
    "/Users/wendy/park-io/008_codex session insights and decision logs"
)

SOURCE_THREADS = [
    {
        "project": "交易系统",
        "thread": "Tradingview api研究",
        "thread_id": "019f3588-b765-7b83-8a14-2fa1c1e22747",
        "source": Path("/Users/wendy/Documents/内容制作/decision-log.md"),
        "know_how_key": "tradingview",
    },
    {
        "project": "交易系统",
        "thread": "接入老虎证券",
        "thread_id": "019f3186-1e48-7f20-a326-3a7a218767a5",
        "source": Path("/Users/wendy/trading-orchestrator/decision-log.md"),
        "know_how_key": "tiger_trading",
    },
    {
        "project": "agent os",
        "thread": "撰写decision-log转know-how提示词",
        "thread_id": "019f35e6-eb99-72f2-b696-37f46e28f5a4",
        "source": Path("/Users/wendy/Documents/agent自管理/decision-log.md"),
        "know_how_key": "agent_os",
    },
    {
        "project": "park os",
        "thread": "整理项目为Park原始输出",
        "thread_id": "019f351e-5b84-79d1-a72a-fe4255751895",
        "source": Path(
            "/Users/wendy/Documents/知识库/chatgpt-project-personal-growth-to-park-raw/decision-log.md"
        ),
        "know_how_key": "park_raw",
    },
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def clean_segment(value: str) -> str:
    return (
        value.strip()
        .replace("/", "／")
        .replace(":", "：")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def reset_output_root(output_root: Path) -> None:
    if not output_root.exists():
        output_root.mkdir(parents=True)
        return
    for child in output_root.iterdir():
        if not child.is_dir():
            child.unlink()
            continue
        for project_child in child.iterdir():
            if project_child.name == "daily-update.md" and project_child.is_file():
                continue
            if project_child.is_dir():
                shutil.rmtree(project_child)
            else:
                project_child.unlink()
        if not any(child.iterdir()):
            child.rmdir()


def know_how_for(key: str, synced_at: str, source: Path) -> str:
    if key == "tradingview":
        reusable = [
            "把图表、行情数据、broker API、交易授权拆成四层判断；不要因为某一层可用就默认另一层可用。",
            "TradingView 会员、Widgets、Charting Library/Advanced Charts 都不是可直接外部分发的 COMEX 数据 API。",
            "自有系统要先拿到合规行情源，再把数据喂给图表组件；图表组件只负责展示，不负责数据授权。",
            "不要用抓包、cookie、非公开接口或 TradingView 前端 session 做交易系统的行情来源。",
        ]
        gotchas = [
            "Widgets 适合嵌入展示，但不是策略系统的数据管道。",
            "Advanced Charts 需要自己供数；它不是行情 vendor。",
        ]
    elif key == "tiger_trading":
        reusable = [
            "新 broker/venue 接入先做 read-only 与 price-feed-first；下单、撤单、平仓、实盘路由必须单独批准。",
            "price feed accepted 不等于 broker-order authorized；行情验收和交易授权要分开记录。",
            "COMEX/MGC 研究要保持 venue-specific，不要和 Binance、OTC 或现货金口径混用。",
            "状态判断必须能追到 evidence：artifact、receipt、测试结果、源码路径或真实运行输出。",
            "dashboard、acceptance、read model 只能展示状态，不能隐式变成配置写入或 order-control surface。",
        ]
        gotchas = [
            "`pending_market_open` 通常表示等待市场时段验证，不等于数据源失败。",
            "不要把 simulated/demo/human-fill evidence 误读为 live broker execution。",
        ]
    elif key == "agent_os":
        reusable = [
            "Codex project 边界按侧边栏 project label 与 thread title 管理；cwd 只是证据 metadata。",
            "Decision log 是最终记录：真实决策、理由、证据、gotchas；不要从旧 transcript 回溯伪造。",
            "Know-how 是从 decision log 抽象出的可复用经验，应该更短、更 general、更像 project/thread harness。",
            "OB 里的 decision-log.md 应直接同步源文件原文；如果源文件更新，OB 版本下一次同步必须跟着更新。",
        ]
        gotchas = [
            "不要再生成 session-note、project-level 合并 log 或历史 transcript backfill。",
            "如果一个源 decision log 实际跨多个 thread，先在源头拆分，再同步到 OB。",
        ]
    elif key == "park_raw":
        reusable = [
            "Park-IO 入库要区分 Park 原始输出、外部输入和 agent 处理稿。",
            "无法验证正文时，不从标题、记忆或登录页推断内容。",
            "原始输出适合保留 `Section 1：原始输出` 与 `Section 2：处理过的输出` 的双层结构。",
            "正式迁移前先做候选扫描，列出来源、建议路径、复核状态，确认后再写入正式文件。",
        ]
        gotchas = [
            "ChatGPT Project 链接通常不是公开内容；无登录态只会看到登录页。",
            "外部文章和 Park 自己的框架化判断要分开。",
        ]
    else:
        reusable = ["该 thread 暂未提炼稳定 know-how。"]
        gotchas = []

    lines = [
        "# Know-How",
        "",
        "## 可复用原则",
        "",
        *[f"- {item}" for item in reusable],
        "",
    ]
    if gotchas:
        lines.extend(["## Gotchas", "", *[f"- {item}" for item in gotchas], ""])
    lines.extend(
        [
            "## 来源",
            "",
            f"- `{source}`",
            f"- Last synced: `{synced_at}`",
            "",
        ]
    )
    return "\n".join(lines)


def generate(output_root: Path, write: bool) -> dict:
    synced_at = now_iso()
    retained: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    if write:
        reset_output_root(output_root)

    for entry in SOURCE_THREADS:
        source: Path = entry["source"]
        if not source.exists():
            skipped.append(
                {
                    "project": entry["project"],
                    "thread": entry["thread"],
                    "source": str(source),
                    "reason": "missing_source_decision_log",
                }
            )
            continue

        project_dir = output_root / clean_segment(entry["project"])
        thread_dir = project_dir / clean_segment(entry["thread"])
        retained.append(
            {
                "project": entry["project"],
                "thread": entry["thread"],
                "thread_id": entry["thread_id"],
                "source": str(source),
                "output": str(thread_dir),
            }
        )

        if not write:
            continue

        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "decision-log.md").write_text(
            source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (thread_dir / "know-how.md").write_text(
            know_how_for(entry["know_how_key"], synced_at, source),
            encoding="utf-8",
        )

    return {
        "output_root": str(output_root),
        "retained_thread_count": len(retained),
        "retained_threads": retained,
        "skipped": skipped,
        "written": write,
        "synced_at": synced_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = generate(Path(args.output_root), write=args.write)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"retained_threads: {result['retained_thread_count']}")
        for item in result["retained_threads"]:
            print(f"{item['project']} / {item['thread']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
