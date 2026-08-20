"""Forge command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import ModelSpec, ROLE_NAMES, RunConfig
from .orchestrator import ForgeOrchestrator
from .web import serve


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="Competitive coding-agent orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run Forge in the foreground")
    run.add_argument("--repo", required=True, help="target Git repository")
    run.add_argument("--brief", required=True, help="final product brief in Markdown")
    run.add_argument("--branch", default="main", help="local branch to fast-forward and push")
    for role in ROLE_NAMES:
        run.add_argument(
            "--" + role.replace("_", "-"),
            required=True,
            metavar="PROVIDER:MODEL[:EFFORT]",
        )
    run.add_argument("--no-push", action="store_true", help="commit locally without pushing")
    run.add_argument("--agent-timeout", type=int, default=3600, metavar="SECONDS")

    ui = sub.add_parser("ui", help="start the local web control room")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8787)
    ui.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "ui":
        serve(args.host, args.port, open_browser=not args.no_browser)
        return 0
    models = {
        role: ModelSpec.parse(getattr(args, role))
        for role in ROLE_NAMES
    }
    config = RunConfig(
        repo=str(Path(args.repo).expanduser().resolve()),
        brief=str(Path(args.brief).expanduser().resolve()),
        branch=args.branch,
        models=models,
        push=not args.no_push,
        agent_timeout_seconds=args.agent_timeout,
    )
    orchestrator = ForgeOrchestrator(
        config,
        on_event=lambda event: print(json.dumps(event, sort_keys=True), flush=True),
    )
    state = orchestrator.run()
    print(json.dumps(state.to_dict(), indent=2))
    return 0 if state.status == "complete" else 1
