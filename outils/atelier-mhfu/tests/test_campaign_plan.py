"""Admission checks use a synthetic registry and never touch a game installation."""
import json
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_plan
import atelier_pilot


def mission(quest, kind="hunt", **extra):
    return {"quest": quest, "name": f"test {quest}", "kind": kind,
            "descriptors": [{"type": 1, "target": 6, "quantity": 1}], **extra}


class CampaignPlan(unittest.TestCase):
    def test_missing_files_and_native_only_ids_are_visible(self):
        plan = atelier_plan.build_plan([1, 2], [mission(1), mission(60001)])
        self.assertEqual(plan["quests"], [1])
        self.assertEqual([(r["quest"], r["status"]) for r in plan["rows"]],
                         [(1, "eligible"), (2, "missing_metadata"), (60001, "not_admitted")])
        self.assertTrue(all(not r["gameplay_verified"] for r in plan["rows"]))

    def test_delivery_and_special_objectives_can_be_surveyed(self):
        plan = atelier_plan.build_plan([1, 2, 3], [mission(1, "delivery"),
            mission(2, "unsupported", automation_exclusion="special objective types: 0x18004"),
            mission(3, "unsupported", automation_exclusion="empty quest placeholder")])
        self.assertEqual(plan["quests"], [1, 2])
        self.assertEqual(plan["eligible_kinds"], {"delivery": 1, "unsupported": 1})
        self.assertEqual(plan["rows"][-1]["status"], "empty")

    def test_empty_downloads_are_not_reported_as_playable(self):
        plan = atelier_plan.build_plan([1], [mission(1), mission(50002, "unsupported",
                                           automation_exclusion="empty quest placeholder")])
        row = plan["rows"][-1]
        self.assertEqual(row["status"], "empty")
        self.assertFalse(row["native_admitted"])

    def test_batches_are_ordered_unique_and_complete(self):
        plan = atelier_plan.build_plan([3, 1, 2, 1], [mission(3), mission(2), mission(1)], 2)
        self.assertEqual(plan["batches"], [[1, 2], [3]])
        self.assertEqual(plan["quests"], [1, 2, 3])

    def test_invalid_or_incomplete_metadata_is_rejected(self):
        for records in ([], [mission(0)], [mission(True)], [mission(65536)],
                        [mission(1), mission(1)], [mission(1, "guess")], [{"quest": 1, "kind": "hunt"}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                atelier_plan.build_plan([1], records)

    def test_batch_size_and_native_ids_are_validated(self):
        for size in (0, -1, True, 1.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                atelier_plan.build_plan([1], [mission(1)], size)
        for quest in (0, True, 65536):
            with self.subTest(quest=quest), self.assertRaises(ValueError):
                atelier_plan.build_plan([quest], [mission(1)])

    def test_local_inputs_are_fingerprinted_and_no_runtime_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, native = root / atelier_plan.REGISTRY, root / atelier_plan.NATIVE_SOURCE
            registry.parent.mkdir(parents=True)
            native.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"schema": 1, "source": "quest_files", "missions": [mission(1)]}),
                                encoding="utf-8")
            native.write_text("kQuestIds[] = {{1, 1}};", encoding="utf-8")
            with patch("atelier_core.Workshop", side_effect=AssertionError("no runtime needed")):
                first = atelier_plan.load_plan(root)
            self.assertEqual(first["quests"], [1])
            self.assertEqual(len(first["inputs"]["registry"]["sha256"]), 64)
            native.write_text("kQuestIds[] = {{1, 2}};", encoding="utf-8")
            second = atelier_plan.load_plan(root)
            self.assertNotEqual(first["inputs"]["native_source"], second["inputs"]["native_source"])
            self.assertEqual(second["counts"]["missing_metadata"], 1)

    def test_bad_or_missing_registry_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "régénérez"):
                atelier_plan.load_plan(root)
            registry = root / atelier_plan.REGISTRY
            registry.parent.mkdir(parents=True)
            for text in ("broken", "[]", '{"schema": 9}', '{"schema": 1, "source": "quest_files"}'):
                registry.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(ValueError):
                    atelier_plan.load_plan(root)

    def test_writing_refuses_to_overwrite_an_existing_report(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "report"
            plan = atelier_plan.build_plan([1], [mission(1), mission(60001)])
            path = atelier_plan.write_plan(plan, out)
            self.assertIn("60001", path.read_text(encoding="utf-8"))
            expected = (out / "plan.json").read_bytes()
            with self.assertRaises(FileExistsError):
                atelier_plan.write_plan(plan, out)
            self.assertEqual((out / "plan.json").read_bytes(), expected)

    def test_cli_plan_does_not_start_workshop_or_campaign(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "report"
            plan = atelier_plan.build_plan([1], [mission(1)])
            with patch("atelier_plan.load_plan", return_value=plan), \
                 patch("atelier_core.Workshop", side_effect=AssertionError("no game")), \
                 patch("atelier_pilot.run_campaign", side_effect=AssertionError("no campaign")):
                self.assertEqual(atelier_pilot.main(["--plan", "--plan-out", str(out)]), 0)
            self.assertTrue((out / "plan.json").is_file())

    def test_all_selects_admissible_nonempty_missions_and_limit_bounds_the_batch(self):
        plan = atelier_plan.build_plan([1, 2, 3], [mission(1), mission(2),
            mission(3, "unsupported", automation_exclusion="empty quest placeholder"), mission(60001)])
        with patch("atelier_plan.load_plan", return_value=plan), \
             patch("atelier_core.Workshop") as workshop, \
             patch("atelier_pilot.run_campaign", return_value=Path("report")) as run:
            self.assertEqual(atelier_pilot.main(["--all", "--limit", "1"]), 0)
        self.assertEqual(run.call_args.args[1], [1])
        self.assertEqual(run.call_args.args[0], workshop.return_value)

    def test_explicit_quest_order_is_preserved(self):
        with patch("atelier_core.Workshop"), patch("atelier_plan.load_plan", side_effect=AssertionError("explicit ids")), \
             patch("atelier_pilot.run_campaign", return_value=Path("report")) as run:
            self.assertEqual(atelier_pilot.main(["21001", "10001", "1001", "--limit", "2"]), 0)
        self.assertEqual(run.call_args.args[1], [21001, 10001])

    def test_invalid_cli_combinations_fail_before_a_workshop_exists(self):
        for args in (["--limit", "0", "1"], ["--all", "1"], ["--plan", "1"],
                     ["--plan", "--limit", "2"], ["--plan-out", "new"], ["0"], ["65536"],
                     ["--resume", "old", "--limit", "1"], ["--resume", "old", "--all"]):
            with self.subTest(args=args), patch("atelier_core.Workshop", side_effect=AssertionError("no launch")), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                atelier_pilot.main(args)
            self.assertEqual(error.exception.code, 2)

    def test_all_refuses_missing_registry_before_any_workshop(self):
        with patch("atelier_plan.load_plan", side_effect=ValueError("missing registry")), \
             patch("atelier_core.Workshop", side_effect=AssertionError("no launch")), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            atelier_pilot.main(["--all"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
