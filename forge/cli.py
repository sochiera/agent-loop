"""Forge command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

from .access import GATE_ENV, gate_from_env
from .models import CODER_ROLES, DEFAULT_MODEL_SELECTORS, ModelSpec, ROLE_NAMES, RunConfig
from .orchestrator import ForgeOrchestrator
from .policy import PromotionSnapshot, load_policy
from .web import serve


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="Continuous product sprint orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run Forge in the foreground")
    run.add_argument("--repo", required=True, help="target Git repository")
    run.add_argument("--brief", required=True, help="final product brief in Markdown")
    run.add_argument("--branch", default="main", help="local branch to fast-forward and push")
    for role in ROLE_NAMES:
        run.add_argument(
            "--" + role.replace("_", "-"),
            default=None,
            metavar="PROVIDER:MODEL[:EFFORT]",
            help=(
                "override the policy-selected model for this role "
                f"(representative default: {DEFAULT_MODEL_SELECTORS[role]})"
            ),
        )
    run.add_argument(
        "--shuffle-coders",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="redraw the three coder models from the weighted policy pool each sprint",
    )
    run.add_argument(
        "--backup",
        default=None,
        metavar="PROVIDER:MODEL[:EFFORT]",
        help="fallback model used when a role model hits a usage limit",
    )
    run.add_argument(
        "--policy-path",
        default="",
        metavar="PATH",
        help="model policy JSON path (defaults to Jan's state file)",
    )
    run.add_argument("--no-push", action="store_true", help="commit locally without pushing")
    run.add_argument("--agent-timeout", type=int, default=3600, metavar="SECONDS")
    resume = sub.add_parser("resume", help="recover a failed run from its last checkpoint")
    resume.add_argument("--repo", required=True, help="target Git repository")
    resume.add_argument("--run-id", required=True, help="existing Forge run id")
    resume.add_argument(
        "--migrate-models",
        action="store_true",
        help="explicitly migrate an off-policy run to the active model policy",
    )

    recover = sub.add_parser(
        "recover", help="resume a failed or interrupted run at its durable sprint phase"
    )
    recover.add_argument("--repo", required=True, help="target Git repository")
    recover.add_argument("--run-id", required=True, help="existing Forge run identifier")
    recover.add_argument(
        "--migrate-models",
        action="store_true",
        help="explicitly migrate an off-policy run to the active model policy",
    )

    ui = sub.add_parser("ui", help="start the local web control room")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8787)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument(
        "--access-gate-file",
        default="",
        metavar="PATH",
        help=(
            "require this 0600 file's contents as the WWW entrance secret "
            f"(defaults to the {GATE_ENV} environment variable)"
        ),
    )

    for command, is_resume in (("swarm-run", False), ("swarm-resume", True)):
        swarm_parser = sub.add_parser(
            command,
            help=(
                "resume a swarm run from its durable state"
                if is_resume
                else "run the cheap-model swarm in the foreground"
            ),
        )
        if is_resume:
            swarm_parser.add_argument("--repo", required=True, help="target Git repository")
            swarm_parser.add_argument("--run-id", required=True, help="existing Forge run id")
        else:
            swarm_parser.add_argument("--repo", required=True, help="target Git repository")
            swarm_parser.add_argument("--brief", required=True, help="(product) brief in Markdown")
            swarm_parser.add_argument("--branch", default="main", help="local swarm branch")
            swarm_parser.add_argument(
                "--planner",
                default=None,
                metavar="PROVIDER:MODEL[:EFFORT]",
                help=f"strong model for the swarm planner (default: {DEFAULT_MODEL_SELECTORS['planner']})",
            )
            swarm_parser.add_argument(
                "--reviewer",
                default=None,
                metavar="PROVIDER:MODEL[:EFFORT]",
                help="strong model choosing the better version (default: codex:gpt-6-sol:medium)",
            )
            swarm_parser.add_argument(
                "--pool",
                default="",
                metavar="SELECTOR[,SELECTOR...]",
                help="override the six-slot cheap pool (defaults to the central policy pool)",
            )
            swarm_parser.add_argument("--teams", type=int, default=3, help="parallel team count (cap 6 worktrees)")
            swarm_parser.add_argument(
                "--ready-threshold",
                type=float,
                default=0.7,
                help="top-priority done fraction that re-arms the planner",
            )
            swarm_parser.add_argument("--seed", type=int, default=None, help="deterministic RNG seed")
            swarm_parser.add_argument(
                "--policy-path",
                default="",
                metavar="PATH",
                help="model policy JSON path (defaults to Jan's state file)",
            )
            swarm_parser.add_argument("--no-push", action="store_true", help="deliver locally only")
            swarm_parser.add_argument("--agent-timeout", type=int, default=3600, metavar="SECONDS")
    return parser


def swarm_controller(args: argparse.Namespace) -> "SwarmController":
    """Build the cheap-model swarm controller from CLI flags."""
    from forge.swarm import SwarmController

    snapshot = load_policy(args.policy_path or None)
    if args.command == "swarm-resume":
        repo = Path(args.repo).expanduser().resolve()
        config_path = repo / ".forge" / "runs" / args.run_id / "config.json"
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        config = RunConfig.from_dict(raw)
        config.repo = str(repo)
        return SwarmController(config, run_id=args.run_id, on_event=None, resume=True)
    roster = select_cli_models(args, snapshot)
    if args.planner:
        roster["planner"] = ModelSpec.parse(args.planner)
    if args.reviewer:
        roster["reviewer"] = ModelSpec.parse(args.reviewer)
    pool = list(CHEAP_CODER_POOL)
    if args.pool:
        pool = [ModelSpec.parse(value.strip()) for value in args.pool.split(",") if value.strip()]
    config = RunConfig(
        repo=str(Path(args.repo).expanduser().resolve()),
        brief=str(Path(args.brief).expanduser().resolve()),
        branch=args.branch,
        models=roster,
        push=not args.no_push,
        agent_timeout_seconds=args.agent_timeout,
        shuffle_coders=False,
        policy_path=args.policy_path,
        coder_pool=pool,
    )
    from forge.swarm import DEFAULT_SWARM_TEAMS

    teams = min(getattr(args, "teams", DEFAULT_SWARM_TEAMS), DEFAULT_SWARM_TEAMS)
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    return SwarmController(config, on_event=None, rng=rng, teams=teams)


def select_cli_models(
    args: argparse.Namespace,
    snapshot: PromotionSnapshot,
    *,
    rng: random.Random | None = None,
) -> dict[str, ModelSpec]:
    """Return the run roster, drawing policy defaults for unspecified roles."""

    overrides = {
        role: ModelSpec.parse(getattr(args, role))
        for role in ROLE_NAMES
        if getattr(args, role)
    }
    return snapshot.select_new_run_models(rng or random.Random(), overrides)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "ui":
        env: dict[str, str] | None = None
        if args.access_gate_file:
            env = dict(os.environ)
            env[GATE_ENV] = args.access_gate_file
        try:
            gate = gate_from_env(env)
        except Exception as exc:
            raise SystemExit(f"forge ui: access gate misconfigured: {exc}")
        serve(args.host, args.port, open_browser=not args.no_browser, gate=gate)
        return 0
    on_event = lambda event: print(json.dumps(event, sort_keys=True), flush=True)
    if args.command in {"swarm-run", "swarm-resume"}:
        if args.command == "swarm-resume" and not (
            Path(args.repo).expanduser().resolve()
            / ".forge"
            / "runs"
            / args.run_id
            / "config.json"
        ).is_file():
            raise SystemExit(f"Forge run config does not exist for the swarm: {args.run_id}")
        controller = swarm_controller(args)
        controller.run()
        summary = controller.summary()
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["status"] in {"completed", "cancelled"} else 1
    if args.command in {"resume", "recover"}:
        repo = Path(args.repo).expanduser().resolve()
        config_path = repo / ".forge" / "runs" / args.run_id / "config.json"
        if args.command == "recover":
            if not config_path.is_file():
                raise SystemExit(f"Forge run config does not exist: {config_path}")
            config = RunConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
            config.repo = str(repo)
            orchestrator = ForgeOrchestrator(
                config,
                run_id=args.run_id,
                on_event=on_event,
            )
        else:
            orchestrator = ForgeOrchestrator.from_existing(
                repo,
                args.run_id,
                on_event=on_event,
            )
        if args.migrate_models:
            migrated = orchestrator.migrate_models()
            if migrated:
                print(json.dumps({"migrated_models": migrated}, sort_keys=True), flush=True)
        state = (
            orchestrator.recover_failed()
            if args.command == "recover"
            else orchestrator.recover()
        )
        print(json.dumps(state.to_dict(), indent=2))
        return 0 if state.status in {"cancelled", "paused"} else 1
    snapshot = load_policy(args.policy_path or None)
    models = select_cli_models(args, snapshot)
    config = RunConfig(
        repo=str(Path(args.repo).expanduser().resolve()),
        brief=str(Path(args.brief).expanduser().resolve()),
        branch=args.branch,
        models=models,
        push=not args.no_push,
        agent_timeout_seconds=args.agent_timeout,
        backup=ModelSpec.parse(args.backup) if args.backup else None,
        shuffle_coders=args.shuffle_coders,
        policy_path=args.policy_path,
        # Explicit coder flags pin the run; otherwise redraw from the cheap pool.
        coder_pool=(
            []
            if any(getattr(args, role) for role in CODER_ROLES)
            else list(snapshot.coder_pool())
        ),
    )
    orchestrator = ForgeOrchestrator(config, on_event=on_event)
    state = orchestrator.run()
    print(json.dumps(state.to_dict(), indent=2))
    return 0 if state.status in {"cancelled", "paused"} else 1
