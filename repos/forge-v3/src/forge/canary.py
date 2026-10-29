"""Disposable real-native acceptance run. No provider fixture is a live receipt."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import sqlite3
import subprocess
import sys
import time

from forge.doctor import png
from forge.state import atomic_json

# Five columns, seven rows; no font/image dependency and no text PNG metadata.
FONT = {
 'S':['01111','10000','10000','01110','00001','00001','11110'],
 'K':['10001','10010','10100','11000','10100','10010','10001'],
 'U':['10001']*6+['01110'],
 'N':['10001','11001','11001','10101','10011','10011','10001'],
 'A':['01110','10001','10001','11111','10001','10001','10001'],
 'M':['10001','11011','10101','10101','10001','10001','10001'],
 'E':['11111','10000','10000','11110','10000','10000','11111'],
 'Q':['01110','10001','10001','10001','10101','10010','01101'],
 'T':['11111']+['00100']*6,
 'Y':['10001','10001','01010']+['00100']*4,
 'I':['11111']+['00100']*5+['11111'],
}


def layout_png(order):
    scale, cell = 4, 200
    def pixel(x, y):
        if y in (0, 55, 109) or x % cell == 0:
            return (60, 60, 60)
        col, local = divmod(x, cell)
        xx, yy = (local - 12) // scale, (y - 12) // scale
        if 0 <= yy < 7 and 0 <= xx < len(order[col]) * 6:
            char, bit = divmod(xx, 6)
            if bit < 5 and FONT[order[col][char]][yy][bit] == '1':
                return (0, 0, 0)
        return (255, 255, 255)
    return png(cell * len(order) + 1, 110, lambda x, y: pixel(min(x, cell * len(order)-1), y))


@contextmanager
def environment(workdir):
    values = {'XDG_CONFIG_HOME': str(workdir / 'config'), 'XDG_STATE_HOME': str(workdir / 'state')}
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def setup(workdir):
    """Create once; refuse to overwrite any existing project or evidence."""
    root = Path(workdir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = root / 'canary.json'
    if marker.exists():
        return json.loads(marker.read_text())
    if any(root.iterdir()):
        raise ValueError('canary workdir must be empty or an existing canary')
    repo = root / 'proj'
    repo.mkdir()
    order = random.SystemRandom().sample(['SKU', 'NAME', 'QUANTITY'], 3)
    files = {
        '.gitignore': '__pycache__/\n.pytest_cache/\n*.pyc\n.forge-junit-*\n',
        'tally/__init__.py': '''"""Small inventory library."""
def remove(stock, sku, quantity):
    stock[sku] -= quantity
    return stock[sku]
''',
        'tests/test_tally.py': '''from tally import remove

def test_remove_available():
    stock = {"a": 5}
    assert remove(stock, "a", 2) == 3
    assert stock == {"a": 3}
''',
        'docs/forge/implemented/inventory.md': '# Tally\n\nremove(stock, sku, quantity) subtracts stock in place.\nCSV export is not implemented.\n',
        'docs/source/INDEX.md': '# Source index\n\nInventory requirements are discovered from committed Markdown.\n',
        'docs/source/spec.md': '''# Tally inventory

| Requirement | Behavior |
|---|---|
| Removal | remove(stock, sku, quantity) subtracts quantity and returns remaining stock. |
| Safety | Removing more than available raises ValueError and leaves stock unchanged. |
| CSV | Add export_csv(items) returning CSV text for mappings with lowercase keys sku, name, quantity. Headers are lowercase; their order is shown ONLY in the attached layout. Handle quoting, stable input row order, empty input header and schema validation. Also expose export_csv_file(items, path): publish identical CSV through a same-directory temporary file, flush/fsync and atomic replacement. Serialization/validation and filesystem publication belong in separate modules. Consume one-shot iterators once. On iterator, write, fsync or replacement failure, preserve any existing destination bytes, remove temporary files and propagate the error. Test failure injection at those boundaries, successful retry and concurrent publications (each visible result must be one complete export). Keep the original string-returning API working. |
| Validation | Export rejects negative quantities with ValueError. |

![Export table layout](export-layout.png)

## Scenario: insufficient inventory
Given stock a is 2
When removing 3 of a
Then ValueError is raised and a remains 2.

## Scenario: CSV text
Given items with commas and quotes in their names
When exporting inventory
Then valid CSV preserves names exactly and follows the pictured column order.

Implement code, regression tests, and affected docs/forge/implemented/inventory.md together.
''',
    }
    for path, text in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    image = layout_png(order)
    (repo / 'docs/source/export-layout.png').write_bytes(image)
    for args in (['init','-b','main'], ['-c','user.name=Forge Canary','-c','user.email=canary@localhost','add','.'],
                 ['-c','user.name=Forge Canary','-c','user.email=canary@localhost','commit','-m','Initial Tally canary']):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    config = root / 'config/forge/projects/canary.toml'
    config.parent.mkdir(parents=True)
    # Gate PATH uses /usr/bin, independently of the BUILD interpreter. A BUILD
    # venv may resolve into protected coordinator tools and must never be bound.
    config.write_text(f'target_repo = {json.dumps(str(repo))}\n[commands]\ntest = ["/usr/bin/python3", "-m", "pytest", "-q"]\ntest_one = ["/usr/bin/python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml={{junit}}", "{{node}}"]\n')
    import shutil
    binary = shutil.which('codex')
    if binary:
        release = Path(binary).resolve().parent
        if (release / 'codex-code-mode-host').is_file():
            with config.open('a') as stream:
                stream.write('[harness.codex]\nruntime_dir = ' + json.dumps(str(release)) + '\n')
    config.chmod(0o600)
    data = {'root': str(root), 'repo': str(repo), 'config': str(config), 'state': str(root / 'state/forge/canary'),
            'secret_order': [c.lower() for c in order], 'image_sha': hashlib.sha256(image).hexdigest(), 'created': time.time()}
    atomic_json(marker, data)
    return data


def golden_code(order):
    return ("import csv,io; from tally import remove,export_csv; "
            "s={'a':2};\ntry: remove(s,'a',3)\nexcept ValueError: pass\nelse: raise AssertionError('negative stock accepted')\n"
            "assert s=={'a':2}; rows=[{'sku':'a','name':'comma, and \"quote\"','quantity':2}]; "
            "out=list(csv.reader(io.StringIO(export_csv(rows)))); "
            "assert out[0]==" + repr(order) + "; "
            "assert out[1]==[str(rows[0][k]) for k in out[0]]; "
            "assert next(csv.reader(io.StringIO(export_csv([]))))==out[0];\n"
            "try: export_csv([{'sku':'x','name':'x','quantity':-1}])\n"
            "except ValueError: pass\nelse: raise AssertionError('negative export accepted')\n")


def collect_golden(data):
    """Independent deterministic acceptance command, inside the project gate boundary."""
    from forge import sandbox
    from forge.config import load_config
    from forge.engine import Engine
    from forge.state import State, atomic_write
    import shutil
    root = Path(data['root'])
    with environment(root):
        config = load_config(data['config'])
        with State(config.state_dir) as state:
            engine = Engine(config, state)
            commit = engine.git.head()
            work = config.state_dir / 'ws/verify/gate-1'
            if work.exists():
                shutil.rmtree(work)
            engine.git.export(commit, work)
            command = (config.commands['test'][0], '-c', golden_code(data['secret_order']))
            spec = sandbox.gate_spec(sandbox.project_roots(config), command=command, work=work,
                                     ro_binds=config.execution['ro_binds'])
            sandbox.revalidate(spec)
            started = time.monotonic()
            proc = subprocess.run(sandbox.argv(spec), capture_output=True, timeout=60)
            atomic_write(root/'golden.stdout', proc.stdout)
            atomic_write(root/'golden.stderr', proc.stderr)
            tree = engine.git.tree(commit)
            record = {'commit':commit,'tree':tree,'rc':proc.returncode,'sandbox':spec.manifest(),
                      'command':command,'elapsed':time.monotonic()-started,
                      'content_unchanged':engine.git.snapshot(work,commit)==tree}
            path = root/'golden.json'
            atomic_json(path,record)
            with state.transaction() as db:
                db.execute('INSERT OR REPLACE INTO artifacts(path,sha256,kind,created) VALUES(?,?,?,?)',
                           (str(path),hashlib.sha256(path.read_bytes()).hexdigest(),'canary-golden',time.time()))
    return record


def verify(data):
    """Read-only SQLite/Git/evidence verification. Never launch a model or target code."""
    from forge.config import load_config
    from forge.gate import CHECKLIST, junit
    from forge import sandbox
    root = Path(data['root'])
    checks = {}
    calls = []
    used = set()
    profiles = {}
    def check(name, condition):
        checks[name] = bool(condition)
    def git(*args):
        env = {'PATH':'/usr/bin:/bin', 'HOME':str(root), 'GIT_CONFIG_NOSYSTEM':'1',
               'GIT_CONFIG_GLOBAL':'/dev/null', 'LANG':'C.UTF-8'}
        return subprocess.check_output(['git','--git-dir='+str(Path(data['repo'])/'.git'),
                    '-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',*args],env=env,cwd=root)
    def blob(commit, path):
        return git('show',commit+':'+path)
    def receipt(db, path, sha, kind):
        row = db.execute('SELECT * FROM artifacts WHERE path=? AND sha256=? AND kind=?',(path,sha,kind)).fetchone()
        payload = Path(path).read_bytes()
        if not row or hashlib.sha256(payload).hexdigest()!=sha:
            raise ValueError('missing or changed '+kind+' receipt')
        return json.loads(payload)
    required = ('batch1_easy_hard','native_calls','bug_red','row4_digest','row4_comparison',
                'image_observation','image_task_citation','fresh_batch2','batch2_implemented_input','pixel_csv_order')
    for name in required:
        check(name,False)
    try:
        with environment(root):
            config = load_config(data['config'])
        profiles = {p.id:p for p in config.profiles}
        db = sqlite3.connect('file:'+str(config.state_dir/'forge.db')+'?mode=ro',uri=True)
        db.row_factory = sqlite3.Row
        try:
            batches = db.execute('SELECT * FROM batches ORDER BY rowid').fetchall()
            tasks = db.execute("SELECT * FROM tasks WHERE state='COMMITTED' ORDER BY rowid").fetchall()
            first = [t for t in tasks if batches and t['batch']==batches[0]['id']]
            check('batch1_easy_hard', {'easy','hard'} <= {t['difficulty'] for t in first})
            check('no_blocked',not db.execute("SELECT 1 FROM tasks WHERE state='BLOCKED' UNION SELECT 1 FROM batches WHERE state='BLOCKED'").fetchone())
            calls = [dict(c) for c in db.execute("SELECT * FROM calls WHERE profile!='deterministic'")]
            runtime = [c for c in calls if c['role']!='probe']
            used = {c['profile'] for c in runtime}
            check('native_calls',bool(runtime) and all(c['sandbox']=='bwrap' for c in runtime))
            for profile in used:
                p = profiles[profile]
                row = db.execute('SELECT * FROM smoke WHERE profile=?',(profile,)).fetchone()
                valid = False
                if row:
                    rec = json.loads(Path(row['manifest_path']).read_text())
                    h = config.harness[p.harness]
                    binary = sandbox.resolve_binary(p.harness,h.get('binary'))
                    expected = {'binary':{'path':str(binary),'version':rec['binary']['version'],
                                           'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()},
                                'model':p.model,'effort':p.effort,'binds_rw':list(h['binds_rw']),
                                'binds_ro':list(h['binds_ro']),'runtime_dir':h.get('runtime_dir')}
                    fp = hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()
                    needed = {'text','readonly'}
                    if any(c['profile']==profile and c['role'] in ('red','implement','fix','escalate') for c in runtime):
                        needed.add('resume')
                    if any(c['profile']==profile and json.loads((Path(c['raw_dir'])/'input.json').read_text())['images'] for c in runtime):
                        needed.add('image')
                    valid = row['status']=='pass' and rec['sandbox_probe']=='pass' and fp==row['fingerprint'] and needed<=set(json.loads(row['capabilities']))
                check('smoke:'+profile,valid)
            for task in tasks:
                row = db.execute('SELECT * FROM commits WHERE task=?',(task['id'],)).fetchone()
                meta = json.loads(task['metadata'])
                r = meta['review_receipt']
                review = receipt(db,r['path'],r['sha256'],'review')
                g = review['gate']
                gate = receipt(db,g['path'],g['sha256'],'gate')
                candidate = review['candidate']
                check('accepted_tree:'+task['id'],row is not None and row['state']=='done' and row['candidate']==task['candidate_commit']==candidate['commit'] and row['tree']==task['candidate_tree']==candidate['tree']==git('rev-parse',candidate['commit']+'^{tree}').decode().strip() and candidate==gate['candidate'])
                call = next(c for c in runtime if c['id']==review['call'])
                check('independent_review:'+task['id'],review['reviewer_family'] not in review['implementer_families'] and bool(review['implementer_families']) and call['outcome']=='ok' and not call['session_in'] and review['result']['verdict']=='PASS' and review['result']['tree']==candidate['tree'] and set(review['result']['checklist'])==CHECKLIST)
                check('gate:'+task['id'],bool(gate['results']) and all(r['rc']==0 and r['sandbox']=='bwrap' for r in gate['results']))
                changed = git('diff','--name-only',candidate['base'],candidate['commit']).decode().splitlines()
                check('code_docs:'+task['id'],any(p.startswith('tally/') for p in changed) and any(p.startswith('docs/') for p in changed))
                check('accepted_ancestry:'+task['id'],subprocess.run(['git','--git-dir='+str(Path(data['repo'])/'.git'),'merge-base','--is-ancestor',candidate['commit'],'main'],capture_output=True).returncode==0)
                if task['kind']=='bug':
                    r = meta['red']
                    red = receipt(db,r['path'],r['sha256'],'red')
                    artifact = db.execute('SELECT created FROM artifacts WHERE path=?',(r['path'],)).fetchone()
                    fixes = [c for c in runtime if c['task']==task['id'] and c['role']=='implement']
                    for node,res in zip(red['nodes'],red['results'],strict=True):
                        junit(Path(res['junit']).read_bytes(),res['rc'],node,True)
                    check('red_before_fix:'+task['id'],bool(fixes) and artifact[0] <= min(c['started'] for c in fixes))
                    check('red_unchanged:'+task['id'],all(git('rev-parse',candidate['commit']+':'+p).decode().strip()==value[1] for p,value in red['blobs'].items()))
            check('bug_red',any(t['kind']=='bug' and t['red_commit'] for t in first))
            if first:
                arc = json.loads((config.state_dir/'evidence'/('batch-'+first[0]['batch'])/'architecture.json').read_text())
                author = next(c for c in runtime if c['id']==arc['architect_call'])
                worker_calls = [c for c in runtime if c['task'] in {t['id'] for t in first}]
                check('architecture_before_workers',bool(worker_calls) and author['ended'] < min(c['started'] for c in worker_calls) and all(subprocess.run(['git','--git-dir='+str(Path(data['repo'])/'.git'),'merge-base','--is-ancestor',arc['candidate']['commit'],t['base_commit']],capture_output=True).returncode==0 for t in first))
                if arc.get('adr_review_call'):
                    reviewer = next(c for c in runtime if c['id']==arc['adr_review_call'])
                    check('architecture_independent_review',profiles[author['profile']].family!=profiles[reviewer['profile']].family and reviewer['outcome']=='ok')
            head = git('rev-parse','main').decode().strip()
            files = git('ls-tree','-r','--name-only',head).decode().splitlines()
            digests = [json.loads(blob(head,p)) for p in files if p.startswith('docs/forge/digests/') and p.endswith('.json')]
            spec = [d for d in digests if d['source']['path']=='docs/source/spec.md']
            reqs = [r for d in spec for read in d['reads'] for r in read['reqs'] if any('table1.row4' in a for a in r['anchors'])]
            check('row4_digest',bool(reqs))
            comparisons = list((config.state_dir/'evidence').glob('batch-*/comparison.json'))
            check('row4_comparison',any(any(r['req_id'] in {q['id'] for q in reqs} for r in json.loads(p.read_text())) for p in comparisons))
            check('image_observation',any(i['sha']==data['image_sha'] and i['observation'] for d in spec for read in d['reads'] for i in read['images']))
            read_ids = {read['call_id'] for d in spec for read in d['reads']}
            csv_ids = {r['id'] for d in spec for read in d['reads'] for r in read['reqs'] if any('table1.row3' in a for a in r['anchors'])}
            check('image_task_citation',any(data['image_sha'] in blob(t['candidate_commit'],'docs/forge/tasks/'+t['id']+'.md').decode() and any(r in blob(t['candidate_commit'],'docs/forge/tasks/'+t['id']+'.md').decode() for r in read_ids) for t in first if csv_ids.intersection(json.loads(t['req_ids']))))
            po2 = [c for c in runtime if len(batches)>1 and c['batch']==batches[1]['id'] and c['role']=='po']
            accepted = [db.execute('SELECT accepted_at FROM commits WHERE task=?',(t['id'],)).fetchone()[0] for t in first]
            check('fresh_batch2',bool(po2) and bool(accepted) and min(c['started'] for c in po2)>max(accepted) and len({c['session_out'] for c in runtime if c['session_out']})>1)
            # Compare actual implemented bodies, rather than the word "implemented" alone.
            latest_first = max(first, key=lambda t: db.execute('SELECT accepted_at FROM commits WHERE task=?',(t['id'],)).fetchone()[0]) if first else None
            implemented_files = git('ls-tree','-r','--name-only',latest_first['candidate_commit']).decode().splitlines() if latest_first else []
            expected = {p:blob(latest_first['candidate_commit'],p).decode() for p in implemented_files if p.startswith('docs/forge/implemented/') and p.endswith('.md')}
            prompts = [json.loads((Path(c['raw_dir'])/'input.json').read_text())['prompt'] for c in po2]
            check('batch2_implemented_input',bool(expected) and any(all(json.dumps(body)[1:-1] in prompt for body in expected.values()) for prompt in prompts))
            path = root/'golden.json'
            row = db.execute("SELECT * FROM artifacts WHERE path=? AND kind='canary-golden'",(str(path),)).fetchone()
            if row:
                golden = receipt(db,str(path),row['sha256'],'canary-golden')
                check('pixel_csv_order',golden['commit']==head and golden['tree']==git('rev-parse',head+'^{tree}').decode().strip() and golden['rc']==0 and golden['content_unchanged'] and golden['sandbox']['sandbox']=='bwrap' and golden['command'][-1]==golden_code(data['secret_order']))
        finally:
            db.close()
    except (OSError,ValueError,RuntimeError,sqlite3.Error,KeyError,StopIteration,subprocess.SubprocessError) as exc:
        check('evidence_error:'+str(exc),False)
    result = {'verdict':'PASS' if all(checks.values()) else 'BLOCK','checks':checks,
              'failures':[k for k,v in checks.items() if not v],
              'calls':[{'id':c['id'],'profile':c['profile'],'configured_model':c['model'],'actual_model':c['actual_model'],
                        'provider_model_reported':c['actual_model'] is not None,'sid':c['session_out'],'effort':c['effort'],'outcome':c['outcome']} for c in calls],
              'configured_unused':sorted(set(profiles)-used)}
    atomic_json(root/'verification.json',result)
    return result


def canary(workdir, *, max_batches=2, verify_only=False):
    root = Path(workdir).expanduser().resolve()
    existing = (root/'canary.json').exists()
    data = setup(root)
    if max_batches > 2:
        raise ValueError('canary validation is bounded to at most two batches')
    if not (verify_only and existing):
        with environment(root):
            env = dict(os.environ)
            completed = 0
            db_path = Path(data['state'])/'forge.db'
            if db_path.exists():
                with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as db:
                    completed = db.execute("SELECT count(*) FROM batches WHERE state IN ('DONE','NO_JUSTIFIED_WORK')").fetchone()[0]
            remaining = max_batches-completed
            commands = [('run',['run','--max-batches',str(remaining),'--until-idle'])] if remaining>0 else []
            if not (root/'doctor-result.json').exists():
                commands.insert(0,('doctor',['doctor','--smoke']))
            for name,args in commands:
                started = time.monotonic()
                with (root/(name+'.log')).open('a') as out:
                    proc = subprocess.Popen([sys.executable,'-m','forge',*args,'--config',data['config']], stdout=out,stderr=subprocess.STDOUT,env=env,start_new_session=True)
                    try:
                        rc = proc.wait(timeout=4*3600)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGTERM)
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid,signal.SIGKILL)
                            proc.wait()
                        rc = 124
                atomic_json(root/(name+'-result.json'),{'exit':rc,'elapsed':time.monotonic()-started})
                if name=='run' or rc in (2,75,124):
                    if rc:
                        break
    if not (verify_only and existing):
        try:
            collect_golden(data)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            atomic_json(root/'golden-error.json', {'error': str(exc)})
    try:
        result = verify(data)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        result = {'verdict':'BLOCK','failures':[str(exc)]}
        atomic_json(root/'verification.json',result)
    # Git bundle contains only the disposable project; state already resides in evidence root.
    subprocess.run(['git','-C',data['repo'],'bundle','create',str(root/'project.bundle'),'--all'],check=True,capture_output=True)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['verdict']=='PASS' else 2
