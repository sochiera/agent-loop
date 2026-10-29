"""Operator command surface for the deterministic local controller."""

import argparse
import json
from pathlib import Path
import sys

from forge import __version__
from forge.config import ConfigError, load_config


def _positive(value):
    try:
        result = int(value)
        if result > 0:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('must be a positive integer')


def _loop_metrics(config, args, checkpoint):
    """Additive status view. Opens forge.db read-only and never writes the capsule."""
    import time
    from forge.loop import _read_capsule, _stopped, goal_identity
    from forge.quota_metrics import snapshot
    view = {'checkpoint': checkpoint}
    if args.metrics:
        view['metrics'] = snapshot(Path(config.state_dir) / 'forge.db', time.time(), goal_id=goal_identity(args.goal)[2])
    if args.capsule:
        capsule = _read_capsule(args.capsule)
        view['authorization'] = ({'status': 'UNREADABLE', 'stopped': None} if capsule is None else
                                 {'status': 'STOPPED' if _stopped(capsule) else capsule.get('status'),
                                  'stopped': _stopped(capsule)})
    return view


def parser():
    cli = argparse.ArgumentParser(prog='forge', description='Forge deterministic local development controller.')
    cli.add_argument('--version', action='version', version=f'forge {__version__}')
    cli.add_argument('--config', help='explicit operator TOML under XDG_CONFIG_HOME/forge/projects')
    subs = cli.add_subparsers(dest='command', required=True)
    loop_status = subs.add_parser('loop-status', help='read-only KISS goal-loop checkpoint',
                                  description='Read-only view of a forge.loop checkpoint. The loop itself is launched '
                                              'only by the trusted in-process wrapper calling forge.loop.run_loop.')
    loop_status.add_argument('--config', default=argparse.SUPPRESS)
    loop_status.add_argument('--goal', required=True)
    loop_status.add_argument('--json', action='store_true', help='JSON output (already the default)')
    loop_status.add_argument('--metrics', action='store_true',
                             help='additive read-only view: {"checkpoint": ..., "metrics": ...}')
    loop_status.add_argument('--capsule', help='read-only capsule; only its authorization status is reported')
    for name in ('config-check', 'init', 'doctor', 'run', 'status', 'cancel', 'unblock', 'satisfied', 'evidence', 'canary'):
        legacy = name not in ('config-check', 'doctor')
        cmd = subs.add_parser(name, help='validate operator config' if name == 'config-check' else 'sandbox probe / native smokes' if name == 'doctor' else 'legacy multi-stage lifecycle',
                              description='Legacy multi-stage lifecycle command, isolated and frozen; the KISS goal loop '
                                          'does not use it.' if legacy else None)
        cmd.add_argument('--config', default=argparse.SUPPRESS)
        if name == 'doctor':
            cmd.add_argument('--smoke', action='store_true')
            cmd.add_argument('--profile')
        elif name == 'run':
            cmd.add_argument('--max-batches', type=_positive)
            cmd.add_argument('--until-idle', action='store_true')
        elif name == 'status':
            cmd.add_argument('--json', action='store_true')
        elif name == 'cancel':
            group = cmd.add_mutually_exclusive_group(required=True)
            group.add_argument('--task')
            group.add_argument('--all', action='store_true')
        elif name == 'unblock':
            cmd.add_argument('id')
            cmd.add_argument('--note', required=True)
            cmd.add_argument('--fresh-session', action='store_true')
        elif name == 'satisfied':
            cmd.add_argument('--note', required=True)
        elif name == 'evidence':
            cmd.add_argument('id')
        elif name == 'canary':
            cmd.add_argument('--workdir', required=True)
            cmd.add_argument('--max-batches', type=_positive, default=2)
            cmd.add_argument('--verify', action='store_true')
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.command == 'canary':
        from forge.canary import canary
        try:
            return canary(args.workdir, max_batches=args.max_batches, verify_only=args.verify)
        except (OSError, ValueError, RuntimeError) as exc:
            print('forge canary: ' + str(exc), file=sys.stderr)
            return 2
    if not args.config:
        print(f'forge {args.command} requires --config PATH; configuration-free invocation not yet implemented; provide trusted operator configuration', file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f'forge: {exc}', file=sys.stderr)
        return 2
    if args.command == 'loop-status':
        from forge.loop import loop_status
        try:
            checkpoint = loop_status(config, args.goal)
        except (OSError, ValueError) as exc:
            print(f'forge: {exc}', file=sys.stderr)
            return 2
        if not (args.metrics or args.capsule):
            print(json.dumps(checkpoint or {'status': 'NONE'}, sort_keys=True))
            return 0
        print(json.dumps(_loop_metrics(config, args, checkpoint or {'status': 'NONE'}), sort_keys=True))
        return 0
    if args.command == 'doctor':
        from forge.doctor import doctor
        return doctor(config, smoke=args.smoke, profile_id=args.profile)
    if args.command != 'config-check':
        from forge.engine import Engine, EngineBlocked, request_cancel, satisfy, status, unblock
        from forge.gitops import GitError
        from forge.state import State, StateError, controller_lock
        try:
            with State(config.state_dir) as state:
                if args.command == 'init':
                    from forge.docsindex import initialize
                    with controller_lock(state.root):
                        print(initialize(config, state))
                elif args.command == 'run':
                    return Engine(config, state).run(max_batches=args.max_batches, until_idle=args.until_idle)
                if args.command == 'status':
                    print(json.dumps(status(state), sort_keys=True))
                elif args.command == 'cancel':
                    print(request_cancel(state, 'all' if args.all else args.task))
                elif args.command == 'satisfied':
                    satisfy(state, args.note)
                    print('OWNER_SATISFIED: ' + args.note)
                elif args.command == 'unblock':
                    with controller_lock(state.root):
                        unblock(Engine(config, state), args.id, args.note, args.fresh_session)
                    print('unblocked: ' + args.id)
                elif args.command == 'evidence':
                    task = state.db.execute('SELECT * FROM tasks WHERE id=?', (args.id,)).fetchone()
                    if task is None:
                        raise EngineBlocked('unknown task: ' + args.id)
                    print(json.dumps({'task': dict(task), 'artifacts': [dict(r) for r in state.db.execute(
                        'SELECT * FROM artifacts WHERE task=?', (args.id,))], 'calls': [dict(r) for r in state.db.execute(
                        'SELECT * FROM calls WHERE task=?', (args.id,))]}, sort_keys=True))
            return 0
        except (EngineBlocked, StateError, GitError, OSError, RuntimeError) as exc:
            print('forge: ' + str(exc), file=sys.stderr)
            return 2
    print(json.dumps({'config': str(config.path), 'target_repo': str(config.target_repo),
                      'state_dir': str(config.state_dir), 'profiles': len(config.profiles),
                      'runtime_verified': False}, sort_keys=True))
    return 0
