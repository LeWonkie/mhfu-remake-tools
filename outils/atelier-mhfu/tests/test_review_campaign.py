"""Synthetic image and log evidence tests; no game data or live runtime needed."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

HERE = Path(__file__).resolve()
MODULE_PATH = HERE.parents[1] / "review_campaign.py"
SPEC = importlib.util.spec_from_file_location("review_campaign", MODULE_PATH)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class ImageIndicatorTests(unittest.TestCase):
    def test_black_threshold_is_inclusive_and_white_is_only_uniform(self):
        boundary = review.image_metrics(Image.new("RGB", (8, 8), (8, 8, 8)), 8, 3)
        self.assertEqual(boundary["mean_luminance"], 8)
        self.assertTrue(boundary["near_black"])
        self.assertTrue(boundary["near_uniform"])
        white = review.image_metrics(Image.new("RGB", (8, 8), "white"), 8, 3)
        self.assertFalse(white["near_black"])
        self.assertTrue(white["near_uniform"])

    def test_dark_texture_is_black_but_not_uniform(self):
        image = Image.new("RGB", (2, 1))
        image.putdata([(0, 0, 0), (16, 16, 16)])
        metrics = review.image_metrics(image, 8, 3)
        self.assertTrue(metrics["near_black"])
        self.assertFalse(metrics["near_uniform"])
        self.assertEqual(metrics["max_rgb_stddev"], 8)

    def test_rgb_variation_is_not_hidden_by_equal_luminance(self):
        image = Image.new("RGB", (2, 1))
        image.putdata([(255, 0, 0), (0, 130, 0)])
        metrics = review.image_metrics(image, 8, 3)
        self.assertEqual(metrics["luminance_stddev"], 0)
        self.assertFalse(metrics["near_uniform"])
        self.assertFalse(metrics["near_black"])

    def test_uniform_threshold_is_inclusive(self):
        image = Image.new("RGB", (2, 1))
        image.putdata([(100, 100, 100), (106, 106, 106)])
        self.assertTrue(review.image_metrics(image, 8, 3)["near_uniform"])
        self.assertFalse(review.image_metrics(image, 8, 2.9)["near_uniform"])

    def test_dark_flag_starts_above_near_black_and_is_inclusive(self):
        dim = review.image_metrics(Image.new("RGB", (4, 4), (18, 18, 18)), 8, 3, 20)
        self.assertTrue(dim["dark"])
        self.assertFalse(dim["near_black"])
        black = review.image_metrics(Image.new("RGB", (4, 4), (8, 8, 8)), 8, 3, 20)
        self.assertFalse(black["dark"])
        self.assertTrue(black["near_black"])
        self.assertTrue(review.image_metrics(Image.new("RGB", (4, 4), (20, 20, 20)), 8, 3, 20)["dark"])
        self.assertFalse(review.image_metrics(Image.new("RGB", (4, 4), (21, 21, 21)), 8, 3, 20)["dark"])
        self.assertEqual(review.DEFAULT_DARK_MEAN, 20)


class LogIntervalTests(unittest.TestCase):
    def test_preceding_interval_does_not_leak_into_next_capture_or_quest(self):
        text = "\r\r\n".join([
            "[render] ran out of vertex space",
            "[debug] > quest start 11001",
            "[render] a frame ran out of vertex space; the draws past it are not drawn (601 so far)",
            "[script] frame 100: shot q11001-arrivee",
            "[framespace] frame 101: 12 draws, 63.9 MiB, 0 dropped, 4 sites",
            "[framespace]   cmd=00000001 ret=00000002 vaddr=00000003 prim=3 vtype=000000 calls=1 verts=4 1.00 MiB",
            "[script] frame 200: shot q11001-marche",
            "[framespace] frame 201: 16 draws, 64.0 MiB, 7 dropped, 5 sites",
            "[script] frame 300: shot q11001-pendant",
            "[render] ran out of vertex space",
            "[debug] > quest start 1201",
            "[script] frame 400: shot q1201-arrivee",
        ])
        intervals, unassigned = review.capture_intervals(text, "stdout.txt")
        first = intervals[11001]["arrivee"]
        self.assertEqual((first["start_line"], first["end_line"]), (2, 4))
        self.assertEqual(first["warning_messages"], 1)
        self.assertEqual(first["warning_counter_peak"], 601)
        self.assertTrue(first["saturation_observed"])
        walk = intervals[11001]["marche"]
        self.assertEqual(walk["warning_messages"], 0)
        self.assertFalse(walk["saturation_observed"])
        self.assertEqual(len(walk["framespace_lines"]), 2)
        self.assertEqual(walk["framespace_peak_mib"], 63.9)
        during = intervals[11001]["pendant"]
        self.assertTrue(during["saturation_observed"])
        self.assertEqual(during["framespace_dropped_draws"], 7)
        self.assertFalse(intervals[1201]["arrivee"]["saturation_observed"])
        self.assertEqual(sum(value["warning_messages"] for value in unassigned), 2)

    def test_automatic_captures_use_renderer_markers(self):
        text = "\n".join([
            "[render] a frame ran out of vertex space; the draws past it are not drawn (1 so far)",
            "[render] frame 60 (1200 draws) -> C:/runs/x/frames/frame_60.bmp",
            "[script] frame 61: shot q10501-arrivee",
            "[framespace] frame 100: 4516 draws, 64.0 MiB, 1624 dropped, 4516 sites",
            "[render] frame 120 (4516 draws) -> C:/runs/x/frames/frame_120.bmp",
            "[render] frame 180 (4816 draws) -> C:/runs/x/frames/frame_180.bmp",
            "[render] a frame ran out of vertex space; the draws past it are not drawn (601 so far)",
        ])
        auto = review.auto_capture_intervals(text, "stdout.txt")
        self.assertEqual(set(auto), {"frame_60.bmp", "frame_120.bmp", "frame_180.bmp"})
        self.assertEqual((auto["frame_60.bmp"]["start_line"], auto["frame_60.bmp"]["end_line"]), (1, 2))
        self.assertTrue(auto["frame_60.bmp"]["saturation_observed"])
        self.assertEqual(auto["frame_120.bmp"]["framespace_dropped_draws"], 1624)
        self.assertEqual(auto["frame_120.bmp"]["draws_submitted"], 4516)
        self.assertEqual(auto["frame_120.bmp"]["capture_frame"], 120)
        # Evidence after the last automatic capture belongs to none of them.
        self.assertFalse(auto["frame_180.bmp"]["saturation_observed"])
        named, _ = review.capture_intervals(text, "stdout.txt")
        self.assertTrue(named[10501]["arrivee"]["saturation_observed"])

    def test_unmarked_stream_stays_unassigned(self):
        captures, unassigned = review.capture_intervals(
            "[render] ran out of vertex space\n[framespace] incomplete diagnostic\n", "stderr.txt")
        self.assertEqual(captures, {})
        self.assertEqual(unassigned[0]["stream"], "stderr.txt")
        self.assertEqual(unassigned[0]["warning_messages"], 1)
        self.assertEqual(unassigned[0]["framespace_summaries"], 0)
        self.assertEqual(len(unassigned[0]["framespace_lines"]), 1)


class ReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review-campaign-")
        self.root = Path(self.temp.name)
        self.run = self.root / "data" / "runs" / "synthetic-run"
        (self.run / "frames").mkdir(parents=True)
        (self.run / "manifest.json").write_text(json.dumps({"state": "finished"}), encoding="utf-8")
        (self.run / "stdout.txt").write_text(
            "[debug] > quest start 11001\n"
            "[render] a frame ran out of vertex space; the draws past it are not drawn (1 so far)\n"
            "[framespace] frame 99: 8 draws, 64.0 MiB, 2 dropped, 3 sites\n"
            "[script] frame 100: shot q11001-arrivee\n", encoding="utf-8")
        (self.run / "stderr.txt").write_text("[render] ran out of vertex space\n", encoding="utf-8")
        Image.new("RGB", (40, 24), "black").save(self.run / "frames" / "q11001-arrivee.bmp")
        Image.new("RGB", (40, 24), "white").save(self.run / "frames" / "frame_600.bmp")
        (self.run / "frames" / "q11001-marche.bmp").write_bytes(b"broken image")
        self.campaign = self.root / "data" / "campaigns" / "synthetic"
        self.campaign.mkdir(parents=True)
        (self.campaign / "campagne.json").write_text(json.dumps({
            "runs": [self.run.name], "results": [{"quest": 11001, "run_id": self.run.name, "returned": True}]
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {str(path.relative_to(self.root / "data")): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (self.root / "data").rglob("*") if path.is_file()}

    def invoke(self, out, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            review.main(["--campaign", str(self.campaign), "--data-dir", str(self.root / "data"),
                         "--out", str(out), *extra])

    def test_report_flags_all_images_preserves_sources_and_keeps_unknown_evidence(self):
        before = self.snapshot()
        out = self.root / "review"
        self.invoke(out)
        self.assertEqual(self.snapshot(), before)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        first = next(item for item in summary["image_audit"] if item["phase"] == "arrivee")
        self.assertEqual(set(first["flags"]), {"vertex_saturation", "near_black", "near_uniform"})
        self.assertEqual(first["log_interval"]["framespace_dropped_draws"], 2)
        auto = next(item for item in summary["image_audit"] if item["phase"] == "frame_600.bmp")
        self.assertEqual(auto["flags"], ["near_uniform"])
        self.assertIsNone(auto["vertex_saturation"])
        broken = next(item for item in summary["image_audit"] if item["phase"] == "marche")
        self.assertIn("error", broken)
        missing = next(item for item in summary["image_audit"] if item["phase"] == "fin")
        self.assertFalse(missing["exists"])
        self.assertNotIn("near_black", missing)
        self.assertEqual(summary["runs"][0]["unassigned_log_evidence"][0]["stream"], "stderr.txt")
        self.assertEqual(summary["runs"][0]["vertex_saturation"]["11001"]["warning_messages"], 1)
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("near_black", page)
        self.assertIn("vertex_saturation", page)
        self.assertIn("stdout.txt:1-4", page)
        self.assertIn("unassigned log evidence: 1 vertex warnings", page)
        self.assertTrue((out / "planche-01.png").is_file())

    def test_existing_output_and_nested_source_output_are_refused(self):
        before = self.snapshot()
        for output in (self.run, self.run / "new-report", self.campaign / "new-report"):
            with self.assertRaises(SystemExit):
                self.invoke(output)
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("original", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.invoke(output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")
        self.assertEqual(self.snapshot(), before)

    def pilot_campaign(self, mode):
        path = self.campaign / "campagne.json"
        campaign = json.loads(path.read_text(encoding="utf-8"))
        campaign.update(driver="atelier_pilot", settings={"captures": mode})
        path.write_text(json.dumps(campaign), encoding="utf-8")
        phases = ("arrivee", "pendant", "fin", "fin2", "village")
        if mode == "detailed":
            phases += ("marche", "resultat")
        for phase in phases:
            Image.new("RGB", (40, 24), "gray").save(self.run / "frames" / f"q11001-{phase}.bmp")
        with (self.run / "stdout.txt").open("a", encoding="utf-8") as log:
            log.write("[framespace] frame 110: 8 draws, 64.0 MiB, 3 dropped, 3 sites\n"
                      "[script] frame 120: shot q11001-fin2\n")
        return phases

    def test_fast_pilot_excludes_unrequested_phases_and_displays_second_end(self):
        phases = self.pilot_campaign("fast")
        before = self.snapshot()
        out = self.root / "fast-review"
        self.invoke(out)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        named = [item for item in summary["image_audit"] if item["phase"] in review.PILOT_PHASES]
        self.assertEqual({item["phase"] for item in named}, set(phases))
        self.assertTrue(all(item["exists"] for item in named))
        second_end = next(item for item in named if item["phase"] == "fin2")
        self.assertTrue(second_end["vertex_saturation"])
        self.assertEqual(second_end["log_interval"]["framespace_dropped_draws"], 3)
        with Image.open(out / "planche-01.png") as sheet:
            self.assertEqual(sheet.width, 5 * 256)
        # Unrequested files still receive the ordinary audit, including corrupt ones.
        self.assertTrue(any("error" in item for item in summary["image_audit"]))
        self.assertEqual(self.snapshot(), before)

    def test_fast_pilot_still_reports_a_missing_requested_second_end(self):
        self.pilot_campaign("fast")
        (self.run / "frames" / "q11001-fin2.bmp").unlink()
        out = self.root / "missing-review"
        self.invoke(out)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        missing = [item["phase"] for item in summary["image_audit"] if not item["exists"]]
        self.assertEqual(missing, ["fin2"])

    def test_detailed_pilot_requests_all_seven_phases(self):
        phases = self.pilot_campaign("detailed")
        out = self.root / "detailed-review"
        self.invoke(out)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        named = [item for item in summary["image_audit"] if item["phase"] in review.PILOT_PHASES]
        self.assertEqual({item["phase"] for item in named}, set(phases))
        self.assertTrue(all(item["exists"] for item in named))
        with Image.open(out / "planche-01.png") as sheet:
            self.assertEqual(sheet.width, 7 * 256)

    def test_invalid_thresholds_are_refused_before_creating_output(self):
        for option, value in (("--black-mean", "nan"), ("--black-mean", "-1"),
                              ("--uniform-stddev", "256"), ("--uniform-stddev", "inf")):
            output = self.root / "invalid"
            with self.assertRaises(SystemExit):
                self.invoke(output, option, value)
            self.assertFalse(output.exists())

    def test_html_escapes_capture_labels(self):
        item = {"quest": 11001, "run_id": "<script>bad</script>", "phase": "<unsafe>",
                "path": None, "exists": False, "flags": [], "log_interval": None}
        page = review.indicators_html([item])
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;unsafe&gt;", page)
        self.assertNotIn("<script>", page)


if __name__ == "__main__":
    unittest.main()
