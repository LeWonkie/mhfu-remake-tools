"""Meaningful evidence, privacy, and isolation tests for local reports."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("atelier_reports", Path(__file__).parents[1] / "atelier_reports.py")
reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reports)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(__file__).resolve().parents[3] / "work" / "report-tests"
        self.fixture_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.fixture_root, prefix="reports-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.fixture_root.resolve()))
        self.temp.cleanup()

    def test_hunt_goal_and_observed_deaths_are_not_presented_as_victory(self):
        run = self.run_dir()
        (run / "hunt.json").write_text(json.dumps({"quest": 11001, "state": "quest ended", "kills": 1,
            "success": None, "mission_state": 7,
            "objective": {"name": "Synthetic quest", "description": "Abattre 20 Bullfangos"},
            "unrelated_private_data": "never export this"}), encoding="utf-8")
        summary = reports.summarize_run(run)
        self.assertNotIn("unrelated_private_data", summary["hunt"])
        page = reports.write_run_report(run).read_text(encoding="utf-8")
        self.assertIn("Abattre 20 Bullfangos", page)
        self.assertIn("Morts observées par le contrôleur", page)
        self.assertIn("Non établi (état de mission 7)", page)
        self.assertNotIn("Victoire confirmée", page)

    def run_dir(self, name="run-a", **changes):
        directory = self.root / "data" / "runs" / name
        directory.mkdir(parents=True)
        metadata = {"id": name, "scenario": "quête-reproduction", "variant": "normal", "asset_sha256": "a" * 64, "build": {"git_commit": "abcdef", "dirty": False, "executable_sha256": "b" * 64}, "settings_overrides": {"frame_rate": 60}, "env_overrides": {"MHP3RD_STRICT_HLE": "1"}, "state": "finished", "exit_code": 4}
        metadata.update(changes)
        (directory / "manifest.json").write_text(json.dumps(metadata), encoding="utf-8")
        return directory

    def test_report_preserves_game_counter_without_inferring_success(self):
        run = self.run_dir()
        value = {"quest": 11001, "success": None, "mission_state": 2,
                 "objective_progress": {"completed": 20, "required": 20, "observed_at": 100},
                 "objective_progress_available": False}
        (run / "hunt.json").write_text(json.dumps(value), encoding="utf-8")
        page = reports.write_run_report(run).read_text(encoding="utf-8")
        self.assertIn("Dernier relevé de l’objectif du jeu", page)
        self.assertIn("20 / 20", page)
        self.assertIn("Non établi (état de mission 2)", page)

    def test_actual_perf_samples_and_counter_lower_bound(self):
        run = self.run_dir()
        (run / "stdout.txt").write_text(
            "[perf] fps 60.0 game 30.0 speed 100% | frame avg 16.7 max 22.5 ms | space vertex 36.5 index 1.2 MiB\n"
            "[perf] fps 40.0 game 30.0 speed 90% | space vertex 60.1 index 2.1 MiB\n"
            "[render] a frame ran out of vertex space; the draws past it are not drawn (1 so far)\n"
            "[render] a frame ran out of vertex space; the draws past it are not drawn (601 so far)\n"
            "HLE imports: 3 total, 2 implemented, 1 logging stubs (strict mode)\n"
            "Missing HLE: sceTestMissing\nRuntime stopped: window closed\n", encoding="utf-8")
        (run / "stderr.txt").write_text("[render] a frame ran out of vertex space; the draws past it are not drawn (301 so far)\nMissing HLE: sceTestMissing\n", encoding="utf-8")
        value = reports.summarize_run(run)
        self.assertEqual(value["perf"]["samples"], 2)
        self.assertEqual(value["perf"]["fps"]["mean"], 50.0)
        self.assertEqual(value["perf"]["game_fps"]["mean"], 30.0)
        self.assertEqual(value["perf"]["speed_percent"]["last"], 90.0)
        self.assertEqual(value["perf"]["vertex_mib_max"], 60.1)
        self.assertEqual(value["perf"]["index_mib_max"], 2.1)
        self.assertEqual(value["vertex_space"]["dropped_draws_lower_bound"], 601)
        self.assertEqual(value["vertex_space"]["warning_lines"], 3)
        self.assertEqual(value["missing_hle_names"], ["sceTestMissing"])
        self.assertEqual(value["runtime_stop"], "window closed")
        self.assertEqual(value["hle"]["logging_stubs"], 1)

    def test_absence_is_not_success(self):
        run = self.run_dir()
        summary = reports.summarize_run(run)
        self.assertEqual(summary["capture_proof"]["status"], "unavailable")
        self.assertEqual(summary["capture_proof"]["visual_validation"], "non_effectuee")
        self.assertIsNone(summary["perf"]["fps"]["mean"])
        report = reports.write_run_report(run)
        self.assertTrue(report.is_absolute())
        self.assertIn("Aucune capture disponible", report.read_text(encoding="utf-8"))

    def test_comparison_warns_on_changed_start_and_escapes_html(self):
        a = self.run_dir("a", scenario="<script>bad()</script>")
        b = self.run_dir("b", scenario="other quest", asset_sha256="c" * 64, settings_overrides={"frame_rate": 30}, variant="test")
        for run in (a, b):
            (run / "frames").mkdir()
            (run / "frames" / "shot.bmp").write_bytes(b"BM-test")
        output = reports.compare_runs(a, b, self.root / "comparison")
        value = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        html = output.read_text(encoding="utf-8")
        self.assertTrue(any("Scénario" in warning for warning in value["warnings"]))
        self.assertTrue(any("Empreinte" in warning for warning in value["warnings"]))
        self.assertTrue(any("Réglages" in warning for warning in value["warnings"]))
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>bad()", html)
        self.assertTrue((output.parent / "premier.bmp").is_file())
        self.assertTrue((output.parent / "second.bmp").is_file())
        self.assertIsNone(value["performance_delta"]["fps_mean_second_minus_first"])

    def test_bug_bundle_is_allowlisted_and_redacts_private_lines(self):
        run = self.run_dir(env={"EVERYTHING": "never-copy"}, env_overrides={"access_token": "unsafe-value", "MHP3RD_STRICT_HLE": "1"})
        (run / "stdout.txt").write_text("fps diagnostics\nAPI_KEY=top-private-value\nauthorization: Bearer another-private-value\n-----BEGIN PRIVATE KEY-----\nbase64-secret-body\n-----END PRIVATE KEY-----\nend of diagnostics\n", encoding="utf-8")
        (run / "stderr.txt").write_text("token=secret-value\n", encoding="utf-8")
        (run / "input.txt").write_text("frame 200 cross\n", encoding="utf-8")
        (run / "data").mkdir()
        (run / "data" / "settings.ini").write_text("frame_rate=60\npassword=private-config\n", encoding="utf-8")
        (run / "data" / "disc.iso").write_bytes(b"DO NOT READ OR BUNDLE")
        (run / "data" / "save.bin").write_bytes(b"PRIVATE SAVE")
        (run / "frames").mkdir()
        (run / "frames" / "shot.png").write_bytes(b"test-image")
        destination = reports.write_bug_bundle(run, "HUD <missing>")
        self.assertEqual(destination.parent.name, "bugs")
        self.assertTrue(destination.with_suffix(".zip").is_file())
        with zipfile.ZipFile(destination.with_suffix(".zip")) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", "stdout.txt", "stderr.txt", "input.txt", "settings.ini", "capture.png", "diagnostics.json", "diagnostics.html", "README.md"})
            contents = "\n".join(archive.read(name).decode("utf-8", errors="replace") for name in archive.namelist())
        for text in ("top-private-value", "another-private-value", "base64-secret-body", "secret-value", "private-config", "unsafe-value", "never-copy", "DO NOT READ OR BUNDLE", "PRIVATE SAVE"):
            self.assertNotIn(text, contents)
        self.assertIn("end of diagnostics", contents)
        self.assertIn("&lt;missing&gt;", (destination / "diagnostics.html").read_text(encoding="utf-8"))
        value = json.loads((destination / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertFalse(value["bug"]["availability"]["synchronized_ge_state"])
        self.assertEqual(value["capture_proof"]["visual_validation"], "non_effectuee")

    def test_bug_bundle_refuses_arbitrary_screenshot_file(self):
        run = self.run_dir()
        external = self.root / "private.png"
        external.write_bytes(b"outside capture")
        destination = reports.write_bug_bundle(run, "test", external)
        summary = json.loads((destination / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["bug"]["availability"]["screenshot"]["available"])
        self.assertFalse((destination / "capture.png").exists())

    def test_overlong_trace_and_malformed_perf_do_not_break_summary(self):
        run = self.run_dir()
        (run / "stdout.txt").write_text("x" * (reports.MAX_LINE_BYTES * 2) + "\n[perf] fps 1..0 game 1 speed 100%\n[perf] fps 60.0 game 30 speed 100%\n", encoding="utf-8")
        summary = reports.summarize_run(run)
        self.assertEqual(summary["perf"]["samples"], 1)
        self.assertEqual(summary["perf"]["fps"]["mean"], 60.0)

    def test_truncated_copy_keeps_tail_and_notice(self):
        source = self.root / "long.txt"
        destination = self.root / "snapshot.txt"
        source.write_text("start\n" + "padding\n" * 100 + "last diagnostic\n", encoding="utf-8")
        previous = reports.MAX_LOG_BYTES
        reports.MAX_LOG_BYTES = 100
        try:
            availability = reports._sanitized_copy(source, destination)
        finally:
            reports.MAX_LOG_BYTES = previous
        self.assertTrue(availability["truncated"])
        value = destination.read_text(encoding="utf-8")
        self.assertIn("Instantané tronqué", value)
        self.assertIn("last diagnostic", value)
        self.assertNotIn("start", value)

    def test_manifest_absent_or_invalid_still_writes_report(self):
        run = self.run_dir()
        (run / "manifest.json").write_text("{half-written", encoding="utf-8")
        report = reports.write_run_report(run)
        self.assertTrue(report.exists())
        self.assertTrue(any("Manifeste" in item for item in reports.summarize_run(run)["limitations"]))


if __name__ == "__main__":
    unittest.main()
