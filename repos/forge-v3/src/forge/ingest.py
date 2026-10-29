"""Pinned rich-source manifests and bounded, provenance-bearing PO evidence."""
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
from posixpath import normpath
import re
from urllib.parse import unquote, urlsplit

from forge.adapters.base import image_media_type
from forge.gate import matches

SCHEMA = 1
CONTENT_LIMIT = 200 * 1024
FILE_LIMIT = 32
IMAGE_LIMIT = 20 * 1024 * 1024
LINK = re.compile(r'(!?)\[[^\]]*\]\(<?([^\s)>]+)>?(?:\s+"[^"]*")?\)')


class IngestError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative(path):
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        raise IngestError('invalid relative path')
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or '.git' in p.parts or p.as_posix() != path:
        raise IngestError('path containment: ' + path)
    return path


def contained(root, path):
    relative(path)
    root = Path(root).resolve()
    p = root / path
    if not p.resolve().is_relative_to(root) or any(x.is_symlink() for x in (p, *p.parents) if x != root and x.is_relative_to(root)):
        raise IngestError('symlink/path containment: ' + path)
    return p


def links(source, text, files):
    """Resolve local Markdown links; external URLs are never controller reads."""
    result = []
    references = {label.casefold().strip(): target for label, target in re.findall(r'^ {0,3}\[([^]\n]+)\]:\s*<?([^\s>]+)>?', text, re.M)}
    found = list(LINK.findall(text))
    for match in re.finditer(r'(!?)\[([^]\n]+)\](?:\[([^]\n]*)\])?(?!\()', text):
        if text[match.end():].startswith(':'):
            continue
        image, label, reference = match.groups()
        target = references.get((reference or label).casefold().strip())
        if target is not None:
            found.append((image, target))
    for image, target in found:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            if image:
                raise IngestError('image must be a committed local payload')
            continue
        target = unquote(parsed.path)
        if not target:
            continue
        # Repo-root paths and ordinary document-relative links are supported.
        path = target if target in files else normpath(str(PurePosixPath(source).parent / target))
        relative(path)
        result.append((bool(image), path))
    return result


def anchors(path, text):
    """Stable table-row and scenario anchors, with source line positions."""
    out, table, row, in_table, scenario, gwt = [], 0, 0, False, 0, False
    fence = None
    lines = text.splitlines()
    for n, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence:
            continue
        is_row = '|' in stripped and bool(stripped)
        separator = bool(re.fullmatch(r'\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?', stripped))
        if separator and n and '|' in lines[n - 1]:
            table += 1
            row, in_table = 0, True
            continue
        if in_table and is_row:
            row += 1
            out.append({'id': f'{path}#table{table}.row{row}', 'line': n})
        elif not is_row:
            in_table = False
        heading = bool(re.match(r'^#{1,6}\s+(?:Scenario|Scenariusz)\b', stripped, re.I))
        given = bool(re.match(r'^(?:[-*]\s*)?(?:\*\*)?(Given|When|Then)\b', stripped, re.I))
        if heading or given and not gwt:
            scenario += 1
            out.append({'id': f'{path}#scenario{scenario}', 'line': n})
        # A heading and its following GWT block share one scenario anchor.
        gwt = heading or given or (gwt and (not stripped or stripped.startswith(('And ', 'But '))))
    return out


def sections(text, limit=CONTENT_LIMIT):
    """Prefer top-level headings; split oversized sections on line boundaries."""
    lines = text.splitlines(keepends=True)
    large = len(text.encode()) > limit
    chunks, current, size, start = [], [], 0, 0
    for n, line in enumerate(lines):
        length = len(line.encode())
        if length > limit:
            raise IngestError('source line exceeds section content bound')
        if current and (size + length > limit or (large and line.startswith('# '))):
            chunks.append((start, n, ''.join(current)))
            current, size, start = [], 0, n
        current.append(line)
        size += length
    if current or not chunks:
        chunks.append((start, len(lines), ''.join(current)))
    return chunks


def manifest(git, commit, patterns, export):
    files, documents = git.files(commit), []
    for path, (mode, blob) in sorted(files.items()):
        if not path.lower().endswith('.md') or not matches(path, patterns):
            continue
        if mode not in ('100644', '100755'):
            raise IngestError('source must be a regular committed file')
        text = contained(export, path).read_text(encoding='utf-8')
        raw = text.encode()
        if raw != git.blob(blob):
            raise IngestError('source export differs from pinned blob')
        images = []
        for is_image, linked in links(path, text, files):
            if not is_image:
                continue
            if linked not in files or files[linked][0] not in ('100644', '100755'):
                raise IngestError('missing/nonregular image: ' + linked)
            payload = contained(export, linked)
            if payload.stat().st_size > IMAGE_LIMIT:
                raise IngestError('image exceeds 20 MiB')
            try:
                media = image_media_type(payload)
            except ValueError as exc:
                raise IngestError(str(exc)) from exc
            images.append({'path': linked, 'sha': sha(payload.read_bytes()), 'blob_sha': files[linked][1], 'media_type': media})
        all_anchors = anchors(path, text)
        headings = re.findall(r'^#{1,6}\s+(.+)$', text, re.M)
        key = sha(json.dumps([SCHEMA, path, sha(raw), images], sort_keys=True).encode())
        documents.append({'path': path, 'sha': sha(raw), 'key': key, 'blob_sha': blob, 'commit': commit,
                          'title': headings[0] if headings else path, 'headings': headings, 'images': images,
                          'sections': [{'body': body, 'anchors': [a['id'] for a in all_anchors if lo <= a['line'] < hi]}
                                       for lo, hi, body in sections(text)]})
    return documents


def valid_read(value, section, images):
    if not isinstance(value, dict) or set(value) != {'reqs', 'images'} or not isinstance(value['reqs'], list):
        return False
    covered, ids = set(), set()
    for req in value['reqs']:
        if not isinstance(req, dict) or set(req) != {'id', 'text', 'anchors', 'kind'}:
            return False
        if not all(isinstance(req[k], str) and req[k].strip() for k in ('id', 'text', 'kind')) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', req['id']) or req['id'] in ids:
            return False
        if not isinstance(req['anchors'], list) or any(a not in section['anchors'] for a in req['anchors']):
            return False
        ids.add(req['id'])
        covered.update(req['anchors'])
    observations = value['images']
    return (covered == set(section['anchors']) and isinstance(observations, list) and
            all(isinstance(i, dict) and set(i) == {'sha', 'observation'} and isinstance(i['observation'], str) and i['observation'].strip() for i in observations) and
            {i['sha'] for i in observations} == {i['sha'] for i in images} and len(observations) == len({i['sha'] for i in images}))


def inventory(git, commit, patterns):
    result = []
    for path, (mode, blob) in sorted(git.files(commit).items()):
        if not path.endswith('.py') or not matches(path, patterns) or mode not in ('100644', '100755'):
            continue
        try:
            tree = ast.parse(git.blob(blob), filename=path)
            names = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')]
            result.append({'path': path, 'names': names})
        except SyntaxError:
            result.append({'path': path, 'names': [], 'parse_error': True})
    return result


def load_evidence(git, commit, export, req_ids, selection, mandatory, test_paths, artifacts=()):
    """Consume actual full files; discovery names never enter evidence records."""
    files = git.files(commit)
    known = {a['path']: a for a in artifacts}
    selected = {r['req_id']: r for r in selection}
    result = {}
    for req in req_ids:
        chosen = selected[req]
        tests = set(chosen['test_paths'])
        evidence = set(chosen['evidence_paths'])
        required = mandatory.get(req, ()) if isinstance(mandatory, dict) else mandatory
        for path in required:
            (tests if matches(path, test_paths) else evidence).add(path)
        supplied, gaps, total = [], [], 0
        if len(tests | evidence) > FILE_LIMIT:
            result[req] = {'supplied': [], 'evidence_gap': ['more than 32 files per requirement']}
            continue
        for path in sorted(tests | evidence):
            is_test = path in tests
            if path in known and not is_test:
                record = known[path]
                payload = Path(path).read_bytes()
                if sha(payload) != record['sha256']:
                    raise IngestError('registered immutable artifact changed: ' + path)
                item = {'artifact_id': path, 'content_sha256': sha(payload), 'producing_call_or_gate': record.get('call') or record['path'], 'type': 'result'}
            else:
                relative(path)
                if path not in files:
                    gaps.append('missing linked content: ' + path)
                    continue
                if files[path][0] not in ('100644', '100755'):
                    raise IngestError('evidence must be a regular file')
                payload = contained(export, path).read_bytes()
                if payload != git.blob(files[path][1]):
                    raise IngestError('evidence export differs from pinned blob')
                item = {'path': path, 'commit': commit, 'blob_sha': files[path][1], 'content_sha256': sha(payload), 'type': 'test' if is_test else 'result'}
                if is_test and (not matches(path, test_paths) or not path.endswith('.py')):
                    raise IngestError('selected test outside configured test paths')
            total += len(payload)
            if total > CONTENT_LIMIT:
                gaps.append('more than 200 KiB per requirement')
                supplied = []
                break
            item['content'] = payload.decode('utf-8', errors='replace')
            supplied.append(item)
        if not any(i['type'] == 'test' for i in supplied):
            gaps.append('no supplied relevant test body')
        if not any(i['type'] == 'result' for i in supplied):
            gaps.append('no supplied linked result evidence')
        result[req] = {'supplied': supplied, 'evidence_gap': gaps}
    return result


def comparison_chunks(evidence):
    chunks, reqs, blobs, size = [], [], {}, 0
    for req, data in evidence.items():
        incoming = {i['content_sha256']: i for i in data['supplied']}
        extra = sum(len(i['content'].encode()) for h, i in incoming.items() if h not in blobs)
        if reqs and size + extra > CONTENT_LIMIT:
            chunks.append({'req_ids': reqs, 'contents': list(blobs.values())})
            reqs, blobs, size = [], {}, 0
            extra = sum(len(i['content'].encode()) for i in incoming.values())
        reqs.append(req)
        blobs.update(incoming)
        size += extra
    if reqs:
        chunks.append({'req_ids': reqs, 'contents': list(blobs.values())})
    return chunks
