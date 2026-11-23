"""Integration checks with a synthetic guest; no game or real saves are used."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atelier_core import (Workshop, VARIANTS, RTC_MIN_US, RTC_MAX_US, capture_size,
                          register_rtc_variant, validate_action, validate_script)


FAKE_GUEST = r'''
import json, os, pathlib, sys, time
game = pathlib.Path(sys.argv[-1])
data = pathlib.Path(os.environ["MHP3RD_DATA_DIR"])
data.mkdir(parents=True, exist_ok=True)
save = game / "ms0" / "PSP" / "SAVEDATA" / "TEST" / "save.bin"
save.parent.mkdir(parents=True, exist_ok=True)
save.write_bytes(save.read_bytes() + b"|fake-run" if save.exists() else b"fake-run")
(data / "seen-env.json").write_text(json.dumps({k: v for k, v in os.environ.items()
    if k.startswith(("MHP3RD_", "PSPRECOMP_"))}), encoding="utf-8")
print("Synthetic guest ready", flush=True)
if "--record-demo" in sys.argv:
    print("[script] 99 steps", flush=True)
    print("unrelated frame 999: pad x 12", flush=True)
    print("[script] frame 10: pad b 12", flush=True)
    print("[script] frame 20: axis lefty 0", flush=True)
    print("[script] frame 40: shot demo", flush=True)
live = pathlib.Path(os.environ["MHP3RD_INPUT_LIVE"])
offset, frame = 0, 40
while True:
    frame += 10
    if live.exists():
        with live.open(encoding="utf-8") as stream:
            stream.seek(offset)
            text = stream.read()
            offset = stream.tell()
        for line in text.splitlines():
            if not line.strip(): continue
            action = line.split(":", 1)[-1].strip()
            print(f"[script] frame {frame}: {action}", flush=True)
            if action == "quit":
                print("Runtime stopped: window closed", flush=True)
                raise SystemExit(4)
    time.sleep(0.02)
'''


class WorkshopCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atelier-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "MHFU project"
        self.game = self.project / "game-mhfu"
        self.save = self.game / "ms0" / "PSP" / "SAVEDATA" / "TEST" / "save.bin"
        self.save.parent.mkdir(parents=True)
        self.save.write_bytes(b"original-save")
        (self.game / "EBOOT.ELF").write_bytes(b"synthetic-elf")
        (self.game / "disc.iso").write_bytes(b"synthetic-iso")
        (self.project / "psprecomp-mhfu" / "out" / "mhfu-probe" / "bin").mkdir(parents=True)
        self.settings = self.root / "appdata" / "Yakumo" / "MHP3rd" / "settings.ini"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("input.background_gamepad=0\ninput.pad.fast_forward=\n", encoding="utf-8")
        self.environ = patch.dict(os.environ, {"APPDATA": str(self.root / "appdata")})
        self.environ.start()
        self.guest = self.root / "fake guest.py"
        self.guest.write_text(FAKE_GUEST, encoding="utf-8")
        self.data = self.root / "state"
        self.tool = self.new_tool()
        self.tool.prepare(seed_ms0=self.game / "ms0")

    def tearDown(self):
        try:
            self.tool.stop()
        finally:
            self.environ.stop()
            self.temp.cleanup()

    def new_tool(self, extra=()):
        return Workshop(self.project, self.data,
                        process_command=[sys.executable, str(self.guest), *extra])

    def run_path(self, run_id):
        return self.data / "runs" / run_id

    def start_ready(self, *, seconds=10, scenario="manual", variant="normal"):
        run_id = self.tool.start(scenario, variant, seconds)
        run = self.run_path(run_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            captures = list(run.rglob("seen-env.json"))
            if captures:
                return run_id, run, json.loads(captures[0].read_text(encoding="utf-8"))
            time.sleep(0.02)
        self.fail("The synthetic guest did not start")

    def finish(self):
        self.tool.stop()
        self.assertTrue(self.tool.wait(timeout=5))

    def test_every_run_copies_saves_without_modifying_original_or_previous_run(self):
        _, first, _ = self.start_ready()
        self.finish()
        first_save = first / "game" / "ms0" / "PSP" / "SAVEDATA" / "TEST" / "save.bin"
        self.assertEqual(first_save.read_bytes(), b"original-save|fake-run")
        _, second, _ = self.start_ready()
        self.finish()
        second_save = second / "game" / "ms0" / "PSP" / "SAVEDATA" / "TEST" / "save.bin"
        self.assertNotEqual(first, second)
        self.assertEqual(second_save.read_bytes(), b"original-save|fake-run")
        self.assertEqual(first_save.read_bytes(), b"original-save|fake-run")
        self.assertEqual(self.save.read_bytes(), b"original-save")
        self.assertEqual(self.settings.read_text(encoding="utf-8"),
                         "input.background_gamepad=0\ninput.pad.fast_forward=\n")

    def test_inherited_game_flags_cannot_enable_unsafe_debug_or_replace_script(self):
        hostile = {
            "MHP3RD_STRICT_HLE": "0", "MHP3RD_DEBUG_MENU": "1",
            "MHP3RD_DEBUG_COMMANDS": "foreign.txt", "MHP3RD_INPUT_SCRIPT": "0:quit",
            "MHP3RD_DEVICE_TEST": "1", "MHP3RD_TRACE_GE": "1",
            "MHP3RD_HIDE_HUD": "1", "PSPRECOMP_INHERITED_TEST": "1",
        }
        with patch.dict(os.environ, hostile):
            _, _, env = self.start_ready(scenario={"name": "plain", "script": "1:quit"})
        self.assertEqual(env.get("MHP3RD_STRICT_HLE"), "1")
        self.assertEqual(env.get("MHP3RD_DEBUG_MENU"), "0")
        for name in set(hostile) - {"MHP3RD_STRICT_HLE", "MHP3RD_DEBUG_MENU", "MHP3RD_INPUT_SCRIPT"}:
            self.assertNotIn(name, env)
        self.assertNotEqual(env.get("MHP3RD_INPUT_SCRIPT"), "0:quit")
        self.tool.stop()
        # A scenario asking for developer commands gets its own command file, never an inherited one.
        with patch.dict(os.environ, hostile):
            _, run, env = self.start_ready()
        self.assertEqual(env.get("MHP3RD_DEBUG_MENU"), "1")
        self.assertEqual(env.get("MHP3RD_DEBUG_COMMANDS"), str(run / "debug.txt"))
        self.assertNotIn("MHP3RD_DEVICE_TEST", env)

    def test_rtc_variant_survives_purge_and_preserves_base(self):
        base = dict(VARIANTS["space_trace"]["env"])
        variant = register_rtc_variant("space_trace", RTC_MIN_US)
        with patch.dict(os.environ, {"MHP3RD_DEVICE_TEST_RTC_US": str(RTC_MAX_US),
                                    "MHP3RD_DEVICE_TEST": "0"}):
            run_id, run, env = self.start_ready(variant=variant)
        self.assertEqual(env["MHP3RD_DEVICE_TEST"], "1")
        self.assertEqual(env["MHP3RD_DEVICE_TEST_RTC_US"], str(RTC_MIN_US))
        self.assertEqual(env["MHP3RD_TRACE_FRAME_SPACE"], "16")
        self.assertEqual(VARIANTS["space_trace"]["env"], base)
        self.finish()
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["rtc_us"], RTC_MIN_US)
        self.assertEqual(manifest["variant"], variant)
        self.assertFalse(manifest["rtc_epoch_confirmed"])  # The synthetic guest never acknowledged an RTC.
        self.assertIsNone(manifest["window_size"])
        self.assertEqual(manifest["quest_results"], [])

    def test_rtc_bounds_reject_before_any_run_and_none_keeps_variant(self):
        self.assertEqual(register_rtc_variant("normal", None), "normal")
        for value in (True, "946684800000000", 1.5, RTC_MIN_US - 1, RTC_MAX_US + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                register_rtc_variant("normal", value)
        with self.assertRaises(ValueError):
            register_rtc_variant("unknown", RTC_MIN_US)
        self.assertIn(register_rtc_variant("normal", RTC_MAX_US), VARIANTS)
        self.assertFalse((self.data / "runs").exists())

    def test_quest_checkpoint_survives_shutdown_with_actual_capture_sizes(self):
        run_id, run, _ = self.start_ready()
        header = bytearray(54)
        header[:2] = b"BM"
        struct.pack_into("<Iii", header, 14, 40, 960, -544)
        shot = run / "frames" / "q11001-arrivee.bmp"
        shot.write_bytes(header)
        native_frame = bytearray(header)
        struct.pack_into("<ii", native_frame, 18, 480, 272)
        (run / "frames" / "frame_600.bmp").write_bytes(native_frame)
        result = {"quest": 11001, "stage": 70, "window_size": [960, 544], "notes": []}
        self.tool.record_quest_result(run_id, result)
        result["notes"].append("must not change stored observations")
        saved = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["quest_results"][0]["stage"], 70)
        self.assertEqual(saved["quest_results"][0]["notes"], [])
        self.finish()
        saved = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["window_size"], [960, 544])
        self.assertEqual(saved["window_sizes"], [[960, 544]])
        self.assertEqual(saved["window_size_source"], "capture_bmp_pixels")
        self.assertEqual(saved["quest_results"][0]["quest"], 11001)
        self.assertEqual(saved["seed_sha256"], self.tool._current_seed()["seed_sha256"])
        # Results recorded after process completion also preserve its stop metadata.
        self.tool.record_quest_result(run_id, {"quest": 11001, "stage": 75})
        later = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(later["runtime_stop"], "window closed")
        self.assertEqual(len(later["quest_results"]), 2)

    def test_capture_size_does_not_invent_unavailable_dimensions(self):
        path = self.root / "missing.bmp"
        self.assertIsNone(capture_size(path))
        for data in (b"", b"not a bmp" * 4, b"BM" + b"\0" * 24):
            path.write_bytes(data)
            self.assertIsNone(capture_size(path))

    def test_debug_commands_go_to_the_run_file_only_when_asked(self):
        _, run, _ = self.start_ready()
        self.tool.debug_command("quest start 1001")
        self.assertEqual((run / "debug.txt").read_text(encoding="utf-8"), "quest start 1001\n")
        with self.assertRaises(ValueError):
            self.tool.debug_command("state\nquit")
        self.tool.stop()
        self.start_ready(scenario={"name": "plain", "script": ""})
        with self.assertRaises(RuntimeError):
            self.tool.debug_command("state")
        self.tool.stop()

    def test_debug_batch_preserves_order_and_rejects_all_before_writing(self):
        _, run, _ = self.start_ready()
        lines = ["poke32 0x08800000 1", "poke32 0x08800004 2"]
        self.tool.debug_commands(lines)
        path = run / "debug.txt"
        self.assertEqual(path.read_bytes(), b"poke32 0x08800000 1\npoke32 0x08800004 2\n")
        before = path.read_bytes()
        for invalid in ([], ["state", "quit\nstate"], ["state", None], ["state\0"]):
            with self.assertRaises(ValueError):
                self.tool.debug_commands(invalid)
            self.assertEqual(path.read_bytes(), before)
        self.tool.stop()
        with self.assertRaises(RuntimeError):
            self.tool.debug_commands(["state"])

    def test_named_seed_is_used_without_changing_the_current_one(self):
        current = (self.data / "current.json").read_text(encoding="utf-8")
        seed_id = json.loads(current)["id"]
        _, run, _ = self.start_ready()
        self.tool.stop()
        run_id = self.tool.start("manual", "normal", 10, seed_id=seed_id)
        self.tool.stop()
        self.assertEqual(json.loads((self.run_path(run_id) / "manifest.json").read_text(encoding="utf-8"))["seed_id"], seed_id)
        self.assertEqual((self.data / "current.json").read_text(encoding="utf-8"), current)
        with self.assertRaises(ValueError):
            self.tool.start("manual", "normal", 10, seed_id="../outside")

    def test_stop_affects_only_workshop_owned_process(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.start_ready()
            self.finish()
            self.assertIsNone(unrelated.poll())
            self.assertIsNone(self.tool.status().get("active"))
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_deadline_closes_run_and_keeps_manifest(self):
        run_id, run, _ = self.start_ready(seconds=1)
        self.assertTrue(self.tool.wait(timeout=6))
        self.assertIsNone(self.tool.status().get("active"))
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], run_id)
        self.assertIsNotNone(manifest.get("exit_code"))

    def test_start_refuses_overlapping_owned_runs(self):
        run_id, _, _ = self.start_ready()
        with self.assertRaises((ValueError, RuntimeError)):
            self.tool.start("manual", "normal", 10)
        self.assertTrue(self.run_path(run_id).is_dir())

    def test_runtime_and_preset_bounds_reject_before_launch(self):
        for seconds in (0, -1, 1801):
            with self.subTest(seconds=seconds), self.assertRaises((ValueError, RuntimeError)):
                self.tool.start("manual", "normal", seconds)
        with self.assertRaises((ValueError, RuntimeError)):
            self.tool.start("manual", "arbitrary-environment", 10)

    def test_checkpoint_preserves_original_and_copies_completed_run_only(self):
        _, run, _ = self.start_ready()
        with self.assertRaises((ValueError, RuntimeError)):
            self.tool.checkpoint("while-running")
        self.finish()
        checkpoint = Path(self.tool.checkpoint("after-test"))
        snapshots = list(checkpoint.rglob("save.bin"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].read_bytes(), b"original-save|fake-run")
        self.assertEqual(self.save.read_bytes(), b"original-save")
        run_save = run / "game" / "ms0" / "PSP" / "SAVEDATA" / "TEST" / "save.bin"
        snapshots[0].write_bytes(b"changed-checkpoint-only")
        self.assertEqual(run_save.read_bytes(), b"original-save|fake-run")

    def test_live_command_cannot_inject_second_action_or_escape_capture_directory(self):
        self.start_ready()
        for bad in ("shot ../escape", "shot C:\\escape", "shot /absolute",
                    "pad b 12\n0:quit", "pad b 12;0:quit", "quest start 505", "poke32 0x1234 0"):
            with self.subTest(action=bad), self.assertRaises((ValueError, RuntimeError)):
                self.tool.command(bad)

    def test_script_validator_rejects_malformed_or_unbounded_input(self):
        for script in ("abc:pad b", "-1:pad b", "10000001:quit", "20:pad b;10:quit",
                       "0:shot ../escape", "0:axis lefty nan", "0:pad b 999999999"):
            with self.subTest(script=script), self.assertRaises(ValueError):
                validate_script(script)
        validate_script("0:pad b 12;20:axis lefty 1;30:axis lefty 0;60:quit")

    def test_scenario_names_cannot_write_outside_scenario_directory(self):
        for name in ("../escape", "..\\escape", "C:\\escape", "/escape"):
            with self.subTest(name=name), self.assertRaises((ValueError, RuntimeError)):
                self.tool.save_scenario(name, "0:quit", 10)

    def test_recording_uses_actual_execution_logs_not_queued_or_unrelated_text(self):
        self.tool = self.new_tool(("--record-demo",))
        self.start_ready()
        self.finish()
        record = Path(self.tool.record_scenario("recorded-test"))
        scenario = json.loads(record.read_text(encoding="utf-8"))
        self.assertIn("pad b 12", scenario["script"])
        self.assertIn("axis lefty 0", scenario["script"])
        self.assertNotIn("pad x", scenario["script"])
        self.assertNotIn("99 steps", scenario["script"])
        validate_script(scenario["script"])

    def test_reload_reads_a_stopped_private_copy_and_keeps_seed_original(self):
        first, _, _ = self.start_ready()
        self.finish()
        source = self.run_path(first) / "game" / "ms0"
        before = (source / "PSP/SAVEDATA/TEST/save.bin").read_bytes()
        second = self.tool.start({"name":"reload", "script":"", "private_ms0":str(source)},seconds=10)
        second_save = self.run_path(second) / "game/ms0/PSP/SAVEDATA/TEST/save.bin"
        deadline = time.monotonic()+5
        while time.monotonic()<deadline and not second_save.read_bytes().endswith(b"|fake-run|fake-run"):
            time.sleep(.02)
        self.finish()
        self.assertEqual(second_save.read_bytes(),before+b"|fake-run")
        self.assertEqual((source/"PSP/SAVEDATA/TEST/save.bin").read_bytes(),before)
        self.assertEqual(self.save.read_bytes(),b"original-save")
        manifest=json.loads((self.run_path(second)/"manifest.json").read_text(encoding="utf-8"))
        self.assertIn("reload_source_sha256",manifest)

    def test_reload_rejects_originals_seeds_and_missing_private_sources(self):
        for source in (self.game/"ms0", self.data/"seeds"/self.tool._current_seed()["id"] /"ms0",
                       self.data/"runs"/"absent"/"game"/"ms0"):
            with self.subTest(source=source),self.assertRaises(ValueError):
                self.tool.start({"name":"reload", "script":"", "private_ms0":str(source)},seconds=10)


if __name__ == "__main__":
    unittest.main()
