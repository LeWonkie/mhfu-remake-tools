"""Reproducible MHFU experiments. Only builds/atelier is mutable state."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 1
EPOCH = '1792000000000000'
GROUPS = ('object_cache', 'interpreter', 'renderer', 'window', 'delay_variants', 'pad')
_active = None


class AtelierError(RuntimeError):
    pass


class Incomplete(AtelierError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def home():
    out = ROOT/'builds/atelier'
    out.mkdir(parents=True, exist_ok=True)
    return out


def unique(parent, label):
    path = parent/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+label+'-'+uuid.uuid4().hex[:6])
    path.mkdir(parents=True)
    return path


def source_files():
    suffixes = {'.py', '.c', '.h', '.cpp', '.hpp', '.inc', '.json', '.txt'}
    files = [p for p in (ROOT/'src').rglob('*') if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts]
    files += [ROOT/'tools/check.py', ROOT/'tools/atelier_core.py', ROOT/'tools/test_atelier.py', ROOT/'manette.ini']
    files += list((ROOT/'builds/regression').glob('*.json'))
    # Fixtures affect generated roots and the saved character, even if src is unchanged.
    for name in ('continuation-exit60', 'checkpoint15-village', 'checkpoint14-saved-character/ms0',
                 'checkpoint14-saved-character/initial-ms0'):
        directory = ROOT/'builds'/name
        for p in directory.rglob('*'):
            relative = p.relative_to(directory)
            if p.is_file() and (name.endswith('ms0') or p.suffix in {'.json', '.txt'} or
                                p.name == 'capture.ram.bin' or relative.parts[0] in ('overlays','modules','flash0')):
                files.append(p)
    return sorted(set(p for p in files if p.exists()))


def source_key():
    return identity({str(p.relative_to(ROOT)): digest(p) for p in source_files()})


def runtime_modules():
    sys.path.insert(0, str(ROOT/'src'))
    import play_native
    return play_native


def python_executable():
    candidate = Path(sys.executable).with_name('python.exe')
    return str(candidate) if candidate.exists() else sys.executable


def file_identity(path):
    """Cache large read-only game/toolchain hashes by their file identity."""
    path = Path(path)
    st = path.stat()
    record = dict(path=str(path.resolve()), size=st.st_size, modified=st.st_mtime_ns)
    cache = home()/'hashes'/f'{identity(record)}.json'
    if cache.exists():
        return read_json(cache)['sha256']
    value = digest(path)
    write_json(cache, {**record, 'sha256': value})
    return value


def environment_key():
    rt = runtime_modules()
    return dict(protocol=PROTOCOL, python=sys.version, zig=file_identity(rt.ZIG),
                elf=file_identity(rt.ELF), data=file_identity(rt.DATA))


@contextmanager
def exclusive():
    path = home()/'active.lock'
    with path.open('a+b') as f:
        if path.stat().st_size == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise AtelierError('Un autre essai de l’atelier est déjà en cours.') from e
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


class Runner:
    def __init__(self, emit):
        self.emit = emit
        self.stopped = threading.Event()
        self.proc = None

    def cancel(self):
        self.stopped.set()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                proc.terminate()

    def check(self):
        if self.stopped.is_set():
            raise Incomplete('Essai arrêté : aucun résultat interrompu ne sera réutilisé.')

    def reap(self):
        proc = self.proc
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
        self.proc = None

    def command(self, args, log, cwd=None):
        self.check()
        log.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            with log.open('w', encoding='utf-8') as output:
                self.proc = subprocess.Popen(args, cwd=cwd or ROOT, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                             errors='replace', bufsize=1, creationflags=flags,
                                             env={**{k:v for k,v in os.environ.items() if not k.startswith(('MHFU_', 'RT_GE_'))},
                                                  'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8'})
                self.check()
                for line in self.proc.stdout:
                    output.write(line); output.flush()
                    self.emit(line.rstrip())
                    self.check()
                code = self.proc.wait()
                self.check()
                return code
        except BaseException:
            self.cancel()
            raise
        finally:
            self.reap()

    def game(self, snapshot, out, dispatches, threads, input_text):
        rt = runtime_modules()
        self.check()
        clone_snapshot(snapshot, out)
        self.check()
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        # Explicitly remove experiment controls inherited from another terminal.
        env = {k:v for k,v in os.environ.items() if not k.startswith(('MHFU_', 'RT_GE_'))}
        env.update(MHFU_LIBC_EPOCH_US=EPOCH, MHFU_INPUT=input_text, RT_GE_THREADS=str(threads), RT_GE_FLUSH_TRACE='0')
        args = [str(out/'mhfu-bootstrap.exe'), '--startup', str(rt.ELF), str(rt.DATA),
                *rt.RUN_ARGS, '--frame-dump', '60', '--dispatches', str(dispatches)]
        started = time.perf_counter()
        try:
            with (out/'stdout.jsonl').open('w', encoding='utf-8') as stdout, (out/'stderr.jsonl').open('w', encoding='utf-8') as stderr:
                self.proc = subprocess.Popen(args, cwd=out, env=env, stdout=stdout, stderr=stderr, creationflags=flags)
                self.check()
                last = 0
                while self.proc.poll() is None:
                    self.check()
                    time.sleep(.2)
                    count = len(list((out/'frames').glob('*.bmp')))
                    if count >= last+10:
                        last = count
                        self.emit(f'{count} images de contrôle enregistrées…')
                code = self.proc.returncode
                self.check()
            result = parse_measurement(out, code, time.perf_counter()-started)
            write_json(out/'measurement.json', result)
            if not result['complete']:
                raise Incomplete(f'Mesure incomplète (sortie {code}, {result["stop"]}) ; chiffres écartés.')
            return result
        except BaseException:
            self.cancel()
            raise
        finally:
            self.reap()


def cancel():
    if _active is not None:
        _active.cancel()


def operation(action, emit, fn):
    global _active
    with exclusive():
        out = unique(home()/'runs', action)
        result = dict(action=action, status='running', created=datetime.now(timezone.utc).isoformat(), directory=str(out))
        write_json(out/'result.json', result)
        runner = Runner(emit)
        _active = runner
        try:
            value = fn(runner, out)
            runner.check()
            result.update(value)
            result.setdefault('summary', 'Essai terminé.')
            if result['status'] == 'running': result['status'] = 'passed'
        except Incomplete as e:
            result.update(status='incomplete', summary=str(e))
        except Exception as e:
            result.update(status='failed', summary=str(e))
        except BaseException:
            runner.cancel()
            result.update(status='incomplete', summary='Essai interrompu ; résultat non réutilisable.')
            raise
        finally:
            result['finished'] = datetime.now(timezone.utc).isoformat()
            result['elapsed_seconds'] = (datetime.fromisoformat(result['finished'])-datetime.fromisoformat(result['created'])).total_seconds()
            write_json(out/'result.json', result)
            labels = {'passed':'Réussi', 'cached':'Résultat réutilisé', 'failed':'Échec', 'incomplete':'Incomplet'}
            (home()/'dernier-bilan.txt').write_text(
                'ATELIER MHFU\n\n'+labels.get(result['status'],result['status'])+'\n'+
                result.get('summary','Essai interrompu.')+'\n\n'+
                f'Durée de cette opération : {result["elapsed_seconds"]:.1f} s\nDossier complet : {out}\n', encoding='utf-8')
            _active = None
        emit(result['summary'])
        return result


def choose_groups(group):
    if group == 'renderer': return ['renderer']
    if group == 'cpu': return ['interpreter', 'delay_variants', 'object_cache']
    if group == 'window': return ['window', 'pad']
    if group != 'auto': raise AtelierError('Zone de test inconnue.')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    r = subprocess.run(['git', 'diff', '--name-only', 'HEAD'], cwd=ROOT, capture_output=True, text=True, creationflags=flags)
    u = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, capture_output=True, text=True, creationflags=flags)
    if r.returncode or u.returncode: return list(GROUPS)
    selected = set()
    for name in (r.stdout+'\n'+u.stdout).splitlines():
        if name.endswith(('.md', '.bat')): continue
        if 'renderer' in name or 'dither' in name or 'hazards' in name: selected.add('renderer')
        elif 'window' in name or 'pad' in name: selected.update(('window','pad'))
        elif name.startswith('tools/atelier') or name == 'tools/test_atelier.py': continue
        elif name.startswith('src/') and ('bootstrap' in name or 'interpreter' in name or 'loader' in name):
            selected.update(('interpreter','delay_variants'))
        else: selected.update(GROUPS)
    return sorted(selected) or list(GROUPS)


def check_cached(kind, key):
    path = home()/'cache'/f'{kind}-{key}.json'
    if path.exists():
        r = read_json(path)
        report = Path(r.get('proof', ''))
        if report.is_file() and digest(report) == r.get('proof_sha256'):
            return r
    return None


def run_checks(runner, out, groups, full):
    own_tests = ROOT/'tools/test_atelier.py'
    if own_tests.is_file():
        if runner.command([python_executable(), str(own_tests)], out/'atelier-tests.log'):
            raise AtelierError('Un contrôle de l’atelier a échoué ; vérification arrêtée.')
    target = out/'checks'
    args = [python_executable(), '-u', str(ROOT/'tools/check.py'), '--out', str(target)]
    if full: args.append('--scenes')
    else: args += ['--tests', *groups]
    code = runner.command(args, out/'checks.log')
    proof = target/'results.json'
    if code or not proof.exists() or read_json(proof).get('status') != 'complete':
        raise AtelierError('Vérification échouée. Le journal conserve le détail ; rien n’a été validé.')
    return proof


def sync_generated(runner, out):
    generator = ROOT/'src/prepare_renderer_source.py'
    if generator.is_file():
        if runner.command([python_executable(), str(generator)], out/'prepare.log'):
            raise AtelierError('Le générateur de rendu a échoué.')
    runner.check()


def quick(group='auto', force=False, emit=print):
    def work(runner, out):
        sync_generated(runner, out)
        groups = choose_groups(group)
        global_key = identity(dict(source=source_key(), environment=environment_key()))
        previous_full = None if force else check_cached('full', global_key)
        if previous_full:
            return dict(status='cached', summary='Ces fichiers ont déjà passé la validation complète ; aucun test à relancer.', proof=previous_full['proof'])
        key = identity(dict(source=source_key(), environment=environment_key(), groups=groups))
        cached = None if force else check_cached('quick', key)
        if cached:
            return dict(status='cached', summary='Tests ciblés déjà réussis pour ces fichiers ; aucune relance.', proof=cached['proof'])
        emit('Tests ciblés : '+', '.join(groups))
        proof = run_checks(runner, out, groups, False)
        after_key = identity(dict(source=source_key(), environment=environment_key(), groups=groups))
        if after_key != key: raise Incomplete('Les fichiers ont changé pendant les tests ; aucune version n’est marquée validée.')
        runner.check()
        write_json(home()/'cache'/f'quick-{key}.json', dict(proof=str(proof), proof_sha256=digest(proof)))
        return dict(summary='Tests ciblés réussis. Cela ne remplace pas la validation avant commit.', groups=groups, proof=str(proof))
    return operation('rapide', emit, work)


def validate(force=False, emit=print):
    def work(runner, out):
        sync_generated(runner, out)
        key = identity(dict(source=source_key(), environment=environment_key()))
        cached = None if force else check_cached('full', key)
        if cached:
            return dict(status='cached', summary='Validation complète déjà réussie pour ces fichiers et références.', proof=cached['proof'])
        emit('Validation complète : tests et deux scènes de référence.')
        proof = run_checks(runner, out, list(GROUPS), True)
        after_key = identity(dict(source=source_key(), environment=environment_key()))
        if after_key != key: raise Incomplete('Les fichiers ont changé pendant la validation ; résultat à refaire.')
        runner.check()
        write_json(home()/'cache'/f'full-{key}.json', dict(proof=str(proof), proof_sha256=digest(proof)))
        return dict(summary='Validation complète réussie : tests, images, mémoire et arrêts conformes.', proof=str(proof))
    return operation('validation', emit, work)


def snapshot_files(source):
    source = Path(source)
    required = ['mhfu-bootstrap.exe', 'mhfu-atrac.dll', 'native_renderer.dll']
    for name in required:
        if not (source/name).is_file(): raise AtelierError(f'Lancement incomplet : {name} manque dans {source.name}.')
    files = [source/'mhfu-bootstrap.exe', *source.glob('*.dll')]
    if (source/'manette.ini').is_file(): files.append(source/'manette.ini')
    for name in ('overlays', 'flash0', 'modules', 'ms0'):
        if not (source/name).is_dir(): raise AtelierError(f'Lancement incomplet : dossier {name} absent.')
        files += [p for p in (source/name).rglob('*') if p.is_file()]
    return sorted(set(files))


def snapshot_manifest(source, include_runtime=True):
    manifest = {str(p.relative_to(source)):digest(p) for p in snapshot_files(source)
                if include_runtime or (p.suffix != '.dll' and p.name != 'mhfu-bootstrap.exe')}
    if not include_runtime:
        # This build-time audit names its capture directory. That path is not
        # a game input; retain all image hashes and semantic fields instead.
        audit = Path(source)/'overlays/observed-overlays.json'
        if audit.exists():
            data = read_json(audit)
            for overlay in data.get('overlays', []):
                for evidence in overlay.get('evidence', []):
                    evidence.pop('capture', None)
            manifest[str(audit.relative_to(source))] = identity(data)
    return manifest


def clone_snapshot(source, out, ms0=None):
    source, out = Path(source), Path(out)
    files = snapshot_files(source)  # preflight BEFORE making a destination
    out.mkdir()
    for name in ('overlays', 'flash0', 'modules', 'ms0'):
        (out/name).mkdir()
    for p in files:
        if ms0 is not None and p.relative_to(source).parts[0] == 'ms0': continue
        target = out/p.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    if ms0 is not None:
        shutil.copytree(ms0, out/'ms0', dirs_exist_ok=True)


def built_source(source=None):
    if source is None: return None
    path = Path(source)
    path = (ROOT/path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to((ROOT/'builds').resolve()):
        raise AtelierError('Choisissez un lancement situé dans builds du projet.')
    return path


def build_current(runner, out, ms0):
    rt = runtime_modules()
    sync_generated(runner, out)
    key = identity(dict(source=source_key(), environment=environment_key()))
    cache = home()/'cache'/f'build-{key}.json'
    if cache.exists():
        record = read_json(cache)
        path = Path(record['directory'])
        if path.exists() and snapshot_manifest(path) == record['manifest']:
            runner.emit('Compilation compatible réutilisée.')
            return path
    # prepare() itself is kept in a child process so Stop can cancel compilation.
    code = ('import sys;from pathlib import Path;'
            f'sys.path.insert(0,{str(ROOT/"src")!r});from play_native import prepare;'
            f'prepare(Path({str(out/"build")!r}),Path({str(ROOT/"builds/continuation-exit60")!r}),Path({str(ms0)!r}))')
    if runner.command([python_executable(), '-u', '-c', code], out/'build.log'):
        raise AtelierError('Compilation échouée ; voir le journal de cet essai.')
    path = out/'build'
    after_key = identity(dict(source=source_key(), environment=environment_key()))
    if after_key != key: raise Incomplete('Le code a changé pendant la compilation ; cette copie ne sera pas réutilisée.')
    runner.check()
    write_json(home()/'cache'/f'build-{key}.json', dict(directory=str(path), manifest=snapshot_manifest(path)))
    return path


def references():
    result = []
    for p in (home()/'references').glob('*/reference.json'):
        try:
            record = read_json(p)
            if record.get('protocol') == PROTOCOL and record.get('id') == p.parent.name:
                result.append(record)
        except (ValueError, OSError): pass
    return sorted(result, key=lambda r:r['created'], reverse=True)


def freeze(source=None, emit=print):
    def work(runner, out):
        source_path = built_source(source) or build_current(runner, out, ROOT/'builds/checkpoint14-saved-character/ms0')
        runner.check()
        ref = unique(home()/'references', 'reference')
        # Never freeze a Memory Stick already modified by a play/run session.
        seed = ROOT/'builds/checkpoint14-saved-character/ms0'
        clone_snapshot(source_path, ref/'snapshot', ms0=seed)
        input_text = (ROOT/'src/input_regression_exit.txt').read_text().strip()
        record = dict(id=ref.name, created=datetime.now(timezone.utc).isoformat(), protocol=PROTOCOL,
                      source=str(source_path), environment=environment_key(), source_key=source_key(),
                      input=input_text, epoch=EPOCH, manifest=snapshot_manifest(ref/'snapshot'))
        runner.check()
        write_json(ref/'reference.json', record)
        return dict(summary='Référence figée. Elle restera disponible pendant les prochains correctifs.', reference=record)
    return operation('reference', emit, work)


def parse_measurement(out, code, elapsed):
    record = dict(exit_code=code, elapsed_seconds=elapsed, profile=None, ram=None, stop=None, frames={})
    for filename in ('stdout.jsonl','stderr.jsonl'):
        with (out/filename).open(encoding='utf-8', errors='replace') as f:
            for line in f:
                if not line.startswith('{'): continue
                try: event = json.loads(line)
                except ValueError: continue
                kind = event.get('event')
                if kind == 'profile': record['profile'] = event
                elif kind == 'renderer_stats': record['renderer_stats'] = event
                elif kind == 'final_state': record['ram'] = event.get('memory_sha256')
                elif kind == 'stop': record['stop'] = event.get('reason')
    record['frames'] = {p.name.split('_')[0]:hashlib.sha256(p.read_bytes()[54:]).hexdigest()
                        for p in sorted((out/'frames').glob('*.bmp'))}
    profile = record['profile'] or {}
    wall = profile.get('wall_s', 0)
    record['complete'] = (code == 14 and record['stop'] == 'scheduler dispatch budget' and
                          bool(record['ram']) and bool(record['frames']) and isinstance(wall, (int,float)) and
                          math.isfinite(wall) and wall > 0 and profile.get('virtual_s', 0) > 0)
    return record


def comparable(before, after):
    return (before.get('complete') and after.get('complete') and before['frames'] == after['frames'] and
            before['ram'] == after['ram'] and before['stop'] == after['stop'] and
            before['profile']['virtual_s'] == after['profile']['virtual_s'])


def assert_idle():
    if os.name != 'nt': return
    command = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(mhfu-bootstrap|zig|pad|renderer-.*test)\\.exe$' -or "
               "($_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -match '(check|play_native|replay_native|advance_native_bootstrap|test_native_[a-z_]+|test_overlay_[a-z_]+|build_native_[a-z_]+|run_probe|measure_full_scene)\\.py') } | "
               "Select-Object -ExpandProperty ProcessId")
    r = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode: raise AtelierError('Impossible de vérifier que le jeu et les tests sont arrêtés.')
    if r.stdout.strip(): raise AtelierError('Un jeu ou une vérification MHFU tourne déjà. Attendez sa fin avant de chronométrer.')


def compare(reference=None, dispatches=200000, threads=1, candidate=None, force=False, emit=print):
    def work(runner, out):
        if dispatches not in (10000,200000,500000) or threads not in (1,16):
            raise AtelierError('Durée ou nombre de threads non pris en charge.')
        available = references()
        ref = next((r for r in available if r['id'] == reference), None) if reference else (available[0] if available else None)
        if ref is None: raise AtelierError('Figez d’abord une référence avant de modifier le code.')
        snapshot = home()/'references'/ref['id']/'snapshot'
        if snapshot_manifest(snapshot) != ref['manifest']: raise AtelierError('La référence a été modifiée ; elle ne sera pas utilisée.')
        if environment_key() != ref['environment']: raise AtelierError('Jeu, Python ou compilateur différent de la référence ; figez une nouvelle référence.')
        assert_idle()
        candidate_path = built_source(candidate) or build_current(runner, out, snapshot/'ms0')
        # Enforce the same game assets, control mapping and clean Memory Stick.
        fresh = out/'candidate'
        clone_snapshot(candidate_path, fresh, ms0=snapshot/'ms0')
        runner.check()
        candidate_manifest = snapshot_manifest(fresh)
        if snapshot_manifest(fresh, False) != snapshot_manifest(snapshot, False):
            raise AtelierError('Les ressources de jeu diffèrent entre référence et candidat. Comparaison refusée.')
        recipe = dict(protocol=PROTOCOL, engine=digest(Path(__file__)), reference=identity(ref['manifest']), input=identity(ref['input']),
                      environment=ref['environment'], dispatches=dispatches, threads=threads, epoch=EPOCH)
        cache = home()/'cache'/f'profile-{identity(recipe)}.json'
        previous = None
        if not force and cache.exists():
            previous = read_json(cache)
            proof = Path(previous.get('proof',''))
            if not proof.is_file() or digest(proof) != previous.get('proof_sha256'): previous = None
        if previous:
            before = read_json(previous['proof'])
            emit('Mesure de référence compatible réutilisée.')
        else:
            emit('Mesure de référence — un seul essai à la fois.')
            before = runner.game(snapshot, out/'before', dispatches, threads, ref['input'])
            proof = out/'before/measurement.json'
            write_json(cache, dict(proof=str(proof), proof_sha256=digest(proof)))
        assert_idle()
        if snapshot_manifest(fresh) != candidate_manifest: raise Incomplete('La copie candidate a été modifiée pendant la mesure de référence.')
        emit('Mesure du correctif dans les mêmes conditions.')
        after = runner.game(fresh, out/'after', dispatches, threads, ref['input'])
        if not comparable(before, after):
            return dict(status='failed', summary='Comparaison refusée : images, mémoire, durée simulée ou arrêt différents.',
                        before=before, after=after, recipe=recipe)
        reduction = (before['profile']['wall_s']-after['profile']['wall_s'])/before['profile']['wall_s']*100
        note = ' Ce segment teste le démarrage, pas la fluidité en jeu.' if dispatches == 10000 else ''
        uncertainty = ' Écart inférieur à 3 % : gain non établi, répéter pour conclure.' if abs(reduction) < 3 else ''
        summary = f'Images et mémoire identiques. Durée : {before["profile"]["wall_s"]:.1f} → {after["profile"]["wall_s"]:.1f} s ({reduction:+.1f} % de réduction).'+uncertainty+note
        return dict(summary=summary, before=before, after=after, duration_reduction_percent=reduction, recipe=recipe,
                    candidate_manifest=candidate_manifest, candidate_source=str(candidate_path))
    return operation('comparaison', emit, work)
