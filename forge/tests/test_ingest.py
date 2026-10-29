"""P4 content tests: real committed blobs; provider content is a labelled fixture."""
import json
from pathlib import Path
import subprocess

import pytest

from forge import ingest
from forge.gitops import Git
from forge.roles import comparison


@pytest.fixture
def pinned(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-qb', 'main', repo], check=True)
    def commit(files):
        for path, body in files.items():
            p = repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body if isinstance(body, bytes) else body.encode())
        subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
        subprocess.run(['git', '-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@local', 'commit', '-qm', 'fixture'], check=True)
        git = Git(repo, tmp_path / 'state')
        head = git.head()
        export = git.export(head, tmp_path / ('export-' + head))
        return git, head, export
    return commit


def test_md_anchor_extraction():
    text = '# Spec\n| Name | Rule |\n| --- | --- |\n| export | reject negative |\n| stock | no underflow |\n\n## Scenario export\nGiven x\nWhen y\nThen z\n\n# Other\nGiven a\nWhen b\nThen c\n'
    assert [a['id'] for a in ingest.anchors('spec.md', text)] == ['spec.md#table1.row1', 'spec.md#table1.row2', 'spec.md#scenario1', 'spec.md#scenario2']


def test_manifest_ignores_index_and_contains_full_table(pinned):
    git, head, root = pinned({'docs/source/hidden.md': '| A | B |\n|---|---|\n| export | negatives rejected |\n', 'docs/forge/INDEX.md': '# empty'})
    docs = ingest.manifest(git, head, ('docs/source/**/*.md',), root)
    assert len(docs) == 1
    assert 'negatives rejected' in docs[0]['sections'][0]['body']
    assert docs[0]['sections'][0]['anchors'] == ['docs/source/hidden.md#table1.row1']


def read_fixture(anchors=(), images=()):
    return {'reqs': [{'id': 'r', 'text': 'reject negative', 'anchors': list(anchors), 'kind': 'constraint'}], 'images': [{'sha': s, 'observation': 'blue left of red'} for s in images]}


def test_po_read_rejects_missing_anchor():
    assert not ingest.valid_read(read_fixture(), {'anchors': ['s#table1.row1']}, [])
    assert ingest.valid_read(read_fixture(['s#table1.row1']), {'anchors': ['s#table1.row1']}, [])


def test_po_read_requires_observation_per_attached_image():
    assert not ingest.valid_read(read_fixture(), {'anchors': []}, [{'sha': 'abc'}])
    assert ingest.valid_read(read_fixture(images=['abc']), {'anchors': []}, [{'sha': 'abc'}])
    bad = read_fixture(images=['abc']); bad['images'][0]['observation'] = ''
    assert not ingest.valid_read(bad, {'anchors': []}, [{'sha': 'abc'}])


@pytest.mark.parametrize('payload', [b'not-image', b'\x89PNG\r\n\x1a\n' + b'x' * ingest.IMAGE_LIMIT], ids=['bad-magic', 'oversize'])
def test_image_magic_size(pinned, payload):
    git, head, root = pinned({'s.md': '![image](a.png)', 'a.png': payload})
    with pytest.raises(ingest.IngestError):
        ingest.manifest(git, head, ('*.md',), root)


def test_image_attached_as_payload(pinned, tmp_path):
    from forge.adapters.claude import ClaudeAdapter
    from forge.adapters.base import Call
    import base64
    payload = b'\x89PNG\r\n\x1a\nfixture-pixel-bytes'
    git, head, root = pinned({'s.md': '![image](a.png)', 'a.png': payload})
    doc = ingest.manifest(git, head, ('*.md',), root)[0]
    call = Call('c', 'po_read', 'p', 'claude', 'claude-opus-5-5', 'high', root, False, 'read', (root / doc['images'][0]['path'],), preset_sid='sid')
    args, stdin = ClaudeAdapter().build_argv(call, '/native', tmp_path / 'in')
    assert base64.b64encode(payload) in stdin


def test_inventory_ast_only_never_executes(pinned):
    git, head, root = pinned({'tests/test_a.py': 'raise RuntimeError("must never run")\ndef test_body():\n assert False\n'})
    assert ingest.inventory(git, head, ('tests/**',)) == [{'path': 'tests/test_a.py', 'names': ['test_body']}]


def supplied(pinned, body='assert 1 == 1'):
    git, head, root = pinned({'tests/test_a.py': 'def helper():\n return 1\ndef test_x():\n ' + body + '\n', 'results.json': '{"rc":0}'})
    data = ingest.load_evidence(git, head, root, ['r'], [{'req_id': 'r', 'test_paths': ['tests/test_a.py'], 'evidence_paths': []}], {'results.json'}, ('tests/**',))
    return data, git, head, root


def test_actual_assertions_helpers_and_provenance(pinned):
    evidence, git, head, root = supplied(pinned)
    item = evidence['r']['supplied'][1]
    assert item['commit'] == head and item['blob_sha'] == git.files(head)[item['path']][1]
    assert 'def helper' in item['content'] and 'assert 1 == 1' in item['content']
    assert item['content_sha256'] == ingest.sha(item['content'].encode())


def test_req_implemented_only_in_tests_changes_status(pinned):
    before, git, head, root = supplied(pinned)
    after, _, _, _ = supplied(pinned, 'assert 1 == 2')
    first = next(i for i in before['r']['supplied'] if i['type'] == 'test')
    second = next(i for i in after['r']['supplied'] if i['type'] == 'test')
    assert first['path'] == second['path'] and first['blob_sha'] != second['blob_sha']
    row = {'req_id': 'r', 'status': 'implemented', 'evidence_refs': [{'content_sha256': i['content_sha256'], 'assertion': 'test_x asserts expected behavior; result rc0'} for i in before['r']['supplied']]}
    assert comparison([row], ['r'], before)
    assert not comparison([row], ['r'], after)


def test_po_missing_or_unread_test_evidence_cannot_claim_implemented(pinned):
    git, head, root = pinned({'seed': 'seed'})
    data = ingest.load_evidence(git, head, root, ['r'], [{'req_id': 'r', 'test_paths': ['tests/missing.py'], 'evidence_paths': []}], (), ('tests/**',))
    assert data['r']['evidence_gap']
    assert not comparison([{'req_id': 'r', 'status': 'implemented', 'evidence_refs': []}], ['r'], data)
    assert comparison([{'req_id': 'r', 'status': 'missing', 'evidence_refs': [], 'evidence_gap': 'test absent'}], ['r'], data)


@pytest.mark.parametrize('path', ['../outside.py', '/etc/passwd', '.git/config', 'a/../../b'])
def test_selection_path_containment(pinned, path):
    git, head, root = pinned({'seed': 'seed'})
    with pytest.raises(ingest.IngestError):
        ingest.load_evidence(git, head, root, ['r'], [{'req_id': 'r', 'test_paths': [path], 'evidence_paths': []}], (), ('tests/**',))


def test_po_content_bound_and_chunk_merge(pinned):
    files = {f'tests/test_{n}.py': 'x="' + str(n) * (110 * 1024) + '"' for n in range(2)}
    git, head, root = pinned(files)
    selected = [{'req_id': str(n), 'test_paths': [f'tests/test_{n}.py'], 'evidence_paths': []} for n in range(2)]
    data = ingest.load_evidence(git, head, root, ['0', '1'], selected, (), ('tests/**',))
    chunks = ingest.comparison_chunks(data)
    assert len(chunks) == 2 and [r for c in chunks for r in c['req_ids']] == ['0', '1']
    selected[0]['test_paths'].append('tests/test_1.py')
    data = ingest.load_evidence(git, head, root, ['0', '1'], selected, (), ('tests/**',))
    assert not data['0']['supplied'] and any('200 KiB' in g for g in data['0']['evidence_gap'])


def test_source_sections_are_bounded():
    body = '# One\n' + 'some words\n' * 24000 + '# Two\nrest\n'
    chunks = ingest.sections(body)
    assert ''.join(s[2] for s in chunks) == body
    assert all(len(s[2].encode()) <= ingest.CONTENT_LIMIT for s in chunks)


def test_images_and_test_bodies_affect_manifest_provenance(pinned):
    files = {'s.md': '![image](pixel.png)', 'pixel.png': b'\x89PNG\r\n\x1a\nblue'}
    git, head, root = pinned(files)
    first = ingest.manifest(git, head, ('*.md',), root)[0]
    files['pixel.png'] = b'\x89PNG\r\n\x1a\nred'
    git, head, root = pinned(files)
    second = ingest.manifest(git, head, ('*.md',), root)[0]
    assert first['sha'] == second['sha'] and first['key'] != second['key']
    assert first['images'][0]['sha'] != second['images'][0]['sha']


def test_empty_source_may_have_no_requirements():
    assert ingest.valid_read({'reqs': [], 'images': []}, {'anchors': []}, [])
    assert not ingest.valid_read({'reqs': [], 'images': []}, {'anchors': ['s#scenario1']}, [])


def test_missing_registered_artifact_blocks_instead_of_fabricating(pinned, tmp_path):
    git, head, root = pinned({'seed': 's'})
    artifact = str(tmp_path / 'missing-result.json')
    with pytest.raises(OSError):
        ingest.load_evidence(git, head, root, ['r'], [{'req_id': 'r', 'test_paths': [], 'evidence_paths': [artifact]}], (), ('tests/**',), [{'path': artifact, 'sha256': 'x', 'kind': 'gate'}])


def test_32_file_bound(pinned):
    files = {f'tests/test_{n}.py': f'def test_{n}():\n assert True\n' for n in range(33)}
    git, head, root = pinned(files)
    data = ingest.load_evidence(git, head, root, ['r'], [{'req_id': 'r', 'test_paths': list(files), 'evidence_paths': []}], (), ('tests/**',))
    assert not data['r']['supplied'] and data['r']['evidence_gap'] == ['more than 32 files per requirement']


def test_symlink_source_is_rejected(pinned):
    git, head, root = pinned({'s.md': '# source'})
    (root / 's.md').unlink()
    (root / 's.md').symlink_to('/etc/passwd')
    with pytest.raises(ingest.IngestError):
        ingest.manifest(git, head, ('*.md',), root)


def test_mandatory_links_are_per_requirement(pinned):
    git, head, root = pinned({'tests/test_a.py': 'def test_a():\n assert True\n', 'tests/test_b.py': 'def test_b():\n assert False\n', 'a.json': '{"rc":0}', 'b.json': '{"rc":1}'})
    selected = [{'req_id': r, 'test_paths': [], 'evidence_paths': []} for r in ('a', 'b')]
    evidence = ingest.load_evidence(git, head, root, ['a', 'b'], selected, {'a': {'tests/test_a.py', 'a.json'}, 'b': {'tests/test_b.py', 'b.json'}}, ('tests/**',))
    assert {i['path'] for i in evidence['a']['supplied']} == {'tests/test_a.py', 'a.json'}
    assert {i['path'] for i in evidence['b']['supplied']} == {'tests/test_b.py', 'b.json'}


def test_reference_style_images_are_manifested_and_validated(pinned):
    git, head, root = pinned({'s.md': '# Spec\n![layout][pixel]\n\n[pixel]: image.png\n', 'image.png': b'\x89PNG\r\n\x1a\nreference-payload'})
    doc = ingest.manifest(git, head, ('*.md',), root)[0]
    assert [i['path'] for i in doc['images']] == ['image.png']
    assert not ingest.valid_read(read_fixture(), {'anchors': []}, doc['images'])


def test_reference_style_test_links_are_mandatory_candidates():
    text = '[full assertions][tests]\n\n[tests]: ../../tests/test_export.py\n'
    assert ingest.links('docs/forge/implemented.md', text, {'tests/test_export.py': ('100644', 'oid')}) == [(False, 'tests/test_export.py')]
