"""Fast tests of experiment validity and reuse; no MHFU game data needed."""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import atelier_core as core


class AtelierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mhfu-atelier-')
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.root_patch = patch.object(core, 'ROOT', self.root)
        self.root_patch.start()
        (self.root/'src').mkdir()

    def tearDown(self):
        core.cancel()
        self.root_patch.stop()
        # The resolved target was verified as a dedicated child of the temp dir above.
        self.temp.cleanup()

    def snapshot(self, name='snapshot'):
        p = self.root/name
        p.mkdir()
        for file in ('mhfu-bootstrap.exe','mhfu-atrac.dll','native_renderer.dll','another.dll'):
            (p/file).write_bytes(file.encode())
        for folder in ('overlays','flash0','modules','ms0'):
            (p/folder).mkdir()
        (p/'ms0/save.dat').write_text('played')
        (p/'modules/module.dat').write_text('module')
        return p

    def measurement(self, name='result', code=14, stop='scheduler dispatch budget', final=True):
        out = self.root/name; out.mkdir()
        events = [dict(event='profile',wall_s=2.0,virtual_s=8.0)]
        if final: events.append(dict(event='final_state',memory_sha256='abc'))
        (out/'stdout.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        (out/'stderr.jsonl').write_text(json.dumps(dict(event='stop',reason=stop)))
        (out/'frames').mkdir()
        (out/'frames/f000060_001.bmp').write_bytes(bytes(54)+b'pixels')
        return core.parse_measurement(out,code,2.1)

    def test_clone_keeps_dlls_empty_folders_and_clean_seed(self):
        source = self.snapshot()
        clean = self.root/'clean'; clean.mkdir(); (clean/'save.dat').write_text('initial')
        out = self.root/'copy'
        core.clone_snapshot(source,out,ms0=clean)
        self.assertEqual((out/'another.dll').read_bytes(),b'another.dll')
        self.assertTrue((out/'flash0').is_dir())
        self.assertEqual((out/'ms0/save.dat').read_text(),'initial')
        self.assertEqual((source/'ms0/save.dat').read_text(),'played')

    def test_missing_audio_decoder_fails_before_launch_or_copy(self):
        source = self.snapshot(); (source/'mhfu-atrac.dll').unlink()
        with self.assertRaises(core.AtelierError): core.clone_snapshot(source,self.root/'copy')
        self.assertFalse((self.root/'copy').exists())

    def test_manifest_detects_changed_reference(self):
        source = self.snapshot(); manifest = core.snapshot_manifest(source)
        (source/'another.dll').write_bytes(b'changed')
        self.assertNotEqual(core.snapshot_manifest(source),manifest)

    def test_capture_directory_is_not_a_game_input(self):
        source = self.snapshot(); copied = self.root/'copy'; core.clone_snapshot(source,copied)
        for folder,label in ((source,'before'),(copied,'after')):
            core.write_json(folder/'overlays/observed-overlays.json',dict(overlays=[dict(evidence=[dict(capture=label,sha256='same')])]))
        self.assertEqual(core.snapshot_manifest(source,False),core.snapshot_manifest(copied,False))
        self.assertNotEqual(core.snapshot_manifest(source),core.snapshot_manifest(copied))
        (copied/'overlays/image.bin').write_bytes(b'changed')
        self.assertNotEqual(core.snapshot_manifest(source,False),core.snapshot_manifest(copied,False))

    def test_source_key_changes_for_runtime_and_fixture_assets(self):
        file = self.root/'src/core.c'; file.write_text('a')
        before = core.source_key(); file.write_text('b')
        self.assertNotEqual(core.source_key(),before)
        fixture = self.root/'builds/continuation-exit60/modules'; fixture.mkdir(parents=True)
        (fixture/'sample.bin').write_bytes(b'a')
        before = core.source_key(); (fixture/'sample.bin').write_bytes(b'b')
        self.assertNotEqual(core.source_key(),before)

    def test_success_requires_expected_stop_exit_final_state_and_frames(self):
        self.assertTrue(self.measurement()['complete'])
        self.assertFalse(self.measurement('wrong-code',code=1)['complete'])
        self.assertFalse(self.measurement('wrong-stop',stop='decoder DLL missing')['complete'])
        self.assertFalse(self.measurement('no-final',final=False)['complete'])

    def test_partial_json_and_nan_cannot_create_success(self):
        out = self.root/'partial'; out.mkdir(); (out/'frames').mkdir()
        (out/'stdout.jsonl').write_text('{"event":"profile"')
        (out/'stderr.jsonl').write_text('')
        self.assertFalse(core.parse_measurement(out,1,1)['complete'])
        result = self.measurement('nan')
        p = self.root/'nan/stdout.jsonl'
        p.write_text(p.read_text().replace('2.0','NaN'))
        self.assertFalse(core.parse_measurement(p.parent,14,1)['complete'])

    def test_any_image_memory_or_virtual_time_change_rejects_speed_claim(self):
        original = self.measurement()
        self.assertTrue(core.comparable(original,copy.deepcopy(original)))
        for field,value in [('frames',{'f000060':'changed'}),('ram','different'),('complete',False),('stop','wrong')]:
            candidate = copy.deepcopy(original); candidate[field] = value
            self.assertFalse(core.comparable(original,candidate))
        candidate = copy.deepcopy(original); candidate['profile']['virtual_s'] += .1
        self.assertFalse(core.comparable(original,candidate))

    def test_only_intact_proof_can_be_reused(self):
        proof = self.root/'proof.json'; proof.write_text('{}')
        cache = core.home()/'cache/quick-test.json'
        core.write_json(cache,dict(proof=str(proof),proof_sha256=core.digest(proof)))
        self.assertIsNotNone(core.check_cached('quick','test'))
        proof.write_text('{"changed":true}')
        self.assertIsNone(core.check_cached('quick','test'))
        proof.unlink()
        self.assertIsNone(core.check_cached('quick','test'))

    def test_interrupted_operation_is_recorded_and_never_passed(self):
        def interrupted(runner,out): raise core.Incomplete('interrompu')
        result = core.operation('test',lambda line:None,interrupted)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(core.read_json(Path(result['directory'])/'result.json')['status'],'incomplete')
        self.assertIn('Incomplet',(core.home()/'dernier-bilan.txt').read_text(encoding='utf-8'))

    def test_quick_reuses_pass_and_invalidates_after_change(self):
        calls = []
        (self.root/'src/renderer.c').write_text('a')
        def fake_checks(runner,out,groups,full):
            calls.append(groups)
            proof = out/'checks/results.json'
            core.write_json(proof,dict(status='complete'))
            return proof
        with patch.object(core,'environment_key',return_value={'test':1}), patch.object(core,'run_checks',side_effect=fake_checks):
            first = core.quick('renderer',emit=lambda _:None)
            second = core.quick('renderer',emit=lambda _:None)
            self.assertEqual((first['status'],second['status']),('passed','cached'))
            self.assertEqual(len(calls),1)
            (self.root/'src/renderer.c').write_text('b')
            self.assertEqual(core.quick('renderer',emit=lambda _:None)['status'],'passed')
            self.assertEqual(len(calls),2)

    def test_failed_check_is_not_cached(self):
        with patch.object(core,'environment_key',return_value={}), patch.object(core,'run_checks',side_effect=core.AtelierError('failed')) as run:
            self.assertEqual(core.quick('renderer',emit=lambda _:None)['status'],'failed')
            self.assertEqual(core.quick('renderer',emit=lambda _:None)['status'],'failed')
            self.assertEqual(run.call_count,2)

    def test_edit_during_tests_cannot_validate_new_source(self):
        file = self.root/'src/renderer.c'; file.write_text('before')
        def editing_check(runner,out,groups,full):
            proof = out/'checks/results.json'; core.write_json(proof,dict(status='complete'))
            file.write_text('after')
            return proof
        with patch.object(core,'environment_key',return_value={}), patch.object(core,'run_checks',side_effect=editing_check):
            result = core.quick('renderer',emit=lambda _:None)
            self.assertEqual(result['status'],'incomplete')
            self.assertFalse(list((core.home()/'cache').glob('quick-*.json')))

    def test_stop_without_child_stays_incomplete(self):
        def stop(runner,out):
            runner.cancel()
            return dict(summary='should not pass')
        self.assertEqual(core.operation('stop',lambda _:None,stop)['status'],'incomplete')

    @unittest.skipUnless(sys.platform=='win32','Windows process guard')
    def test_guard_recognizes_actual_check_command(self):
        seen = []
        def query(args,**kwargs):
            seen.append(args[-1]); return subprocess.CompletedProcess(args,0,stdout='',stderr='')
        with patch.object(core.subprocess,'run',side_effect=query): core.assert_idle()
        pattern = re.findall(r"-match '([^']+)'",seen[0])[-1]
        self.assertIsNotNone(re.search(pattern,'python.exe tools/check.py --scenes'))
        self.assertIsNotNone(re.search(pattern,'python.exe src/play_native.py'))

    def test_cancel_stops_owned_child(self):
        runner = core.Runner(lambda _:None)
        errors = []
        def work():
            try: runner.command([sys.executable,'-u','-c','import time; print("ready",flush=True); time.sleep(30)'],self.root/'cancel.log',cwd=self.root)
            except core.Incomplete: errors.append('stopped')
        worker = threading.Thread(target=work); worker.start()
        for _ in range(100):
            if runner.proc is not None: break
            time.sleep(.01)
        runner.cancel(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,['stopped'])


if __name__ == '__main__': unittest.main(verbosity=2)
