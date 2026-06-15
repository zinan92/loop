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
        "  /loop init [project]\n"
        "  /loop start [project]\n"
        "  /loop status [project] [--json]\n"
        "  /loop digest [project] [--json]\n"
        "  /loop pause [project]\n"
        "  /loop resume [project]\n"
        "  /loop stop [project]\n"
        "  /loop run-now [project] [--supervised]\n"
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
        if arg in {"--json", "--supervised"}:
            passthrough.append(arg)
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

    if command in {"init", "start", "status", "digest", "pause", "resume", "stop", "run-now"}:
        project, passthrough = optional_project_arg(rest)
        loopctl_args = [command] + passthrough
        if project:
            loopctl_args += ["--project", project]
    elif command == "scheduler":
        if not rest or rest[0] not in {"install", "load", "status", "uninstall"}:
            return usage()
        project, _ = optional_project_arg(rest[1:])
        loopctl_args = ["scheduler", rest[0]]
        if project:
            loopctl_args += ["--project", project]
    else:
        return usage()

    result = subprocess.run([sys.executable, str(LOOPCTL)] + loopctl_args)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
