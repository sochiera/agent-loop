"""Enforced bubblewrap boundary: bind classification, canonical validation, argv, real probe.

There is exactly one execution mode. Any validation, revalidation or bwrap setup
failure raises SandboxError, which callers classify as env_block; nothing ever
falls back to running a native CLI or gate command on the host.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

BWRAP = '/usr/bin/bwrap'
NATIVE_BIN = PurePosixPath('/opt/forge-native/bin')
NATIVE_RUNTIME = PurePosixPath('/opt/forge-native/runtime')
SANDBOX_PATH = '/usr/bin:/bin:/opt/forge-native/bin'
# Destinations owned by the fixed system layout; no class except its owner may overlay them.
FIXED_DESTS = tuple(PurePosixPath(p) for p in ('/usr', '/etc', '/lib', '/lib64', '/bin', '/sbin', '/proc', '/dev',
                                                '/tmp', '/run', '/work', '/in', '/opt/forge-native'))
# Hidden top-level HOME entries are credential/config roots unless an exact class exception applies.
# `.local` is only a container; its sensitive children are listed explicitly.
HOME_CONTAINERS = frozenset({'.local'})
HOME_SENSITIVE_CHILDREN = ('.local/state', '.local/share/keyrings')
CREDENTIAL_NAMES = re.compile(r'^(auth\.json|\.credentials\.json|credentials(\.json)?|.*\.pem|id_(rsa|ed25519|ecdsa)\S*|'
                              r'forge\.db|\.netrc|\.git-credentials|oauth.*\.json|token(s)?\.json)$')
WORK_LEAF = re.compile(r'^(src|gate-\d+|review-\d+)$')
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


class SandboxError(RuntimeError):
    """Boundary cannot be enforced as requested; the route is env_block."""


@dataclass(frozen=True)
class Mount:
    cls: str          # work | input | executable | runtime | harness | toolchain
    source: Path      # canonical
    dest: PurePosixPath
    writable: bool
    identity: tuple[int, int]  # (st_dev, st_ino) at validation

    def manifest(self):
        return {'class': self.cls, 'source': str(self.source), 'dest': str(self.dest),
                'mode': 'rw' if self.writable else 'ro', 'dev': self.identity[0], 'ino': self.identity[1]}


@dataclass(frozen=True)
class Spec:
    kind: str                 # agent | gate
    mounts: tuple[Mount, ...]
    network: bool
    home: PurePosixPath
    command: tuple[str, ...]  # absolute in-sandbox executable first
    extra_env: tuple[tuple[str, str], ...] = ()
    label: str = field(default='bwrap')

    def manifest(self):
        return {'sandbox': self.label, 'kind': self.kind, 'network': self.network, 'home': str(self.home),
                'mounts': [m.manifest() for m in self.mounts], 'command': list(self.command)}


@dataclass(frozen=True)
class Roots:
    """Protected host roots for one project. All paths canonical."""
    home: Path
    state: Path
    config: Path
    target: Path
    harness: dict          # harness name -> tuple[Path] of canonical credential leaves (all harnesses)
    extra: tuple[Path, ...] = ()

    def protected(self, except_harness: str | None = None) -> list[tuple[str, Path]]:
        result = [('state', self.state), ('config', self.config), ('target', self.target)]
        result += [('protected', p) for p in self.extra]
        for name, leaves in self.harness.items():
            if name != except_harness:
                result += [(f'harness:{name}', p) for p in leaves]
        if self.home.is_dir():
            for entry in sorted(self.home.iterdir()):
                if entry.name.startswith('.') and entry.name not in HOME_CONTAINERS:
                    result.append(('home-hidden', canonical(entry, must_exist=False)))
        result += [('home-hidden', canonical(self.home / c, must_exist=False)) for c in HOME_SENSITIVE_CHILDREN]
        return result


def canonical(path, must_exist=True) -> Path:
    path = Path(os.path.expanduser(str(path)))
    if not path.is_absolute():
        raise SandboxError(f'bind source must be absolute: {path}')
    try:
        return Path(os.path.realpath(path, strict=must_exist))
    except OSError as exc:
        raise SandboxError(f'bind source does not exist: {path}') from exc


def overlaps(a, b) -> bool:
    """Component-wise ancestor/descendant/equal test; never a string prefix test."""
    pa, pb = PurePosixPath(a).parts, PurePosixPath(b).parts
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def inside(child, parent) -> bool:
    pc, pp = PurePosixPath(child).parts, PurePosixPath(parent).parts
    return len(pc) >= len(pp) and pc[:len(pp)] == pp


def _identity(path: Path) -> tuple[int, int]:
    info = os.stat(path)
    return info.st_dev, info.st_ino


def _always_forbidden(source: Path, roots: Roots):
    for root in (Path('/'), Path('/home'), roots.home, roots.state, roots.config):
        if inside(root, source):
            raise SandboxError(f'bind {source} is (an ancestor of) forbidden root {root}')


def _disjoint(source: Path, roots: Roots, allowed: list[Path], harness: str | None):
    for label, root in roots.protected(except_harness=harness):
        if any(root == a for a in allowed):
            continue
        if overlaps(source, root):
            raise SandboxError(f'bind {source} overlaps protected {label} root {root}')


def work_mount(roots: Roots, source, writable: bool) -> Mount:
    source = canonical(source)
    rel = source.relative_to(roots.state).parts if inside(source, roots.state) else ()
    if len(rel) != 3 or rel[0] != 'ws' or not SAFE_ID.match(rel[1]) or not WORK_LEAF.match(rel[2]) or not source.is_dir():
        raise SandboxError(f'work mount must be a selected state/ws/<task>/(src|gate-n|review-n) leaf: {source}')
    return Mount('work', source, PurePosixPath('/work'), writable, _identity(source))


def input_mount(roots: Roots, source) -> Mount:
    source = canonical(source)
    rel = source.relative_to(roots.state).parts if inside(source, roots.state) else ()
    if len(rel) != 3 or rel[0] != 'calls' or not SAFE_ID.match(rel[1]) or rel[2] != 'in' or not source.is_dir():
        raise SandboxError(f'input mount must be this call\'s state/calls/<id>/in leaf: {source}')
    return Mount('input', source, PurePosixPath('/in'), False, _identity(source))


def harness_mounts(roots: Roots, harness: str, binds_rw, binds_ro) -> list[Mount]:
    own = list(roots.harness.get(harness, ()))
    mounts = []
    for raw, writable in [(b, True) for b in binds_rw] + [(b, False) for b in binds_ro]:
        lexical = Path(os.path.expanduser(raw))
        source = canonical(lexical)
        if source not in own:
            raise SandboxError(f'harness bind {source} is not an approved {harness} leaf')
        _always_forbidden(source, roots)
        _disjoint(source, roots, own, harness)
        if not inside(lexical, roots.home) or lexical == roots.home:
            raise SandboxError(f'harness destination must be inside HOME: {lexical}')
        mounts.append(Mount('harness', source, PurePosixPath(lexical), writable, _identity(source)))
    return mounts


def executable_mounts(roots: Roots, harness: str, binary, runtime_dir=None) -> tuple[list[Mount], str]:
    """Mount the exact resolved native binary (or its vetted release dir) at a fixed alias."""
    if not SAFE_ID.match(harness):
        raise SandboxError('invalid harness name')
    binary = canonical(binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise SandboxError(f'native executable missing or not executable: {binary}')
    own = list(roots.harness.get(harness, ()))
    within_own = [r for r in own if inside(binary, r)]
    _always_forbidden(binary, roots)
    _disjoint(binary, roots, within_own, harness)
    if runtime_dir is None:
        dest = NATIVE_BIN / harness
        return [Mount('executable', binary, dest, False, _identity(binary))], str(dest)
    runtime = canonical(runtime_dir)
    if not runtime.is_dir() or not inside(binary, runtime) or runtime == binary:
        raise SandboxError('runtime dir must be a directory containing the native executable')
    _always_forbidden(runtime, roots)
    for label, root in roots.protected():
        if inside(root, runtime):
            raise SandboxError(f'runtime dir {runtime} contains protected {label} root {root}')
        if overlaps(runtime, root) and not (root in own and inside(runtime, root)):
            raise SandboxError(f'runtime dir {runtime} overlaps protected {label} root {root}')
    for path, dirs, files in os.walk(runtime):
        for name in files + dirs:
            if CREDENTIAL_NAMES.match(name):
                raise SandboxError(f'runtime dir contains credential-like file {Path(path) / name}')
    dest = NATIVE_RUNTIME / harness
    return ([Mount('runtime', runtime, dest, False, _identity(runtime))],
            str(dest / binary.relative_to(runtime)))


def toolchain_mounts(roots: Roots, ro_binds, taken: list[Mount]) -> list[Mount]:
    mounts = []
    for raw in ro_binds:
        lexical = Path(os.path.expanduser(raw))
        source = canonical(lexical)
        _always_forbidden(source, roots)
        _disjoint(source, roots, [], None)
        for other in taken + mounts:
            if overlaps(source, other.source):
                raise SandboxError(f'toolchain bind {source} overlaps {other.cls} mount {other.source}')
        mounts.append(Mount('toolchain', source, PurePosixPath(lexical), False, _identity(source)))
    return mounts


def _check_destinations(mounts):
    seen = []
    for mount in mounts:
        dest = mount.dest
        if mount.cls in ('work', 'input'):
            expected = PurePosixPath('/work' if mount.cls == 'work' else '/in')
            if dest != expected:
                raise SandboxError('work/input destination alias mismatch')
        elif mount.cls in ('executable', 'runtime'):
            if not inside(dest, PurePosixPath('/opt/forge-native')):
                raise SandboxError('native mount outside /opt/forge-native')
        else:
            if dest in (PurePosixPath('/'), PurePosixPath('/home')) or any(overlaps(dest, f) for f in FIXED_DESTS):
                raise SandboxError(f'{mount.cls} destination {dest} overlays a fixed sandbox destination')
        for other in seen:
            if overlaps(dest, other):
                raise SandboxError(f'mount destinations overlap: {dest} / {other}')
        seen.append(dest)


def agent_spec(roots: Roots, *, harness, binary, native_args, work, writable, input_dir, binds_rw, binds_ro,
               runtime_dir=None, extra_env=(), ro_binds=()) -> Spec:
    """`ro_binds` are the same validated read-only toolchain leaves the gate receives."""
    work_m, input_m = work_mount(roots, work, writable), input_mount(roots, input_dir)
    exe_mounts, exe = executable_mounts(roots, harness, binary, runtime_dir)
    mounts = [work_m, input_m, *exe_mounts, *harness_mounts(roots, harness, binds_rw, binds_ro)]
    mounts += toolchain_mounts(roots, ro_binds, mounts)
    _check_destinations(mounts)
    return Spec('agent', tuple(mounts), True, PurePosixPath(roots.home), (exe, *native_args), tuple(extra_env))


def gate_spec(roots: Roots, *, command, work, input_dir=None, ro_binds=(), network=False, extra_env=()) -> Spec:
    mounts = [work_mount(roots, work, True)]
    if input_dir is not None:
        mounts.append(input_mount(roots, input_dir))
    mounts += toolchain_mounts(roots, ro_binds, mounts)
    _check_destinations(mounts)
    if not command or not all(isinstance(a, str) and '\x00' not in a for a in command):
        raise SandboxError('gate command must be a nonempty trusted argv')
    return Spec('gate', tuple(mounts), bool(network), PurePosixPath('/tmp/home'), tuple(command), tuple(extra_env))


def revalidate(spec: Spec):
    """Right before spawn: canonical source and inode identity must be unchanged."""
    for mount in spec.mounts:
        try:
            now = Path(os.path.realpath(mount.source, strict=True))
            ident = _identity(now)
        except OSError as exc:
            raise SandboxError(f'mount source vanished: {mount.source}') from exc
        if now != mount.source or ident != mount.identity:
            raise SandboxError(f'mount source replaced since validation: {mount.source}')


def argv(spec: Spec) -> list[str]:
    if not os.access(BWRAP, os.X_OK):
        raise SandboxError(f'{BWRAP} not available')
    args = [BWRAP, '--unshare-all']
    if spec.network:
        args.append('--share-net')
    args += ['--die-with-parent', '--new-session', '--clearenv',
             '--ro-bind', '/usr', '/usr', '--ro-bind', '/etc', '/etc',
             '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
             '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/sbin', '/sbin',
             '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/home', '--dir', str(spec.home)]
    if spec.network:
        # Host layout: /etc/resolv.conf links into systemd-resolved's runtime dir (no secrets).
        args += ['--ro-bind-try', '/run/systemd/resolve', '/run/systemd/resolve']
    for mount in sorted(spec.mounts, key=lambda m: len(m.dest.parts)):
        args += ['--bind' if mount.writable else '--ro-bind', str(mount.source), str(mount.dest)]
    args += ['--chdir', '/work', '--setenv', 'HOME', str(spec.home), '--setenv', 'PATH', SANDBOX_PATH,
             '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'TERM', 'dumb']
    for key, value in spec.extra_env:
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or re.search(r'KEY|TOKEN|SECRET|PASSWORD|AUTH', key):
            raise SandboxError(f'refusing sandbox env var {key}')
        args += ['--setenv', key, value]
    return args + ['--', *spec.command]


def project_roots(config, extra=()) -> Roots:
    """Protected roots for an operator config (all harness credential leaves are protected)."""
    from forge.config import config_root
    harness = {}
    for name, entry in config.harness.items():
        harness[name] = tuple(canonical(p, must_exist=False) for p in (*entry['binds_rw'], *entry['binds_ro']))
    return Roots(canonical(Path.home()), canonical(config.state_dir, must_exist=False),
                 canonical(config_root().parent, must_exist=False), canonical(config.target_repo),
                 harness, tuple(canonical(p, must_exist=False) for p in extra))


def resolve_binary(harness: str, configured=None) -> Path:
    found = configured or shutil.which(harness)
    if not found:
        raise SandboxError(f'{harness} executable not found')
    return canonical(found)


PROBE_SCRIPT = r'''
leak=0
for p in "$@"; do
  if [ -e "$p" ] || [ -L "$p" ]; then echo "LEAK $p"; leak=1; fi
done
for p in /proc/[0-9]*; do n=${p#/proc/}; [ "$n" -gt 64 ] && { echo "PIDNS-LEAK $n"; leak=1; }; done
env | sort > /tmp/env.txt
while IFS= read -r line; do case "${line%%=*}" in HOME|PATH|LANG|TERM|PWD|SHLVL|_|OLDPWD) ;; *) echo "ENV-LEAK ${line%%=*}"; leak=1;; esac; done < /tmp/env.txt
if touch /work/.forge-probe-write 2>/dev/null; then rm -f /work/.forge-probe-write; echo WORK-RW; else echo WORK-RO; fi
if touch /in/.forge-probe-write 2>/dev/null; then echo "IN-WRITABLE"; leak=1; fi
python3 -c "import socket; socket.create_connection(('1.1.1.1', 53), 2)" 2>/dev/null && echo NET-UP || echo NET-DOWN
exit $leak
'''


def run_probe(spec: Spec, secret_paths, timeout=60):
    """Execute the adversarial probe inside a real sandbox (spec.command is replaced)."""
    probe = Spec(spec.kind, spec.mounts, spec.network, spec.home,
                 ('/usr/bin/sh', '-c', PROBE_SCRIPT, 'forge-probe', *map(str, secret_paths)))
    revalidate(probe)
    proc = subprocess.run(argv(probe), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr
