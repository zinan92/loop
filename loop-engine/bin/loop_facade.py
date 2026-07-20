#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOOPCTL = HERE / "loopctl.py"


def usage(code: int = 2) -> int:
    print(
        "Usage:\n"
        "  /loop init [project] [--provider codex|claude]\n"
        "  /loop setup [--yes] [--provider codex|claude]\n"
        "  /loop doctor [project]\n"
        "  /loop portfolio init|add|status|intake|init-loop [project|--all-eligible]\n"
        "  /loop morning [project...]\n"
        "  /loop handoff [project...] [--json]\n"
        "  /loop approve [project] [--approve-medium | --medium-envelope NAME | --init-loop | --all-init-loop]\n"
        "  /loop reject [project]\n"
        "  /loop start-day [project...]\n"
        "  /loop evening [project...]\n"
        "  /loop start [project]\n"
        "  /loop status [project] [--json]\n"
        "  /loop digest [project] [--json]\n"
        "  /loop pause [project]\n"
        "  /loop resume [project]\n"
        "  /loop stop [project]\n"
        "  /loop run-now [project] [--supervised]\n"
        "  /loop board-claimer once|start|stop|status [options]\n"
        "  /loop notify setup|test|status\n"
        "  /loop scheduler install [project]\n"
        "  /loop scheduler load [project]\n"
        "  /loop scheduler status [project]\n"
        "  /loop scheduler uninstall [project]\n\n"
        "Defaults: hourly forever, no frequency or duration flags.\n"
        "Scheduler install is a no-op; scheduler load is the explicit ON switch."
    )
    return code


def optional_project_arg(args: list[str]) -> tuple[str | None, list[str]]:
    passthrough: list[str] = []
    project: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {
            "--json",
            "--supervised",
            "--yes",
            "--start",
            "--medium",
            "--approve-medium",
            "--init-loop",
            "--all-init-loop",
            "--all-eligible",
        }:
            passthrough.append(arg)
        elif arg in {
            "--provider",
            "--medium-envelope",
            "--allowed-file",
            "--verification-command",
            "--notify-mode",
            "--webhook-url-file",
            "--linear-api-key-file",
        }:
            if index + 1 >= len(args):
                raise SystemExit(usage())
            passthrough.extend([arg, args[index + 1]])
            index += 1
        elif arg == "--project":
            if index + 1 >= len(args):
                raise SystemExit(usage())
            project = args[index + 1]
            index += 1
        elif project is None:
            project = arg
        else:
            raise SystemExit(usage())
        index += 1
    return project, passthrough


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "/loop":
        args = args[1:]
    if not args or args[0] in {"help", "-h", "--help"}:
        return usage(0)

    command = args[0]
    rest = args[1:]
    loopctl_args: list[str]

    if command in {
        "init",
        "setup",
        "doctor",
        "start",
        "status",
        "digest",
        "pause",
        "resume",
        "stop",
        "run-now",
        "approve",
        "reject",
    }:
        project, passthrough = optional_project_arg(rest)
        loopctl_args = [command] + passthrough
        if project:
            loopctl_args += ["--project", project]
    elif command in {"morning", "start-day", "evening", "handoff"}:
        loopctl_args = [command]
        for arg in rest:
            if arg in {"--start", "--json"}:
                loopctl_args.append(arg)
            else:
                loopctl_args += ["--project", arg]
    elif command == "notify":
        if not rest or rest[0] not in {"setup", "test", "status"}:
            return usage()
        loopctl_args = ["notify", rest[0]]
        loopctl_args.extend(rest[1:])
    elif command == "portfolio":
        if not rest or rest[0] not in {"init", "add", "status", "intake", "init-loop"}:
            return usage()
        loopctl_args = ["portfolio"] + rest
    elif command == "scheduler":
        if not rest or rest[0] not in {"install", "load", "status", "uninstall"}:
            return usage()
        project, _ = optional_project_arg(rest[1:])
        loopctl_args = ["scheduler", rest[0]]
        if project:
            loopctl_args += ["--project", project]
    elif command == "board-claimer":
        result = subprocess.run([sys.executable, str(HERE / "board_claimer.py")] + rest)
        return result.returncode
    else:
        return usage()

    result = subprocess.run([sys.executable, str(LOOPCTL)] + loopctl_args)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
