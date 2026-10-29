"""Compact documentation navigation and controller-owned initial skeleton."""
import hashlib
from pathlib import Path
import time
from urllib.parse import quote

from forge.ingest import contained, relative
from forge.state import atomic_write

SKELETON = {
    'intent/README.md': '# Intent\n\nSource references define desired behavior. Changes require owner authority.\n',
    'implemented/README.md': '# Implemented state\n\nRecord behavior with links to full tests and immutable result evidence.\n',
    'adr/README.md': '# Architecture decisions\n\nAppend reviewed decisions; preserve existing intent and decisions.\n',
    'tasks/README.md': '# Task evidence\n\nEach task records Plan, Changes, Tests, Docs and Evidence.\n',
    'digests/README.md': '# Source digests\n\nController-recorded source/blob/image hashes and native read provenance.\n',
    'sources.md': '# Source references\n\nThe controller manifests every configured Markdown source, independently of this index.\n',
}


def generate(root, sources=(), object_format='sha1'):
    root = Path(root)
    rows = ['# Forge index', '', 'Source → intent → implemented state → ADR → task evidence.', '',
            'The index is navigation; source discovery uses the trusted configured patterns.', '']
    paths = set(sources)
    folder = contained(root, 'docs/forge')
    if folder.exists():
        paths.update(p.relative_to(root).as_posix() for p in folder.rglob('*') if p.is_file() and p.name != 'INDEX.md')
    for path in sorted(paths):
        relative(path)
        data = contained(root, path).read_bytes()
        # Git blob IDs are independently computable, including not-yet-committed docs.
        blob = hashlib.new(object_format, b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        rows.append(f'- [{path}](../../{quote(path, safe="/")}) · blob `{blob}`')
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write(contained(root, 'docs/forge/INDEX.md'), ('\n'.join(rows) + '\n').encode())


def skeleton(root, sources=(), object_format='sha1'):
    for path, text in SKELETON.items():
        destination = contained(root, 'docs/forge/' + path)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(destination, text.encode())
    generate(root, sources, object_format)


def initialize(config, state):
    from forge.gitops import Git, GitError
    git = Git(config.target_repo, state.root, config.branch)
    base = git.head()
    files = git.files(base)
    if 'docs/forge/INDEX.md' in files and all('docs/forge/' + p in files for p in SKELETON):
        return base
    work = state.root / 'ws' / 'init' / ('review-' + str(time.time_ns()))
    git.export(base, work)
    from forge.gate import matches
    sources = [p for p in files if p.endswith('.md') and matches(p, config.sources)]
    skeleton(work, sources, 'sha256' if len(base) == 64 else 'sha1')
    candidate = git.candidate('init', work, base, 'Initialize Forge documentation')
    if not git.cas(candidate):
        raise GitError('init main moved; preserved candidate ' + candidate.ref + '; rerun init')
    return candidate.commit
