"""P1 boundary tests: bind validation units plus REAL bubblewrap adversarial execution."""

import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import time

import pytest

from forge import sandbox
from forge.sandbox import SandboxError

FAKE_NATIVE = '#!/usr/bin/sh\nexec /usr/bin/sh /in/script.sh "$@"\n'


def make_layout(base: Path):
    """Fake HOME mirroring the three recon layouts; credentials are dummy files."""
    home = base / 'home'
    files = {
        '.codex/auth.json': 'codex-secret', '.claude/.credentials.json': 'claude-secret', '.claude.json': '{}',
        '.grok/auth.json': 'grok-secret', '.ssh/id_rsa': 'ssh-secret', '.gnupg/key': 'gpg-secret',
        '.coordinator/auth.json': 'coordinator-secret', 'venv/bin/tool': 'tool',
    }
    for rel, content in files.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(content)
    bins = {'codex': home / '.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex',
            'claude': home / '.local/share/claude/versions/2.1.287',
            'grok': home / '.grok/downloads/grok-1.0.52-linux-x86_64'}
    for path in bins.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FAKE_NATIVE)
        path.chmod(0o755)
    state = home / '.local/state/forge/demo'
    (state / 'calls').mkdir(parents=True)
    (state / 'forge.db').write_text('controller-state')
    config = home / '.config/forge'
    config.mkdir(parents=True)
    target = base / 'target'
    target.mkdir()
    harness = {'codex': (home / '.codex',), 'claude': (home / '.claude', home / '.claude.json',
                                                       home / '.local/share/claude'), 'grok': (home / '.grok',)}
    roots = sandbox.Roots(home.resolve(), state.resolve(), config.resolve(), target.resolve(),
                          {k: tuple(p.resolve() for p in v) for k, v in harness.items()})
    return roots, bins


def leaf(roots, task='t1', name='src'):
    path = roots.state / 'ws' / task / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def call_in(roots, call='c1'):
    path = roots.state / 'calls' / call / 'in'
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent(roots, bins, harness='codex', writable=True, args=(), **kw):
    binds = {'codex': (['~/.codex'], []), 'claude': (['~/.claude', '~/.claude.json'], ['~/.local/share/claude']),
             'grok': (['~/.grok'], [])}[harness]
    home = str(roots.home)
    rw = [b.replace('~', home, 1) for b in binds[0]]
    ro = [b.replace('~', home, 1) for b in binds[1]]
    return sandbox.agent_spec(roots, harness=harness, binary=bins[harness], native_args=args,
                              work=kw.get('work') or leaf(roots), writable=writable,
                              input_dir=kw.get('input_dir') or call_in(roots), binds_rw=rw, binds_ro=ro)


def run(spec, timeout=30):
    sandbox.revalidate(spec)
    return subprocess.run(sandbox.argv(spec), capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def with_command(spec, *command):
    return sandbox.Spec(spec.kind, spec.mounts, spec.network, spec.home, command)


@pytest.fixture
def layout(tmp_path):
    return make_layout(tmp_path)


# ---- unit: exact-leaf acceptance -------------------------------------------------

def test_selected_work_and_input_leaves_under_state_accepted(layout):
    roots, bins = layout
    spec = agent(roots, bins)
    classes = {m.cls: m for m in spec.mounts}
    assert classes['work'].dest == PurePosixPath('/work') and classes['work'].writable
    assert classes['input'].dest == PurePosixPath('/in') and not classes['input'].writable
    assert spec.command[0] == '/opt/forge-native/bin/codex'


@pytest.mark.parametrize('harness', ['codex', 'claude', 'grok'])
def test_home_native_binary_alias_accepted(layout, harness):
    roots, bins = layout
    spec = agent(roots, bins, harness)
    exe = next(m for m in spec.mounts if m.cls == 'executable')
    assert exe.source == bins[harness].resolve() and exe.dest == PurePosixPath(f'/opt/forge-native/bin/{harness}')
    assert not exe.writable
    harness_sources = {m.source for m in spec.mounts if m.cls == 'harness'}
    assert harness_sources == set(roots.harness[harness])


def test_home_toolchain_leaf_accepted(layout):
    roots, _ = layout
    spec = sandbox.gate_spec(roots, command=('true',), work=leaf(roots, name='gate-1'),
                             ro_binds=[str(roots.home / 'venv')])
    assert any(m.cls == 'toolchain' and not m.writable for m in spec.mounts)
    assert not spec.network and spec.home == PurePosixPath('/tmp/home')


# ---- unit: rejection --------------------------------------------------------------

@pytest.mark.parametrize('rel', ['', '.local', '.local/state', '.local/state/forge/demo'])
def test_bind_ancestor_rejected(layout, rel):
    roots, _ = layout
    with pytest.raises(SandboxError):
        sandbox.gate_spec(roots, command=('true',), work=leaf(roots, name='gate-1'),
                          ro_binds=[str(roots.home / rel)])
    for root in ('/', '/home'):
        with pytest.raises(SandboxError):
            sandbox.gate_spec(roots, command=('true',), work=leaf(roots, name='gate-1'), ro_binds=[root])


def test_bind_symlink_alias_rejected(layout):
    roots, bins = layout
    link = roots.home / 'innocent'
    link.symlink_to(roots.state)
    with pytest.raises(SandboxError):
        sandbox.gate_spec(roots, command=('true',), work=leaf(roots, name='gate-1'), ro_binds=[str(link)])
    work_link = roots.home / 'worklink'
    work_link.symlink_to(roots.state / 'calls')
    with pytest.raises(SandboxError):
        sandbox.work_mount(roots, work_link, True)
    # A symlinked leaf that resolves to a different task's leaf is accepted only as that canonical leaf.
    other = leaf(roots, 't2')
    alias = roots.state / 'ws' / 't1' / 'review-1'
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(roots.state / 'calls')
    with pytest.raises(SandboxError):
        sandbox.work_mount(roots, alias, False)
    assert sandbox.work_mount(roots, other, False).source == other


@pytest.mark.parametrize('rel', ['', 'calls', 'calls/c1', 'ws', 'ws/t1', 'calls/c1/in/..'])
def test_state_root_and_sibling_call_export_rejected(layout, rel):
    roots, _ = layout
    call_in(roots)
    leaf(roots)
    target = roots.state / rel
    with pytest.raises(SandboxError):
        sandbox.work_mount(roots, target, True)
    with pytest.raises(SandboxError):
        sandbox.input_mount(roots, target)
    with pytest.raises(SandboxError):
        sandbox.input_mount(roots, leaf(roots))  # a work leaf is not this call's input


def test_cannot_bind_ssh_or_other_harness_creds(layout):
    roots, bins = layout
    for path in (roots.home / '.ssh', roots.home / '.claude', roots.home / '.coordinator', roots.target,
                 roots.config):
        with pytest.raises(SandboxError):
            sandbox.gate_spec(roots, command=('true',), work=leaf(roots, name='gate-1'), ro_binds=[str(path)])
    with pytest.raises(SandboxError, match='not an approved codex leaf'):
        sandbox.harness_mounts(roots, 'codex', [str(roots.home / '.claude')], [])
    with pytest.raises(SandboxError):
        sandbox.executable_mounts(roots, 'claude', bins['codex'])  # codex binary lies in another harness root


def test_mount_wrong_alias_or_replaced_root_rejected(layout):
    roots, bins = layout
    spec = agent(roots, bins)
    bad = sandbox.Mount('toolchain', roots.home / 'venv', PurePosixPath('/work/x'), False, (0, 0))
    with pytest.raises(SandboxError):
        sandbox._check_destinations([*spec.mounts, bad])
    wrong = sandbox.Mount('work', spec.mounts[0].source, PurePosixPath('/in'), True, spec.mounts[0].identity)
    with pytest.raises(SandboxError):
        sandbox._check_destinations([wrong])
    for dest in ('/usr/local', '/etc/x', '/opt/forge-native/bin/evil', '/', '/home'):
        with pytest.raises(SandboxError):
            sandbox._check_destinations([sandbox.Mount('toolchain', roots.home / 'venv', PurePosixPath(dest),
                                                       False, (0, 0))])
    work = next(m for m in spec.mounts if m.cls == 'work').source
    work.rename(work.with_name('src-old'))
    work.mkdir()  # same path, different inode: a writable target replaced the mount root
    with pytest.raises(SandboxError, match='replaced'):
        sandbox.revalidate(spec)


def test_exec_parent_containing_credentials_rejected(layout):
    roots, bins = layout
    release = bins['codex'].parent.parent
    mounts, exe = sandbox.executable_mounts(roots, 'codex', bins['codex'], release)
    assert mounts[0].cls == 'runtime' and exe == '/opt/forge-native/runtime/codex/bin/codex'
    (release / 'auth.json').write_text('x')
    with pytest.raises(SandboxError, match='credential'):
        sandbox.executable_mounts(roots, 'codex', bins['codex'], release)
    with pytest.raises(SandboxError):
        sandbox.executable_mounts(roots, 'codex', bins['codex'], roots.home / '.codex')
    with pytest.raises(SandboxError):
        sandbox.executable_mounts(roots, 'codex', bins['codex'], roots.home)


def test_component_wise_overlap_not_string_prefix():
    assert sandbox.overlaps('/a/b', '/a/b/c') and sandbox.overlaps('/a/b/c', '/a/b')
    assert not sandbox.overlaps('/a/bc', '/a/b')
    assert not sandbox.inside('/home/janet', '/home/jan')


def test_argv_fixed_layout_no_host_fallback(layout, monkeypatch):
    roots, bins = layout
    args = sandbox.argv(agent(roots, bins))
    assert args[:3] == ['/usr/bin/bwrap', '--unshare-all', '--share-net']
    for flag in ('--die-with-parent', '--new-session', '--clearenv'):
        assert flag in args
    assert args[args.index('--tmpfs', args.index('--tmpfs') + 1) + 1] == '/home'
    monkeypatch.setattr(sandbox, 'BWRAP', '/nonexistent/bwrap')
    with pytest.raises(SandboxError):
        sandbox.argv(agent(roots, bins))
    with pytest.raises(SandboxError):
        sandbox.argv(sandbox.Spec('agent', (), True, PurePosixPath('/home/x'), ('true',), (('OPENAI_API_KEY', 'x'),)))


# ---- REAL bubblewrap adversarial execution ----------------------------------------

def test_sandbox_cannot_read_state_db_abs_path_or_creds_real(layout):
    roots, bins = layout
    spec = agent(roots, bins, 'codex')
    secrets = [roots.state / 'forge.db', roots.state / 'calls', roots.home / '.ssh/id_rsa',
               roots.home / '.claude/.credentials.json', roots.home / '.grok/auth.json',
               roots.home / '.coordinator/auth.json', roots.config, roots.target,
               Path.home() / '.ssh', Path.home() / '.gnupg']
    rc, out, err = sandbox.run_probe(spec, secrets)
    assert rc == 0, (out, err)
    assert 'WORK-RW' in out.split() and {'NET-UP', 'NET-DOWN'} & set(out.split())
    # The accepted in-use harness credential (R1) and the native alias are visible; nothing else is.
    seen = run(with_command(spec, '/usr/bin/sh', '-c', f'cat {roots.home}/.codex/auth.json; echo; '
                                                       'test -x /opt/forge-native/bin/codex && echo EXE; '
                                                       f'ls -A {roots.home}'))
    assert seen.stdout.split() == ['codex-secret', 'EXE', '.codex']


def test_cannot_write_controller_state_real(layout):
    roots, bins = layout
    spec = agent(roots, bins)
    proc = run(with_command(spec, '/usr/bin/sh', '-c', f'echo pwn > {roots.state}/forge.db; '
                                                       f'mkdir -p {roots.state}/x && touch {roots.state}/x/y; '
                                                       'touch /in/pwn'))
    assert (roots.state / 'forge.db').read_text() == 'controller-state'
    assert not (roots.state / 'x').exists() and not list((roots.state / 'calls/c1/in').iterdir())
    assert proc.returncode != 0


def test_review_write_erofs_real(layout):
    roots, bins = layout
    spec = agent(roots, bins, writable=False, work=leaf(roots, name='review-1'))
    proc = run(with_command(spec, '/usr/bin/touch', '/work/x'))
    assert proc.returncode != 0 and 'Read-only file system' in proc.stderr
    assert not (leaf(roots, name='review-1') / 'x').exists()


def test_gate_no_network_by_default_real(layout):
    roots, _ = layout
    spec = sandbox.gate_spec(roots, command=('/usr/bin/python3', '-c', 'import socket; '
                                             'socket.create_connection(("1.1.1.1", 53), 2)'),
                             work=leaf(roots, name='gate-1'))
    proc = run(spec)
    assert proc.returncode != 0 and 'unreachable' in proc.stderr.lower()
    home = run(sandbox.gate_spec(roots, command=('/usr/bin/sh', '-c', 'echo $HOME; ls -A /home'),
                                 work=leaf(roots, name='gate-1')))
    assert home.stdout.split() == ['/tmp/home']


def test_env_has_no_api_keys_real(layout, monkeypatch):
    roots, bins = layout
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-leak')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-leak')
    proc = run(with_command(agent(roots, bins), '/usr/bin/env'))
    names = {line.split('=', 1)[0] for line in proc.stdout.splitlines()}
    assert proc.returncode == 0 and names <= {'HOME', 'PATH', 'LANG', 'TERM', 'PWD'}
    assert 'sk-leak' not in proc.stdout


def test_bwrap_setup_failure_is_reported_not_bypassed(layout):
    roots, bins = layout
    spec = agent(roots, bins)
    work = spec.mounts[0].source
    shutil.rmtree(work)
    proc = subprocess.run(sandbox.argv(spec), capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0 and proc.stderr.startswith('bwrap:') and proc.stdout == ''


def test_sigkill_controller_tears_down_pid_namespace_real(layout, tmp_path):
    roots, bins = layout
    marker = f'forge-pidns-{os.getpid()}-{time.time_ns()}'
    spec = with_command(agent(roots, bins), '/usr/bin/sh', '-c',
                        f'(sleep 300; : {marker}) & (sleep 300; : {marker}) & echo started; wait')
    controller = subprocess.Popen([sys.executable, '-c',
                                   'import subprocess, sys, json; '
                                   'p = subprocess.Popen(json.loads(sys.argv[1]), start_new_session=True); '
                                   'p.wait()', __import__('json').dumps(sandbox.argv(spec))])

    def alive():
        found = []
        for proc in Path('/proc').iterdir():
            if proc.name.isdigit():
                try:
                    if marker.encode() in (proc / 'cmdline').read_bytes():
                        found.append(proc.name)
                except OSError:
                    pass
        return found
    deadline = time.time() + 10
    while len(alive()) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(alive()) >= 2
    controller.send_signal(signal.SIGKILL)
    controller.wait()
    deadline = time.time() + 10
    while alive() and time.time() < deadline:
        time.sleep(0.05)
    assert alive() == []


# ---- KISS loop: agent toolchain binds and gate environment ------------------------

def test_agent_receives_same_validated_toolchain_binds_as_gate(layout):
    roots, bins = layout
    home = str(roots.home)
    spec = sandbox.agent_spec(roots, harness='codex', binary=bins['codex'], native_args=(), work=leaf(roots),
                              writable=True, input_dir=call_in(roots), binds_rw=[home + '/.codex'], binds_ro=[],
                              ro_binds=[home + '/venv'], extra_env=(('CARGO_TARGET_DIR', '/work/target'),))
    toolchain = [m for m in spec.mounts if m.cls == 'toolchain']
    assert len(toolchain) == 1 and not toolchain[0].writable
    assert ('CARGO_TARGET_DIR', '/work/target') in spec.extra_env
    with pytest.raises(SandboxError):
        sandbox.agent_spec(roots, harness='codex', binary=bins['codex'], native_args=(), work=leaf(roots),
                           writable=True, input_dir=call_in(roots), binds_rw=[], binds_ro=[],
                           ro_binds=[home + '/.ssh'])


def test_gate_extra_env_is_set_and_filtered_real(layout):
    roots, _ = layout
    spec = sandbox.gate_spec(roots, command=('/usr/bin/sh', '-c', 'echo $CARGO_TARGET_DIR'),
                             work=leaf(roots, name='gate-1'), extra_env=(('CARGO_TARGET_DIR', '/work/target'),))
    assert run(spec).stdout.strip() == '/work/target'
    bad = sandbox.gate_spec(roots, command=('true',), work=leaf(roots, name='gate-1'), extra_env=(('API_TOKEN', 'x'),))
    with pytest.raises(SandboxError):
        sandbox.argv(bad)
