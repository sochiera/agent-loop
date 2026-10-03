"""Forge command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import threading
from pathlib import Path

from .access import GATE_ENV, gate_from_env
from .models import DEFAULT_MODEL_SELECTORS, ModelSpec, ROLE_NAMES, RunConfig
from .orchestrator import ForgeOrchestrator
from .policy import SOL, PromotionSnapshot, load_policy
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
        swarm_parser.add_argument(
            "--policy-path",
            default="",
            metavar="PATH",
            help=(
                "model policy JSON path (a resume keeps the run's own path unless "
                "this is given; both default to Jan's state file)"
            ),
        )
        if is_resume:
            swarm_parser.add_argument("--repo", required=True, help="target Git repository")
            swarm_parser.add_argument("--run-id", required=True, help="existing Forge run id")
            swarm_parser.add_argument(
                "--migrate-models",
                action="store_true",
                help=(
                    "explicitly move roles the current policy refuses (e.g. a retired "
                    "planner) onto it; tasks, worktrees and patches are kept"
                ),
            )
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
                help="override the cheap pool (defaults to the central policy pool)",
            )
            swarm_parser.add_argument("--teams", type=int, default=3, help="parallel team count (cap 6 worktrees)")
            swarm_parser.add_argument(
                "--ready-threshold",
                type=float,
                default=0.7,
                help="top-priority done fraction that re-arms the planner",
            )
            swarm_parser.add_argument("--seed", type=int, default=None, help="deterministic RNG seed")
            swarm_parser.add_argument("--no-push", action="store_true", help="deliver locally only")
            swarm_parser.add_argument("--agent-timeout", type=int, default=3600, metavar="SECONDS")
    status = sub.add_parser(
        "swarm-status", help="print a swarm run's durable progress and live agents"
    )
    status.add_argument("--repo", required=True, help="target Git repository")
    status.add_argument("--run-id", required=True, help="existing Forge run id")
    return parser


def swarm_status(repo: Path, run_id: str) -> dict:
    """Bounded progress view from Forge's own durable files: task counts,
    teams, live agents with their age and the controller heartbeat."""

    import datetime as dt

    root = repo / ".forge" / "runs" / run_id / "swarm"
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    heartbeat_path = root / "heartbeat.json"
    heartbeat = (
        json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if heartbeat_path.is_file()
        else {}
    )
    now = dt.datetime.now(dt.timezone.utc)

    def age(stamp: str) -> int | None:
        try:
            return int((now - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds())
        except (TypeError, ValueError):
            return None

    from forge.swarm import process_start_ticks

    pid = int(heartbeat.get("pid") or 0)
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    # A live pid alone may be a later process reusing it: the controller is
    # the process whose start time the heartbeat recorded.
    recorded = str(heartbeat.get("pid_start_ticks") or "")
    identity_verified = bool(alive and recorded and process_start_ticks(pid) == recorded)
    if recorded and not identity_verified:
        alive = False
    counts: dict[str, int] = {}
    for task in state.get("tasks", []):
        counts[task["status"]] = counts.get(task["status"], 0) + 1
    return {
        "status": state.get("status"),
        "message": state.get("message"),
        "tasks": counts,
        "teams": [
            {
                "team": team["id"],
                "task": team["task_id"],
                "phase": team["phase"],
                "review_round": team.get("review_round", 0),
                "fix_round": team.get("fix_round", 0),
                "done_jobs": team.get("done_jobs"),
            }
            for team in state.get("teams", [])
        ],
        "controller": {
            "pid": pid,
            "alive": alive,
            "identity_verified": identity_verified,
            "controller_id": heartbeat.get("controller_id", ""),
            "heartbeat_status": heartbeat.get("status"),
            "heartbeat_age_seconds": age(heartbeat.get("updated_at", "")),
        },
        "inflight": [
            {**entry, "elapsed_seconds": age(entry.get("started_at", ""))}
            for entry in heartbeat.get("inflight", [])
        ],
        "agent_timeout_seconds": heartbeat.get("agent_timeout_seconds"),
        "recent_warnings": state.get("warnings", [])[-5:],
    }


def swarm_controller(args: argparse.Namespace, on_event=None) -> "SwarmController":
    """Build the cheap-model swarm controller from CLI flags."""
    from forge.swarm import SwarmController

    if args.command == "swarm-resume":
        return SwarmController.resume_existing(
            args.repo,
            args.run_id,
            migrate_models=args.migrate_models,
            policy_path=args.policy_path,
            on_event=on_event,
        )
    snapshot = load_policy(args.policy_path or None)
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    # The planner draws from the strong pool; the strong reviewer defaults to
    # Sol because the swarm's final reviewer must be Sol or Opus.
    overrides = {
        "reviewer": ModelSpec.parse(args.reviewer) if args.reviewer else SOL,
    }
    if args.planner:
        overrides["planner"] = ModelSpec.parse(args.planner)
    roster = snapshot.select_new_run_models(rng, overrides)
    pool = list(snapshot.cheap_pool())
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
        cheap_pool=pool,
    )
    from forge.swarm import DEFAULT_SWARM_TEAMS

    teams = min(getattr(args, "teams", DEFAULT_SWARM_TEAMS), DEFAULT_SWARM_TEAMS)
    return SwarmController(config, on_event=on_event, rng=rng, teams=teams)


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
    if args.command == "swarm-status":
        repo = Path(args.repo).expanduser().resolve()
        if not (repo / ".forge" / "runs" / args.run_id / "swarm" / "state.json").is_file():
            raise SystemExit(f"Forge swarm run does not exist: {args.run_id}")
        print(json.dumps(swarm_status(repo, args.run_id), indent=2, sort_keys=True))
        return 0
    if args.command in {"swarm-run", "swarm-resume"}:
        from forge.swarm import SwarmFailed

        if args.command == "swarm-resume" and not (
            Path(args.repo).expanduser().resolve()
            / ".forge"
            / "runs"
            / args.run_id
            / "config.json"
        ).is_file():
            raise SystemExit(f"Forge run config does not exist for the swarm: {args.run_id}")
        try:
            controller = swarm_controller(args, on_event)
        except (SwarmFailed, ValueError) as exc:
            raise SystemExit(f"forge {args.command}: {exc}")
        # A supervisor stops the swarm with SIGTERM; cancel cleanly so the
        # durable state records a terminal status instead of "running". The
        # cancel runs off the main thread, which may hold the runner's lock.
        signal.signal(
            signal.SIGTERM,
            lambda _signum, _frame: threading.Thread(target=controller.cancel, daemon=True).start(),
        )
        from forge.locking import ExecutionLocked

        try:
            controller.run()
        except (SwarmFailed, ExecutionLocked) as exc:
            # Refused before taking over the run: nothing durable changed.
            raise SystemExit(f"forge {args.command}: {exc}")
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
    )
    orchestrator = ForgeOrchestrator(config, on_event=on_event)
    state = orchestrator.run()
    print(json.dumps(state.to_dict(), indent=2))
    return 0 if state.status in {"cancelled", "paused"} else 1
