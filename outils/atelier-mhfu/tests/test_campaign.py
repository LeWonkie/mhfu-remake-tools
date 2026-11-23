"""Campaign persistence and state attribution with synthetic sessions only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atelier_campaign as campaign
import atelier_core


RTC_US = 1_700_000_000_000_000


class SyntheticWorkshop:
    def __init__(self, root):
        self.data_dir = root
        self.current = {"id": "first", "seed_sha256": "original-hash"}
        self.seeds = {"first": copy.copy(self.current),
                      "second": {"id": "second", "seed_sha256": "other-hash"}}
        self.sessions = []

    def _current_seed(self):
        return copy.copy(self.current)

    def _seed(self, seed_id):
        return copy.copy(self.seeds[seed_id])

    def record_quest_result(self, run_id, result):
        path = self.data_dir / "runs" / run_id / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["quest_results"].append(copy.deepcopy(result))
        manifest["window_size"] = result.get("window_size")
        path.write_text(json.dumps(manifest), encoding="utf-8")


class SyntheticSession:
    def __init__(self, workshop, seed_id, seconds, dump_overlays=True, variant="normal"):
        self.w = workshop
        self.seed_id = seed_id
        self.variant = variant
        self.began = time.monotonic()
        self.stopped = False
        self.stop_count = 0
        self.run_id = f"synthetic-{len(workshop.sessions) + 1}"
        self.dir = workshop.data_dir / "runs" / self.run_id
        self.dir.mkdir(parents=True)
        (self.dir / "manifest.json").write_text(json.dumps({"runtime_stop": None,
             "quest_results": [], "window_size": None}), encoding="utf-8")
        workshop.sessions.append(self)
        self.armed = []

    def arm_loadout(self, loadout):
        self.armed.append(loadout)
        return True

    def alive(self):
        return not self.stopped

    def wait_task(self, task, timeout, quest=None):
        return (task, quest or 1, 70)

    def stop(self):
        self.stopped = True
        self.stop_count += 1


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="campaign-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workshop = SyntheticWorkshop(self.root)
        self.addCleanup(patch.stopall)
        patch.object(campaign, "Session", SyntheticSession).start()
        patch.object(campaign.time, "sleep").start()

    def checkpoint(self):
        paths = list((self.root / "campaigns").glob("*/campagne.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_cancel_during_quest_never_restarts_next_quest(self):
        cancel = threading.Event()
        def drive(session, quest, look_seconds, result, **options):
            result.started = True
            cancel.set()
        with patch.object(campaign, "run_quest", side_effect=drive) as drive_mock:
            campaign.run_campaign(self.workshop, [11001, 11002], cancel=cancel)
        self.assertEqual(drive_mock.call_count, 1)
        self.assertEqual(len(self.workshop.sessions), 1)
        self.assertEqual(self.checkpoint()["state"], "cancelled")
        self.assertTrue(self.workshop.sessions[0].stopped)

    def test_interrupt_preserves_previous_and_partial_quest_and_stops_session(self):
        def drive(session, quest, look_seconds, result, **options):
            saved = self.checkpoint()
            if quest == 11001:
                self.assertEqual(saved["results"], [])
                result.started = result.reached = result.returned = True
                result.stage = 70
                result.window_size = [960, 544]
            else:
                self.assertEqual(saved["results"][0]["stage"], 70)
                self.assertTrue(saved["results"][0]["returned"])
                result.started = True
                result.notes.append("synthetic interruption")
                raise KeyboardInterrupt

        with patch.object(campaign, "run_quest", side_effect=drive):
            with self.assertRaises(KeyboardInterrupt):
                campaign.run_campaign(self.workshop, [11001, 11002], log=lambda message: None,
                                      rtc_us=RTC_US)
        data = self.checkpoint()
        self.assertEqual(data["state"], "interrupted")
        self.assertEqual(data["variant"], f"normal_rtc_{RTC_US}")
        self.assertEqual(data["rtc_us"], RTC_US)
        self.assertEqual(data["seed_id"], "first")
        self.assertEqual(data["seed_sha256"], "original-hash")
        self.assertEqual(data["window_size"], [960, 544])
        self.assertEqual([row["quest"] for row in data["results"]], [11001, 11002])
        self.assertEqual(data["results"][1]["notes"], ["synthetic interruption"])
        session = self.workshop.sessions[0]
        self.assertEqual(session.stop_count, 1)
        manifest = json.loads((session.dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["quest_results"]), 2)
        self.assertTrue(manifest["quest_results"][1]["started"])

    def test_seed_is_pinned_across_restarts_when_current_seed_changes(self):
        def drive(session, quest, look_seconds, result, **options):
            self.workshop.current = self.workshop.seeds["second"]
            result.started = result.reached = True
            result.stage = 75
            result.returned = quest == 11002

        with patch.object(campaign, "run_quest", side_effect=drive):
            campaign.run_campaign(self.workshop, [11001, 11002], log=lambda message: None)
        self.assertEqual([session.seed_id for session in self.workshop.sessions], ["first", "first"])
        self.assertEqual([session.armed for session in self.workshop.sessions], [["best"], ["best"]],
                         "each new game gets the loadout before its save is loaded")
        self.assertTrue(all(session.stopped for session in self.workshop.sessions))
        data = self.checkpoint()
        self.assertEqual(data["state"], "completed")
        self.assertEqual(data["seed_id"], "first")
        self.assertEqual(data["seed_sha256"], "original-hash")
        self.assertIsNone(data["rtc_us"])

    def test_explicit_seed_is_used_even_when_current_seed_differs(self):
        with patch.object(campaign, "run_quest"):
            campaign.run_campaign(self.workshop, [11001], seed_id="second", log=lambda message: None)
        self.assertEqual(self.workshop.sessions[0].seed_id, "second")
        self.assertEqual(self.checkpoint()["seed_sha256"], "other-hash")

    def test_failure_writes_report_and_stops_session(self):
        def drive(session, quest, look_seconds, result, **options):
            result.started = True
            raise RuntimeError("synthetic guest failed")

        with patch.object(campaign, "run_quest", side_effect=drive):
            with self.assertRaisesRegex(RuntimeError, "synthetic guest failed"):
                campaign.run_campaign(self.workshop, [11001], log=lambda message: None)
        self.assertEqual(self.checkpoint()["state"], "failed")
        self.assertTrue(self.checkpoint()["results"][0]["started"])
        self.assertTrue(self.workshop.sessions[0].stopped)

    def test_failed_atomic_replace_keeps_last_complete_json(self):
        out = self.root / "report"
        out.mkdir()
        previous = '{"results": ["previous checkpoint"]}'
        target = out / "campagne.json"
        target.write_text(previous, encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                campaign._write_report(self.workshop, out, [campaign.QuestResult(11001)])
        self.assertEqual(target.read_text(encoding="utf-8"), previous)

    def test_invalid_clock_is_rejected_before_campaign_creation(self):
        with self.assertRaises(ValueError):
            campaign.run_campaign(self.workshop, [11001], rtc_us=1)
        with self.assertRaises(ValueError):
            campaign.run_campaign(self.workshop, [11001], loadout="worst")
        with self.assertRaises(ValueError):
            campaign.run_campaign(self.workshop, [11001], cheats=("god",))
        self.assertFalse((self.root / "campaigns").exists())

    def test_startup_failure_is_checkpointed_and_session_stopped(self):
        with patch.object(SyntheticSession, "wait_task", return_value=None):
            campaign.run_campaign(self.workshop, [11001], log=lambda message: None)
        self.assertEqual(self.checkpoint()["state"], "failed")
        self.assertFalse(self.checkpoint()["results"][0]["reached"])
        self.assertTrue(self.workshop.sessions[0].stopped)

    def test_same_second_campaigns_do_not_overwrite_previous_results(self):
        with patch.object(campaign, "run_quest"):
            first = campaign.run_campaign(self.workshop, [11001], log=lambda message: None)
            original = (first / "campagne.json").read_bytes()
            second = campaign.run_campaign(self.workshop, [11002], log=lambda message: None)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "campagne.json").read_bytes(), original)


class QuestStateTests(unittest.TestCase):
    def test_wait_task_rejects_another_quest_or_unloaded_area(self):
        session = object.__new__(campaign.Session)
        states = iter([(10, 11001, 70), (11, 11002, 75), (11, 11001, 0), (11, 11001, 70)])
        with patch.object(session, "alive", return_value=True), \
             patch.object(session, "state", side_effect=lambda: next(states)), \
             patch.object(campaign.time, "sleep"):
            self.assertEqual(session.wait_task(11, 1, quest=11001), (11, 11001, 70))

    def test_loadout_is_armed_only_once_the_game_reads_commands(self):
        session = object.__new__(campaign.Session)
        answers = iter([None, None, "[debug] hunter , HR 0, 0z; task 5 (other)",
                        "[debug] loadout best armed: worn when the character is loaded"])
        with patch.object(session, "alive", return_value=True), \
             patch.object(session, "ask", side_effect=lambda *args: next(answers)) as ask:
            self.assertTrue(session.arm_loadout("best"))
        self.assertEqual([c.args[0] for c in ask.call_args_list], ["state", "state", "state", "loadout best"])
        with patch.object(session, "alive", return_value=False):
            self.assertFalse(session.arm_loadout("best"), "a game that is gone is not armed")

    def quest_session(self):
        from unittest.mock import Mock
        session = Mock()
        session.run_id = "synthetic"
        session.dir = Path(tempfile.gettempdir()) / "synthetic-no-game-captures"
        session.alive.return_value = True
        session.wait_task.side_effect = [(11, 11001, 70), (11, 11001, 75), (10, 1, 1)]
        session.ask.side_effect = lambda command, *args: {
            "quest start 11001": "[debug] started quest 11001",
            "quest time 2": "[debug] quest time set to 2",
            "quest": "[debug] time left 49:30",
        }.get(command, "[debug] hold")
        session.shot.side_effect = lambda name: name
        return session

    def test_effective_stage_is_refreshed_after_loading(self):
        session = self.quest_session()
        with patch.object(campaign.time, "sleep"), \
             patch.object(atelier_core, "capture_size", return_value=[960, 544]):
            result = campaign.run_quest(session, 11001, 0)
        self.assertTrue(result.reached)
        self.assertEqual(result.stage, 75)
        self.assertIsNotNone(result.stage_observed_at)
        self.assertEqual(result.window_size, [960, 544])
        self.assertEqual(session.wait_task.call_args_list[0].kwargs, {"quest": 11001})
        self.assertEqual(session.wait_task.call_args_list[1].kwargs, {"quest": 11001})

    def test_best_loadout_before_start_and_cheats_while_looking(self):
        session = self.quest_session()
        session.ask.side_effect = lambda command, *args: {
            "quest start 11001": "[debug] started quest 11001",
            "loadout best": "[debug] wearing 5:944",
        }.get(command, "[debug] hold")
        with patch.object(campaign.time, "sleep"), \
             patch.object(atelier_core, "capture_size", return_value=[960, 544]):
            result = campaign.run_quest(session, 11001, 0)
        commands = [c.args[0] for c in session.ask.call_args_list]
        self.assertLess(commands.index("loadout best"), commands.index("quest start 11001"))
        on = [commands.index(f"hold {c} on") for c in campaign.DEFAULT_CHEATS]
        off = [commands.index(f"hold {c} off") for c in campaign.DEFAULT_CHEATS]
        self.assertLess(max(on), min(off))
        self.assertLess(max(off), commands.index("quest time 2"))
        self.assertEqual((result.loadout_answer, result.cheats_held), ("wearing 5:944", list(campaign.DEFAULT_CHEATS)))

    def test_refused_loadout_is_noted_and_none_sends_nothing(self):
        session = self.quest_session()
        with patch.object(campaign.time, "sleep"), \
             patch.object(atelier_core, "capture_size", return_value=[960, 544]):
            result = campaign.run_quest(session, 11001, 0)
        self.assertTrue(any("équipement non porté" in note for note in result.notes))
        session = self.quest_session()
        with patch.object(campaign.time, "sleep"), \
             patch.object(atelier_core, "capture_size", return_value=[960, 544]):
            result = campaign.run_quest(session, 11001, 0, loadout="none", cheats=())
        commands = [c.args[0] for c in session.ask.call_args_list]
        self.assertFalse(any(c.startswith(("loadout", "hold")) for c in commands))
        self.assertIsNone(result.loadout_answer)

    def test_no_effective_stage_is_claimed_when_postload_state_is_unconfirmed(self):
        session = self.quest_session()
        session.wait_task.side_effect = [(11, 11001, 70), None]
        with patch.object(campaign.time, "sleep"):
            result = campaign.run_quest(session, 11001, 0)
        self.assertTrue(result.started)
        self.assertFalse(result.reached)
        self.assertIsNone(result.stage)
        session.shot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
